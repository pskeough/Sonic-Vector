"""Flask Web App Server & API Backend with Direct AI Mixing Assistant & OAuth Authorization.

Responsibility: Merges real-time Spotify User OAuth playback polling with a 
high-fidelity GPU-accelerated Local AI Mixing Assistant (or Gemini API) that 
recommends customized parametric EQ configurations and provides engineering justifications.
Gracefully handles write permissions and provides self-healing centroids fallback.
"""

import os
import sys
import time
import json
import sqlite3
import logging
import threading
import yaml
import requests
from pathlib import Path
from flask import Flask, jsonify, request, render_template

# Make sure the main project folder is in the Python path
parent_dir = Path(__file__).parent.absolute()
if (parent_dir / "src").exists():
    PROJECT_ROOT = parent_dir
else:
    PROJECT_ROOT = parent_dir.parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

# Load config.yaml credentials and inject into environment variables
config_path = Path(__file__).parent.absolute() / "config.yaml"
if not config_path.exists():
    config_path = PROJECT_ROOT / "config.yaml"

if config_path.exists():
    try:
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f) or {}
        spotify_cfg = config_data.get("spotify", {})
        if "client_id" in spotify_cfg:
            os.environ["SPOTIFY_CLIENT_ID"] = spotify_cfg["client_id"]
        if "client_secret" in spotify_cfg:
            os.environ["SPOTIFY_CLIENT_SECRET"] = spotify_cfg["client_secret"]
        if "redirect_uri" in spotify_cfg:
            os.environ["SPOTIFY_REDIRECT_URI"] = spotify_cfg["redirect_uri"]
    except Exception as e:
        print(f"Error loading config.yaml into environment variables: {e}")

# Import clients, predictor, unified LLM client, and main SpotifyService
from src.spotify.service import SpotifyService
from src.utils import Config
from src.utils.llm_client import LLMClient
from spotify_client import SpotifyAPIClient
from lastfm_client import LastFMClient
from embed_song_predictor import SemanticEQPredictor, load_apo_path_from_config

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__, static_folder='static', template_folder='templates')

# State variables (thread-safe lock protected)
state_lock = threading.Lock()
app_state = {
    "mode": "auto",  
    "ai_engine": "similarity",  # Default to similarity (Vector Centroids) instead of llm
    "sound_style": "balanced",  # "balanced", "bass_boost", "warm", "vocal", "chill", "loudness"
    "apo_write_status": "success",  
    "spotify_authenticated": False,
    "pipeline_status": {
        "spotify": "Disconnected",
        "playback": "Waiting for stream...",
        "dsp_engine": "Vector Similarity Centroids",
        "apo_writer": "Not active"
    },
    "current_track": {
        "track_id": "",
        "track_name": "No Track Playing",
        "artist_name": "No Active Artist",
        "album_name": "",
        "album_art": "",
        "genres": [],
        "tags": [],
        "weights": {},
        "mixing_reason": "Equalizer flat. Stream music to load active mixing profile."
    },
    "eq": {
        "low_shelf_gain": 0.0,   "low_shelf_freq": 120.0,
        "first_band_gain": 0.0,  "first_band_freq": 250.0,  "first_band_q": 0.71,
        "second_band_gain": 0.0, "second_band_freq": 1000.0, "second_band_q": 0.71,
        "third_band_gain": 0.0,  "third_band_freq": 3500.0, "third_band_q": 0.71,
        "high_shelf_gain": 0.0,  "high_shelf_freq": 10000.0
    },
    "mix": {
        "preamp_gain": 0.0,
        "strength": 1.0,
        "bass_boost": 0.0,
        "vocal_clarity": 0.0,
        "airiness": 0.0
    }
}

# Core components initialized globally
predictor = None
lastfm = None
spotify_client = None  
spotify_oauth = None   
llm_client = None      # Unified LLM caller (Gemini/Llama.cpp)
main_config = None     # Main project settings Config
apo_path = None
active_monitoring = True
authenticating_lock = threading.Lock()
is_authenticating = False


