"""Spotify service for authentication and API calls"""

import os
import json
import time
import base64
import hashlib
import secrets
import webbrowser
import requests
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs, urlparse

# Define BASE_DIR relative to this file's location
# This file is in src/spotify, so we go up three levels to the project root
BASE_DIR = Path(__file__).parent.parent.parent

# How long to hold the loopback listener open waiting for Spotify to call back.
# Long enough for a password manager, a 2FA prompt and a slow "Agree" click;
# short enough that an abandoned attempt frees the port on its own.
AUTH_TIMEOUT_S = 300


class _CallbackServer(HTTPServer):
    """Loopback listener for the OAuth redirect.

    allow_reuse_address matters here: without it, a login attempt that ended
    badly leaves the port in TIME_WAIT and the next attempt fails to bind for
    a couple of minutes, which reads to the user as "the button is broken".
    """

    allow_reuse_address = True


def _describe_auth_error(error: Optional[str], redirect_uri: str) -> str:
    """Turn Spotify's terse error code into something actionable."""
    if error == "access_denied":
        return "You declined the authorization request in Spotify."
    if error in ("invalid_client", "invalid_request"):
        return (f"Spotify rejected the request ({error}). The usual cause is a "
                f"redirect URI mismatch: your Spotify app's settings must list "
                f"exactly {redirect_uri}")
    if error:
        return f"Spotify returned '{error}'."
    return ("The login window closed before Spotify sent a response. "
            "Nothing was changed; you can try again.")


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Sonic Vector</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#1c1914;color:#ece8db;
             padding:60px;text-align:center">
  <h2 style="color:%(colour)s;margin-bottom:8px">%(heading)s</h2>
  <p style="color:#b6b1a0;line-height:1.6">%(detail)s</p>
  <script>setTimeout(function(){window.close();}, %(delay)d);</script>
