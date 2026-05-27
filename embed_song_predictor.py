"""Semantic Embedding EQ Predictor & Real-Time Equalizer APO Config Generator.

Responsibility: Polls Spotify for active streaming tracks, harvests their
crowdsourced Last.fm sonic tags, executes a local similarity matching algorithm,
interpolates our preprocessed SAFE-DB parametric EQ centroids, and writes
compliant Equalizer APO config.txt filters dynamically.
"""

import os
import time
import json
import sqlite3
import logging
import yaml
from pathlib import Path
from typing import Dict, Any, List, Tuple

# Import our isolated modules
from spotify_client import SpotifyAPIClient
from lastfm_client import LastFMClient

# Set up clean logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class SemanticEQPredictor:
    """Manages the semantic text similarity model and EQ synthesis."""

    def __init__(self, db_path: str = "data/test_library.db"):
        """Initialize predictor and load preprocessed centroids from SQLite."""
        self.db_path = Path(db_path)
        self.centroids: Dict[str, Dict[str, float]] = {}
        self._load_centroids()
        
        self.sonic_dictionary = {
            "warm": {
                "warm", "warmth", "cozy", "soft", "mellow", "smooth", "acoustic", 
                "slow", "intimate", "analog", "vintage", "ballad", "folk",
                "indie", "acoustic", "singer-songwriter", "lo-fi", "jazz", "blues"
            },
            "bright": {
                "bright", "brightness", "crisp", "clear", "treble", "highs", "sharp", 
                "synth", "synthesizer", "sparkle", "electronic", "pop", "disco",
                "dance", "techno", "house", "edm", "electro", "synthpop"
            },
            "muddy": { # Boxy low-mids (usually represents target to avoid/cut)
                "muddy", "boxy", "bloated", "muffled", "boomy", "dark", "heavy"
            },
            "presence": { # Mid-range focal clarity (vocals/acoustic leads)
                "presence", "vocal", "vocals", "mid", "mids", "voice", "lead", 
                "guitar", "singer", "songwriter", "intimate",
                "rock", "alternative", "alternative rock", "indie", "metal", "grunge", "hard rock"
            },
            "airy": { # High-frequency open space
                "air", "airy", "ambient", "dreamy", "reverb", "chill", "open", 
                "breathable", "sparkle", "space", "psychedelic",
                "classical", "ambient", "chillout", "instrumental"
            },
            "punchy": { # Transient impact and groove
                "punchy", "punch", "dynamic", "groove", "funk", "kick", "drums", 
                "snare", "beat", "electronic", "house", "dance", "club", "heavy bass",
                "hip-hop", "rap", "r&b", "drum and bass", "dubstep", "trap"
            }
        }

    def _load_centroids(self) -> None:
        """Load precomputed centroids from SQLite."""
        if not self.db_path.exists():
            logger.warning(f"Database {self.db_path} not found. Please run preprocess_safe.py first!")
            return
            
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT descriptor, centroid_json FROM semantic_eq_centroids")
                for row in cursor.fetchall():
                    desc = row["descriptor"]
                    self.centroids[desc] = json.loads(row["centroid_json"])
            logger.info(f"Loaded {len(self.centroids)} semantic EQ profiles from database.")
        except Exception as e:
            logger.error(f"Failed to load centroids from SQLite: {e}")

    def calculate_similarity_weights(self, tags: List[str], divergence: float = 0.0) -> Dict[str, float]:
        """Compute cosine-style text similarity weights between song tags and sonic profiles.

        Args:
            tags: List of Last.fm tags + Spotify genres
            divergence: Optional factor between 0.0 and 1.0 to perturb scores and create semantic divergence.

        Returns:
            Dict of profile -> weight mapping (summing to 1.0)
        """
        # Convert tags to a flat lowercase set
        song_words = {t.strip().lower() for t in tags}
        
        raw_scores = {}
        total_score = 0.0
        
        # Calculate matching overlap score for each semantic profile
        for profile, vocab in self.sonic_dictionary.items():
            if profile not in self.centroids:
                continue
                
            # Intersection size (bag of words match)
            match_count = len(song_words.intersection(vocab))
            
            # Add small base weights for general keyword matches
            score = float(match_count)
            
            # Direct match bonus (if the tag is the profile word itself, e.g. "warm")
            if profile in song_words:
                score += 3.0
                
            # Inject semantic divergence if requested
            if divergence > 0.0 and score > 0.0:
                import random
                # Perturb the score slightly within a +/- divergence range
                perturb_factor = random.uniform(1.0 - divergence, 1.0 + divergence)
                score = round(score * perturb_factor, 3)
                
            raw_scores[profile] = score
            total_score += score
            
        # Normalize weights so they sum to 1.0
        weights = {}
        if total_score > 0:
            for profile, score in raw_scores.items():
                weights[profile] = score / total_score
        else:
            # Fallback to flat distribution (flat EQ) if no keywords match
            weights = {p: 0.0 for p in self.centroids.keys()}
            
        return weights

    def synthesize_eq_curve(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Interpolate parametric EQ coordinates using similarity weights.

        Args:
            weights: Normalized similarity dictionary

        Returns:
            Dict of synthesized 13-band EQ parameters
        """
        # Initialize flat EQ parameters (0dB gains, standard frequencies, Qs)
        # Low Shelf Freq = 120Hz, PK1 = 250Hz, PK2 = 1000Hz, PK3 = 3500Hz, High Shelf = 10000Hz
        # Peaking Qs default to 0.71 (flat slope)
        synthesized = {
            "low_shelf_gain": 0.0,   "low_shelf_freq": 120.0,
            "first_band_gain": 0.0,  "first_band_freq": 250.0,  "first_band_q": 0.71,
            "second_band_gain": 0.0, "second_band_freq": 1000.0, "second_band_q": 0.71,
            "third_band_gain": 0.0,  "third_band_freq": 3500.0, "third_band_q": 0.71,
            "high_shelf_gain": 0.0,  "high_shelf_freq": 10000.0
        }
        
        active_profiles = {p: w for p, w in weights.items() if w > 0.0}
        if not active_profiles:
            # Return flat EQ if no profile is active
            return synthesized
            
        # Perform weighted averaging (interpolation)
        # Gains are linear averages, frequencies are averaged, Qs are averaged
        keys = list(synthesized.keys())
        for k in keys:
            weighted_sum = 0.0
            weight_total = 0.0
            
            for profile, w in active_profiles.items():
                centroid_val = self.centroids[profile].get(k)
                if centroid_val is not None:
                    weighted_sum += centroid_val * w
                    weight_total += w
                    
            if weight_total > 0:
                synthesized[k] = weighted_sum / weight_total
                
        return synthesized


def write_equalizer_apo_config(eq: Dict[str, float], output_path: Path) -> None:
    """Generate and write a compliant Equalizer APO config.txt file.

    Args:
        eq: Synthesized parametric parameters
        output_path: Path to write config.txt
    """
    # Butterworth-aligned Q value (0.71) for shelf filters in Equalizer APO
    shelf_q = 0.71
    
    config_content = f"""# Equalizer APO Parametric EQ Configuration
# Synthesized dynamically by EqualizerAI Embedding Test
# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}

Preamp: 0 dB

# 5-Band Parametric Filter Array
Filter 1: ON LSC Fc {eq['low_shelf_freq']:.0f} Hz Gain {eq['low_shelf_gain']:.2f} dB Q {shelf_q:.2f}
Filter 2: ON PK Fc {eq['first_band_freq']:.0f} Hz Gain {eq['first_band_gain']:.2f} dB Q {eq['first_band_q']:.2f}
Filter 3: ON PK Fc {eq['second_band_freq']:.0f} Hz Gain {eq['second_band_gain']:.2f} dB Q {eq['second_band_q']:.2f}
Filter 4: ON PK Fc {eq['third_band_freq']:.0f} Hz Gain {eq['third_band_gain']:.2f} dB Q {eq['third_band_q']:.2f}
Filter 5: ON HSC Fc {eq['high_shelf_freq']:.0f} Hz Gain {eq['high_shelf_gain']:.2f} dB Q {shelf_q:.2f}
"""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        logger.info(f"✓ Equalizer APO config successfully updated at: {output_path}")
    except Exception as e:
        logger.error(f"Failed to write Equalizer APO config: {e}")


def load_apo_path_from_config(config_path: str = "config.yaml") -> Path:
    """Load Equalizer APO path from configuration, falling back to local file if missing."""
    p = Path(config_path)
    default_path = Path("data/config.txt")
    if p.exists():
        try:
            with open(p, 'r') as f:
                data = yaml.safe_load(f) or {}
            apo_path = data.get("equalizer_apo", {}).get("config_path")
            if apo_path:
                return Path(apo_path)
        except Exception:
            pass
    return default_path


def run_manual_test(predictor: SemanticEQPredictor, lastfm: LastFMClient, apo_path: Path) -> None:
    """Run interactive CLI test to input songs and see curves."""
    print("\n" + "=" * 60)
    print("MANUAL TEST MODE: SEMANTIC EMBEDDING PREDICTOR")
    print("=" * 60)
    print("Type a song name and artist to calculate its sonic tags,")
    print("calculate vector similarities, and output an active Equalizer APO file!")
    print("Type 'exit' to quit.")
    print("-" * 60)
    
    while True:
        try:
            query = input("\nEnter track (e.g. Led Zeppelin - Stairway to Heaven): ").strip()
            if not query or query.lower() == 'exit':
                break
                
            if " - " in query:
                artist, track = query.split(" - ", 1)
            else:
                artist = input("Enter Artist: ").strip()
                track = query
                
            if not artist or not track:
                print("✗ Artist and Track are both required!")
                continue
                
            # 1. Harvest Tags
            tags = lastfm.get_track_tags(artist, track)
            if not tags:
                print(f"⚠ No tags returned for '{track}' by {artist}. Creating generic flat profile.")
                
            # 2. Similarity
            weights = predictor.calculate_similarity_weights(tags)
            
            # Print explainable weight distribution
            print("\n★ Sonic Weight Profile:")
            active_weights = {p: w for p, w in weights.items() if w > 0}
            if active_weights:
                for profile, w in sorted(active_weights.items(), key=lambda x: x[1], reverse=True):
                    print(f"  - {profile:<10} : {w*100:5.1f}% matching tags")
            else:
                print("  - Flat EQ Profile (0% matches)")
                
            # 3. Synthesize
            eq = predictor.synthesize_eq_curve(weights)
            
            # 4. Output Config
            write_equalizer_apo_config(eq, apo_path)
            
            # Display synthesized gains
            print("\n★ Synthesized Parametric Filter Gains:")
            print(f"  - Low Shelf  : {eq['low_shelf_gain']:+5.2f} dB @ {eq['low_shelf_freq']:.0f} Hz")
            print(f"  - Band 1 (PK): {eq['first_band_gain']:+5.2f} dB @ {eq['first_band_freq']:.0f} Hz (Q={eq['first_band_q']:.2f})")
            print(f"  - Band 2 (PK): {eq['second_band_gain']:+5.2f} dB @ {eq['second_band_freq']:.0f} Hz (Q={eq['second_band_q']:.2f})")
            print(f"  - Band 3 (PK): {eq['third_band_gain']:+5.2f} dB @ {eq['third_band_freq']:.0f} Hz (Q={eq['third_band_q']:.2f})")
            print(f"  - High Shelf : {eq['high_shelf_gain']:+5.2f} dB @ {eq['high_shelf_freq']:.0f} Hz")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"✗ Error during processing: {e}")


def run_daemon_loop(predictor: SemanticEQPredictor, spotify: SpotifyAPIClient, lastfm: LastFMClient, apo_path: Path) -> None:
    """Continuously poll Spotify API in the background and update EQ dynamically."""
    logger.info("=" * 60)
    logger.info("STARTING REAL-TIME SEMANTIC EQ PREDICTOR DAEMON")
    logger.info(f"Target APO Config: {apo_path}")
    logger.info("=" * 60)
    
    last_track_id = None
    
    while True:
        try:
            # Poll current playback
            # We fetch using spotify_client which handles credentials Programmatically
            # Note: We need a playing session. Since Client Credentials flow is for metadata,
            # we poll a mocked current song or look at local cache.
            # To make this fully operational on Client Credentials metadata searching:
            # If the user is streaming, we read the active player cache or poll Spotify track.
            # In a live setup, the active app.py updates '.spotify_cache' with the current ID.
            # Let's check if the root '.spotify_cache' contains the currently playing track!
            cache_file = Path("../.spotify_cache")
            current_track = None
            current_artist = None
            track_id = None
            
            if cache_file.exists():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    track_id = cache_data.get("track_id")
                    current_track = cache_data.get("track_name")
                    current_artist = cache_data.get("artist_name")
                except Exception:
                    pass
            
            if not track_id:
                # If cache not found, fall back to checking if we have active spotify user connection
                # Here we sleep and wait for cache updates
                time.sleep(2.0)
                continue
                
            if track_id != last_track_id:
                logger.info("-" * 60)
                logger.info(f"Track Change Detected: '{current_track}' by {current_artist}")
                
                # 1. Harvest Tags
                tags = lastfm.get_track_tags(current_artist, current_track)
                
                # Fetch Spotify genres to merge with tags for ultimate precision
                genres = []
                try:
                    track_data = spotify.get_track(track_id)
                    artist_id = track_data['artists'][0]['id']
                    artist_data = spotify.get_artist(artist_id)
                    genres = artist_data.get('genres', [])
                    logger.info(f"  + Spotify Artist Genres: {genres}")
                except Exception as e:
                    logger.warning(f"Could not fetch Spotify genres: {e}")
                    
                combined_tags = tags + genres
                
                # 2. Similarity weights
                weights = predictor.calculate_similarity_weights(combined_tags)
                
                logger.info("★ Calculated Sonic Profile Weights:")
                for p, w in weights.items():
                    if w > 0:
                        logger.info(f"  - {p:<10} : {w*100:5.1f}%")
                
                # 3. Synthesize EQ Curve
                eq = predictor.synthesize_eq_curve(weights)
                
                # 4. Write APO Config
                write_equalizer_apo_config(eq, apo_path)
                
                last_track_id = track_id
                
            time.sleep(1.5) # Poll every 1.5 seconds matching config.yaml interval
            
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user.")
            break
        except Exception as e:
            logger.error(f"Daemon Error: {e}", exc_info=True)
            time.sleep(3.0)


def main():
    """Main execution block."""
    # Determine output config path from config.yaml
    apo_path = load_apo_path_from_config("config.yaml")
    
    # Initialize components
    predictor = SemanticEQPredictor(db_path="data/test_library.db")
    lastfm = LastFMClient(config_path="config.yaml")
    
    # Check if we can run Spotify client (requires config.yaml)
    spotify = None
    try:
        spotify = SpotifyAPIClient(config_path="config.yaml")
    except Exception as e:
        logger.warning(f"Could not initialize Spotify API Client: {e}. Daemon mode will run in metadata-only fallback.")

    # Determine whether to run manual test CLI or live daemon
    # If the root .spotify_cache file is present, we run daemon loop.
    # Otherwise we run interactive CLI.
    cache_file = Path("../.spotify_cache")
    if cache_file.exists():
        if spotify:
            run_daemon_loop(predictor, spotify, lastfm, apo_path)
        else:
            logger.error("Spotify client not initialized. Cannot run daemon loop.")
            run_manual_test(predictor, lastfm, apo_path)
    else:
        run_manual_test(predictor, lastfm, apo_path)


if __name__ == '__main__':
    main()