def apply_and_write_apo():
    """Merge EQ band gains, apply mix overlays, and write to Equalizer APO config.txt."""
    global app_state, apo_path
    
    with state_lock:
        eq = app_state["eq"].copy()
        mix = app_state["mix"].copy()
        
    # Apply intensity multiplier (strength scale)
    strength = mix["strength"]
    low_shelf = eq["low_shelf_gain"] * strength
    b1 = eq["first_band_gain"] * strength
    b2 = eq["second_band_gain"] * strength
    b3 = eq["third_band_gain"] * strength
    high_shelf = eq["high_shelf_gain"] * strength
    
    # Apply mix enhancements overlays
    low_shelf += mix["bass_boost"]
    b2 += mix["vocal_clarity"]
    b3 += mix["vocal_clarity"] * 0.5
    high_shelf += mix["airiness"]
    
    # Build consolidated parameters
    consolidated_eq = {
        "low_shelf_freq": eq["low_shelf_freq"],
        "low_shelf_gain": max(-15.0, min(15.0, low_shelf)),
        "first_band_freq": eq["first_band_freq"],
        "first_band_gain": max(-15.0, min(15.0, b1)),
        "first_band_q": eq["first_band_q"],
        "second_band_freq": eq["second_band_freq"],
        "second_band_gain": max(-15.0, min(15.0, b2)),
        "second_band_q": eq["second_band_q"],
        "third_band_freq": eq["third_band_freq"],
        "third_band_gain": max(-15.0, min(15.0, b3)),
        "third_band_q": eq["third_band_q"],
        "high_shelf_freq": eq["high_shelf_freq"],
        "high_shelf_gain": max(-15.0, min(15.0, high_shelf)),
    }
    
    # Custom format with Preamp
    shelf_q = 0.71
    config_content = f"""# Equalizer APO Parametric EQ Configuration
# Synthesized dynamically by EqualizerAI Web Dashboard
# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}

Preamp: {mix['preamp_gain']:.2f} dB

# 5-Band Parametric Filter Array
Filter 1: ON LSC Fc {consolidated_eq['low_shelf_freq']:.0f} Hz Gain {consolidated_eq['low_shelf_gain']:.2f} dB Q {shelf_q:.2f}
Filter 2: ON PK Fc {consolidated_eq['first_band_freq']:.0f} Hz Gain {consolidated_eq['first_band_gain']:.2f} dB Q {consolidated_eq['first_band_q']:.2f}
Filter 3: ON PK Fc {consolidated_eq['second_band_freq']:.0f} Hz Gain {consolidated_eq['second_band_gain']:.2f} dB Q {consolidated_eq['second_band_q']:.2f}
Filter 4: ON PK Fc {consolidated_eq['third_band_freq']:.0f} Hz Gain {consolidated_eq['third_band_gain']:.2f} dB Q {consolidated_eq['third_band_q']:.2f}
Filter 5: ON HSC Fc {consolidated_eq['high_shelf_freq']:.0f} Hz Gain {consolidated_eq['high_shelf_gain']:.2f} dB Q {shelf_q:.2f}
"""
    
    write_success = False
    path_exists = False
    
    try:
        if apo_path.parent.exists():
            path_exists = True
            with open(apo_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            write_success = True
    except Exception as e:
        logger.error(f"Failed to write Equalizer APO config: {e}")
        
    try:
        backup_path = Path("data/config.txt")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
    except Exception:
        pass
        
    with state_lock:
        if not path_exists:
            app_state["apo_write_status"] = "not_found"
        elif not write_success:
            app_state["apo_write_status"] = "permission_denied"
        else:
            app_state["apo_write_status"] = "success"


def save_track_eq_to_db(track_id, track_name, artist_name, album_name, eq_gains, mix_settings=None):
    """Save the customized EQ settings and advanced mastering overlays for a song to the recallable sqlite songs database."""
    try:
        db_path = PROJECT_ROOT / "data" / "songs.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS songs (
                    track_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT,
                    duration_ms INTEGER,
                    release_date TEXT,
                    popularity INTEGER,
                    explicit INTEGER,
                    artist_genres TEXT,
                    artist_top_tracks_avg_popularity REAL,
                    related_artists_genres TEXT,
                    processing_status TEXT,
                    eq_bass REAL,
                    eq_low_mid REAL,
                    eq_mid REAL,
                    eq_high_mid REAL,
                    eq_treble REAL,
                    tags TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    local_play_count INTEGER DEFAULT 0,
                    last_played_at TIMESTAMP
                )
            """)
            
            # Check for missing columns and dynamically upgrade the schema!
            cursor = conn.execute("PRAGMA table_info(songs)")
            columns = [row[1] for row in cursor.fetchall()]
            
            missing_cols = {
                "mix_preamp": "REAL DEFAULT 0.0",
                "mix_strength": "REAL DEFAULT 1.0",
                "mix_bass_boost": "REAL DEFAULT 0.0",
                "mix_vocal_clarity": "REAL DEFAULT 0.0",
                "mix_airiness": "REAL DEFAULT 0.0"
            }
            
            for col_name, col_type in missing_cols.items():
                if col_name not in columns:
                    conn.execute(f"ALTER TABLE songs ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Dynamically upgraded songs.db: Added '{col_name}' column.")
            
            conn.commit()
            
            if mix_settings is None:
                with state_lock:
                    mix_settings = app_state["mix"].copy()
            
            conn.execute("""
                INSERT INTO songs (
                    track_id, name, artist, album,
                    eq_bass, eq_low_mid, eq_mid, eq_high_mid, eq_treble,
                    mix_preamp, mix_strength, mix_bass_boost, mix_vocal_clarity, mix_airiness,
                    processing_status, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processed', CURRENT_TIMESTAMP)
                ON CONFLICT(track_id) DO UPDATE SET
                    eq_bass = excluded.eq_bass,
                    eq_low_mid = excluded.eq_low_mid,
                    eq_mid = excluded.eq_mid,
                    eq_high_mid = excluded.eq_high_mid,
                    eq_treble = excluded.eq_treble,
                    mix_preamp = excluded.mix_preamp,
                    mix_strength = excluded.mix_strength,
                    mix_bass_boost = excluded.mix_bass_boost,
                    mix_vocal_clarity = excluded.mix_vocal_clarity,
                    mix_airiness = excluded.mix_airiness,
                    processing_status = 'processed',
                    last_updated = CURRENT_TIMESTAMP
            """, (
                track_id,
                track_name,
                artist_name,
                album_name,
                eq_gains.get("low_shelf_gain", 0.0),
                eq_gains.get("first_band_gain", 0.0),
                eq_gains.get("second_band_gain", 0.0),
                eq_gains.get("third_band_gain", 0.0),
                eq_gains.get("high_shelf_gain", 0.0),
                mix_settings.get("preamp_gain", 0.0),
                mix_settings.get("strength", 1.0),
                mix_settings.get("bass_boost", 0.0),
                mix_settings.get("vocal_clarity", 0.0),
                mix_settings.get("airiness", 0.0)
            ))
            conn.commit()
            logger.info(f"✓ Saved track EQ & Mix overlays to songs database for '{track_name}' by {artist_name}")
    except Exception as e:
        logger.error(f"Failed to save track EQ mix to database: {e}")


def load_track_eq_from_db(track_id):
    """Recall saved EQ settings and advanced overlays for a track from the sqlite songs database."""
    try:
        db_path = PROJECT_ROOT / "data" / "songs.db"
        if not db_path.exists():
            return None
            
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("PRAGMA table_info(songs)")
            columns = [row[1] for row in cursor.fetchall()]
            
            query_cols = ["eq_bass", "eq_low_mid", "eq_mid", "eq_high_mid", "eq_treble"]
            has_mix_cols = all(col in columns for col in ["mix_preamp", "mix_strength", "mix_bass_boost", "mix_vocal_clarity", "mix_airiness"])
            
            if has_mix_cols:
                query_cols += ["mix_preamp", "mix_strength", "mix_bass_boost", "mix_vocal_clarity", "mix_airiness"]
                
            cursor = conn.execute(
                f"SELECT {', '.join(query_cols)} FROM songs WHERE track_id = ?",
                (track_id,)
            )
            row = cursor.fetchone()
            if row and row["eq_bass"] is not None:
                result = {
                    "eq": {
                        "low_shelf_gain": row["eq_bass"],
                        "first_band_gain": row["eq_low_mid"],
                        "second_band_gain": row["eq_mid"],
                        "third_band_gain": row["eq_high_mid"],
                        "high_shelf_gain": row["eq_treble"]
                    }
                }
                
                if has_mix_cols and row["mix_preamp"] is not None:
                    result["mix"] = {
                        "preamp_gain": row["mix_preamp"],
                        "strength": row["mix_strength"],
                        "bass_boost": row["mix_bass_boost"],
                        "vocal_clarity": row["mix_vocal_clarity"],
                        "airiness": row["mix_airiness"]
                    }
                else:
                    result["mix"] = {
                        "preamp_gain": 0.0,
                        "strength": 1.0,
                        "bass_boost": 0.0,
                        "vocal_clarity": 0.0,
                        "airiness": 0.0
                    }
                return result
    except Exception as e:
        logger.error(f"Failed to load track EQ from database: {e}")
    return None


# Target Sound Signature Preset Offsets for Vector Similarity Blending
STYLE_OFFSETS = {
    "balanced": {
        "low_shelf_gain": 0.0, "first_band_gain": 0.0, "second_band_gain": 0.0,
        "third_band_gain": 0.0, "high_shelf_gain": 0.0
    },
    "bass_boost": {
        "low_shelf_gain": 4.5, "first_band_gain": 0.5, "second_band_gain": -1.0,
        "third_band_gain": 0.5, "high_shelf_gain": 2.5
    },
    "warm": {
        "low_shelf_gain": 2.0, "first_band_gain": 2.0, "second_band_gain": 0.5,
        "third_band_gain": -1.5, "high_shelf_gain": -2.0
    },
    "vocal": {
        "low_shelf_gain": -1.5, "first_band_gain": 0.5, "second_band_gain": 2.5,
        "third_band_gain": 1.5, "high_shelf_gain": 0.5
    },
    "chill": {
        "low_shelf_gain": 1.5, "first_band_gain": 0.5, "second_band_gain": -0.5,
        "third_band_gain": -0.5, "high_shelf_gain": 0.5
    },
    "loudness": {
        "low_shelf_gain": 4.0, "first_band_gain": 0.5, "second_band_gain": -1.5,
        "third_band_gain": 1.0, "high_shelf_gain": 3.0
    }
}

# Dynamic prompt injection descriptions for professional mastering styles
STYLE_PROMPT_INSTRUCTIONS = {
    "balanced": "Balanced / Flat Reference: Neutral, clean, pristine reproduction. Maintain the original artist's balance with very subtle corrections (mostly 0.0dB, up to +/-1.5dB) to preserve pure reference audio.",
    "bass_boost": "Club / Bass Head: Punchy, deep sub-bass boost (around 60Hz +4.0 to +6.0dB), warm/neutral low-mids, and crisp, glittering airy highs (around 12kHz +2.0 to +3.5dB). Elevate deep rhythms.",
    "warm": "Warm / Cozy Analog: Rich, thick low-end and lower mid-range warmth (around 120Hz-250Hz +2.0 to +3.5dB) with smooth, highly comfortable rolled-off high frequencies (around 12kHz -1.5 to -3.0dB). Perfect for acoustic tracks and vintage warmth.",
    "vocal": "Vocal & Acoustic Focus: Emphasize vocal presence, guitar texture, and mid-range lead articulation (around 1000Hz +2.5 to +4.0dB and 4000Hz +1.5 to +2.5dB), while keeping bass and trebles polite and controlled.",
    "chill": "Relaxed / Chill Vibe: Easy, non-fatiguing background listening. Gentle, smooth bass support (+1.5 to +2.5dB), relaxed vocal presence, and extremely soft highs.",
    "loudness": "Loudness Compensation: Compensate human hearing curves (Fletcher-Munson) at moderate/lower volumes. Boost low sub-bass (+4.0 to +5.0dB) and upper airy treble (+3.0 to +4.0dB), with a minor dip in the mid-range (-1.5dB) for a lush, deep, expansive soundstage."
}


def get_ai_predicted_eq(track_name: str, artist_name: str, genres: list, tags: list, sound_style: str = "balanced") -> dict:
    """Uses the unified LLMClient to query the active AI server (Llama/Gemini) for custom EQ parameters."""
    global llm_client, main_config
    
    if not llm_client:
        raise RuntimeError("LLM client not initialized.")
        
    genres_str = ", ".join(genres) if genres else "Unknown"
    tags_str = ", ".join(tags[:10]) if tags else "Unknown"
    style_instruction = STYLE_PROMPT_INSTRUCTIONS.get(sound_style, STYLE_PROMPT_INSTRUCTIONS["balanced"])
    
    prompt = f"""You are a professional audio mastering engineer. Recommend optimal 5-band parametric equalizer settings for the following song, tailored to the user's preferred target sound signature.

SONG DETAILS:
  Title: {track_name}
  Artist: {artist_name}
  Genres: {genres_str}
  Tags: {tags_str}

USER'S PREFERRED TARGET SOUND SIGNATURE:
  {style_instruction}

Analyze the artist's mixing signature, era production style, and blend it with the user's preferred target sound signature. Provide optimal parametric EQ gains for:
- **bass (60 Hz)**: fundamentally boosts sub-bass, kick impact (EDM/Hip-hop usually +2 to +4dB, Classical -1 to -2dB)
- **low_mid (250 Hz)**: lower harmonics warmth (boost +1 to +2dB, reduce -2dB if muddy)
- **mid (1000 Hz)**: vocal clarity presence (boost +1 to +2dB)
- **high_mid (4000 Hz)**: definition and articulation (boost +1 to +2dB, reduce if harsh)
- **treble (12000 Hz)**: high air and brilliance (boost +2 to +4dB)

IMPORTANT CONSTRAINTS:
1. Recommend adjustments in standard engineering terms (between -8.0 and +8.0 dB). Make adjustments audible, expressive, and satisfying but highly musical.
2. Provide a brief 1-2 sentence engineering justification under the key "mixing_reason" (e.g. "Daft Punk's 'Get Lucky' has pristine modern production. To support the 'Club / Bass Head' target, boosted sub-bass and added glittering air, keeping low-mids clean and warm.")
3. Respond ONLY in valid JSON. No markdown code blocks.

REQUIRED JSON FORMAT:
{{
  "bass": 0.0,
  "low_mid": 0.0,
  "mid": 0.0,
  "high_mid": 0.0,
  "treble": 0.0,
  "mixing_reason": "Analysis explanation..."
}}"""

    logger.info(f"Querying AI Mixing Assistant for '{track_name}' by {artist_name}...")
    try:
        # Request JSON response format
        response = llm_client.generate_content(
            model=main_config.gemini_model if main_config.llm_provider == 'gemini' else main_config.local_model,
            contents=prompt,
            config={
                "temperature": 0.7,
                "response_mime_type": "application/json"
            }
        )
        
        # Parse JSON output safely
        text = response.text.strip()
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            text = text.replace("```", "").strip()
            
        result = json.loads(text)
        
        # Clamp gains safely
        eq_curve = {
            "low_shelf_gain": max(-12.0, min(12.0, float(result.get("bass", 0.0)))),
            "first_band_gain": max(-12.0, min(12.0, float(result.get("low_mid", 0.0)))),
            "second_band_gain": max(-12.0, min(12.0, float(result.get("mid", 0.0)))),
            "third_band_gain": max(-12.0, min(12.0, float(result.get("high_mid", 0.0)))),
            "high_shelf_gain": max(-12.0, min(12.0, float(result.get("treble", 0.0)))),
            "mixing_reason": result.get("mixing_reason", "AI mix profile loaded successfully.")
        }
        
        logger.info(f"✓ AI Mixing success: {eq_curve}")
        return eq_curve
    except Exception as e:
        logger.error(f"Failed to fetch AI Mixing Assistant parameters: {e}")
        raise e


def monitor_spotify_playback():
    """Background loop polling active Spotify playback via direct OAuth get_current_track()."""
    global app_state, predictor, lastfm, spotify_client, spotify_oauth, active_monitoring
    
    logger.info("Background direct Spotify User playback thread started.")
    last_track_id = None
    last_status_log_time = 0
    
    while active_monitoring:
        try:
            # Throttled terminal logger
            now = time.time()
            log_throttled = (now - last_status_log_time) > 10.0
            
            auth_ok = False
            if spotify_oauth:
                try:
                    auth_ok = spotify_oauth.is_authenticated()
                except Exception as e:
                    logger.debug(f"Auth check failed: {e}")
            
            with state_lock:
                app_state["spotify_authenticated"] = auth_ok
                current_mode = app_state["mode"]
                current_engine = app_state["ai_engine"]
                sound_style = app_state["sound_style"]
            
            if not auth_ok:
                with state_lock:
                    app_state["current_track"] = {
                        "track_id": "",
                        "track_name": "Spotify Account Not Connected",
                        "artist_name": "Please click Connect Spotify above to authorize.",
                        "album_name": "",
                        "album_art": "",
                        "genres": [],
                        "tags": [],
                        "weights": {},
                        "mixing_reason": "Spotify integration requires dynamic OAuth connection.",
                        "is_playing": False,
                        "is_private_session": False
                    }
                if log_throttled:
                    logger.warning("[Spotify Monitor] Spotify Account is NOT authorized. Please open http://127.0.0.1:5001 and click 'Connect Spotify Account'.")
                    last_status_log_time = now
                time.sleep(2.0)
                continue
                
            # ALWAYS poll Spotify playback even in manual override, to detect song changes and auto-save!
            track_info = None
            try:
                track_info = spotify_oauth.get_current_track()
            except Exception as e:
                logger.warning(f"Error fetching direct current track: {e}")
                
            # Case 1: No active session (device disconnected / Spotify closed)
            if not track_info:
                # Trigger auto-save of previous track's custom settings on stop
                if last_track_id:
                    with state_lock:
                        prev_track_name = app_state["current_track"].get("track_name")
                        prev_artist_name = app_state["current_track"].get("artist_name")
                        prev_album_name = app_state["current_track"].get("album_name")
                        current_gains = app_state["eq"].copy()
                        current_mix = app_state["mix"].copy()
                    
                    if prev_track_name and prev_track_name not in [
                        "No Track Playing", "Spotify Player Paused", "Spotify Account Not Connected", 
                        "🔒 Spotify Private Session Active", "No Active Spotify Playback", 
                        "Playback Not Supported", "Spotify Account Disconnected"
                    ]:
                        save_track_eq_to_db(last_track_id, prev_track_name, prev_artist_name, prev_album_name, current_gains, current_mix)
                    last_track_id = None

                with state_lock:
                    app_state["current_track"] = {
                        "track_id": "",
                        "track_name": "No Active Spotify Playback",
                        "artist_name": "Open your Spotify app and play a song to auto-master.",
                        "album_name": "Waiting for active player session...",
                        "album_art": "",
                        "genres": [],
                        "tags": [],
                        "weights": {},
                        "mixing_reason": "No active playback session detected. Open Spotify on your phone or PC, start streaming, and make sure it is playing.",
                        "is_playing": False,
                        "is_private_session": False
                    }
                if log_throttled:
                    logger.info("[Spotify Monitor] Listening... Connected to account successfully, but no active device playback session detected. Play a song on your phone/PC app!")
                    last_status_log_time = now
                time.sleep(1.5)
                continue
                
            # Case 2: Spotify Private Session Active (Blocks API metadata)
            if track_info.get("is_private_session"):
                # Trigger auto-save of previous track on private session block
                if last_track_id:
                    with state_lock:
                        prev_track_name = app_state["current_track"].get("track_name")
                        prev_artist_name = app_state["current_track"].get("artist_name")
                        prev_album_name = app_state["current_track"].get("album_name")
                        current_gains = app_state["eq"].copy()
                        current_mix = app_state["mix"].copy()
                    
                    if prev_track_name and prev_track_name not in [
                        "No Track Playing", "Spotify Player Paused", "Spotify Account Not Connected", 
                        "🔒 Spotify Private Session Active", "No Active Spotify Playback", 
                        "Playback Not Supported", "Spotify Account Disconnected"
                    ]:
                        save_track_eq_to_db(last_track_id, prev_track_name, prev_artist_name, prev_album_name, current_gains, current_mix)
                    last_track_id = None

                with state_lock:
                    app_state["current_track"] = {
                        "track_id": "",
                        "track_name": "🔒 Spotify Private Session Active",
                        "artist_name": "Disable 'Private Session' in Spotify Settings to allow EQ sync.",
                        "album_name": "Spotify is blocking metadata retrieval",
                        "album_art": "",
                        "genres": [],
                        "tags": [],
                        "weights": {},
                        "mixing_reason": "Your Spotify client is in a private session. Spotify blocks song metadata queries while in Private Session for privacy. Turn off 'Private Session' in Spotify app settings to begin auto-mastering!",
                        "is_playing": False,
                        "is_private_session": True
                    }
                if log_throttled:
                    logger.warning("[WARNING] Spotify Private Session detected! Disabling 'Private Session' in Spotify (Profile icon -> Settings -> Private Session) is required to read tracks and auto-mix.")
                    last_status_log_time = now
                time.sleep(2.5)
                continue
                
            track_uri = track_info.get("track_uri", "")
            track_id = track_uri.split(":")[-1] if track_uri else ""
            track_name = track_info.get("name", "")
            artist_name = track_info.get("artist", "")
            album_name = track_info.get("album", "")
            album_art = track_info.get("image_url", "")
            is_playing = track_info.get("is_playing", False)
            
            # Case 3: Untrackable content (local file, podcast, ad)
            if not track_id or not track_name or track_name == "Unknown":
                # Trigger auto-save of previous track on untrackable content
                if last_track_id:
                    with state_lock:
                        prev_track_name = app_state["current_track"].get("track_name")
                        prev_artist_name = app_state["current_track"].get("artist_name")
                        prev_album_name = app_state["current_track"].get("album_name")
                        current_gains = app_state["eq"].copy()
                        current_mix = app_state["mix"].copy()
                    
                    if prev_track_name and prev_track_name not in [
                        "No Track Playing", "Spotify Player Paused", "Spotify Account Not Connected", 
                        "🔒 Spotify Private Session Active", "No Active Spotify Playback", 
                        "Playback Not Supported", "Spotify Account Disconnected"
                    ]:
                        save_track_eq_to_db(last_track_id, prev_track_name, prev_artist_name, prev_album_name, current_gains, current_mix)
                    last_track_id = None

                with state_lock:
                    app_state["current_track"] = {
                        "track_id": "",
                        "track_name": "Playback Not Supported",
                        "artist_name": "Local files, podcasts, or ads cannot be auto-mixed.",
                        "album_name": "",
                        "album_art": "",
                        "genres": [],
                        "tags": [],
                        "weights": {},
                        "mixing_reason": "The playing item is not in Spotify's online catalog or lacks standard metadata (e.g. downloaded local MP3s, advertisements, or podcasts). Play a streamed song.",
                        "is_playing": False,
                        "is_private_session": False
                    }
                if log_throttled:
                    logger.info("[Spotify Monitor] Playback detected but item metadata is untrackable (e.g. local files, podcasts, ads).")
                    last_status_log_time = now
                time.sleep(1.5)
                continue
                
            # If in MANUAL mode and track has NOT changed, suspend active auto-EQ overwriting to let user mix!
            if current_mode == "manual" and track_id == last_track_id:
                # Still check and update play/pause state in manual mode
                with state_lock:
                    prev_playing = app_state["current_track"].get("is_playing", False)
                    if prev_playing != is_playing:
                        app_state["current_track"]["is_playing"] = is_playing
                        logger.info(f"[Spotify Monitor] (Manual Mode) Playback state changed: {'Playing' if is_playing else 'Paused'} for '{track_name}'")
                        last_status_log_time = now
                time.sleep(1.5)
                continue

            # If track changed, recalculate
            if track_id != last_track_id:
                # 1. Trigger auto-save of previous track's EQ mix to recallable database!
                if last_track_id:
                    with state_lock:
                        prev_track_name = app_state["current_track"].get("track_name")
                        prev_artist_name = app_state["current_track"].get("artist_name")
                        prev_album_name = app_state["current_track"].get("album_name")
                        current_gains = app_state["eq"].copy()
                        current_mix = app_state["mix"].copy()
                    
                    if prev_track_name and prev_track_name not in [
                        "No Track Playing", "Spotify Player Paused", "Spotify Account Not Connected", 
                        "🔒 Spotify Private Session Active", "No Active Spotify Playback", 
                        "Playback Not Supported", "Spotify Account Disconnected"
                    ]:
                        save_track_eq_to_db(last_track_id, prev_track_name, prev_artist_name, prev_album_name, current_gains, current_mix)

                logger.info(f"[Spotify Monitor] Active song detected: '{track_name}' by {artist_name} (Playing: {is_playing})")
                last_status_log_time = now  # Reset throttled log timer on track change
                
                # Automatically reset to AUTO mode on track change to enable seamless transition!
                with state_lock:
                    app_state["mode"] = "auto"
                    current_mode = "auto"
                
                # 2. Try to recall existing custom EQ mix from songs database!
                recalled = load_track_eq_from_db(track_id)
                
                genres = []
                tags = []
                weights = {}
                dyn_mix = {
                    "preamp_gain": 0.0,
                    "strength": 1.0,
                    "bass_boost": 0.0,
                    "vocal_clarity": 0.0,
                    "airiness": 0.0
                }
                
                if recalled:
                    logger.info(f"[Spotify Monitor] ✓ Recalled custom EQ profile from songs database for track {track_id}!")
                    eq_curve = {
                        "low_shelf_gain": recalled["eq"]["low_shelf_gain"], "low_shelf_freq": 120.0,
                        "first_band_gain": recalled["eq"]["first_band_gain"], "first_band_freq": 250.0, "first_band_q": 0.71,
                        "second_band_gain": recalled["eq"]["second_band_gain"], "second_band_freq": 1000.0, "second_band_q": 0.71,
                        "third_band_gain": recalled["eq"]["third_band_gain"], "third_band_freq": 3500.0, "third_band_q": 0.71,
                        "high_shelf_gain": recalled["eq"]["high_shelf_gain"], "high_shelf_freq": 10000.0
                    }
                    dyn_mix = recalled["mix"]
                    mixing_reason = "Recalled custom EQ profile & Mastering Overlays from local songs database."
                    
                    # Fetch basic metadata for UI display
                    if spotify_client:
                        try:
                            track_data = spotify_client.get_track(track_id)
                            artist_id = track_data['artists'][0]['id']
                            artist_data = spotify_client.get_artist(artist_id)
                            genres = artist_data.get('genres', [])
                        except Exception:
                            pass
                    if lastfm:
                        try:
                            tags = lastfm.get_track_tags(artist_name, track_name)
                        except Exception:
                            pass
                else:
                    # No recalled profile: do normal tag similarity matching!
                    if spotify_client:
                        try:
                            track_data = spotify_client.get_track(track_id)
                            artist_id = track_data['artists'][0]['id']
                            artist_data = spotify_client.get_artist(artist_id)
                            genres = artist_data.get('genres', [])
                        except Exception as e:
                            logger.warning(f"Failed to fetch artist genres: {e}")
                    
                    if lastfm:
                        try:
                            tags = lastfm.get_track_tags(artist_name, track_name)
                        except Exception as e:
                            logger.warning(f"Last.fm tags fetch failed: {e}")
                            
                    combined_tags = tags + genres
                    
                    # Default empty baseline
                    eq_curve = {
                        "low_shelf_gain": 0.0, "low_shelf_freq": 120.0,
                        "first_band_gain": 0.0, "first_band_freq": 250.0, "first_band_q": 0.71,
                        "second_band_gain": 0.0, "second_band_freq": 1000.0, "second_band_q": 0.71,
                        "third_band_gain": 0.0, "third_band_freq": 3500.0, "third_band_q": 0.71,
                        "high_shelf_gain": 0.0, "high_shelf_freq": 10000.0
                    }
                    
                    # --- STRATEGY 1: DIRECT AI MODEL GENERATION ---
                    if current_engine == "llm":
                        try:
                            ai_mix = get_ai_predicted_eq(track_name, artist_name, genres, tags, sound_style)
                            eq_curve["low_shelf_gain"] = ai_mix["low_shelf_gain"]
                            eq_curve["first_band_gain"] = ai_mix["first_band_gain"]
                            eq_curve["second_band_gain"] = ai_mix["second_band_gain"]
                            eq_curve["third_band_gain"] = ai_mix["third_band_gain"]
                            eq_curve["high_shelf_gain"] = ai_mix["high_shelf_gain"]
                            mixing_reason = ai_mix["mixing_reason"]
                        except Exception as e:
                            logger.warning(f"AI Mixing Assistant failed. Toggling self-healing fallback to Similarity centroids: {e}")
                            current_engine = "similarity" # Force similarity fallback
                            with state_lock:
                                app_state["ai_engine"] = "similarity"
                    
                    # --- STRATEGY 2: OFFLINE VECTOR MATCHING ---
                    if current_engine == "similarity":
                        weights = predictor.calculate_similarity_weights(combined_tags)
                        interpolated = predictor.synthesize_eq_curve(weights)
                        
                        # Blend similarity centroid curve with target sound style offset
                        offsets = STYLE_OFFSETS.get(sound_style, STYLE_OFFSETS["balanced"])
                        eq_curve["low_shelf_gain"] = interpolated["low_shelf_gain"] + offsets["low_shelf_gain"]
                        eq_curve["first_band_gain"] = interpolated["first_band_gain"] + offsets["first_band_gain"]
                        eq_curve["second_band_gain"] = interpolated["second_band_gain"] + offsets["second_band_gain"]
                        eq_curve["third_band_gain"] = interpolated["third_band_gain"] + offsets["third_band_gain"]
                        eq_curve["high_shelf_gain"] = interpolated["high_shelf_gain"] + offsets["high_shelf_gain"]
                        
                        # Generate technically explained centroids list
                        active_w = [f"{p} ({w*100:.0f}%)" for p, w in weights.items() if w > 0]
                        style_desc = sound_style.replace("_", " ").title()
                        mixing_reason = f"Synthesized curves blended using preprocessed SAFE centroids ({', '.join(active_w)}) and styled as '{style_desc}'."
                        
                        # Calculate smart dynamic Advanced Mastering Overlays based on tag weights!
                        punchy_w = weights.get("punchy", 0.0)
                        presence_w = weights.get("presence", 0.0)
                        airy_w = weights.get("airy", 0.0)
                        warm_w = weights.get("warm", 0.0)
                        bright_w = weights.get("bright", 0.0)
                        muddy_w = weights.get("muddy", 0.0)
                        
                        # Smart blending logic
                        bass_boost = round((punchy_w * 4.0) + (warm_w * 1.5) - (bright_w * 1.0), 1)
                        bass_boost = max(0.0, min(8.0, bass_boost))
                        
                        vocal_clarity = round((presence_w * 3.0) + (warm_w * 1.0), 1)
                        vocal_clarity = max(0.0, min(6.0, vocal_clarity))
                        
                        airiness = round((airy_w * 3.0) + (bright_w * 1.5) - (muddy_w * 1.5), 1)
                        airiness = max(0.0, min(6.0, airiness))
                        
                        # Dynamically compute safe preamp headroom to prevent digital clipping!
                        max_boost = max(bass_boost, vocal_clarity, airiness)
                        preamp_gain = round(-0.8 * max_boost, 2)
                        
                        dyn_mix = {
                            "preamp_gain": preamp_gain,
                            "strength": 1.0,
                            "bass_boost": bass_boost,
                            "vocal_clarity": vocal_clarity,
                            "airiness": airiness
                        }
                
                # Update status variables safely
                with state_lock:
                    app_state["current_track"] = {
                        "track_id": track_id,
                        "track_name": track_name,
                        "artist_name": artist_name,
                        "album_name": album_name,
                        "album_art": album_art,
                        "genres": genres,
                        "tags": tags,
                        "weights": weights,
                        "mixing_reason": mixing_reason,
                        "is_playing": is_playing,
                        "is_private_session": False
                    }
                    app_state["eq"]["low_shelf_gain"] = eq_curve["low_shelf_gain"]
                    app_state["eq"]["first_band_gain"] = eq_curve["first_band_gain"]
                    app_state["eq"]["second_band_gain"] = eq_curve["second_band_gain"]
                    app_state["eq"]["third_band_gain"] = eq_curve["third_band_gain"]
                    app_state["eq"]["high_shelf_gain"] = eq_curve["high_shelf_gain"]
                    
                    # Persist dynamic overlays in Auto mode
                    if app_state["mode"] == "auto":
                        app_state["mix"] = dyn_mix
                    
                # Write to active APO filters
                apply_and_write_apo()
                last_track_id = track_id
            else:
                # Track has not changed, but play/pause state may have changed
                with state_lock:
                    prev_playing = app_state["current_track"].get("is_playing", False)
                    if prev_playing != is_playing:
                        app_state["current_track"]["is_playing"] = is_playing
                        logger.info(f"[Spotify Monitor] Playback state changed: {'Playing' if is_playing else 'Paused'} for '{track_name}'")
                        last_status_log_time = now
            
            # Dynamically compute and store the live pipeline status on every iteration!
            with state_lock:
                app_state["pipeline_status"]["spotify"] = "Connected (OAuth active)" if auth_ok else "Disconnected"
                
                if not auth_ok:
                    app_state["pipeline_status"]["playback"] = "Waiting for Spotify connection..."
                elif not track_info:
                    app_state["pipeline_status"]["playback"] = "Waiting for stream in Spotify app..."
                elif track_info.get("is_private_session"):
                    app_state["pipeline_status"]["playback"] = "🔒 Blocked (Private Session Active)"
                elif not track_id or not track_name or track_name == "Unknown":
                    app_state["pipeline_status"]["playback"] = "Unsupported content (Local/Ad/Podcast)"
                else:
                    app_state["pipeline_status"]["playback"] = f"Active: '{track_name}'"
                
                if not auth_ok or not track_info or track_info.get("is_private_session") or not track_id:
                    app_state["pipeline_status"]["dsp_engine"] = "Idle (Waiting for metadata)"
                elif app_state["ai_engine"] == "similarity":
                    app_state["pipeline_status"]["dsp_engine"] = "📊 Vector Similarity Centroids"
                else:
                    app_state["pipeline_status"]["dsp_engine"] = "🤖 AI Mixing Assistant"
                
                write_st = app_state["apo_write_status"]
                if write_st == "success":
                    app_state["pipeline_status"]["apo_writer"] = "✓ Hardware EQ Active (config.txt updated)"
                elif write_st == "permission_denied":
                    app_state["pipeline_status"]["apo_writer"] = "❌ Write Permission Error (Run as Admin)"
                else:
                    app_state["pipeline_status"]["apo_writer"] = "⚠ Equalizer APO Not Found (Bypassed)"
                
        except Exception as e:
            logger.error(f"Error in direct user monitor loop: {e}", exc_info=True)
            
        time.sleep(1.5)
                



# --- REST API CONTROLLERS ---

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/status', methods=['GET'])
def get_status():
    with state_lock:
        return jsonify(app_state)


def recalculate_current_track_eq():
    """Recomputes EQ parameters for the currently active track based on the selected engine and style."""
    global app_state, predictor, lastfm, spotify_client
    
    with state_lock:
        track = app_state["current_track"].copy()
        current_engine = app_state["ai_engine"]
        sound_style = app_state["sound_style"]
        
    track_name = track.get("track_name", "")
    artist_name = track.get("artist_name", "")
    genres = track.get("genres", [])
    tags = track.get("tags", [])
    track_id = track.get("track_id", "")
    
    # Skip if no real song is playing
    if not track_name or track_name in ["No Track Playing", "Spotify Player Paused", "Spotify Account Not Connected", "Spotify is not connected", "Stream music on your Spotify app to auto-mix.", "Please click Connect Spotify above to authorize."]:
        return
        
    combined_tags = tags + genres
    
    eq_curve = {
        "low_shelf_gain": 0.0, "first_band_gain": 0.0, "second_band_gain": 0.0,
        "third_band_gain": 0.0, "high_shelf_gain": 0.0
    }
    mixing_reason = ""
    weights = {}
    dyn_mix = {
        "preamp_gain": 0.0,
        "strength": 1.0,
        "bass_boost": 0.0,
        "vocal_clarity": 0.0,
        "airiness": 0.0
    }
    
    # Try to recall existing custom EQ mix from songs database first!
    recalled = None
    if track_id:
        recalled = load_track_eq_from_db(track_id)
        
    if recalled:
        logger.info(f"Recalculate: ✓ Recalled custom EQ profile from database for track {track_id}!")
        eq_curve = {
            "low_shelf_gain": recalled["eq"]["low_shelf_gain"],
            "first_band_gain": recalled["eq"]["first_band_gain"],
            "second_band_gain": recalled["eq"]["second_band_gain"],
            "third_band_gain": recalled["eq"]["third_band_gain"],
            "high_shelf_gain": recalled["eq"]["high_shelf_gain"]
        }
        dyn_mix = recalled["mix"]
        mixing_reason = "Recalled custom EQ profile & Mastering Overlays from local songs database."
        weights = {}
    else:
        if current_engine == "llm":
            try:
                ai_mix = get_ai_predicted_eq(track_name, artist_name, genres, tags, sound_style)
                eq_curve["low_shelf_gain"] = ai_mix["low_shelf_gain"]
                eq_curve["first_band_gain"] = ai_mix["first_band_gain"]
                eq_curve["second_band_gain"] = ai_mix["second_band_gain"]
                eq_curve["third_band_gain"] = ai_mix["third_band_gain"]
                eq_curve["high_shelf_gain"] = ai_mix["high_shelf_gain"]
                mixing_reason = ai_mix["mixing_reason"]
            except Exception as e:
                logger.warning(f"AI Mixing Assistant failed during recalculation, falling back to similarity: {e}")
                current_engine = "similarity"
                with state_lock:
                    app_state["ai_engine"] = "similarity"
                
        if current_engine == "similarity":
            weights = predictor.calculate_similarity_weights(combined_tags)
            interpolated = predictor.synthesize_eq_curve(weights)
            
            offsets = STYLE_OFFSETS.get(sound_style, STYLE_OFFSETS["balanced"])
            eq_curve["low_shelf_gain"] = interpolated["low_shelf_gain"] + offsets["low_shelf_gain"]
            eq_curve["first_band_gain"] = interpolated["first_band_gain"] + offsets["first_band_gain"]
            eq_curve["second_band_gain"] = interpolated["second_band_gain"] + offsets["second_band_gain"]
            eq_curve["third_band_gain"] = interpolated["third_band_gain"] + offsets["third_band_gain"]
            eq_curve["high_shelf_gain"] = interpolated["high_shelf_gain"] + offsets["high_shelf_gain"]
            
            active_w = [f"{p} ({w*100:.0f}%)" for p, w in weights.items() if w > 0]
            style_desc = sound_style.replace("_", " ").title()
            mixing_reason = f"Synthesized curves blended using preprocessed SAFE centroids ({', '.join(active_w)}) and styled as '{style_desc}'."
            
            # Calculate smart dynamic Advanced Mastering Overlays based on tag weights!
            punchy_w = weights.get("punchy", 0.0)
            presence_w = weights.get("presence", 0.0)
            airy_w = weights.get("airy", 0.0)
            warm_w = weights.get("warm", 0.0)
            bright_w = weights.get("bright", 0.0)
            muddy_w = weights.get("muddy", 0.0)
            
            # Smart blending logic
            bass_boost = round((punchy_w * 4.0) + (warm_w * 1.5) - (bright_w * 1.0), 1)
            bass_boost = max(0.0, min(8.0, bass_boost))
            
            vocal_clarity = round((presence_w * 3.0) + (warm_w * 1.0), 1)
            vocal_clarity = max(0.0, min(6.0, vocal_clarity))
            
            airiness = round((airy_w * 3.0) + (bright_w * 1.5) - (muddy_w * 1.5), 1)
            airiness = max(0.0, min(6.0, airiness))
            
            # Dynamically compute safe preamp headroom to prevent digital clipping!
            max_boost = max(bass_boost, vocal_clarity, airiness)
            preamp_gain = round(-0.8 * max_boost, 2)
            
            dyn_mix = {
                "preamp_gain": preamp_gain,
                "strength": 1.0,
                "bass_boost": bass_boost,
                "vocal_clarity": vocal_clarity,
                "airiness": airiness
            }
        
    with state_lock:
        app_state["current_track"]["weights"] = weights if current_engine == "similarity" else {}
        app_state["current_track"]["mixing_reason"] = mixing_reason
        app_state["eq"]["low_shelf_gain"] = eq_curve["low_shelf_gain"]
        app_state["eq"]["first_band_gain"] = eq_curve["first_band_gain"]
        app_state["eq"]["second_band_gain"] = eq_curve["second_band_gain"]
        app_state["eq"]["third_band_gain"] = eq_curve["third_band_gain"]
        app_state["eq"]["high_shelf_gain"] = eq_curve["high_shelf_gain"]
        
        # Persist dynamic overlays in Auto mode
        if app_state["mode"] == "auto":
            app_state["mix"] = dyn_mix


@app.route('/api/engine', methods=['POST'])
def set_engine():
    """Toggle dynamic prediction engine."""
    data = request.json or {}
    new_engine = data.get("engine")
    if new_engine not in ["llm", "similarity"]:
        return jsonify({"success": False, "message": "Invalid engine."}), 400
        
    with state_lock:
        app_state["ai_engine"] = new_engine
        logger.info(f"AI Mixing Engine set to: {new_engine}")
        
    # Recompute immediately
    recalculate_current_track_eq()
    apply_and_write_apo()
    return jsonify({"success": True, "engine": new_engine})


@app.route('/api/style', methods=['POST'])
def set_sound_style():
    """Set the preferred target sound signature style."""
    data = request.json or {}
    new_style = data.get("style")
    if new_style not in STYLE_OFFSETS:
        return jsonify({"success": False, "message": "Invalid sound style."}), 400
        
    with state_lock:
        app_state["sound_style"] = new_style
        logger.info(f"Target Sound Style set to: {new_style}")
        
    # Recalculate immediately with new style
    recalculate_current_track_eq()
    apply_and_write_apo()
    return jsonify({"success": True, "style": new_style, "state": app_state})


@app.route('/api/mode', methods=['POST'])
def set_mode():
    data = request.json or {}
    new_mode = data.get("mode")
    if new_mode not in ["auto", "manual"]:
        return jsonify({"success": False, "message": "Invalid mode."}), 400
        
    with state_lock:
        app_state["mode"] = new_mode
        logger.info(f"EQ Control Mode set to: {new_mode}")
        
    if new_mode == "auto":
        recalculate_current_track_eq()
        apply_and_write_apo()
        
    return jsonify({"success": True, "mode": new_mode})


@app.route('/api/update_eq', methods=['POST'])
def update_eq_parameters():
    data = request.json or {}
    
    with state_lock:
        if app_state["mode"] == "auto":
            app_state["mode"] = "manual"
            logger.info("Manual drag detected: Toggled into manual override mode.")
            
        if "eq" in data:
            for k in app_state["eq"]:
                if k in data["eq"]:
                    app_state["eq"][k] = float(data["eq"][k])
                    
        if "mix" in data:
            for k in app_state["mix"]:
                if k in data["mix"]:
                    app_state["mix"][k] = float(data["mix"][k])
                    
    apply_and_write_apo()
    return jsonify({"success": True, "state": app_state})


@app.route('/api/eq/reset', methods=['POST'])
def reset_eq():
    with state_lock:
        app_state["mode"] = "manual"
        app_state["eq"]["low_shelf_gain"] = 0.0
        app_state["eq"]["first_band_gain"] = 0.0
        app_state["eq"]["second_band_gain"] = 0.0
        app_state["eq"]["third_band_gain"] = 0.0
        app_state["eq"]["high_shelf_gain"] = 0.0
        app_state["eq"]["first_band_q"] = 0.71
        app_state["eq"]["second_band_q"] = 0.71
        app_state["eq"]["third_band_q"] = 0.71
        app_state["mix"] = {
            "preamp_gain": 0.0,
            "strength": 1.0,
            "bass_boost": 0.0,
            "vocal_clarity": 0.0,
            "airiness": 0.0
        }
        
    apply_and_write_apo()
    return jsonify({"success": True})


@app.route('/api/eq/redo', methods=['POST'])
def redo_eq_mix():
    """Redo the current song's EQ mix by introducing semantic divergence in the similarity embedding model."""
    global app_state, predictor
    
    with state_lock:
        track = app_state["current_track"].copy()
        current_engine = app_state["ai_engine"]
        sound_style = app_state["sound_style"]
        
    track_name = track.get("track_name", "")
    artist_name = track.get("artist_name", "")
    genres = track.get("genres", [])
    tags = track.get("tags", [])
    
    # Check if a valid track is loaded
    if not track_name or track_name in ["No Track Playing", "Spotify Account Disconnected", "Spotify is not connected", "Please click Connect Spotify above to authorize."]:
        return jsonify({"success": False, "message": "No active track to remix."}), 400
        
    combined_tags = tags + genres
    
    # 1. Similarity weights calculation with 35% semantic divergence!
    divergence = 0.35
    weights = predictor.calculate_similarity_weights(combined_tags, divergence=divergence)
    eq_curve = predictor.synthesize_eq_curve(weights)
    
    # 2. Add target sound style offsets
    offsets = STYLE_OFFSETS.get(sound_style, STYLE_OFFSETS["balanced"])
    eq_curve["low_shelf_gain"] = eq_curve["low_shelf_gain"] + offsets["low_shelf_gain"]
    eq_curve["first_band_gain"] = eq_curve["first_band_gain"] + offsets["first_band_gain"]
    eq_curve["second_band_gain"] = eq_curve["second_band_gain"] + offsets["second_band_gain"]
    eq_curve["third_band_gain"] = eq_curve["third_band_gain"] + offsets["third_band_gain"]
    eq_curve["high_shelf_gain"] = eq_curve["high_shelf_gain"] + offsets["high_shelf_gain"]
            
    style_desc = sound_style.replace("_", " ").title()
    active_w = [f"{p} ({w*100:.0f}%)" for p, w in weights.items() if w > 0]
    
    mixing_reason = f"Remixed EQ using preprocessed SAFE centroids with 35% semantic divergence ({', '.join(active_w)}) and styled as '{style_desc}'."
    
    # Calculate smart dynamic Advanced Mastering Overlays based on tag weights!
    punchy_w = weights.get("punchy", 0.0)
    presence_w = weights.get("presence", 0.0)
    airy_w = weights.get("airy", 0.0)
    warm_w = weights.get("warm", 0.0)
    bright_w = weights.get("bright", 0.0)
    muddy_w = weights.get("muddy", 0.0)
    
    # Smart blending logic
    bass_boost = round((punchy_w * 4.0) + (warm_w * 1.5) - (bright_w * 1.0), 1)
    bass_boost = max(0.0, min(8.0, bass_boost))
    
    vocal_clarity = round((presence_w * 3.0) + (warm_w * 1.0), 1)
    vocal_clarity = max(0.0, min(6.0, vocal_clarity))
    
    airiness = round((airy_w * 3.0) + (bright_w * 1.5) - (muddy_w * 1.5), 1)
    airiness = max(0.0, min(6.0, airiness))
    
    # Dynamically compute safe preamp headroom to prevent digital clipping!
    max_boost = max(bass_boost, vocal_clarity, airiness)
    preamp_gain = round(-0.8 * max_boost, 2)
    
    dyn_mix = {
        "preamp_gain": preamp_gain,
        "strength": 1.0,
        "bass_boost": bass_boost,
        "vocal_clarity": vocal_clarity,
        "airiness": airiness
    }
    
    # Apply and save to state
    with state_lock:
        app_state["mode"] = "auto"  # Force auto mode so that overlays are active and it syncs nicely!
        app_state["current_track"]["weights"] = weights
        app_state["current_track"]["mixing_reason"] = mixing_reason
        app_state["eq"]["low_shelf_gain"] = eq_curve["low_shelf_gain"]
        app_state["eq"]["first_band_gain"] = eq_curve["first_band_gain"]
        app_state["eq"]["second_band_gain"] = eq_curve["second_band_gain"]
        app_state["eq"]["third_band_gain"] = eq_curve["third_band_gain"]
        app_state["eq"]["high_shelf_gain"] = eq_curve["high_shelf_gain"]
        app_state["mix"] = dyn_mix
            
    apply_and_write_apo()
    return jsonify({"success": True, "state": app_state})


@app.route('/api/spotify/status', methods=['GET'])
def get_spotify_status():
    global spotify_oauth
    auth_ok = False
    if spotify_oauth:
        try:
            auth_ok = spotify_oauth.is_authenticated()
        except Exception:
            pass
    return jsonify({"authenticated": auth_ok})


@app.route('/api/spotify/authenticate', methods=['POST'])
def trigger_spotify_authenticate():
    global spotify_oauth, is_authenticating
    
    with authenticating_lock:
        if is_authenticating:
            return jsonify({"success": True, "message": "Authentication already in progress."})
        is_authenticating = True
        
    def run_auth():
        global is_authenticating
        try:
            logger.info("Initializing Spotify browser authorization pop-up...")
            spotify_oauth.authenticate()
        except Exception as e:
            logger.error(f"Spotify authentication flow error: {e}")
        finally:
            with authenticating_lock:
                is_authenticating = False
                
    threading.Thread(target=run_auth, daemon=True).start()
    return jsonify({"success": True, "message": "Authorization pop-up triggered."})


@app.route('/api/spotify/disconnect', methods=['POST'])
def trigger_spotify_disconnect():
    global spotify_oauth
    if spotify_oauth:
        try:
            logger.info("Clearing Spotify authentication tokens from memory and disk...")
            spotify_oauth.clear_tokens()
            
            with state_lock:
                app_state["spotify_authenticated"] = False
                app_state["current_track"] = {
                    "track_id": "",
                    "track_name": "Spotify Account Disconnected",
                    "artist_name": "Please click Connect Spotify above to authorize another account.",
                    "album_name": "",
                    "album_art": "",
                    "genres": [],
                    "tags": [],
                    "weights": {},
                    "mixing_reason": "Tokens cleared. Direct user playback sync suspended.",
                    "is_playing": False,
                    "is_private_session": False
                }
            return jsonify({"success": True, "message": "Spotify credentials successfully cleared."})
        except Exception as e:
            logger.error(f"Failed to clear Spotify credentials: {e}")
            return jsonify({"success": False, "message": f"Error disconnecting: {e}"}), 500
    return jsonify({"success": False, "message": "Spotify service not initialized."}), 400


@app.route('/api/search', methods=['POST'])
def search_and_mix():
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"success": False, "message": "Query query is required."}), 400
        
    artist_query = ""
    track_query = ""
    
    if " - " in query:
        artist_query, track_query = query.split(" - ", 1)
    else:
        track_query = query
        artist_query = data.get("artist", "").strip()
        
    track_id = f"custom_search_{time.time()}"
    track_name = track_query
    artist_name = artist_query or "Unknown Artist"
    album_art = ""
    album_name = ""
    genres = []
    
    # Run query against authenticated Spotify OAuth
    if spotify_oauth and spotify_oauth.is_authenticated():
        try:
            search_str = f"track:{track_query}"
            if artist_query:
                search_str += f" artist:{artist_query}"
            
            headers = {"Authorization": f"Bearer {spotify_oauth.access_token}"}
            res = requests.get(
                "https://api.spotify.com/v1/search",
                headers=headers,
                params={"q": search_str, "type": "track", "limit": 1}
            )
            if res.status_code == 200:
                items = res.json().get("tracks", {}).get("items", [])
                if items:
                    track_data = items[0]
                    track_id = track_data["id"]
                    track_name = track_data["name"]
                    artist_name = track_data["artists"][0]["name"]
                    album_name = track_data["album"]["name"]
                    images = track_data["album"]["images"]
                    if images:
                        album_art = images[0]["url"]
                        
                    # Fetch genres
                    artist_id = track_data["artists"][0]["id"]
                    res_artist = requests.get(f"https://api.spotify.com/v1/artists/{artist_id}", headers=headers)
                    if res_artist.status_code == 200:
                        genres = res_artist.json().get("genres", [])
        except Exception as e:
            logger.warning(f"Spotify search override failed: {e}")
            
    # Fallback to Last.fm
    tags = []
    if lastfm:
        try:
            tags = lastfm.get_track_tags(artist_name, track_name)
        except Exception as e:
            logger.warning(f"Last.fm tags check failed for search: {e}")
            
    combined_tags = tags + genres
    
    # Check if a custom EQ mix exists in the songs database first!
    recalled = load_track_eq_from_db(track_id)
    
    eq_curve = {
        "low_shelf_gain": 0.0,
        "first_band_gain": 0.0,
        "second_band_gain": 0.0,
        "third_band_gain": 0.0,
        "high_shelf_gain": 0.0
    }
    mixing_reason = ""
    weights = {}
    dyn_mix = {
        "preamp_gain": 0.0,
        "strength": 1.0,
        "bass_boost": 0.0,
        "vocal_clarity": 0.0,
        "airiness": 0.0
    }
    
    if recalled:
        logger.info(f"Search Recalled: ✓ Recalled custom EQ profile from database for search track {track_id}!")
        eq_curve = {
            "low_shelf_gain": recalled["eq"]["low_shelf_gain"],
            "first_band_gain": recalled["eq"]["first_band_gain"],
            "second_band_gain": recalled["eq"]["second_band_gain"],
            "third_band_gain": recalled["eq"]["third_band_gain"],
            "high_shelf_gain": recalled["eq"]["high_shelf_gain"]
        }
        dyn_mix = recalled["mix"]
        mixing_reason = "Recalled custom EQ profile & Mastering Overlays from local songs database."
    else:
        with state_lock:
            current_engine = app_state["ai_engine"]
            sound_style = app_state["sound_style"]
            
        if current_engine == "llm":
            try:
                ai_mix = get_ai_predicted_eq(track_name, artist_name, genres, tags, sound_style)
                eq_curve["low_shelf_gain"] = ai_mix["low_shelf_gain"]
                eq_curve["first_band_gain"] = ai_mix["first_band_gain"]
                eq_curve["second_band_gain"] = ai_mix["second_band_gain"]
                eq_curve["third_band_gain"] = ai_mix["third_band_gain"]
                eq_curve["high_shelf_gain"] = ai_mix["high_shelf_gain"]
                mixing_reason = ai_mix["mixing_reason"]
            except Exception:
                current_engine = "similarity"
                
        if current_engine == "similarity":
            weights = predictor.calculate_similarity_weights(combined_tags)
            interpolated = predictor.synthesize_eq_curve(weights)
            
            # Blend similarity centroid curve with target sound style offset
            offsets = STYLE_OFFSETS.get(sound_style, STYLE_OFFSETS["balanced"])
            eq_curve["low_shelf_gain"] = interpolated["low_shelf_gain"] + offsets["low_shelf_gain"]
            eq_curve["first_band_gain"] = interpolated["first_band_gain"] + offsets["first_band_gain"]
            eq_curve["second_band_gain"] = interpolated["second_band_gain"] + offsets["second_band_gain"]
            eq_curve["third_band_gain"] = interpolated["third_band_gain"] + offsets["third_band_gain"]
            eq_curve["high_shelf_gain"] = interpolated["high_shelf_gain"] + offsets["high_shelf_gain"]
            
            active_w = [f"{p} ({w*100:.0f}%)" for p, w in weights.items() if w > 0]
            style_desc = sound_style.replace("_", " ").title()
            mixing_reason = f"Synthesized manually blended curves: {', '.join(active_w)} (styled as '{style_desc}')."
            
            # Calculate smart dynamic Advanced Mastering Overlays based on tag weights!
            punchy_w = weights.get("punchy", 0.0)
            presence_w = weights.get("presence", 0.0)
            airy_w = weights.get("airy", 0.0)
            warm_w = weights.get("warm", 0.0)
            bright_w = weights.get("bright", 0.0)
            muddy_w = weights.get("muddy", 0.0)
            
            # Smart blending logic
            bass_boost = round((punchy_w * 4.0) + (warm_w * 1.5) - (bright_w * 1.0), 1)
            bass_boost = max(0.0, min(8.0, bass_boost))
            
            vocal_clarity = round((presence_w * 3.0) + (warm_w * 1.0), 1)
            vocal_clarity = max(0.0, min(6.0, vocal_clarity))
            
            airiness = round((airy_w * 3.0) + (bright_w * 1.5) - (muddy_w * 1.5), 1)
            airiness = max(0.0, min(6.0, airiness))
            
            # Dynamically compute safe preamp headroom to prevent digital clipping!
            max_boost = max(bass_boost, vocal_clarity, airiness)
            preamp_gain = round(-0.8 * max_boost, 2)
            
            dyn_mix = {
                "preamp_gain": preamp_gain,
                "strength": 1.0,
                "bass_boost": bass_boost,
                "vocal_clarity": vocal_clarity,
                "airiness": airiness
            }
        
    with state_lock:
        app_state["mode"] = "auto"  # Force auto mode so the UI updates and tracks changes correctly
        app_state["current_track"] = {
            "track_id": track_id,
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name,
            "album_art": album_art,
            "genres": genres,
            "tags": tags,
            "weights": {} if recalled or current_engine == "llm" else weights,
            "mixing_reason": mixing_reason
        }
        app_state["eq"]["low_shelf_gain"] = eq_curve["low_shelf_gain"]
        app_state["eq"]["first_band_gain"] = eq_curve["first_band_gain"]
        app_state["eq"]["second_band_gain"] = eq_curve["second_band_gain"]
        app_state["eq"]["third_band_gain"] = eq_curve["third_band_gain"]
        app_state["eq"]["high_shelf_gain"] = eq_curve["high_shelf_gain"]
        app_state["mix"] = dyn_mix
        
    apply_and_write_apo()
    return jsonify({"success": True, "state": app_state})


