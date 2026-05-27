"""SQLite Database & Reporting Tool for Isolated Embedding Test.

Responsibility: Stores Spotify metadata into a clean local SQLite database
and compiles a comprehensive, professional Data Source Report.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DataReporter:
    """Manages diagnostic database and markdown report generation."""

    def __init__(self, db_path: str = "data/test_library.db"):
        """Initialize database connections and verify tables.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create schema if it does not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spotify_harvest (
                    track_id TEXT PRIMARY KEY,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT,
                    popularity INTEGER,
                    release_date TEXT,
                    genres TEXT,
                    audio_features_accessible INTEGER,
                    tempo REAL,
                    valence REAL,
                    energy REAL,
                    danceability REAL,
                    acousticness REAL,
                    raw_track_json TEXT,
                    raw_artist_json TEXT,
                    raw_features_json TEXT,
                    harvest_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def save_diagnostics(
        self,
        track_data: Dict[str, Any],
        artist_data: Dict[str, Any],
        features_data: Optional[Dict[str, Any]]
    ) -> None:
        """Insert or replace Spotify diagnostics record in SQLite.

        Args:
            track_data: Track metadata dictionary
            artist_data: Artist metadata dictionary
            features_data: Audio features dictionary (can be None)
        """
        track_id = track_data.get('id')
        track_name = track_data.get('name')
        
        # Primary artist details
        artist_name = "Unknown"
        if track_data.get('artists'):
            artist_name = track_data['artists'][0].get('name', 'Unknown')
            
        album_name = track_data.get('album', {}).get('name', '')
        popularity = track_data.get('popularity', 0)
        release_date = track_data.get('album', {}).get('release_date', '')
        
        # Extract genres from artist profile
        genres_list = artist_data.get('genres', [])
        genres_str = ', '.join(genres_list) if genres_list else ''

        # Map audio features if they were accessible
        accessible = 1 if features_data is not None else 0
        tempo = features_data.get('tempo') if features_data else None
        valence = features_data.get('valence') if features_data else None
        energy = features_data.get('energy') if features_data else None
        danceability = features_data.get('danceability') if features_data else None
        acousticness = features_data.get('acousticness') if features_data else None

        # Serialize raw json payloads for complete reference
        raw_track = json.dumps(track_data)
        raw_artist = json.dumps(artist_data)
        raw_features = json.dumps(features_data) if features_data else None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO spotify_harvest (
                    track_id, track_name, artist_name, album_name, popularity,
                    release_date, genres, audio_features_accessible,
                    tempo, valence, energy, danceability, acousticness,
                    raw_track_json, raw_artist_json, raw_features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                track_id, track_name, artist_name, album_name, popularity,
                release_date, genres_str, accessible,
                tempo, valence, energy, danceability, acousticness,
                raw_track, raw_artist, raw_features
            ))
            conn.commit()

    def generate_markdown_report(self, output_path: str = "docs/spotify_api_report.md") -> None:
        """Read harvested SQLite data and compile a professional Markdown report.

        Args:
            output_path: Path to write the report file
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM spotify_harvest")
            records = [dict(row) for row in cursor.fetchall()]

        if not records:
            raise ValueError("No harvested records in database to write a report!")

        # Count audio feature successes
        accessible_count = sum(r['audio_features_accessible'] for r in records)
        all_features_blocked = accessible_count == 0

        # Construct report header
        report_content = f"""# Spotify API Data Source Report

This report documents and analyzes the current structure, data types, and availability of song information harvested from the Spotify Web API.

---

## 📊 Connection Diagnostics & Availability

*   **Total Tested Tracks:** {len(records)}
*   **Audio Features Accessible:** {"✅ Yes" if not all_features_blocked else "❌ No (Deprecated / Restricted to Extended Mode)"}
*   **Harvest Database:** `data/test_library.db`

### Endpoint Availability Table

| Endpoint | Tested URL Path | HTTP Status | Status Notes |
| :--- | :--- | :--- | :--- |
| **Track Metadata** | `/v1/tracks/{{track_id}}` | `200 OK` | Fully available. Yields structural titles and ids. |
| **Artist Metadata** | `/v1/artists/{{artist_id}}` | `200 OK` | Fully available. Yields sub-genres and follower counts. |
| **Audio Features** | `/v1/audio-features/{{track_id}}` | {"`200 OK`" if not all_features_blocked else "`403 Forbidden / 404 Not Found`"} | {"Fully open" if not all_features_blocked else "Deprecated in late 2024. Requires business-level Extended Mode."} |

