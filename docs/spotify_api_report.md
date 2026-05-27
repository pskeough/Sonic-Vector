# Spotify API Data Source Report

This report documents and analyzes the current structure, data types, and availability of song information harvested from the Spotify Web API.

---

## 📊 Connection Diagnostics & Availability

*   **Total Tested Tracks:** 4
*   **Audio Features Accessible:** ❌ No (Deprecated / Restricted to Extended Mode)
*   **Harvest Database:** `data/test_library.db`

### Endpoint Availability Table

| Endpoint | Tested URL Path | HTTP Status | Status Notes |
| :--- | :--- | :--- | :--- |
| **Track Metadata** | `/v1/tracks/{track_id}` | `200 OK` | Fully available. Yields structural titles and ids. |
| **Artist Metadata** | `/v1/artists/{artist_id}` | `200 OK` | Fully available. Yields sub-genres and follower counts. |
| **Audio Features** | `/v1/audio-features/{track_id}` | `403 Forbidden / 404 Not Found` | Deprecated in late 2024. Requires business-level Extended Mode. |

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

### ⚠️ Acoustic Vector Endpoint Status (Audio Features Blocked)

> [!WARNING]
> Since the `/v1/audio-features` endpoint is **blocked** on your current Spotify developer key due to late-2024 API policies, we **cannot** rely on Spotify's direct quantitative audio fields.
> 
> **How to Circumvent this Limitation for the Embedding Model:**
> 1. We will rely heavily on the **`genres`**, **`popularity`**, **`artist_name`**, and **`album_name`** fields which are still fully accessible.
> 2. We can utilize local, open-source audio processing libraries (like `librosa` or `scipy.signal` in python) to extract raw frequency characteristics (BPM, spectral centroids, MFCCs) directly from local audio files!
> 3. This actually makes us **100% independent of Spotify's cloud restrictions** and ensures your system continues working forever!

---

## 🎵 Sample Harvested Track Profiles

Below is the real data parsed and stored in your SQLite database for our test subjects:

### Song: *Creep* by **Radiohead**
*   **Album:** Pablo Honey (1993-02-22)
*   **Genres:** `art rock, alternative rock`
*   **Popularity:** 93/100
*   **Acoustic Values:** *(Blocked by Spotify API)*

### Song: *Get Lucky (Radio Edit) [feat. Pharrell Williams and Nile Rodgers]* by **Daft Punk**
*   **Album:** Get Lucky (Radio Edit) [feat. Pharrell Williams and Nile Rodgers] (2013-04-19)
*   **Genres:** `french house, electronic, electro`
*   **Popularity:** 83/100
*   **Acoustic Values:** *(Blocked by Spotify API)*

### Song: *Lose Yourself* by **Eminem**
*   **Album:** Just Lose It (2004-01-01)
*   **Genres:** `rap, hip hop`
*   **Popularity:** 83/100
*   **Acoustic Values:** *(Blocked by Spotify API)*

### Song: *Stairway to Heaven - Remaster* by **Led Zeppelin**
*   **Album:** Led Zeppelin IV (Deluxe Edition) (1971-11-08)
*   **Genres:** `classic rock, rock, hard rock, rock and roll`
*   **Popularity:** 84/100
*   **Acoustic Values:** *(Blocked by Spotify API)*