# --- Server Init ---

def init_services():
    global predictor, lastfm, spotify_client, spotify_oauth, apo_path, llm_client, main_config
    
    config_yaml = "config.yaml"
    
    # Config and LLM Assistant Boot
    try:
        main_config = Config()
        llm_client = LLMClient(main_config)
        logger.info(f"LLM Mixing Assistant successfully initialized (Provider: {main_config.llm_provider}).")
    except Exception as e:
        logger.error(f"Failed to boot LLM Client: {e}")
        
    predictor = SemanticEQPredictor(db_path="data/test_library.db")
    lastfm = LastFMClient(config_path=config_yaml)
    
    try:
        spotify_client = SpotifyAPIClient(config_path=config_yaml)
    except Exception as e:
        logger.warning(f"Spotify client credentials flow bypassed: {e}")
        
    try:
        spotify_oauth = SpotifyService()
        logger.info("Successfully linked to main Spotify User OAuth Service.")
    except Exception as e:
        logger.error(f"Critical: Failed to boot Spotify OAuth Service: {e}")
        
    apo_path = load_apo_path_from_config(config_yaml)


def main():
    global active_monitoring
    
    init_services()
    
    monitor_thread = threading.Thread(target=monitor_spotify_playback)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    def close_monitor():
        global active_monitoring
        active_monitoring = False
        
    def open_browser():
        time.sleep(1.2)
        import webbrowser
        webbrowser.open("http://127.0.0.1:5001")
        
    browser_thread = threading.Thread(target=open_browser)
    browser_thread.daemon = True
    browser_thread.start()
    
    logger.info("Dynamic Semantic EQ GUI Dashboard Server running on: http://127.0.0.1:5001")
    try:
        app.run(host="127.0.0.1", port=5001, debug=False)
    finally:
        close_monitor()


if __name__ == '__main__':
    main()