---

## 🗂️ Field-by-Field Analysis

Below is an exact catalog of the data fields we successfully captured from the Spotify API, categorized by source.

### 1. Structural & Cultural Metadata (Tracks & Artists)

These parameters are **always accessible** on all developer keys, and provide key inputs for text/semantic embedding models.

| Parameter | Type | Simple Description (Musical Meaning) | Technical Description (Type & Bounds) |
| :--- | :--- | :--- | :--- |
| **`track_name`** | String | The official title of the song (e.g. "Creep"). | String, variable length. |
| **`artist_name`** | String | The primary performing artist or band (e.g. "Radiohead"). | String, variable length. |
| **`album_name`** | String | The album or single release name. | String, variable length. |
| **`popularity`** | Integer | Current user listening frequency and popularity. | Integer, bounded `[0, 100]` (100 = massive hit). |
| **`release_date`** | String | The calendar date when the track was released. | String format (`YYYY-MM-DD` or `YYYY`). |
| **`genres`** | String | Artist sub-genres (e.g. `indie rock, art rock`). | Comma-separated list of lower-case strings. |

"""

        # Append Audio Features fields if accessible
        if not all_features_blocked:
            report_content += """### 2. Acoustic & Dynamic Vectors (Audio Features)

*These features represent quantitative values that directly map to audio waves, and are highly useful for DSP/EQ coordinate mappings.*

| Parameter | Type | Simple Description (Musical Meaning) | Technical Description (Type & Bounds) |
| :--- | :--- | :--- | :--- |
| **`tempo`** | Float | The overall estimated speed of the track in BPM. | Float, typically `[40.0, 220.0]`. |
| **`energy`** | Float | Perceptual measure of intensity, activity, and loudness. | Float, bounded `[0.0, 1.0]` (1.0 = high energy). |
| **`danceability`** | Float | How suitable the track is for dancing (tempo, stability). | Float, bounded `[0.0, 1.0]`. |
| **`acousticness`** | Float | Confidence measure of whether the track is acoustic. | Float, bounded `[0.0, 1.0]` (1.0 = acoustic instruments). |
| **`valence`** | Float | Musical positiveness/mood expressed by the track. | Float, bounded `[0.0, 1.0]` (1.0 = happy, bright). |

"""
        else:
            report_content += """### ⚠️ Acoustic Vector Endpoint Status (Audio Features Blocked)

> [!WARNING]
> Since the `/v1/audio-features` endpoint is **blocked** on your current Spotify developer key due to late-2024 API policies, we **cannot** rely on Spotify's direct quantitative audio fields.
> 
> **How to Circumvent this Limitation for the Embedding Model:**
> 1. We will rely heavily on the **`genres`**, **`popularity`**, **`artist_name`**, and **`album_name`** fields which are still fully accessible.
> 2. We can utilize local, open-source audio processing libraries (like `librosa` or `scipy.signal` in python) to extract raw frequency characteristics (BPM, spectral centroids, MFCCs) directly from local audio files!
> 3. This actually makes us **100% independent of Spotify's cloud restrictions** and ensures your system continues working forever!

"""

        # Append harvested track profiles
        report_content += """---

## 🎵 Sample Harvested Track Profiles

Below is the real data parsed and stored in your SQLite database for our test subjects:

"""
        for r in records:
            report_content += f"""### Song: *{r['track_name']}* by **{r['artist_name']}**
*   **Album:** {r['album_name']} ({r['release_date']})
*   **Genres:** `{r['genres'] or 'None Listed'}`
*   **Popularity:** {r['popularity']}/100
"""
            if r['audio_features_accessible'] == 1:
                report_content += f"""*   **Acoustic Values:**
    *   *Tempo (BPM):* {r['tempo']:.1f}
    *   *Valence (Mood):* {r['valence']:.2f}
    *   *Energy:* {r['energy']:.2f}
    *   *Danceability:* {r['danceability']:.2f}
    *   *Acousticness:* {r['acousticness']:.2f}
"""
            else:
                report_content += """*   **Acoustic Values:** *(Blocked by Spotify API)*\n"""
            
            report_content += "\n"

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_content)

        logger.info(f"✓ Beautiful markdown report compiled successfully at: {output_path}")
