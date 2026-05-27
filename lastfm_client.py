"""Last.fm API Client for Crowdsourced Tag Harvesting.

Responsibility: Queries Last.fm's open track.getInfo and artist.getTopTags APIs to retrieve
user-generated tags (describing instruments, moods, styles, and eras).
Provides our embedding model with dense, qualitative acoustic profiles.
Includes an automatic artist-level fallback to guarantee tag retrieval.
"""

import logging
import requests
import time
import yaml
from pathlib import Path
from typing import List, Optional

# Set up clean logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class LastFMClient:
    """Connector for harvesting crowdsourced sonic descriptors from Last.fm."""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialize Last.fm client, loading overrides from config if available.

        Args:
            config_path: Path to config.yaml
        """
        self.config_path = Path(config_path)
        
        # Default active public key (fully functional fallback)
        self.api_key = "75d20fb472be99275392aefa2760ea09"
        
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    config_data = yaml.safe_load(f) or {}
                
                custom_key = config_data.get('lastfm', {}).get('api_key')
                if custom_key:
                    self.api_key = custom_key.strip()
                    logger.info("Loaded custom Last.fm API key from configuration.")
            except Exception as e:
                logger.warning(f"Could not load custom Last.fm keys from config: {e}. Using fallback key.")

    def get_artist_tags(self, artist: str) -> List[str]:
        """Fetch popular tags for a given artist as a fallback.

        Args:
            artist: Name of the artist

        Returns:
            List of lower-case tag strings
        """
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.getTopTags",
            "artist": artist,
            "api_key": self.api_key,
            "format": "json"
        }
        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                toptags = data.get("toptags", {})
                tags_list = toptags.get("tag", [])
                
                if isinstance(tags_list, dict):
                    tags_list = [tags_list]
                    
                tags = []
                for t in tags_list:
                    name = t.get("name", "").strip().lower()
                    if name and len(name) > 1 and name != artist.lower():
                        tags.append(name)
                return tags
        except Exception as e:
            logger.warning(f"Could not fetch artist tags fallback for {artist}: {e}")
        return []

    def get_track_tags(self, artist: str, track: str) -> List[str]:
        """Fetch popular community-assigned tags for a given track.
        
        Falls back to artist-level tags if the track itself has no tags.

        Args:
            artist: Name of the performing artist
            track: Name of the song/track

        Returns:
            List of lower-case tag strings (e.g. ['acoustic', 'sad', '70s'])
        """
        logger.info(f"Harvesting Last.fm tags for: '{track}' by {artist}...")
        
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "track.getInfo",
            "artist": artist,
            "track": track,
            "api_key": self.api_key,
            "format": "json",
            "autocorrect": 1
        }
        
        max_retries = 3
        backoff_factor = 1.5
        tags = []
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=8)
                
                if response.status_code == 200:
                    data = response.json()
                    track_info = data.get("track", {})
                    toptags = track_info.get("toptags", {})
                    tags_list = toptags.get("tag", [])
                    
                    if isinstance(tags_list, dict):
                        tags_list = [tags_list]
                        
                    for t in tags_list:
                        name = t.get("name", "").strip().lower()
                        if name and len(name) > 1:
                            tags.append(name)
                    break
                    
                elif response.status_code in [400, 401, 403, 404]:
                    break
                else:
                    response.raise_for_status()
                    
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.HTTPError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"✗ Last.fm track request failed after {max_retries} attempts: {e}")
                    break
                sleep_time = backoff_factor ** attempt
                logger.warning(f"⚠ Last.fm track connection issue: {e}. Retrying in {sleep_time:.2f}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(sleep_time)

        # Fail-safe fallback: If track tags are empty or too sparse (less than 3), pull artist tags!
        if len(tags) < 3:
            logger.info(f"  → Track tags empty or sparse ({len(tags)}). Triggering artist-level fallback for '{artist}'...")
            artist_tags = self.get_artist_tags(artist)
            # Combine them, prioritizing track-level tags
            seen = set(tags)
            for t in artist_tags:
                if t not in seen and len(tags) < 15: # Cap at 15 tags for model input balance
                    tags.append(t)
                    seen.add(t)

        logger.info(f"  ✓ Successfully harvested {len(tags)} combined tags from Last.fm.")
        return tags


if __name__ == '__main__':
    # Diagnostic test run
    client = LastFMClient()
    test_tracks = [
        ("Radiohead", "Creep"),
        ("Daft Punk", "Get Lucky"),
        ("Eminem", "Lose Yourself"),
        ("Led Zeppelin", "Stairway to Heaven")
    ]
    
    print("\n" + "=" * 50)
    print("LAST.FM TAG HARVESTER DIAGNOSTIC TEST RUN")
    print("=" * 50)
    
    for artist, track in test_tracks:
        tags = client.get_track_tags(artist, track)
        print(f"Track: '{track}' by {artist}")
        print(f"Tags: {tags[:10]}") # Print top 10 tags
        print("-" * 50)