</body></html>"""


class SpotifyCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for Spotify OAuth callback"""

    def _page(self, status, colour, heading, detail, delay=2500):
        body = (_PAGE % {"colour": colour, "heading": heading,
                         "detail": detail, "delay": delay}).encode("utf-8")
        self.send_response(status)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Handle GET request for OAuth callback"""
        parsed_url = urlparse(self.path)

        # Browsers ask for /favicon.ico on any page they render. Answering it
        # as if it were the callback used to consume the single request this
        # server was willing to handle, so the real callback was never read.
        if parsed_url.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        if '/callback' not in parsed_url.path:
            self.send_error(404)
            return

        query_params = parse_qs(parsed_url.query)

        if 'code' in query_params:
            self.server.auth_code = query_params['code'][0]
            self.server.auth_error = None
            self._page(200, "#a4c076", "Spotify connected",
                       "You can close this window and go back to Sonic Vector.")
            return

        # Spotify reports refusals and misconfiguration here rather than at the
        # authorize call, so this is the only place the real reason appears.
        error = (query_params.get('error') or ['unknown_error'])[0]
        self.server.auth_error = error
        detail = {
            'access_denied':
                "You declined the authorization request. Nothing was changed.",
            'invalid_client':
                "Spotify rejected this app's Client ID.",
        }.get(error, f"Spotify returned: {error}")
        if error not in ('access_denied', 'invalid_client'):
            detail += ("<br><br>If this mentions the redirect URI, make sure your "
                       "Spotify app's settings list exactly the same address "
                       "Sonic Vector is configured with.")
        self._page(400, "#e06a50", "Spotify authorization failed", detail,
                   delay=9000)

    def log_message(self, format, *args):
        """Suppress log messages"""
        pass


class SpotifyService:
    """Spotify Web API service with OAuth authentication"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self.redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

        if not self.client_id or not self.client_secret:
            raise ValueError("Spotify credentials not found in environment variables. Please check your .env file.")

        self.token_file = BASE_DIR / "data" / "spotify_tokens.json"
        self.spotipy_cache_file = BASE_DIR / ".spotify_cache"
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        # Why the last connect attempt failed, in words a listener can act on.
        # Surfaced through /api/spotify/status; without it the Connect button
        # simply re-enabled itself after eight seconds and explained nothing.
        self.last_error = None

        self.auth_url = "https://accounts.spotify.com/authorize"
        self.token_url = "https://accounts.spotify.com/api/token"
        self.api_base = "https://api.spotify.com/v1"

        self._load_tokens()
        self.logger.info(f"SpotifyService initialized. Token file: {self.token_file}")

    def _load_tokens(self):
        """Load tokens from disk and validate the data."""
        if self.token_file.exists():
            try:
                with open(self.token_file, 'r') as f:
                    data = json.load(f)

                self.access_token = data.get('access_token')
                self.refresh_token = data.get('refresh_token')
                expires_str = data.get('expires_at')

                if expires_str:
                    self.token_expires_at = datetime.fromisoformat(expires_str)
                    time_until_expiry = self.token_expires_at - datetime.now()
                    self.logger.info(f"Loaded tokens from file. Expires in: {time_until_expiry}")
                else:
                    self.logger.warning("Loaded tokens but expiry time is missing")

                if self.access_token and self.refresh_token:
                    self.logger.debug("Successfully loaded access and refresh tokens")
                else:
                    self.logger.warning(f"Token file incomplete - access_token: {bool(self.access_token)}, refresh_token: {bool(self.refresh_token)}")

            except json.JSONDecodeError as e:
                self.logger.error(f"Token file is corrupted (invalid JSON): {e}")
                self.access_token = None
                self.refresh_token = None
                self.token_expires_at = None
            except Exception as e:
                self.logger.error(f"Failed to load Spotify tokens: {e}")
                self.access_token = None
                self.refresh_token = None
                self.token_expires_at = None
        else:
            self.logger.info(f"No token file found at {self.token_file}. Authentication required.")

    def _save_tokens(self):
        """Save tokens to disk."""
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None
            }
            with open(self.token_file, 'w') as f:
                json.dump(data, f, indent=2)

            if self.token_expires_at:
                time_until_expiry = self.token_expires_at - datetime.now()
                self.logger.debug(f"Tokens saved. Expires in: {time_until_expiry}")
            else:
                self.logger.warning("Tokens saved but no expiry time set")
        except Exception as e:
            self.logger.error(f"Failed to save tokens: {e}")

    def _generate_code_verifier(self):
        return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')

    def _generate_code_challenge(self, verifier):
        digest = hashlib.sha256(verifier.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')

    def authenticate(self) -> bool:
        """
        Initiate the OAuth authentication flow with Spotify.

        Opens a browser for user authorization and exchanges the code for tokens.
        Returns True if successful, False otherwise.
        """
        self.logger.info("Starting Spotify OAuth authentication flow")

        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)

        auth_params = {
            'client_id': self.client_id,
            'response_type': 'code',
            'redirect_uri': self.redirect_uri,
            'code_challenge_method': 'S256',
            'code_challenge': code_challenge,
            'scope': 'user-read-currently-playing user-read-playback-state user-modify-playback-state user-top-read playlist-modify-public playlist-modify-private'
        }

        auth_url = f"{self.auth_url}?{urlencode(auth_params)}"

        # Bind before opening the browser. If the port is unavailable the whole
        # flow is doomed, and finding that out after the user has already
        # logged in wastes their time and loses the code.
        server_address = urlparse(self.redirect_uri)
        try:
            server = _CallbackServer(
                (server_address.hostname or "127.0.0.1",
                 server_address.port or 8888),
                SpotifyCallbackHandler)
        except OSError as e:
            self.last_error = (
                f"Could not listen on {self.redirect_uri} ({e}). Another program "
                f"may be using that port, or a previous login attempt may still "
                f"be waiting.")
            self.logger.error(self.last_error)
            return False

        server.auth_code = None
        server.auth_error = None
        server.timeout = 1.0

        self.logger.info("Opening browser for Spotify authorization...")
        webbrowser.open(auth_url)

        self.logger.info(f"Waiting for authorization callback at {self.redirect_uri}...")
        try:
            # handle_request() on its own blocks forever. If the listener closed
            # the tab, or never finished logging in, the old code left this
            # thread parked on the port for the life of the process, so every
            # later attempt to connect failed to bind and the button appeared
            # dead. Poll with a deadline instead.
            deadline = time.time() + AUTH_TIMEOUT_S
            while time.time() < deadline:
                server.handle_request()
                if server.auth_code or server.auth_error:
                    break
            else:
                self.last_error = (
                    f"No response from Spotify within {AUTH_TIMEOUT_S // 60} minutes. "
                    f"The login window may have been closed.")
                self.logger.warning(self.last_error)
                return False
        finally:
            server.server_close()

        if server.auth_code is None:
            self.last_error = _describe_auth_error(
                server.auth_error, self.redirect_uri)
            self.logger.error(f"Authentication failed: {self.last_error}")
            return False

        self.logger.debug("Received authorization code, exchanging for tokens...")

        token_data = {
            'client_id': self.client_id,
            'grant_type': 'authorization_code',
            'code': server.auth_code,
            'redirect_uri': self.redirect_uri,
            'code_verifier': code_verifier
        }

        try:
            response = requests.post(self.token_url, data=token_data, timeout=10)
            response.raise_for_status()
            tokens = response.json()

            self.access_token = tokens['access_token']
            self.refresh_token = tokens['refresh_token']
            expires_in = tokens.get('expires_in', 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)

            self._save_tokens()

            self.last_error = None
            self.logger.info(f"Spotify authentication successful! Token expires in {expires_in} seconds")
            return True

        except requests.exceptions.RequestException as e:
            error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
            self.last_error = f"Spotify refused to issue a token: {error_text[:200]}"
            self.logger.error(f"Token exchange failed: {error_text}")
            return False

    def _refresh_access_token(self, retry_count: int = 0, max_retries: int = 3) -> bool:
        """Refresh the access token using the refresh token with exponential backoff retry."""
        if not self.refresh_token:
            self.logger.warning("Cannot refresh token: no refresh token available")
            return False

        self.logger.info(f"Attempting to refresh access token (attempt {retry_count + 1}/{max_retries + 1})")

        token_data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }

        try:
            response = requests.post(self.token_url, data=token_data, timeout=10)
            response.raise_for_status()
            tokens = response.json()

            self.access_token = tokens['access_token']
            expires_in = tokens.get('expires_in', 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)

            # Spotify may or may not return a new refresh token
            if 'refresh_token' in tokens:
                self.refresh_token = tokens['refresh_token']
                self.logger.debug("Received new refresh token")

            self._save_tokens()
            self.logger.info("Token refresh successful")
            return True

        except requests.exceptions.RequestException as e:
            error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
            self.logger.error(f"Token refresh failed (attempt {retry_count + 1}): {error_text}")

            # Retry with exponential backoff
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # 1s, 2s, 4s
                self.logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                return self._refresh_access_token(retry_count + 1, max_retries)

            # All retries exhausted, clear tokens
            self.logger.error("All token refresh attempts failed. Clearing tokens.")
            self.access_token = None
            self.refresh_token = None
            self.token_expires_at = None
            self._save_tokens()  # Clear invalid tokens
            return False

    def _ensure_valid_token(self) -> bool:
        """
        Ensure we have a valid access token.

        Returns True if we have a valid token, False if re-authentication is needed.
        This method will attempt to refresh the token if it's expired or near expiry.
        """
        if not self.access_token:
            self.logger.debug("Access token missing")
            return False

        if not self.refresh_token:
            self.logger.warning("Refresh token missing - full re-authentication required")
            return False

        if not self.token_expires_at:
            self.logger.warning("Token expiration time missing - full re-authentication required")
            return False

        now = datetime.now()
        time_until_expiry = self.token_expires_at - now

        # Log current token status
        self.logger.debug(f"Token status check - Time until expiry: {time_until_expiry}")

        # Refresh if token expires in less than 5 minutes
        if time_until_expiry <= timedelta(minutes=5):
            self.logger.info("Access token expired or near expiry, attempting to refresh...")
            if not self._refresh_access_token():
                self.logger.error("Token refresh failed. Full re-authentication required.")
                return False
            self.logger.info("Token refreshed successfully")
            return True

        # Token is still valid
        self.logger.debug("Token is valid")
        return True

    def is_authenticated(self) -> bool:
        """
        Check if the service is currently authenticated with Spotify.

        Returns True if we have valid credentials, False otherwise.
        This is a lightweight check that won't trigger token refresh unless necessary.
        """
        # Quick check: do we have the basic tokens?
        if not self.access_token or not self.refresh_token:
            self.logger.debug("Authentication check failed: missing tokens")
            return False

        # If we don't have an expiry time, we can't validate - need re-auth
        if not self.token_expires_at:
            self.logger.debug("Authentication check failed: missing expiry time")
            return False

        # Check if token is still valid (with buffer)
        # We only ensure valid token here, which will refresh if needed
        return self._ensure_valid_token()

    def clear_tokens(self) -> None:
        """
        Clear all authentication tokens from memory and disk.
        Also removes conflicting spotipy cache file if it exists.

        Use this method to force a fresh authentication.
        """
        self.logger.info("Clearing all authentication tokens")

        # Clear in-memory tokens
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None

        # Delete token file
        if self.token_file.exists():
            try:
                self.token_file.unlink()
                self.logger.info(f"Deleted token file: {self.token_file}")
            except Exception as e:
                self.logger.error(f"Failed to delete token file: {e}")

        # Delete conflicting spotipy cache file if it exists
        if self.spotipy_cache_file.exists():
            try:
                self.spotipy_cache_file.unlink()
                self.logger.info(f"Deleted conflicting spotipy cache: {self.spotipy_cache_file}")
            except Exception as e:
                self.logger.warning(f"Failed to delete spotipy cache file: {e}")

        self.logger.info("All tokens cleared. Fresh authentication required.")

    def _make_api_request(self, endpoint: str, method: str = 'GET', data: dict = None) -> Optional[Dict[str, Any]]:
        if not self.is_authenticated():
            return None

        headers = {'Authorization': f'Bearer {self.access_token}'}
        url = f"{self.api_base}/{endpoint.lstrip('/')}"

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers)
            elif method == 'POST':
                headers['Content-Type'] = 'application/json'
                response = requests.post(url, headers=headers, json=data)
            elif method == 'PUT':
                headers['Content-Type'] = 'application/json'
                response = requests.put(url, headers=headers, json=data)
            else:
                return None

            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 1))
                print(f"Rate limited, waiting {retry_after} seconds...")
                time.sleep(retry_after)
                return self._make_api_request(endpoint, method, data)

            response.raise_for_status()

            # Debug logging for queue endpoint
            if 'queue' in endpoint:
                print(f"DEBUG queue: status={response.status_code}, content_length={len(response.content)}, content={response.text[:100] if response.text else 'empty'}")

            if response.status_code == 204:
                return {}

            # Check content length to avoid JSONDecodeError on empty responses that aren't 204
            if not response.content:
                return {}

        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response:
                print(f"API request to '{endpoint}' failed: {e.response.status_code} - {e.response.text}")
            else:
                print(f"API request to '{endpoint}' failed: {str(e)}")
            return None

        # Try to parse JSON response (outside the RequestException try/except)
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Failed to decode JSON from '{endpoint}'. Status: {response.status_code}. Content: '{response.text[:200]}'")
            # For successful status codes with invalid JSON, treat as success (Spotify sometimes returns garbage)
            if 200 <= response.status_code < 300:
                print(f"Treating as successful despite invalid JSON (status {response.status_code})")
                return {}
            return None

    def get_queue(self) -> Optional[Dict[str, Any]]:
        """Get the user's playback queue.
        
        Returns:
            Dictionary containing queue data or None if failed
        """
        data = self._make_api_request('me/player/queue')
        if not data:
            return None
            
        queue_items = []
        if 'queue' in data:
            for item in data['queue'][:5]:  # Limit to next 5 tracks
                queue_items.append({
                    'name': item.get('name', 'Unknown'),
                    'artist': ', '.join([artist['name'] for artist in item.get('artists', [])]),
                    'uri': item.get('uri'),
                    'image_url': item.get('album', {}).get('images', [{}])[0].get('url') if item.get('album', {}).get('images') else None
                })
                
        return {'queue': queue_items}

    def get_current_track(self) -> Optional[Dict[str, Any]]:
        data = self._make_api_request('me/player/currently-playing')
        
        # If currently playing is empty, check general player state
        if not data or 'item' not in data or data['item'] is None:
            player_state = self._make_api_request('me/player')
            
            # If the player state reports that the active session is a Private Session, return a specific metadata flag
            if player_state and player_state.get('device', {}).get('is_private_session'):
                return {
                    'name': 'Private Session Active',
                    'artist': 'Please disable Private Session in Spotify to sync EQs.',
                    'album': 'Blocked by Spotify Client settings',
                    'duration_ms': 0,
                    'progress_ms': 0,
                    'is_playing': False,
                    'is_private_session': True,
                    'image_url': None,
                    'track_uri': '',
                    'shuffle_state': False,
                    'repeat_state': 'off'
                }
                
            if not player_state or 'item' not in player_state or player_state['item'] is None:
                return None
            data = player_state

        track = data['item']
        image_url = None
        if track.get('album', {}).get('images'):
            image_url = track['album']['images'][0]['url']

        return {
            'name': track.get('name', 'Unknown'),
            'artist': ', '.join([artist['name'] for artist in track.get('artists', [])]),
            'album': track.get('album', {}).get('name', 'Unknown'),
            'duration_ms': track.get('duration_ms', 0),
            'progress_ms': data.get('progress_ms', 0),
            'is_playing': data.get('is_playing', False),
            'is_private_session': data.get('device', {}).get('is_private_session', False) if 'device' in data else False,
            'image_url': image_url,
            'track_uri': track.get('uri', ''),
            'shuffle_state': data.get('shuffle_state', False),
            'repeat_state': data.get('repeat_state', 'off')
        }

    def get_devices(self) -> Optional[list]:
        """Get list of available devices."""
        data = self._make_api_request('me/player/devices')
        if data and 'devices' in data:
            return data['devices']
        return None

    def play_track(self, uri: str, context_uri: str = None, offset: int = None) -> bool:
        """Play a track on the user's active Spotify device.

        Args:
            uri: Spotify track URI (format: spotify:track:XXXXX)
            context_uri: Optional playlist/album URI to play from (format: spotify:playlist:XXXXX)
            offset: Optional track index in the context to start from (0-based)

        Returns:
            True if playback started successfully, False otherwise
        """
        if not uri or not uri.startswith('spotify:track:'):
            print(f"Invalid Spotify URI: {uri}")
            return False

        # Check for active devices first
        devices = self.get_devices()
        target_device_id = None

        if not devices:
            print("No Spotify devices found (active or inactive).")
            return False

        # Check for active device
        active_device = next((d for d in devices if d.get('is_active')), None)

        if active_device:
            # Look for active device
            pass
        else:
            # No active device, pick first available (usually smartphone or desktop app open in background)
            target_device_id = devices[0]['id']
            print(f"No active device found. Targeting available device: {devices[0]['name']}")

        # Build playback payload
        payload = {}

        if context_uri and offset is not None:
            # Play with context (playlist/album) starting at specific track
            payload['context_uri'] = context_uri
            payload['offset'] = {'position': offset}
        else:
            # Play single track
            payload['uris'] = [uri]

        # Construct URL with device_id if we need to target a specific inactive device
        endpoint = 'me/player/play'
        if target_device_id:
            endpoint += f'?device_id={target_device_id}'

        data = self._make_api_request(
            endpoint,
            method='PUT',
            data=payload
        )

        # _make_api_request returns {} for 204 No Content (success)
        # Returns None for errors
        if data is not None:
            if context_uri:
                print(f"Successfully started playback for {uri} in context {context_uri} at offset {offset}")
            else:
                print(f"Successfully started playback for {uri}")
            return True
        else:
            print(f"Failed to start playback for {uri}")
            return False

    def add_to_queue(self, uri: str) -> bool:
        """Add a track to the user's Spotify playback queue.

        Args:
            uri: Spotify track URI (format: spotify:track:XXXXX)

        Returns:
            True if track was added to queue successfully, False otherwise
        """
        if not uri or not uri.startswith('spotify:track:'):
            print(f"Invalid Spotify URI: {uri}")
            return False

        # Check for active devices first
        devices = self.get_devices()

        if not devices:
            print("No Spotify devices found. Please open Spotify on a device first.")
            return False

        active_device = next((d for d in devices if d.get('is_active')), None)

        # Spotify's queue endpoint REQUIRES an active playback session
        # If no device is playing, we'll play the track instead of queueing it
        if not active_device:
            print(f"No active playback session. Starting playback instead of queueing.")
            return self.play_track(uri)

        # We have an active device, proceed with queueing
        endpoint = f'me/player/queue?uri={uri}'

        data = self._make_api_request(
            endpoint,
            method='POST'
        )

        # _make_api_request returns {} for 204 No Content (success)
        # Returns None for errors
        if data is not None:
            print(f"Successfully added {uri} to queue")
            return True
        else:
            print(f"Failed to add {uri} to queue. Trying to play instead...")
            # Fallback: if queue fails, try to play the track
            return self.play_track(uri)

    def pause_playback(self) -> bool:
        """Pause playback on the user's active Spotify device.

        Returns:
            True if playback was paused successfully, False otherwise
        """
        data = self._make_api_request('me/player/pause', method='PUT')
        if data is not None:
            print("Playback paused")
            return True
        return False

    def resume_playback(self) -> bool:
        """Resume playback on the user's active Spotify device.

        Returns:
            True if playback was resumed successfully, False otherwise
        """
        data = self._make_api_request('me/player/play', method='PUT')
        if data is not None:
            print("Playback resumed")
            return True
        return False

    def toggle_playback(self) -> bool:
        """Toggle between play and pause based on current state.

        Returns:
            True if toggle was successful, False otherwise
        """
        current = self.get_current_track()
        if current and current.get('is_playing'):
            return self.pause_playback()
        else:
            return self.resume_playback()

    def skip_to_next(self) -> bool:
        """Skip to next track in playback queue.

        Returns:
            True if skip was successful, False otherwise
        """
        data = self._make_api_request('me/player/next', method='POST')
        if data is not None:
            print("Skipped to next track")
            return True
        return False

    def skip_to_previous(self) -> bool:
        """Skip to previous track or restart current track.

        Returns:
            True if skip was successful, False otherwise
        """
        data = self._make_api_request('me/player/previous', method='POST')
        if data is not None:
            print("Skipped to previous track")
            return True
        return False

    def seek_to_position(self, position_ms: int) -> bool:
        """Seek to a specific position in the currently playing track.

        Args:
            position_ms: Position in milliseconds to seek to

        Returns:
            True if seek was successful, False otherwise
        """
        data = self._make_api_request(f'me/player/seek?position_ms={position_ms}', method='PUT')
        if data is not None:
            print(f"Seeked to position: {position_ms}ms")
            return True
        return False

    def set_shuffle(self, state: bool) -> bool:
        """Set shuffle mode on or off.

        Args:
            state: True to enable shuffle, False to disable

        Returns:
            True if shuffle state was set successfully, False otherwise
        """
        state_str = 'true' if state else 'false'
        data = self._make_api_request(f'me/player/shuffle?state={state_str}', method='PUT')
        if data is not None:
            print(f"Shuffle set to: {state}")
            return True
        return False

    def set_repeat(self, state: str) -> bool:
        """Set repeat mode.

        Args:
            state: 'off', 'context' (repeat playlist/album), or 'track' (repeat one song)

        Returns:
            True if repeat state was set successfully, False otherwise
        """
        if state not in ['off', 'context', 'track']:
            print(f"Invalid repeat state: {state}. Must be 'off', 'context', or 'track'")
            return False

        data = self._make_api_request(f'me/player/repeat?state={state}', method='PUT')
        if data is not None:
            print(f"Repeat set to: {state}")
            return True
        return False

    def toggle_shuffle(self) -> bool:
        """Toggle shuffle mode based on current state.

        Returns:
            True if toggle was successful, False otherwise
        """
        current = self.get_current_track()
        if current:
            new_state = not current.get('shuffle_state', False)
            return self.set_shuffle(new_state)
        return False

    def cycle_repeat(self) -> bool:
        """Cycle through repeat modes: off -> context -> track -> off.

        Returns:
            True if cycle was successful, False otherwise
        """
        current = self.get_current_track()
        if current:
            current_state = current.get('repeat_state', 'off')
            # Cycle: off -> context -> track -> off
            next_state = {'off': 'context', 'context': 'track', 'track': 'off'}.get(current_state, 'off')
            return self.set_repeat(next_state)
        return False

    def get_queue(self) -> Optional[Dict[str, Any]]:
        """Get the current playback queue.

        Returns:
            Dictionary containing queue information with 'currently_playing' and 'queue' keys,
            or None if request fails
        """
        data = self._make_api_request('me/player/queue')
        if data:
            # Extract relevant information from queue
            queue_items = []
            for item in data.get('queue', [])[:10]:  # Limit to next 10 tracks
                if item:
                    image_url = None
                    if item.get('album', {}).get('images'):
                        image_url = item['album']['images'][0]['url']

                    queue_items.append({
                        'name': item.get('name', 'Unknown'),
                        'artist': ', '.join([artist['name'] for artist in item.get('artists', [])]),
                        'album': item.get('album', {}).get('name', 'Unknown'),
                        'duration_ms': item.get('duration_ms', 0),
                        'image_url': image_url,
                        'track_uri': item.get('uri', '')
                    })

            return {
                'currently_playing': data.get('currently_playing'),
                'queue': queue_items
            }
        return None

    def get_user_playlists_with_images(self) -> Optional[list]:
        """Get user's playlists with cover images and metadata.

        Returns:
            List of playlist dictionaries with id, name, tracks count, and image_url,
            or None if request fails
        """
        data = self._make_api_request('me/playlists?limit=50')
        if data and 'items' in data:
            playlists = []
            for item in data.get('items', []):
                if item:
                    # Get cover image URL
                    image_url = None
                    if item.get('images') and len(item['images']) > 0:
                        image_url = item['images'][0]['url']

                    playlists.append({
                        'id': item.get('id', ''),
                        'name': item.get('name', 'Unknown Playlist'),
                        'tracks': item.get('tracks', {}).get('total', 0),
                        'image_url': image_url,
                        'uri': item.get('uri', '')
                    })

            return playlists
        return None

    def get_playlist_tracks(self, playlist_id: str, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Get tracks from a specific playlist.

        Args:
            playlist_id: Spotify playlist ID
            limit: Maximum number of tracks to fetch (default: 50)

        Returns:
            Dictionary containing playlist info and track list, or None if request fails
        """
        # First get playlist metadata
        playlist_data = self._make_api_request(f'playlists/{playlist_id}')
        if not playlist_data:
            return None

        # Get playlist tracks
        tracks_data = self._make_api_request(f'playlists/{playlist_id}/tracks?limit={limit}')
        if not tracks_data or 'items' not in tracks_data:
            return None

        tracks = []
        for item in tracks_data.get('items', []):
            if item and item.get('track'):
                track = item['track']

                # Get album art URL
                album_art_url = None
                if track.get('album', {}).get('images'):
                    album_art_url = track['album']['images'][0]['url']

                tracks.append({
                    'name': track.get('name', 'Unknown'),
                    'artist': ', '.join([artist['name'] for artist in track.get('artists', [])]),
                    'album': track.get('album', {}).get('name', 'Unknown'),
                    'duration_ms': track.get('duration_ms', 0),
                    'track_uri': track.get('uri', ''),
                    'album_art_url': album_art_url
                })

        # Get playlist cover image
        playlist_image_url = None
        if playlist_data.get('images') and len(playlist_data['images']) > 0:
            playlist_image_url = playlist_data['images'][0]['url']

        return {
            'playlist_id': playlist_id,
            'playlist_name': playlist_data.get('name', 'Unknown Playlist'),
            'playlist_uri': playlist_data.get('uri', ''),
            'playlist_image_url': playlist_image_url,
            'total_tracks': playlist_data.get('tracks', {}).get('total', 0),
            'tracks': tracks
        }

    def get_user_top_items(self, time_range='medium_term', limit=50) -> Optional[Dict[str, Any]]:
        """Get user's top tracks and artists from Spotify.

        This method fetches the user's most listened-to tracks and artists
        over a specified time range. Useful for understanding user preferences
        and generating personalized music profiles.

        Args:
            time_range: Time range for top items. Options:
                - 'short_term': approximately last 4 weeks
                - 'medium_term': approximately last 6 months (default)
                - 'long_term': calculated from several years of data
            limit: Maximum number of items to fetch per category (max 50, default 50)

        Returns:
            Dictionary containing top tracks and artists:
            {
                'top_tracks': [
                    {
                        'name': str,
                        'artist': str,
                        'album': str,
                        'track_uri': str,
                        'popularity': int,
                        'album_art_url': str (optional)
                    },
                    ...
                ],
                'top_artists': [
                    {
                        'name': str,
                        'genres': [str, ...],
                        'popularity': int,
                        'artist_uri': str,
                        'image_url': str (optional)
                    },
                    ...
                ],
                'time_range': str
            }
            or None if request fails
        """
        # Validate time_range
        valid_ranges = ['short_term', 'medium_term', 'long_term']
        if time_range not in valid_ranges:
            self.logger.warning(f"Invalid time_range '{time_range}', using 'medium_term'")
            time_range = 'medium_term'

        # Validate limit
        limit = min(max(1, limit), 50)  # Clamp between 1 and 50

        try:
            # Fetch top tracks
            tracks_data = self._make_api_request(
                f'me/top/tracks?time_range={time_range}&limit={limit}'
            )

            # Fetch top artists
            artists_data = self._make_api_request(
                f'me/top/artists?time_range={time_range}&limit={limit}'
            )

            if not tracks_data or not artists_data:
                return None

            # Format tracks
            top_tracks = []
            for item in tracks_data.get('items', []):
                if item:
                    # Get album art URL
                    album_art_url = None
                    if item.get('album', {}).get('images'):
                        album_art_url = item['album']['images'][0]['url']

                    top_tracks.append({
                        'name': item.get('name', 'Unknown'),
                        'artist': ', '.join([artist['name'] for artist in item.get('artists', [])]),
                        'album': item.get('album', {}).get('name', 'Unknown'),
                        'track_uri': item.get('uri', ''),
                        'popularity': item.get('popularity', 0),
                        'album_art_url': album_art_url
                    })

            # Format artists
            top_artists = []
            for item in artists_data.get('items', []):
                if item:
                    # Get artist image URL
                    image_url = None
                    if item.get('images') and len(item['images']) > 0:
                        image_url = item['images'][0]['url']

                    top_artists.append({
                        'name': item.get('name', 'Unknown'),
                        'genres': item.get('genres', []),
                        'popularity': item.get('popularity', 0),
                        'artist_uri': item.get('uri', ''),
                        'image_url': image_url
                    })

            return {
                'top_tracks': top_tracks,
                'top_artists': top_artists,
                'time_range': time_range
            }

        except Exception as e:
            self.logger.error(f"Failed to get user top items: {e}", exc_info=True)
            return None

    def create_playlist(self, name: str, description: str = "", public: bool = False) -> Optional[str]:
        """Create a new playlist for the current user.

        Args:
            name: Name for the new playlist
            description: Optional playlist description
            public: Whether the playlist should be public (default: False)

        Returns:
            Playlist ID if successful, None otherwise
        """
        # First get current user's ID
        user_data = self._make_api_request('me')
        if not user_data or 'id' not in user_data:
            self.logger.error("Failed to get current user ID")
            return None

        user_id = user_data['id']

        # Create playlist
        payload = {
            'name': name,
            'description': description,
            'public': public
        }

        playlist_data = self._make_api_request(
            f'users/{user_id}/playlists',
            method='POST',
            data=payload
        )

        if playlist_data and 'id' in playlist_data:
            playlist_id = playlist_data['id']
            self.logger.info(f"Successfully created playlist '{name}' (ID: {playlist_id})")
            return playlist_id
        else:
            self.logger.error(f"Failed to create playlist '{name}'")
            return None

    def add_tracks_to_playlist(self, playlist_id: str, track_uris: list) -> bool:
        """Add tracks to an existing playlist.

        Args:
            playlist_id: Spotify playlist ID
            track_uris: List of Spotify track URIs (format: spotify:track:XXXXX)

        Returns:
            True if tracks were added successfully, False otherwise
        """
        if not track_uris:
            self.logger.warning("No track URIs provided")
            return False

        # Validate URIs
        valid_uris = [uri for uri in track_uris if uri and uri.startswith('spotify:track:')]
        if len(valid_uris) != len(track_uris):
            self.logger.warning(f"Filtered out {len(track_uris) - len(valid_uris)} invalid URIs")

        if not valid_uris:
            self.logger.error("No valid track URIs to add")
            return False

        # Spotify API allows max 100 tracks per request
        # Split into batches if necessary
        batch_size = 100
        success = True

        for i in range(0, len(valid_uris), batch_size):
            batch = valid_uris[i:i + batch_size]

            payload = {
                'uris': batch
            }

            result = self._make_api_request(
                f'playlists/{playlist_id}/tracks',
                method='POST',
                data=payload
            )

            if result is None:
                self.logger.error(f"Failed to add batch of {len(batch)} tracks to playlist {playlist_id}")
                success = False
            else:
                self.logger.info(f"Added {len(batch)} tracks to playlist {playlist_id}")

        return success

    def search_track(self, query: str, limit: int = 1) -> Optional[list]:
        """Search for a track on Spotify.

        Args:
            query: Search query (e.g., "Song Name Artist")
            limit: Number of results to return (default: 1)

        Returns:
            List of track dictionaries with name, artist, album, uri, and image_url,
            or None if request fails
        """
        if not self.is_authenticated():
            return None

        params = {
            'q': query,
            'type': 'track',
            'limit': limit
        }
        
        data = self._make_api_request(f'search?{urlencode(params)}')
        if not data or 'tracks' not in data:
            return None
            
        tracks = []
        for item in data['tracks']['items']:
            if item:
                # Get album art URL
                image_url = None
                if item.get('album', {}).get('images'):
                    image_url = item['album']['images'][0]['url']

                tracks.append({
                    'name': item.get('name', 'Unknown'),
                    'artist': ', '.join([artist['name'] for artist in item.get('artists', [])]),
                    'album': item.get('album', {}).get('name', 'Unknown'),
                    'uri': item.get('uri', ''),
                    'image_url': image_url,
                    'is_local': False
                })
                
        return tracks