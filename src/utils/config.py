"""Configuration management for EqualizerAI."""

import yaml
import os
from pathlib import Path


class Config:
    """Simple configuration loader and accessor."""

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                f"Please copy config.example.yaml to config.yaml and fill in your credentials."
            )

        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def get(self, *keys, default=None):
        """Get nested configuration value using dot notation.

        Example:
            config.get('spotify', 'client_id')
            config.get('app', 'polling_interval', default=2.0)
        """
        value = self._config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
            if value is None:
                return default
        return value

    @property
    def spotify_client_id(self) -> str:
        return self.get('spotify', 'client_id')

    @property
    def spotify_client_secret(self) -> str:
        return self.get('spotify', 'client_secret')

    @property
    def spotify_redirect_uri(self) -> str:
        return self.get('spotify', 'redirect_uri')

    @property
    def gemini_api_key(self) -> str:
        return self.get('gemini', 'api_key')

    @property
    def gemini_model(self) -> str:
        return self.get('gemini', 'model', default='gemini-2.5-flash-lite')

    @property
    def gemini_translator_model(self) -> str:
        return self.get('gemini', 'translator_model', default='gemini-2.5-flash-lite')

    @property
    def gemini_synthesizer_model(self) -> str:
        return self.get('gemini', 'synthesizer_model', default='gemini-2.5-flash')

    @property
    def llm_provider(self) -> str:
        return self.get('llm', 'provider', default='gemini')

    @property
    def local_api_url(self) -> str:
        return self.get('llm', 'local_api_url', default='http://127.0.0.1:8080/v1')

    @property
    def local_model(self) -> str:
        return self.get('llm', 'local_model', default='local-model')

    @property
    def equalizer_config_path(self) -> str:
        return self.get('equalizer_apo', 'config_path')

    @property
    def polling_interval(self) -> float:
        return self.get('app', 'polling_interval', default=1.5)

    @property
    def log_level(self) -> str:
        return self.get('app', 'log_level', default='INFO')

    @property
    def database_path(self) -> str:
        return self.get('app', 'database_path', default='data/songs.db')

    # Profile Engine Configuration
    @property
    def profile_update_frequency_days(self) -> int:
        """Number of days between automatic profile updates."""
        return self.get('profile', 'update_frequency_days', default=30)

    @property
    def profile_recency_window_days(self) -> int:
        """Number of days to look back for recent songs analysis."""
        return self.get('profile', 'recency_window_days', default=180)

    @property
    def profile_min_songs_threshold(self) -> int:
        """Minimum number of recent songs required for profile update."""
        return self.get('profile', 'min_songs_threshold', default=20)

    @property
    def profile_ai_model(self) -> str:
        """AI model to use for profile generation and analysis."""
        return self.get('profile', 'ai_model', default='gemini-2.5-flash')
