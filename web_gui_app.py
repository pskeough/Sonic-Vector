"""Flask Web App Server & API Backend with Direct AI Mixing Assistant & OAuth Authorization.

Responsibility: Merges real-time Spotify User OAuth playback polling with a 
high-fidelity GPU-accelerated Local AI Mixing Assistant (or Gemini API) that 
recommends customized parametric EQ configurations and provides engineering justifications.
Gracefully handles write permissions and provides self-healing centroids fallback.
"""

import atexit
import base64
import math
import os
import re
import signal
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
from src.utils import Config, is_placeholder, set_section_values
from src.utils.llm_client import LLMClient
from src.dsp import apo, render
from src import desktop
from src.nowplaying import SmtcNowPlaying
from src.preferences import PreferenceStore
from spotify_client import SpotifyAPIClient, SpotifyNotConfigured
from lastfm_client import LastFMClient
from embed_song_predictor import SemanticEQPredictor, load_apo_path_from_config

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__, static_folder='static', template_folder='templates')

# State variables (thread-safe lock protected)
state_lock = threading.Lock()
# Serializes the whole snapshot-render-write sequence. Held across the APO
# write so a slider drag during a track change cannot land out of order.
commit_lock = threading.RLock()
app_state = {
    "mode": "auto",
    "ai_engine": "similarity",  # Default to similarity (Vector Centroids) instead of llm
    "sound_style": "balanced",  # "balanced", "bass_boost", "warm", "vocal", "chill", "loudness"
    "apo_write_status": "ok",
    "apo_status": {"state": "unknown", "detail": "", "endpoints": []},
    "bypass": False,
    "output": {
        "preamp_db": 0.0, "safety_preamp_db": 0.0, "user_trim_db": 0.0,
        "limited": False, "limit_scale": 1.0, "headroom_peak_db": 0.0,
        "bypassed": False, "response": []
    },
    # False unless an LLM provider is actually reachable. The engine dropdown
    # hides the AI option when this is false, because an entry that cannot work
    # is worse than no entry: picking it changed nothing and explained nothing.
    "llm_available": False,
    # "windows" | "spotify" | "none". Where now-playing comes from.
    "now_playing_source": "none",
    "spotify_authenticated": False,
    # False when no Spotify credentials are configured at all, which suppresses
    # the "connect your account" strip. Opting out is a choice, not a problem.
    "spotify_configured": False,
    "spotify_redirect_uri": "http://127.0.0.1:8888/callback",
    "pipeline_status": {
        "spotify": "Not configured (optional)",
        "playback": "Search a track to start",
        "dsp_engine": "Vector Similarity Centroids",
        "apo_writer": "Not active"
    },
    "current_track": {
        "track_id": "",
        "track_name": "No Track Loaded",
        "artist_name": "Search a track below to build a mix.",
        "album_name": "",
        "album_art": "",
        "genres": [],
        "tags": [],
        "weights": {},
        # "idle" | "spotify" | "search". The monitor thread may only replace a
        # track it put there itself; see _set_idle_track_locked.
        "source": "idle",
        # Explicit, because every placeholder headline is a truthy string and
        # "is a real song loaded?" used to be asked by comparing against a list
        # of those strings, copied into six places and stale in four of them.
        "placeholder": True,
        "mixing_reason": "Equalizer flat. Search a track, or connect Spotify, to load a mixing profile."
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
# The active now-playing source. Either the Windows media session reader (no
# account, no network, sees every player) or the Spotify OAuth service. Both
# answer is_authenticated() and get_current_track(), so the monitor loop below
# does not know or care which one it is holding.
now_playing = None
smtc_source = None     # kept separately: it also serves cached album art
llm_client = None      # Unified LLM caller (Gemini/Llama.cpp)
main_config = None     # Main project settings Config
apo_path = None
preferences = None    # listener vote log
active_monitoring = True
authenticating_lock = threading.Lock()
is_authenticating = False

# track_id the listener has personally adjusted this session. Only a track in
# here may be written to the preference database; anything else would be the
# app storing its own output and calling it a preference.
user_edited_track_id = None


def mark_user_edit():
    """Record that the listener, not the algorithm, changed the current mix."""
    global user_edited_track_id
    with state_lock:
        user_edited_track_id = app_state["current_track"].get("track_id") or None


# The prompt in get_ai_predicted_eq() asks the model for gains at 60/250/1000/
# 4000/12000 Hz, so those are the frequencies its answer must be applied at.
# They used to be discarded and the gains applied at 120/250/1000/3500/10000,
# which meant the model was scored against a filter set it was never shown.
# Legacy headlines, kept only so a track dict written by an older build (or
# recalled from a database row) is still recognised as a placeholder. New code
# sets current_track["placeholder"] instead; see _is_placeholder.
PLACEHOLDER_TRACK_NAMES = {
    "", "No Track Playing", "No Track Loaded", "Spotify Player Paused",
    "Spotify Account Not Connected", "Spotify private session active",
    "No Active Spotify Playback", "Playback Not Supported",
    "Spotify Account Disconnected", "Spotify Not Connected",
}


def _is_placeholder(track: dict) -> bool:
    """True when current_track is a status message rather than a real song."""
    if track.get("placeholder"):
        return True
    return str(track.get("track_name", "")) in PLACEHOLDER_TRACK_NAMES


def idle_track(headline: str, detail: str, reason: str, album: str = "") -> dict:
    """Build a placeholder current_track. Nothing here is a song."""
    return {
        "track_id": "",
        "track_name": headline,
        "artist_name": detail,
        "album_name": album,
        "album_art": "",
        "genres": [],
        "tags": [],
        "weights": {},
        "source": "idle",
        "placeholder": True,
        "mixing_reason": reason,
        "is_playing": False,
        "is_private_session": False,
    }


def _set_idle_track_locked(track: dict) -> None:
    """Publish a placeholder, unless the listener loaded a track by hand.

    The monitor thread used to overwrite current_track unconditionally every
    1.5 s. With no Spotify account linked that meant a track loaded through the
    search box survived for one poll and was then replaced by "Spotify Account
    Not Connected", which took the tags, the profile weights, Remix, Analyze
    and the rating buttons down with it. Searching is the supported way to use
    this app without Spotify, so a search result outranks a status message.

    Caller must hold state_lock.
    """
    if app_state["current_track"].get("source") == "search":
        return
    app_state["current_track"] = track


def spotify_idle_track() -> dict:
    """The placeholder that fits however now-playing is currently set up."""
    source = app_state.get("now_playing_source")

    if source == "windows":
        return idle_track(
            "Nothing Playing",
            "Press play in any app and this will follow it.",
            "Watching the Windows media session. Start a track in Spotify, a "
            "browser, or any other player and it will be detected and mixed "
            "automatically. No account required.",
        )

    if source == "spotify":
        return idle_track(
            "Spotify Not Connected",
            "Click CONNECT SPOTIFY above, or search a track below.",
            "Spotify credentials are configured but the account is not linked yet. "
            "Authorize it to auto-detect now-playing, or just search a track.",
        )

    return idle_track(
        "No Track Loaded",
        "Search a track below to build a mix.",
        "Equalizer flat. Search any song to analyze it and load a mixing profile. "
        "Automatic detection is unavailable on this machine; the search box does "
        "everything else.",
    )


def _refresh_pipeline_locked(auth_ok: bool, track_info=None) -> None:
    """Recompute the four signal-path rows. Caller must hold state_lock.

    This used to live at the tail of the monitor loop, after four `continue`
    statements, so on an install without Spotify it never ran once: the panel
    reported "Waiting for stream..." and an unexplained "Disconnected" for the
    entire session no matter what the app was actually doing.
    """
    track = app_state["current_track"]
    configured = app_state.get("spotify_configured")
    source = app_state.get("now_playing_source")

    if source == "windows":
        app_state["pipeline_status"]["spotify"] = "Windows media session (no account)"
    elif auth_ok:
        app_state["pipeline_status"]["spotify"] = "Connected (OAuth active)"
    elif configured:
        app_state["pipeline_status"]["spotify"] = "Not linked (click Connect)"
    else:
        app_state["pipeline_status"]["spotify"] = "Not configured (optional)"

    if track.get("source") == "search":
        app_state["pipeline_status"]["playback"] = f"Loaded by search: '{track['track_name']}'"
    elif not auth_ok:
        app_state["pipeline_status"]["playback"] = (
            "Waiting for Spotify connection..." if configured
            else "Search a track to start")
    elif source == "windows" and not track_info:
        app_state["pipeline_status"]["playback"] = "Watching for playback on this PC..."
    elif not track_info:
        app_state["pipeline_status"]["playback"] = "Waiting for stream in Spotify app..."
    elif track_info.get("is_private_session"):
        app_state["pipeline_status"]["playback"] = "Blocked (private session active)"
    elif _is_placeholder(track):
        app_state["pipeline_status"]["playback"] = "Unsupported content (Local/Ad/Podcast)"
    else:
        app_state["pipeline_status"]["playback"] = f"Active: '{track['track_name']}'"

    if _is_placeholder(track):
        app_state["pipeline_status"]["dsp_engine"] = "Idle (waiting for a track)"
    elif app_state["ai_engine"] == "similarity":
        app_state["pipeline_status"]["dsp_engine"] = "Keyword profile matching"
    else:
        app_state["pipeline_status"]["dsp_engine"] = "AI mixing assistant"

    # A successful file write means nothing if APO is not attached to the
    # output the listener is actually using, so the write status alone must
    # never be reported as "active".
    write_st = app_state["apo_write_status"]
    apo_state = app_state["apo_status"].get("state", "unknown")
    if write_st == "denied":
        app_state["pipeline_status"]["apo_writer"] = "Cannot write config.txt (permission denied)"
    elif write_st == "path_missing":
        app_state["pipeline_status"]["apo_writer"] = "Equalizer APO config folder not found"
    elif write_st != "ok":
        app_state["pipeline_status"]["apo_writer"] = "Failed to write config.txt"
    elif apo_state == "apo_not_installed":
        app_state["pipeline_status"]["apo_writer"] = "Equalizer APO is not installed"
    elif apo_state == "apo_no_active_endpoint":
        app_state["pipeline_status"]["apo_writer"] = "Not applied: APO is not enabled on your current output"
    elif app_state["bypass"]:
        app_state["pipeline_status"]["apo_writer"] = "Bypassed (comparing against flat)"
    elif apo_state == "apo_ready":
        app_state["pipeline_status"]["apo_writer"] = "EQ active on your output"
    else:
        app_state["pipeline_status"]["apo_writer"] = "config.txt written (APO status unknown)"


def describe_mix(weights: dict, sound_style: str, verb: str = "Synthesized") -> str:
    """Explain the curve in the signal-path note.

    Tracks the matcher has no opinion about are common and legitimate: an
    obscure release simply has no Last.fm tags that map to a profile. That case
    used to render as "blended using preprocessed SAFE centroids ()", an empty
    list presented as an achievement, which reads like a bug because it looks
    like one.
    """
    style_desc = sound_style.replace("_", " ").title()
    active = [f"{p} ({w * 100:.0f}%)" for p, w in (weights or {}).items() if w > 0]

    if not active:
        return (f"No profile keywords matched this track's tags, so the EQ stays "
                f"flat apart from the '{style_desc}' voicing. Tune it by hand and "
                f"the setting is remembered for this track.")

    return (f"{verb} curves blended using preprocessed SAFE centroids "
            f"({', '.join(active)}) and styled as '{style_desc}'.")


def llm_available() -> bool:
    """Whether the optional AI mixing engine has anything to talk to.

    This app is designed to run on no API keys at all, so the AI engine is a
    bonus path, never the working assumption. A local OpenAI-compatible server
    needs no key and counts as available; the hosted provider needs one and
    without it the engine is not offered at all, rather than being offered and
    then silently falling back on every track.
    """
    if llm_client is None or main_config is None:
        return False
    if main_config.llm_provider != "gemini":
        return True
    return not is_placeholder(main_config.gemini_api_key)


def llm_fallback_note(exc: Exception) -> str:
    """Prefix for mixing_reason when the AI engine could not answer.

    The fallback to keyword matching was silent, so on an install with no
    Gemini key (the shipped default) choosing "AI MIXING ASSISTANT" changed the
    curve slightly, reported a keyword-matching explanation, and never said why
    the engine it named was not the one that ran.
    """
    reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if len(reason) > 140:
        reason = reason[:137] + "..."
    return (f"AI mixing assistant unavailable ({reason}). Fell back to keyword "
            f"profile matching. ")


LLM_BAND_FREQS = {
    "low_shelf_freq": 60.0,
    "first_band_freq": 250.0, "first_band_q": 0.71,
    "second_band_freq": 1000.0, "second_band_q": 0.71,
    "third_band_freq": 4000.0, "third_band_q": 0.71,
    "high_shelf_freq": 12000.0,
}


def blend_curve(interpolated: dict, sound_style: str) -> dict:
    """Combine an interpolated centroid with the style offset, keeping the
    centroid's own band placement.

    The web path used to overwrite every frequency with a fixed 120/250/1000/
    3500/10000 Hz grid and copy across only the five gains, so the punchy
    centroid's 68.9 Hz shelf was applied at 120 Hz and its 3007 Hz snap band
    landed at 1000 Hz, in the honk region. The CLI path kept the interpolated
    values, so the two emitted different EQ from identical weights.
    """
    offsets = STYLE_OFFSETS.get(sound_style, STYLE_OFFSETS["balanced"])
    out = dict(interpolated)
    for key, offset in offsets.items():
        out[key] = interpolated.get(key, 0.0) + offset
    return out


def _set_eq_locked(eq_curve: dict) -> None:
    """Copy a curve into app_state['eq']. Caller must hold state_lock.

    Frequencies and Qs are copied when the curve carries them, so a profile's
    own band placement survives rather than being flattened onto a fixed grid.
    """
    for key in app_state["eq"]:
        if key in eq_curve:
            app_state["eq"][key] = float(eq_curve[key])


def dynamic_overlays(weights: dict) -> dict:
    """Derive the mastering overlay sliders from the active profile weights.

    `muddy` is a defect descriptor. Someone tagging a track "muddy", "boxy" or
    "boomy" is naming what they would remove, not a target to reproduce, so it
    only ever subtracts here and it is excluded from the additive centroid
    blend for the same reason. The previous code blended the muddy centroid
    additively (+3.7 dB low shelf, +4.5 dB at 298 Hz), which boosted the mud
    on exactly the tracks a listener had complained about.

    The preamp is deliberately not computed here. commit_state() derives it
    from the realized filter cascade, which is the only place with enough
    information to get it right.
    """
    def w(profile: str) -> float:
        return weights.get(profile, 0.0)

    bass_boost = max(0.0, min(8.0, round(
        w("punchy") * 4.0 + w("warm") * 1.5 - w("bright") * 1.0, 1)))
    vocal_clarity = max(0.0, min(6.0, round(
        w("presence") * 3.0 + w("warm") * 1.0, 1)))
    airiness = max(0.0, min(6.0, round(
        w("airy") * 3.0 + w("bright") * 1.5 - w("muddy") * 1.5, 1)))

    return {
        "preamp_gain": 0.0,
        "strength": 1.0,
        "bass_boost": bass_boost,
        "vocal_clarity": vocal_clarity,
        "airiness": airiness,
    }


def _composite_eq(eq: dict, mix: dict) -> dict:
    """Merge the band gains and the overlay sliders into one filter set.

    This is the only place the overlays are folded in. Previously the same
    arithmetic appeared in four places and drifted between them.
    """
    strength = mix["strength"]
    out = dict(eq)
    out["low_shelf_gain"] = eq["low_shelf_gain"] * strength + mix["bass_boost"]
    out["first_band_gain"] = eq["first_band_gain"] * strength
    out["second_band_gain"] = eq["second_band_gain"] * strength + mix["vocal_clarity"]
    out["third_band_gain"] = eq["third_band_gain"] * strength + mix["vocal_clarity"] * 0.5
    out["high_shelf_gain"] = eq["high_shelf_gain"] * strength + mix["airiness"]
    return out


def commit_state():
    """Render the current state to a filter set and publish it to Equalizer APO.

    Every path that changes the sound goes through here, under one lock, so
    a slider drag during a track change cannot interleave with the monitor
    thread and write curves out of order.

    The preamp is derived from the realized cascade rather than guessed from
    the overlay sliders. The previous rule (-0.8 * max of three sliders)
    ignored the band gains entirely and let the composite response reach
    +8.5 dB above unity on ordinary material.
    """
    global app_state, apo_path

    with commit_lock:
        with state_lock:
            eq = app_state["eq"].copy()
            mix = app_state["mix"].copy()
            bypassed = app_state["bypass"]

        composite = _composite_eq(eq, mix)
        budgeted, limited, scale = render.apply_headroom_budget(composite)

        # User trim rides on top of the safety preamp, never replaces it.
        user_trim = max(-12.0, min(0.0, float(mix.get("preamp_gain", 0.0))))
        safety_preamp = render.required_preamp_db(budgeted)
        preamp = max(render.PREAMP_FLOOR_DB, safety_preamp + user_trim)

        if bypassed:
            # Level-matched flat arm, so the comparison is about tone rather
            # than about which side is louder. Analytic match; see
            # render.pink_weighted_mean_db.
            emitted = dict(apo.FLAT_EQ)
            emitted_preamp = max(
                render.PREAMP_FLOOR_DB,
                min(0.0, preamp + render.pink_weighted_mean_db(budgeted)),
            )
            note = "BYPASS (level-matched, assumed spectrum)"
        else:
            emitted = budgeted
            emitted_preamp = preamp
            note = f"headroom-limited to {scale:.3f} of requested" if limited else ""

        text = apo.build_config_text(emitted, emitted_preamp, note)
        status = apo.write_config_atomic(apo_path, text)

        # Local mirror, useful when APO is not installed or not registered.
        apo.write_config_atomic(PROJECT_ROOT / "data" / "config.txt", text)

        headroom_peak = max(render.render_curve(emitted)) + emitted_preamp

        with state_lock:
            app_state["apo_write_status"] = status
            app_state["output"] = {
                "preamp_db": round(emitted_preamp, 2),
                "safety_preamp_db": round(safety_preamp, 2),
                "user_trim_db": round(user_trim, 2),
                "limited": limited,
                "limit_scale": round(scale, 4),
                "headroom_peak_db": round(headroom_peak, 3),
                "bypassed": bypassed,
                "response": render.response_points(emitted, emitted_preamp),
            }


def write_flat_config(reason: str = "shutdown"):
    """Leave the system EQ flat. Called at startup and on every exit path.

    Without this, killing the app left whatever curve was last written applied
    to every sound on the machine, indefinitely.
    """
    global apo_path
    try:
        text = apo.build_config_text(
            apo.FLAT_EQ, 0.0, f"flat ({reason}) - Sonic Vector is not running"
        )
        apo.write_config_atomic(apo_path, text)
        apo.write_config_atomic(PROJECT_ROOT / "data" / "config.txt", text)
        logger.info(f"Equalizer APO config reset to flat ({reason}).")
    except Exception as e:
        logger.error(f"Failed to reset Equalizer APO config to flat: {e}")


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
            logger.info(f"OK: Saved track EQ & Mix overlays to songs database for '{track_name}' by {artist_name}")
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

    # Say "no key" rather than letting the provider answer with a raw 400 that
    # the listener then sees quoted in the signal-path note.
    if not llm_available():
        raise RuntimeError("no LLM provider is configured")

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
        
        logger.info(f"OK: AI Mixing success: {eq_curve}")
        return eq_curve
    except Exception as e:
        logger.error(f"Failed to fetch AI Mixing Assistant parameters: {e}")
        raise e


def _looks_like_spotify_id(track_id: str) -> bool:
    """Spotify IDs are 22-character base62. SMTC keys are 16 hex characters."""
    return bool(track_id) and len(track_id) == 22 and track_id.isalnum()


def fetch_artist_genres(track_id: str, track_name: str, artist_name: str) -> list:
    """Artist genres for a track, whichever source identified it.

    Pure enrichment: genres widen the tag set the centroid matcher sees, and
    the app is perfectly happy with none. It needs only Spotify's app
    credentials, never a logged-in user, so it works on the Windows path too.

    The Windows media session has no track IDs, so a track identified that way
    is looked up by name first. Calling get_track() with a 16-hex local key
    would just be a guaranteed 400.
    """
    if spotify_client is None or not track_name:
        return []

    try:
        if _looks_like_spotify_id(track_id):
            track_data = spotify_client.get_track(track_id)
        else:
            query = f"track:{track_name}"
            if artist_name:
                query += f" artist:{artist_name}"
            items = (spotify_client.search_track(query) or {}) \
                .get("tracks", {}).get("items", [])
            if not items:
                return []
            track_data = items[0]

        artist_id = track_data["artists"][0]["id"]
        return spotify_client.get_artist(artist_id).get("genres", []) or []
    except Exception as e:
        logger.debug(f"Genre enrichment unavailable for '{track_name}': {e}")
        return []


def _autosave_previous_track(last_track_id: str) -> None:
    """Persist the outgoing track's mix, but only if the listener shaped it.

    Saving unconditionally means storing the app's own output as if it were a
    preference and then recalling it forever: once a track had been heard, the
    style and engine dropdowns became silent no-ops for it. The track-change
    path was fixed for that, but the stop, private-session and unsupported-
    content paths still saved unconditionally, so merely pausing Spotify
    latched the auto-generated curve onto the song. All four now come here.
    """
    if not last_track_id or user_edited_track_id != last_track_id:
        return

    with state_lock:
        track = app_state["current_track"].copy()
        gains = app_state["eq"].copy()
        mix = app_state["mix"].copy()

    if _is_placeholder(track):
        return

    save_track_eq_to_db(last_track_id, track.get("track_name"),
                        track.get("artist_name"), track.get("album_name"),
                        gains, mix)


def monitor_spotify_playback():
    """Background loop polling the active now-playing source for track changes.

    Source-agnostic: it holds whatever _select_now_playing_source picked, which
    is normally the Windows media session and only falls back to Spotify OAuth.
    The name is kept because the launcher and the tests reference it."""
    global app_state, predictor, lastfm, spotify_client, now_playing, active_monitoring
    
    logger.info("Background direct Spotify User playback thread started.")
    last_track_id = None
    last_status_log_time = 0
    
    while active_monitoring:
        try:
            # Throttled terminal logger
            now = time.time()
            log_throttled = (now - last_status_log_time) > 10.0
            
            auth_ok = False
            if now_playing:
                try:
                    auth_ok = now_playing.is_authenticated()
                except Exception as e:
                    logger.debug(f"Auth check failed: {e}")
            
            # auth_ok means "the now-playing source is ready", which is a
            # different claim from "a Spotify account is linked". Conflating
            # them made the Windows source light up the Spotify connected
            # state. They are now answered separately: the account can be
            # linked while Windows does the detecting, and usually will be.
            if now_playing is spotify_oauth:
                spotify_linked = auth_ok
            elif spotify_oauth is not None:
                try:
                    spotify_linked = spotify_oauth.is_authenticated()
                except Exception:
                    spotify_linked = False
            else:
                spotify_linked = False

            with state_lock:
                app_state["spotify_authenticated"] = spotify_linked
                current_mode = app_state["mode"]
                current_engine = app_state["ai_engine"]
                sound_style = app_state["sound_style"]
            
            if not auth_ok:
                with state_lock:
                    _set_idle_track_locked(spotify_idle_track())
                    _refresh_pipeline_locked(auth_ok=False)
                    configured = app_state["spotify_configured"]
                # Not having Spotify set up is a supported configuration, not a
                # fault, so it is not worth a WARNING every ten seconds for the
                # life of the process. Only nag when credentials exist and the
                # account is simply not linked yet.
                if log_throttled and configured:
                    logger.info("[Monitor] Spotify is configured but not linked. "
                                "Open the dashboard and click 'Connect Spotify'.")
                    last_status_log_time = now
                time.sleep(2.0)
                continue

            # ALWAYS poll Spotify playback even in manual override, to detect song changes and auto-save!
            track_info = None
            try:
                track_info = now_playing.get_current_track()
            except Exception as e:
                logger.warning(f"Error fetching direct current track: {e}")
                
            # Case 1: No active session (device disconnected / Spotify closed)
            if not track_info:
                _autosave_previous_track(last_track_id)
                last_track_id = None

                with state_lock:
                    if app_state["now_playing_source"] == "windows":
                        _set_idle_track_locked(idle_track(
                            "Nothing Playing",
                            "Press play in any app and this will follow it.",
                            "Watching the Windows media session. Start a track in any "
                            "player on this PC and it will be detected and mixed.",
                        ))
                    else:
                        _set_idle_track_locked(idle_track(
                            "No Active Spotify Playback",
                            "Open your Spotify app and play a song to auto-master.",
                            "No active playback session detected. Open Spotify on your phone "
                            "or PC and start streaming, or search a track below to mix it by hand.",
                            album="Waiting for active player session...",
                        ))
                    _refresh_pipeline_locked(auth_ok=True, track_info=None)
                if log_throttled:
                    logger.info("[Monitor] Listening, but nothing is playing yet.")
                    last_status_log_time = now
                time.sleep(1.5)
                continue

            # Case 2: Spotify Private Session Active (Blocks API metadata)
            if track_info.get("is_private_session"):
                _autosave_previous_track(last_track_id)
                last_track_id = None

                with state_lock:
                    private = idle_track(
                        "Spotify private session active",
                        "Disable 'Private Session' in Spotify Settings to allow EQ sync.",
                        "Your Spotify client is in a private session. Spotify blocks song "
                        "metadata queries while in Private Session for privacy. Turn off "
                        "'Private Session' in Spotify app settings to begin auto-mastering!",
                        album="Spotify is blocking metadata retrieval",
                    )
                    private["is_private_session"] = True
                    _set_idle_track_locked(private)
                    _refresh_pipeline_locked(auth_ok=True, track_info=track_info)
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
                _autosave_previous_track(last_track_id)
                last_track_id = None

                with state_lock:
                    _set_idle_track_locked(idle_track(
                        "Playback Not Supported",
                        "Local files, podcasts, or ads cannot be auto-mixed.",
                        "The playing item is not in Spotify's online catalog or lacks "
                        "standard metadata (e.g. downloaded local MP3s, advertisements, "
                        "or podcasts). Play a streamed song, or search it by name below.",
                    ))
                    _refresh_pipeline_locked(auth_ok=True, track_info=track_info)
                if log_throttled:
                    logger.info("[Monitor] Playback detected but item metadata is untrackable (e.g. local files, podcasts, ads).")
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
                        logger.info(f"[Monitor] (Manual Mode) Playback state changed: {'Playing' if is_playing else 'Paused'} for '{track_name}'")
                        last_status_log_time = now
                time.sleep(1.5)
                continue

            # If track changed, recalculate
            if track_id != last_track_id:
                # 1. Save the previous track's mix, but only if the listener
                #    actually shaped it; see _autosave_previous_track.
                _autosave_previous_track(last_track_id)

                logger.info(f"[Monitor] Active song detected: '{track_name}' by {artist_name} (Playing: {is_playing})")
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
                fallback_note = ""
                dyn_mix = {
                    "preamp_gain": 0.0,
                    "strength": 1.0,
                    "bass_boost": 0.0,
                    "vocal_clarity": 0.0,
                    "airiness": 0.0
                }

                if recalled:
                    logger.info(f"[Monitor] OK: Recalled custom EQ profile from songs database for track {track_id}!")
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
                    genres = fetch_artist_genres(track_id, track_name, artist_name)
                    if lastfm:
                        try:
                            tags = lastfm.get_track_tags(artist_name, track_name)
                        except Exception:
                            pass
                else:
                    # No recalled profile: do normal tag similarity matching!
                    genres = fetch_artist_genres(track_id, track_name, artist_name)

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
                            eq_curve.update(LLM_BAND_FREQS)
                            mixing_reason = ai_mix["mixing_reason"]
                        except Exception as e:
                            logger.warning(f"AI Mixing Assistant failed. Toggling self-healing fallback to Similarity centroids: {e}")
                            fallback_note = llm_fallback_note(e)
                            current_engine = "similarity" # Force similarity fallback
                            with state_lock:
                                app_state["ai_engine"] = "similarity"

                    # --- STRATEGY 2: OFFLINE VECTOR MATCHING ---
                    if current_engine == "similarity":
                        weights = predictor.calculate_similarity_weights(combined_tags)
                        interpolated = predictor.synthesize_eq_curve(weights)

                        # Blend similarity centroid curve with target sound style offset
                        eq_curve = blend_curve(interpolated, sound_style)

                        # Generate technically explained centroids list
                        mixing_reason = fallback_note + describe_mix(weights, sound_style)
                        
                        dyn_mix = dynamic_overlays(weights)
                
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
                        # Whichever source actually identified it, so the UI can
                        # say so rather than always crediting Spotify.
                        "source": track_info.get("source", "spotify"),
                        "placeholder": False,
                        "mixing_reason": mixing_reason,
                        "is_playing": is_playing,
                        "is_private_session": False
                    }
                    _set_eq_locked(eq_curve)
                    
                    # Persist dynamic overlays in Auto mode
                    if app_state["mode"] == "auto":
                        app_state["mix"] = dyn_mix
                    
                # Write to active APO filters
                commit_state()
                last_track_id = track_id
            else:
                # Track has not changed, but play/pause state may have changed
                with state_lock:
                    prev_playing = app_state["current_track"].get("is_playing", False)
                    if prev_playing != is_playing:
                        app_state["current_track"]["is_playing"] = is_playing
                        logger.info(f"[Monitor] Playback state changed: {'Playing' if is_playing else 'Paused'} for '{track_name}'")
                        last_status_log_time = now
            
            with state_lock:
                _refresh_pipeline_locked(auth_ok=True, track_info=track_info)

        except Exception as e:
            logger.error(f"Error in direct user monitor loop: {e}", exc_info=True)
            
        time.sleep(1.5)
                



# --- REST API CONTROLLERS ---

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/favicon.ico')
def favicon():
    """Browsers ask for this at the root regardless of the <link> tag, and a
    404 on every page load is noise in a log people are told to read."""
    return app.send_static_file('favicon.ico')


@app.route('/api/status', methods=['GET'])
def get_status():
    with state_lock:
        return jsonify(app_state)


def recalculate_current_track_eq(force_recompute: bool = False):
    """Recompute the EQ for the active track from the selected engine and style.

    force_recompute skips the saved-profile recall. Changing the style or the
    engine is an explicit instruction, so it has to win over a stored curve;
    otherwise those controls appear broken on any track with a saved profile.
    """
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

    # Skip if no real song is loaded
    if _is_placeholder(track):
        return

    combined_tags = tags + genres
    
    eq_curve = {
        "low_shelf_gain": 0.0, "first_band_gain": 0.0, "second_band_gain": 0.0,
        "third_band_gain": 0.0, "high_shelf_gain": 0.0
    }
    mixing_reason = ""
    weights = {}
    fallback_note = ""
    dyn_mix = {
        "preamp_gain": 0.0,
        "strength": 1.0,
        "bass_boost": 0.0,
        "vocal_clarity": 0.0,
        "airiness": 0.0
    }

    # Try to recall existing custom EQ mix from songs database first!
    recalled = None
    if track_id and not force_recompute:
        recalled = load_track_eq_from_db(track_id)
        
    if recalled:
        logger.info(f"Recalculate: OK: Recalled custom EQ profile from database for track {track_id}!")
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
                eq_curve.update(LLM_BAND_FREQS)
                mixing_reason = ai_mix["mixing_reason"]
            except Exception as e:
                logger.warning(f"AI Mixing Assistant failed during recalculation, falling back to similarity: {e}")
                fallback_note = llm_fallback_note(e)
                current_engine = "similarity"
                with state_lock:
                    app_state["ai_engine"] = "similarity"

        if current_engine == "similarity":
            weights = predictor.calculate_similarity_weights(combined_tags)
            interpolated = predictor.synthesize_eq_curve(weights)

            eq_curve = blend_curve(interpolated, sound_style)

            mixing_reason = fallback_note + describe_mix(weights, sound_style)
            
            dyn_mix = dynamic_overlays(weights)
        
    with state_lock:
        app_state["current_track"]["weights"] = weights if current_engine == "similarity" else {}
        app_state["current_track"]["mixing_reason"] = mixing_reason
        _set_eq_locked(eq_curve)
        
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
    if new_engine == "llm" and not llm_available():
        return jsonify({"success": False,
                        "message": "No LLM provider is configured. Keyword profile "
                                   "matching is the engine."}), 400

    with state_lock:
        app_state["ai_engine"] = new_engine
        logger.info(f"AI Mixing Engine set to: {new_engine}")

    # Recompute immediately, overriding any saved profile for this track.
    recalculate_current_track_eq(force_recompute=True)
    commit_state()
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

    # Recalculate immediately, overriding any saved profile for this track.
    recalculate_current_track_eq(force_recompute=True)
    commit_state()
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
        commit_state()
        
    return jsonify({"success": True, "mode": new_mode})


# Accepted range per field. Anything outside is a client bug or an attack, not
# something to silently clamp into a shape we then apply to the user's audio.
EQ_FIELD_LIMITS = {
    "low_shelf_gain": (-15.0, 15.0), "low_shelf_freq": (20.0, 500.0),
    "first_band_gain": (-15.0, 15.0), "first_band_freq": (20.0, 20000.0),
    "first_band_q": (0.1, 10.0),
    "second_band_gain": (-15.0, 15.0), "second_band_freq": (20.0, 20000.0),
    "second_band_q": (0.1, 10.0),
    "third_band_gain": (-15.0, 15.0), "third_band_freq": (20.0, 20000.0),
    "third_band_q": (0.1, 10.0),
    "high_shelf_gain": (-15.0, 15.0), "high_shelf_freq": (1000.0, 20000.0),
}
MIX_FIELD_LIMITS = {
    "preamp_gain": (-12.0, 0.0), "strength": (0.0, 2.0),
    "bass_boost": (-8.0, 8.0), "vocal_clarity": (-6.0, 6.0),
    "airiness": (-6.0, 6.0),
}


def _coerce_field(name: str, raw, limits: dict):
    """Parse one numeric field, or raise ValueError with a usable message.

    float("nan") passes silently through min()/max() in CPython (min(15.0, nan)
    returns 15.0), so posting a NaN gain used to yield a +15 dB band. Non-finite
    values are rejected outright rather than clamped.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {raw!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {raw!r}")
    lo, hi = limits[name]
    if not (lo <= value <= hi):
        raise ValueError(f"{name} must be within [{lo}, {hi}], got {value}")
    return value


@app.route('/api/update_eq', methods=['POST'])
def update_eq_parameters():
    data = request.json or {}

    try:
        new_eq = {
            k: _coerce_field(k, data["eq"][k], EQ_FIELD_LIMITS)
            for k in app_state["eq"] if k in data.get("eq", {})
        }
        new_mix = {
            k: _coerce_field(k, data["mix"][k], MIX_FIELD_LIMITS)
            for k in app_state["mix"] if k in data.get("mix", {})
        }
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400

    with state_lock:
        if app_state["mode"] == "auto":
            app_state["mode"] = "manual"
            logger.info("Manual drag detected: Toggled into manual override mode.")
        app_state["eq"].update(new_eq)
        app_state["mix"].update(new_mix)

    mark_user_edit()
    commit_state()
    with state_lock:
        return jsonify({"success": True, "state": app_state})


@app.route('/api/bypass', methods=['POST'])
def set_bypass():
    """Hold-to-compare: swap between the processed curve and flat.

    The two arms are level-matched so the comparison is about tone rather than
    about which side happens to be louder. The match is analytic (pink-weighted
    over an assumed spectrum), which is why the UI labels it "assumed"; a real
    measured match needs the loopback capture that lands in the next phase.
    """
    data = request.json or {}
    enabled = bool(data.get("enabled", False))
    with state_lock:
        app_state["bypass"] = enabled
    commit_state()
    with state_lock:
        return jsonify({
            "success": True,
            "bypass": enabled,
            "level_match": "assumed",
            "output": app_state["output"],
        })


@app.route('/api/feedback', methods=['POST'])
def record_feedback():
    """Record a thumbs-up / thumbs-down against the mix currently playing.

    This is capture only. There is no model consuming it yet; the point is that
    votes are worth nothing retroactively, so collection starts the moment the
    button exists. Each vote is stored with the exact curve that was audible,
    projected into the 4-axis preference basis, so it stays usable when the
    model lands.
    """
    if preferences is None:
        return jsonify({"success": False,
                        "message": "Preference store unavailable."}), 503

    data = request.json or {}
    verdict = str(data.get("verdict", "")).lower()
    if verdict not in ("up", "down"):
        return jsonify({"success": False,
                        "message": "verdict must be 'up' or 'down'."}), 400

    with state_lock:
        track = app_state["current_track"].copy()
        eq = app_state["eq"].copy()
        output = app_state["output"].copy()
        style = app_state["sound_style"]
        engine = app_state["ai_engine"]
        bypassed = app_state["bypass"]

    # A vote cast while the EQ is bypassed is a vote about no EQ at all.
    if bypassed:
        return jsonify({"success": False,
                        "message": "Release Compare before rating the mix."}), 409

    # The placeholder headlines are truthy strings, so a plain emptiness check
    # let a vote through with nothing playing.
    if _is_placeholder(track):
        return jsonify({"success": False,
                        "message": "Nothing is loaded to rate."}), 409

    try:
        result = preferences.record_unary(
            verdict=verdict,
            eq=eq,
            preamp_db=float(output.get("preamp_db", 0.0)),
            track=track,
            sound_style=style,
            engine=engine,
            limited=bool(output.get("limited")),
        )
    except Exception as e:
        logger.error(f"Failed to record preference: {e}")
        return jsonify({"success": False, "message": "Could not record vote."}), 500

    return jsonify({"success": True, **result, "summary": preferences.summary()})


@app.route('/api/feedback/summary', methods=['GET'])
def feedback_summary():
    if preferences is None:
        return jsonify({"total": 0, "up": 0, "down": 0})
    return jsonify(preferences.summary())


@app.route('/api/art/<key>', methods=['GET'])
def get_album_art(key):
    """Serve cover art captured from the Windows media session.

    SMTC hands over an image stream rather than a URL, so unlike the Spotify
    path there is nothing the browser can fetch directly. The reader caches the
    bytes when a track starts and this hands them out. Immutable because the
    key is a hash of artist and title: a given key's art never changes.
    """
    if smtc_source is None:
        return ("", 404)

    # Path traversal is not possible through Flask's <key> converter, but the
    # key is used as a cache lookup, so keep it to the shape we generate.
    if not re.fullmatch(r"[0-9a-f]{16}", key or ""):
        return ("", 400)

    data, content_type = smtc_source.get_art(key)
    if not data:
        return ("", 404)

    response = app.response_class(data, mimetype=content_type or "image/png")
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    response.headers["Content-Length"] = str(len(data))
    return response


@app.route('/api/quit', methods=['POST'])
def quit_app():
    """Stop the server and hand the audio device back flat.

    Launched from the taskbar there is no console to close and no tray icon,
    so without this the process ran forever with the last curve still applied
    to every sound on the machine, and the only way out was Task Manager,
    which is a hard kill and therefore leaves the EQ applied.

    shutdown() writes flat synchronously before the process ends, so the exit
    is clean in the sense that matters: the listener's audio is unprocessed
    again the moment this returns.
    """
    def stop():
        # Let the response reach the browser first, so the page can say what
        # happened rather than dying mid-request.
        time.sleep(0.4)
        shutdown("dashboard quit")
        # Werkzeug removed the in-request shutdown hook in 2.1, and the flat
        # write has already happened, so exiting outright is both sufficient
        # and the only option that does not depend on the server internals.
        os._exit(0)

    threading.Thread(target=stop, daemon=True).start()
    return jsonify({"success": True,
                    "message": "Sonic Vector is stopping. Your EQ has been reset to flat."})


@app.route('/api/apo/status', methods=['GET'])
def get_apo_status():
    """Re-probe whether Equalizer APO is on the output the user is using."""
    status = apo.probe_apo_status()
    with state_lock:
        app_state["apo_status"] = status
    return jsonify(status)


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
        
    commit_state()
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
    if _is_placeholder(track):
        return jsonify({"success": False,
                        "message": "Nothing loaded to remix. Search a track first."}), 400

    combined_tags = tags + genres
    
    # 1. Similarity weights calculation with 35% semantic divergence!
    divergence = 0.35
    weights = predictor.calculate_similarity_weights(combined_tags, divergence=divergence)
    interpolated = predictor.synthesize_eq_curve(weights)

    # 2. Add target sound style offsets
    eq_curve = blend_curve(interpolated, sound_style)

    mixing_reason = describe_mix(weights, sound_style, verb="Remixed")


    dyn_mix = dynamic_overlays(weights)
    
    # Apply and save to state
    with state_lock:
        app_state["mode"] = "auto"  # Force auto mode so that overlays are active and it syncs nicely!
        app_state["current_track"]["weights"] = weights
        app_state["current_track"]["mixing_reason"] = mixing_reason
        _set_eq_locked(eq_curve)
        app_state["mix"] = dyn_mix
            
    commit_state()
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
    with authenticating_lock:
        in_progress = is_authenticating
    return jsonify({
        "authenticated": auth_ok,
        "in_progress": in_progress,
        # Why the last attempt failed. The Connect button used to just
        # re-enable itself after eight seconds and say nothing at all.
        "last_error": getattr(spotify_oauth, "last_error", None) if not auth_ok else None,
    })


CONFIG_YAML = "config.yaml"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


def _mask_secret(value: str) -> str:
    """Show enough of a credential to recognise it, not enough to reuse it."""
    text = str(value or "")
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}{'*' * (len(text) - 8)}{text[-4:]}"


@app.route('/api/spotify/config', methods=['GET'])
def get_spotify_config():
    """What the dashboard needs to render the setup panel.

    The secret is never sent back, masked or otherwise: the panel only needs to
    know whether one is stored.
    """
    with state_lock:
        configured = app_state["spotify_configured"]
        redirect_uri = app_state["spotify_redirect_uri"]
    return jsonify({
        "configured": configured,
        "client_id": _mask_secret(os.environ.get("SPOTIFY_CLIENT_ID", "")) if configured else "",
        "redirect_uri": redirect_uri,
        "default_redirect_uri": DEFAULT_REDIRECT_URI,
        "dashboard_url": "https://developer.spotify.com/dashboard",
    })


@app.route('/api/spotify/config', methods=['POST'])
def set_spotify_config():
    """Store Spotify API credentials and bring the integration up live.

    Editing config.yaml by hand and restarting was the only way to turn Spotify
    on, which is a poor answer to "how do I enable this". The credentials are
    verified against Spotify's token endpoint before anything is written, so a
    typo is reported here rather than surfacing later as a silent no-op.
    """
    global spotify_client, spotify_oauth

    data = request.json or {}
    client_id = str(data.get("client_id", "")).strip()
    client_secret = str(data.get("client_secret", "")).strip()
    redirect_uri = str(data.get("redirect_uri", "")).strip() or DEFAULT_REDIRECT_URI

    if not client_id or not client_secret:
        return jsonify({"success": False,
                        "message": "Both the Client ID and the Client Secret are required."}), 400
    if is_placeholder(client_id) or is_placeholder(client_secret):
        return jsonify({"success": False,
                        "message": "Those are the example placeholder values, not real credentials."}), 400
    # Spotify's rules since 2025-04-09: HTTPS everywhere, except loopback, where
    # HTTP is allowed but only as an explicit IP literal. "localhost" is
    # specifically rejected by Spotify, so accepting it here would just move the
    # failure to the authorize call, where it reads as INVALID_CLIENT and is
    # much harder to diagnose.
    # https://developer.spotify.com/documentation/web-api/concepts/redirect_uri
    if redirect_uri.startswith(("http://localhost", "https://localhost")):
        return jsonify({"success": False,
                        "message": "Spotify does not accept 'localhost' in a redirect URI. "
                                   "Use http://127.0.0.1:8888/callback instead, and set the "
                                   "same value in your Spotify app's settings."}), 400
    if not redirect_uri.startswith(("http://127.0.0.1", "http://[::1]", "https://")):
        return jsonify({"success": False,
                        "message": "The redirect URI must be an explicit loopback address "
                                   "(http://127.0.0.1:PORT/... or http://[::1]:PORT/...) "
                                   "or an https:// URL."}), 400

    # Verify before persisting. Client credentials is the cheapest call that
    # proves the pair is real, and it needs no browser round trip.
    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        res = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False,
                        "message": f"Could not reach Spotify to verify the keys: {e}"}), 502

    if res.status_code != 200:
        detail = ""
        try:
            body = res.json()
            detail = str(body.get("error_description") or body.get("error") or "")
        except ValueError:
            pass
        message = "Spotify rejected that Client ID / Client Secret pair."
        if detail:
            message += f" (Spotify said: {detail})"
        return jsonify({"success": False, "message": message}), 400

    try:
        set_section_values(PROJECT_ROOT / CONFIG_YAML, "spotify", {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        })
    except Exception as e:
        logger.error(f"Could not write Spotify credentials to config.yaml: {e}")
        return jsonify({"success": False,
                        "message": f"Verified, but config.yaml could not be written: {e}"}), 500

    os.environ["SPOTIFY_CLIENT_ID"] = client_id
    os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret
    os.environ["SPOTIFY_REDIRECT_URI"] = redirect_uri

    try:
        spotify_client = SpotifyAPIClient(config_path=str(PROJECT_ROOT / CONFIG_YAML))
        spotify_oauth = SpotifyService()
    except Exception as e:
        logger.error(f"Spotify credentials saved but the client would not start: {e}")
        return jsonify({"success": False,
                        "message": f"Saved, but the Spotify client would not start: {e}"}), 500

    with state_lock:
        app_state["spotify_configured"] = True
        app_state["spotify_redirect_uri"] = redirect_uri
        if _is_placeholder(app_state["current_track"]):
            _set_idle_track_locked(spotify_idle_track())
        _refresh_pipeline_locked(auth_ok=False)

    logger.info("Spotify credentials accepted and verified. Connect the account to start syncing.")
    return jsonify({
        "success": True,
        "message": "Credentials verified and saved. Click CONNECT SPOTIFY to link your account.",
        "redirect_uri": redirect_uri,
    })




@app.route('/api/spotify/authenticate', methods=['POST'])
def trigger_spotify_authenticate():
    global spotify_oauth, is_authenticating

    # Without this the endpoint raised AttributeError on None inside a daemon
    # thread, so the browser saw a cheerful "Authorization pop-up triggered"
    # and nothing ever opened.
    if spotify_oauth is None:
        return jsonify({"success": False,
                        "message": "Spotify is not set up yet. Add your API keys first."}), 400

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
                # A track the listener searched for is theirs, not Spotify's,
                # so unlinking the account must not take it off the screen.
                if app_state["current_track"].get("source") != "search":
                    app_state["current_track"] = idle_track(
                        "Spotify Account Disconnected",
                        "Click CONNECT SPOTIFY above to authorize another account.",
                        "Tokens cleared. Playback sync is suspended; the search box "
                        "and every EQ control still work.",
                    )
                _refresh_pipeline_locked(auth_ok=False)
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
    
    # Enrich with Spotify if it is available. Two independent routes, because
    # neither is required: the OAuth token when an account is linked, and the
    # client-credentials token when credentials exist but nobody has logged in.
    # The catalogue search does not need a user, so requiring the login for it
    # was needlessly narrowing what worked.
    search_str = f"track:{track_query}"
    if artist_query:
        search_str += f" artist:{artist_query}"

    track_data = None
    headers = None
    if spotify_oauth and spotify_oauth.is_authenticated():
        try:
            headers = {"Authorization": f"Bearer {spotify_oauth.access_token}"}
            res = requests.get(
                "https://api.spotify.com/v1/search",
                headers=headers,
                params={"q": search_str, "type": "track", "limit": 1},
                timeout=10,
            )
            if res.status_code == 200:
                items = res.json().get("tracks", {}).get("items", [])
                track_data = items[0] if items else None
        except Exception as e:
            logger.warning(f"Spotify search (OAuth) failed: {e}")
            headers = None

    if track_data is None and spotify_client is not None:
        try:
            result = spotify_client.search_track(search_str) or {}
            items = result.get("tracks", {}).get("items", [])
            track_data = items[0] if items else None
            if track_data is not None:
                headers = spotify_client._get_auth_headers()
        except Exception as e:
            logger.warning(f"Spotify search (client credentials) failed: {e}")

    if track_data is not None:
        try:
            track_id = track_data["id"]
            track_name = track_data["name"]
            artist_name = track_data["artists"][0]["name"]
            album_name = track_data["album"]["name"]
            images = track_data["album"]["images"]
            if images:
                album_art = images[0]["url"]

            artist_id = track_data["artists"][0]["id"]
            res_artist = requests.get(
                f"https://api.spotify.com/v1/artists/{artist_id}",
                headers=headers, timeout=10)
            if res_artist.status_code == 200:
                genres = res_artist.json().get("genres", [])
        except Exception as e:
            logger.warning(f"Could not read Spotify search result: {e}")

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
    fallback_note = ""
    dyn_mix = {
        "preamp_gain": 0.0,
        "strength": 1.0,
        "bass_boost": 0.0,
        "vocal_clarity": 0.0,
        "airiness": 0.0
    }

    if recalled:
        logger.info(f"Search Recalled: OK: Recalled custom EQ profile from database for search track {track_id}!")
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
                eq_curve.update(LLM_BAND_FREQS)
                mixing_reason = ai_mix["mixing_reason"]
            except Exception as e:
                logger.warning(f"AI Mixing Assistant failed on search, falling back to similarity: {e}")
                fallback_note = llm_fallback_note(e)
                current_engine = "similarity"

        if current_engine == "similarity":
            weights = predictor.calculate_similarity_weights(combined_tags)
            interpolated = predictor.synthesize_eq_curve(weights)

            # Blend similarity centroid curve with target sound style offset
            eq_curve = blend_curve(interpolated, sound_style)

            mixing_reason = fallback_note + describe_mix(weights, sound_style)

            dyn_mix = dynamic_overlays(weights)

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
            # "search" is what keeps the monitor thread from replacing this with
            # a Spotify status message on its next poll, 1.5 s from now.
            "source": "search",
            "placeholder": False,
            "is_playing": False,
            "is_private_session": False,
            "mixing_reason": mixing_reason
        }
        _set_eq_locked(eq_curve)
        app_state["mix"] = dyn_mix
        _refresh_pipeline_locked(auth_ok=app_state["spotify_authenticated"])

    commit_state()
    with state_lock:
        return jsonify({"success": True, "state": app_state})


# --- Server Init ---

def _select_now_playing_source(config_yaml: str) -> None:
    """Choose where "what is playing" comes from, and start it.

    Order of preference, unless config.yaml pins one with
    `now_playing.source: windows | spotify | none`:

      1. **Windows media session.** Costs nothing, needs no account, and sees
         every player on the machine rather than one streaming service. This is
         the default because the app was meant to work without any API and,
         until now, automatic detection was the one thing that did not.
      2. **Spotify OAuth**, if credentials exist and Windows is unavailable.
      3. Nothing, in which case the search box is the way in and everything
         else still works.
    """
    global now_playing, smtc_source

    preference = "auto"
    try:
        cfg = yaml.safe_load(Path(config_yaml).read_text(encoding="utf-8")) or {}
        preference = str((cfg.get("now_playing") or {}).get("source", "auto")).lower()
    except Exception:
        pass
    if preference not in ("auto", "windows", "spotify", "none"):
        logger.warning(f"Unknown now_playing.source '{preference}'; using auto.")
        preference = "auto"

    if preference == "none":
        logger.info("Now-playing detection disabled by config. Use the search box.")
        _publish_source("none")
        return

    if preference in ("auto", "windows"):
        if SmtcNowPlaying.is_available():
            source = SmtcNowPlaying()
            if source.start():
                smtc_source = source
                now_playing = source
                logger.info("Now-playing source: Windows media session. "
                            "No account needed; it sees every player on this PC.")
                _publish_source("windows")
                return
            logger.warning(f"Windows media session did not start: {source.last_error}")
        else:
            logger.info(f"Windows media session unavailable "
                        f"({SmtcNowPlaying.unavailable_reason()}).")
        if preference == "windows":
            _publish_source("none")
            return

    if spotify_oauth is not None:
        now_playing = spotify_oauth
        logger.info("Now-playing source: Spotify (connect your account to use it).")
        _publish_source("spotify")
        return

    logger.info("No now-playing source available. The search box still works, "
                "as does every EQ control.")
    _publish_source("none")


def _publish_source(name: str) -> None:
    with state_lock:
        app_state["now_playing_source"] = name


def init_services():
    global predictor, lastfm, spotify_client, spotify_oauth, apo_path, llm_client
    global main_config, preferences, now_playing, smtc_source

    config_yaml = "config.yaml"
    
    # Config and LLM Assistant Boot
    try:
        main_config = Config()
        llm_client = LLMClient(main_config)
    except Exception as e:
        logger.error(f"Failed to boot LLM Client: {e}")

    with state_lock:
        app_state["llm_available"] = llm_available()

    if app_state["llm_available"]:
        logger.info(f"Optional AI mixing engine available (provider: {main_config.llm_provider}).")
    else:
        logger.info("Optional AI mixing engine not configured. Keyword profile "
                    "matching is the engine; it needs no API key and no network.")

    predictor = SemanticEQPredictor(db_path="data/test_library.db")
    lastfm = LastFMClient(config_path=config_yaml)
    
    try:
        spotify_client = SpotifyAPIClient(config_path=config_yaml)
    except SpotifyNotConfigured as e:
        # Expected on any install that has not opted in to Spotify.
        logger.info(f"Spotify: {e}")
    except Exception as e:
        logger.warning(f"Spotify client credentials flow bypassed: {e}")
        
    # Spotify OAuth is only worth starting if there are credentials for it to
    # use. Reporting "successfully linked" on an install with no client_id was
    # false, and it made a deliberate opt-out look like a working connection.
    with state_lock:
        app_state["spotify_configured"] = spotify_client is not None
        app_state["spotify_redirect_uri"] = (
            os.environ.get("SPOTIFY_REDIRECT_URI") or DEFAULT_REDIRECT_URI)
        app_state["current_track"] = spotify_idle_track()

    if spotify_client is not None:
        try:
            spotify_oauth = SpotifyService()
            logger.info("Spotify OAuth service ready. Connect your account in the dashboard.")
        except Exception as e:
            logger.warning(f"Spotify OAuth service unavailable: {e}")

    _select_now_playing_source(config_yaml)

    apo_path = load_apo_path_from_config(config_yaml)

    try:
        preferences = PreferenceStore("data/preferences.db")
        logger.info("Preference store ready (%d votes recorded so far).",
                    preferences.summary()["total"])
    except Exception as e:
        logger.error(f"Preference store unavailable: {e}")

    # Refuse to run on a broken measuring instrument. Every headroom guarantee
    # downstream is computed with it, so a silent failure here would mean
    # silently clipping the user's audio.
    report, ok = render.run_selftests()
    if not ok:
        logger.error("DSP renderer self-test FAILED. Refusing to start.\n" + report)
        raise SystemExit(1)
    logger.info("DSP renderer self-test: ALL PASS")

    status = apo.probe_apo_status()
    with state_lock:
        app_state["apo_status"] = status
        # The monitor loop only refreshes pipeline_status once Spotify is
        # connected, and it short-circuits before that. Seed the output line
        # here so a user who has not connected anything still sees the truth
        # about whether the EQ can reach their speakers.
        if status["state"] == "apo_not_installed":
            app_state["pipeline_status"]["apo_writer"] = "Equalizer APO is not installed"
        elif status["state"] == "apo_no_active_endpoint":
            app_state["pipeline_status"]["apo_writer"] = "Not applied: APO is not enabled on your current output"
        elif status["state"] == "apo_ready":
            app_state["pipeline_status"]["apo_writer"] = "EQ active on your output"

    if status["state"] == "apo_ready":
        logger.info(f"Equalizer APO: {status['detail']}")
    else:
        logger.warning(f"Equalizer APO: {status['detail']}")


_shutdown_done = threading.Event()


def shutdown(reason: str = "exit"):
    """Stop the monitor and hand the audio device back untouched.

    Idempotent: it runs from atexit, from signal handlers, and from the Windows
    console control handler, and any of them may fire together.
    """
    global active_monitoring
    if _shutdown_done.is_set():
        return
    _shutdown_done.set()
    active_monitoring = False
    write_flat_config(reason)


def _install_exit_handlers():
    """Make flat-on-exit hold for every way this process can die.

    The old code reset nothing, and its only cleanup hook was the `finally` of
    app.run(), which does not run when the launcher's console window is closed.
    A curve written for one song therefore stayed applied to every sound on the
    machine until the app was next started.
    """
    atexit.register(lambda: shutdown("atexit"))

    def _sig(signum, _frame):
        shutdown(f"signal {signum}")
        raise SystemExit(0)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _sig)
            except (ValueError, OSError):
                pass   # not on the main thread, or unsupported here

    # Closing the console window sends CTRL_CLOSE_EVENT, which is not a POSIX
    # signal and is not delivered to Python's signal handlers.
    try:
        import win32api
        import win32con

        def _console_handler(event):
            if event in (win32con.CTRL_CLOSE_EVENT,
                         win32con.CTRL_LOGOFF_EVENT,
                         win32con.CTRL_SHUTDOWN_EVENT):
                shutdown(f"console event {event}")
                return True
            return False

        win32api.SetConsoleCtrlHandler(_console_handler, True)
    except Exception as e:
        logger.warning(
            f"Console close handler unavailable ({e}). Closing the window "
            "with the X button may leave the EQ applied."
        )


def main():
    port = int(os.environ.get("SONICVECTOR_PORT", "5001"))

    # Refuse to become a second instance, before touching anything.
    #
    # Two copies share one Equalizer APO config file, so the loser of the race
    # still does damage on its way down: init_services() and the startup
    # commit_state() would write flat over the running instance's curve, then
    # Flask would fail to bind, then the exit handler would write flat again.
    # The listener's EQ silently went flat and the window that caused it had
    # already closed. Checking the port first makes a double-launch a no-op.
    if desktop.server_is_running(port=port):
        logger.warning(
            "Sonic Vector is already running on port %d. Leaving the running "
            "instance alone; open http://127.0.0.1:%d to reach it.", port, port)
        return

    # Claim a Windows application identity before any window exists, so the
    # taskbar groups this under Sonic Vector rather than under the interpreter.
    desktop.set_app_user_model_id()

    init_services()
    _install_exit_handlers()

    # Start from a known state rather than inheriting whatever was left behind.
    # commit_state() rather than write_flat_config() because it also publishes
    # output.response, so the scope draws a real (flat) trace immediately
    # instead of staying blank until the first track change.
    commit_state()

    monitor_thread = threading.Thread(target=monitor_spotify_playback)
    monitor_thread.daemon = True
    monitor_thread.start()

    # Set SONICVECTOR_NO_BROWSER=1 to stop the app hijacking the default
    # browser, which matters when it runs headless, under a tray host, or
    # inside a preview pane.
    if os.environ.get("SONICVECTOR_NO_BROWSER", "").strip() not in ("1", "true", "yes"):
        def open_browser():
            # Wait for the port rather than sleeping a fixed 1.2 s. On a cold
            # start that guess opened the browser before Flask was listening,
            # and an error page is read as "the app is broken".
            for _ in range(80):
                if desktop.server_is_running(port=port):
                    break
                time.sleep(0.25)
            how = desktop.open_app_window(f"http://127.0.0.1:{port}")
            logger.info(f"Dashboard opened as {how}.")

        browser_thread = threading.Thread(target=open_browser)
        browser_thread.daemon = True
        browser_thread.start()
    logger.info(f"Sonic Vector dashboard running on: http://127.0.0.1:{port}")
    try:
        app.run(host="127.0.0.1", port=port, debug=False)
    finally:
        shutdown("server stopped")


if __name__ == '__main__':
    main()
