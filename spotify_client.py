"""Spotify API Client for Isolated Embedding Test.

Responsibility: Handles programmatic authentication via Client Credentials flow
and provides direct access to Spotify's song metadata and audio features.
"""

import base64
import logging
import requests
import time
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

# Set up clean logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class SpotifyNotConfigured(Exception):
    """Raised when no usable Spotify credentials are present.

    This is an expected state, not an error: the app is designed to run without
    Spotify. Callers should catch it and carry on with reduced functionality.
    """


def _is_configured(value: Optional[str]) -> bool:
    """True when a credential is present and is not the example placeholder."""
    text = str(value or "").strip()
    return bool(text) and not text.startswith("your_")


class SpotifyAPIClient:
    """Programmatic Spotify Web API client."""

    def __init__(self, config_path: str = "config.yaml"):
        """Initialize Spotify client by loading keys from configuration.

        Args:
            config_path: Path to config.yaml
        """
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                f"Please ensure config.yaml is in the directory."
            )

        # Load keys from yaml
        with open(self.config_path, 'r') as f:
            config_data = yaml.safe_load(f)

        self.client_id = config_data.get('spotify', {}).get('client_id')
        self.client_secret = config_data.get('spotify', {}).get('client_secret')

        # Spotify is optional. An unconfigured install should say so once and
        # move on, not fire a doomed token request and log a 400 as an ERROR,
        # which reads like a fault rather than a choice the user made.
        if not _is_configured(self.client_id) or not _is_configured(self.client_secret):
            raise SpotifyNotConfigured(
                "no credentials in config.yaml. Genre enrichment is off. "
                "Now-playing detection is unaffected: that comes from the "
                "Windows media session and needs no account."
            )

        self.access_token: Optional[str] = None
        self.token_expires_in: int = 0
        
        # Authenticate immediately
        self.authenticate()

    def authenticate(self) -> bool:
        """Authenticate with Spotify using the Client Credentials Flow.

        This flow is programmatic and requires no browser redirects or user sign-in.
        Useful for metadata harvesting and search.

        Returns:
            True if authentication succeeded, False otherwise
        """
        logger.info("Requesting Spotify access token...")
        auth_url = "https://accounts.spotify.com/api/token"
        
        # Base64 encode credentials
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_creds = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

        headers = {
            "Authorization": f"Basic {encoded_creds}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "client_credentials"
        }

        try:
            response = requests.post(auth_url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            token_info = response.json()
            
            self.access_token = token_info.get("access_token")
            self.token_expires_in = token_info.get("expires_in", 3600)
            
            logger.info("OK: Spotify authentication successful!")
            return True
        except Exception as e:
            logger.error(f"FAILED: Failed to authenticate with Spotify: {e}")
            return False

    def _get_auth_headers(self) -> Dict[str, str]:
        """Generate Authorization headers.

        Returns:
            Dict containing authorization bearer token
        """
        if not self.access_token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def _make_api_request(self, endpoint: str) -> Dict[str, Any]:
        """General helper to perform Spotify GET requests with retry logic.

        Args:
            endpoint: API endpoint path (e.g. 'tracks/3n3Ppam...')

        Returns:
            JSON response dictionary
        """
        url = f"https://api.spotify.com/v1/{endpoint}"
        
        max_retries = 3
        backoff_factor = 1.5
        
        for attempt in range(max_retries):
            try:
                headers = self._get_auth_headers()
                response = requests.get(url, headers=headers, timeout=10)
                
                # Raise HTTP errors with specific response information if possible
                if response.status_code != 200:
                    logger.warning(f"API Warning: {response.status_code} returned for endpoint: {endpoint}")
                    # If rate limited, tell the developer
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 2))
                        logger.error(f"Rate limited by Spotify! Retry after: {retry_after} seconds.")
                        time.sleep(retry_after)
                        continue
                    
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else 500
                if status_code in [400, 401, 403, 404]:
                    logger.error(f"FAILED: Permanent HTTP error: {status_code} - {e}")
                    raise
                if attempt == max_retries - 1:
                    logger.error(f"FAILED: Request failed after {max_retries} attempts: {e}")
                    raise
                sleep_time = backoff_factor ** attempt
                logger.warning(f"WARNING: HTTP error {status_code}: {e}. Retrying in {sleep_time:.2f}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(sleep_time)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == max_retries - 1:
                    logger.error(f"FAILED: Request failed after {max_retries} attempts: {e}")
                    raise
                sleep_time = backoff_factor ** attempt
                logger.warning(f"WARNING: Connection error or Timeout: {e}. Retrying in {sleep_time:.2f}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(sleep_time)

    def get_track(self, track_id: str) -> Dict[str, Any]:
        """Fetch full track metadata.

        Args:
            track_id: Spotify track ID

        Returns:
            API response dictionary containing track name, artists, album, popularity, etc.
        """
        logger.info(f"Fetching metadata for track: {track_id}...")
        return self._make_api_request(f"tracks/{track_id}")

    def get_artist(self, artist_id: str) -> Dict[str, Any]:
        """Fetch artist details to retrieve genres.

        Args:
            artist_id: Spotify artist ID

        Returns:
            API response dictionary containing genres, popularity, etc.
        """
        logger.info(f"Fetching metadata for artist: {artist_id}...")
        return self._make_api_request(f"artists/{artist_id}")

    def get_audio_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """DEPRECATED BY SPOTIFY. Returns None on any app created after 2024-11-27.

        Spotify retired this endpoint on 2024-11-27. Only apps that already had
        a quota extension granted or pending on that date can still call it;
        every other client gets 403, permanently, and there is no replacement
        and no appeal. See:
        https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api

        Nothing in the app depends on it: the EQ is derived from Last.fm tags
        and Spotify artist genres, neither of which was affected. It is kept
        only so an older script that imports it does not break, and so the next
        person to reach for danceability/energy finds this note first rather
        than spending an afternoon debugging a 403 that is working as intended.

        Args:
            track_id: Spotify track ID

        Returns:
            API response containing audio features, or None (the usual case)
        """
        logger.info(f"Testing audio features endpoint for: {track_id}...")
        try:
            return self._make_api_request(f"audio-features/{track_id}")
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 500
            logger.error(f"Audio Features request returned HTTP {status_code}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch audio features: {e}")
            return None

    def search_track(self, query: str) -> Optional[Dict[str, Any]]:
        """Search Spotify for a track by name and artist.

        Args:
            query: Search query string (e.g. 'track:Creep artist:Radiohead')

        Returns:
            Search response dictionary containing tracks
        """
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        logger.info(f"Searching Spotify for: '{query}'...")
        try:
            return self._make_api_request(f"search?q={encoded_query}&type=track&limit=1")
        except Exception as e:
            logger.error(f"Search query failed: {e}")
            return None
