"""Configuration management for EqualizerAI."""

import json
import os
import re
import tempfile
import yaml
from pathlib import Path


# A credential that is absent, blank, or still the example placeholder is not a
# credential. Every "is this service set up?" check in the app funnels here so
# they cannot disagree with each other.
def is_placeholder(value) -> bool:
    """True when a config value is missing or still the example placeholder."""
    text = str(value or "").strip()
    return (not text) or text.startswith("your_")


_ENTRY_RE = re.compile(
    r'^(?P<indent>\s+)(?P<key>[A-Za-z0-9_]+)\s*:\s*'
    r'(?P<val>"[^"]*"|\'[^\']*\'|[^#\n]*?)\s*(?P<comment>#.*)?$'
)


def _atomic_write(path: Path, text: str) -> None:
    """Replace a file in one step, so a crash cannot leave it half-written."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".cfg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def set_section_values(path, section: str, values: dict) -> None:
    """Update `key: value` entries under a top-level YAML section, in place.

    Round-tripping the whole document through yaml.safe_dump would strip every
    comment in config.yaml, and those comments are the only setup instructions
    a first-time user gets. This rewrites the specific lines instead, adding
    the section, or any key missing from it, when they are not already there.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()

    header = re.compile(rf"^{re.escape(section)}\s*:\s*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)

    if start is None:
        block = [f"{section}:"] + [f"  {k}: {json.dumps(str(v))}"
                                   for k, v in values.items()]
        if lines and lines[-1].strip():
            lines.append("")
        _atomic_write(path, "\n".join(lines + block) + "\n")
        return

    # The section runs to the next line that starts in column zero.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i][:1].isspace():
            end = i
            break

    remaining = dict(values)
    indent = "  "
    last_entry = start
    for i in range(start + 1, end):
        match = _ENTRY_RE.match(lines[i])
        if not match:
            continue
        indent = match.group("indent")
        last_entry = i
        key = match.group("key")
        if key in remaining:
            comment = match.group("comment")
            trailer = f"  {comment}" if comment else ""
            lines[i] = f"{indent}{key}: {json.dumps(str(remaining.pop(key)))}{trailer}"

    # Anything the section did not already carry is appended to it.
    for offset, (key, value) in enumerate(remaining.items()):
        lines.insert(last_entry + 1 + offset,
                     f"{indent}{key}: {json.dumps(str(value))}")

    _atomic_write(path, "\n".join(lines) + "\n")


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
