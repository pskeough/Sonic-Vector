# Data Alternatives & Ground-Truth Dataset Research

This document outlines our deep-dive research into alternative music metadata APIs, open-source databases, and ground-truth equalizer datasets. Our goal is to assess whether we can build a scaled, 100% offline, or open-source pipeline that does not rely on Spotify's closed-source cloud restrictions.

---

## 📊 PART 1: The Semantic EQ Ground-Truth Datasets

To train a model or retrieve professional EQ curves mathematically, we need a "ground truth" mapping of *human audio descriptions* to *parametric equalizer parameters* (Frequency, Gain, Q-factor). 

We have researched the two most prominent academic databases that solve this problem:

### 1. The SAFE-DB (Semantic Audio Feature Extraction Database)
*   **What it is:** A crowdsourced database gathered from professional DAWs (Pro Tools, Logic) where mixing engineers logged their exact multi-band EQ parameters alongside a descriptive term (e.g. "warm", "boxy", "airy").
*   **Core File:** `SAFEEqualiserUserData.csv` (contains 13 parameters for a 5-band parametric EQ).
*   **Simple Language Evaluation:** Yes, this will work! If a song is tagged with "acoustic, warm, intimate," we can look up the mathematical "centroid" (the average curve) for the word "warm" and apply those parametric settings to the equalizer.
*   **Technical Evaluation:** The raw CSV contains user-submitted values, meaning it has noise and outliers. To use it, we must preprocess it:
    1. Group the records by their descriptive semantic tags.
    2. Filter out outliers (e.g., using Z-score filtering on gain and frequency boundaries).
    3. Calculate the average frequency, gain, and Q-factor for each cluster to define a canonical EQ vector.

### 2. The SocialEQ Dataset (Northwestern University)
*   **What it is:** A database that maps semantic words to a **40-band graphical EQ** based on listeners' preferences.
*   **Simple Language Evaluation:** Instead of parametric knobs, this dataset tells us exactly which frequencies to boost or cut across the entire spectrum (from 20Hz to 20kHz) when someone wants a song to sound "bright," "clear," or "boomy."
*   **Technical Evaluation:** Since it uses a 40-band graphic representation, it provides high-resolution spectral modifications. We can use it to cross-validate or smooth out the parametric shapes we extract from SAFE-DB.

---

## 🗂️ PART 2: Circumventing Spotify with Open-Source Music Corpuses

If you want to scale this system globally and avoid being tied to a closed-source, highly restricted API like Spotify, there is an incredible ecosystem of free, open-source alternatives.

### 1. Canonical Metadata: MusicBrainz (The MetaBrainz Foundation)
*   **What it is:** A 100% free, community-maintained open-source music encyclopedia. It tracks millions of artists, albums, recordings, genres, and tags.
*   **How we use it:**
    *   **Open REST API:** We can query MusicBrainz for track titles and retrieve their sub-genres and tags.
    *   **PostgreSQL Dumps:** The entire database is open and downloadable. We can host a local copy of the metadata, making queries instant and entirely offline!
*   **Comparison:** Unlike Spotify, MusicBrainz never deprecates metadata, requires no user authentication flow, and is completely free for commercial and private use.

### 2. Audio Fingerprinting: AcoustID & Chromaprint
*   **What it is:** An open-source audio identification service.
*   **How we use it:** If a user plays an audio file locally, we can run a local utility (**Chromaprint**) to generate a short, mathematical fingerprint of the audio waves. We send this fingerprint to the AcoustID API, which returns the canonical MusicBrainz track ID. 
*   **Why it's powerful:** We do not need Spotify's text search; the audio file identifies *itself* based on its acoustic waveform!

### 3. Listening Habits: ListenBrainz
*   **What it is:** An open alternative to Last.fm that tracks music listening logs.
*   **How we use it:** It provides public datasets of track popularity and listening connections, allowing us to build collaborative filtering embeddings.

---

## 🎛️ PART 3: Going 100% Offline with Python Audio Analysis (DSP)

Instead of relying on the cloud to tell us how a song sounds, we can analyze the audio waves **directly on your computer** using Python. This is the ultimate method for an offline, private, and future-proof EQ system.

| Tool / Library | Technical Extraction | Simple Language Meaning (Musical Impact for EQ) |
| :--- | :--- | :--- |
| **Essentia** (MetaBrainz) | `rhythm.bpm`, `tonal.key_key` | Extracts the exact tempo (BPM) and musical key (e.g. A Minor), showing if a song is upbeat or slow, and what note frequencies are active. |
| **Librosa** (Python DSP) | `spectral_centroid` | Measures the "center of gravity" of the frequencies. A high centroid means a song is bright/high-pitched; a low centroid means it is bass-heavy. |
| **Librosa** (Python DSP) | `spectral_rolloff` | Determines the frequency below which 85% of the spectral energy lies. Great for detecting heavy sub-bass vs acoustic mids. |
| **Librosa** (Python DSP) | `MFCCs` | Mel-Frequency Cepstral Coefficients. Represents the overall timbral "shape" of the music (the "fingerprint" of the instruments and vocals). |

### The Offline Hybrid Architecture (The Ultimate EQ System)

If we combine these tools, we can run the entire system completely offline without ever hitting the cloud:

```
[Local Audio File (MP3/FLAC)]
       │
       ├───► [Local Chromaprint Fingerprint] ──► [AcoustID/MusicBrainz] ──► [Genre/Artist Tags]
       │                                                                            │
       │                                                                            ▼
       ├───► [Local Librosa/Essentia DSP] ─────► [Acoustic Wave Vectors] ────► [Text Embedding]
       │                                                                            │
       ▼                                                                            ▼
[Timbral Features (MFCCs)] ◄─────────────────────────────────────────────── [Concatenation]
       │                                                                            │
       └───────────────────────────────► [Local Machine Learning Model] ────────────┘
                                                        │
                                                        ▼
                                       [Precise Parametric EQ Settings]
```

---

## 📝 Conclusion & Action Plan

1.  **SAFE-DB Is Perfect**: It bridges the gap between semantic descriptors (retrieved from our text embeddings) and hardware parametric EQ configurations.
2.  **MusicBrainz + Librosa Bypasses Spotify**: By utilizing MusicBrainz for text metadata/genres, and Librosa locally for audio wave features, we can create a system that is 100% open-source, private, and decoupled from Spotify.
