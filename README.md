# Sonic Vector

Sonic Vector is a local audio mastering dashboard and background tracking service. It integrates system-wide Windows audio equalization with real-time Spotify playback monitoring. By comparing track metadata, genres, and crowdsourced acoustic tags against pre-calibrated semantic audio profiles, it dynamically calculates and applies optimal parametric EQ configurations in real time.

The system maps target sound signatures (such as Bass Boost, Warmth, Vocal Clarity, and Loudness Compensation) using vector similarity interpolation over preprocessed semantic audio centroids. It writes these curves directly to the configuration file of the Windows Equalizer APO utility.

---

## Technical Features

* **Playback Tracking.** Polls Spotify user playback via OAuth token exchanges to extract active artist information, genre associations, and Last.fm tags.
* **Centroid Interpolation.** Interpolates pre-calibrated SAFE-DB semantic audio centroids (warm, bright, presence, punchy, airy) based on active track tags.
* **Equalizer APO Integration.** Generates and writes compliant parametric EQ curves straight to the hardware equalization path.
* **Song Caching.** Saves user-customized overrides to a local SQLite database, automatically recalling them whenever that specific track plays again.

---

## Prerequisites

1. **Python 3.9 or higher.** Ensure Python is added to your system environment variables.
2. **Equalizer APO.** Required for real-time hardware audio equalization on Windows. Download it here: https://sourceforge.net/projects/equalizerapo/
3. **Spotify Developer Account.** Required to obtain API keys for tracking playback.

---

## Step 1: Spotify API Credentials Setup

To read currently playing tracks, the dashboard connects to the Spotify Web API. You must create your own Spotify Developer App to get credentials.

1. Go to the **Spotify Developer Dashboard** at https://developer.spotify.com/dashboard and log in with your Spotify account.
2. Click **Create app**.
3. Fill out the application details:
   * **App name:** Sonic Vector
   * **App description:** Local audio equalization controller
   * **APIs Used:** Select **Web API**
4. Under the **Redirect URIs** field, enter this exact address:
   ```
   http://127.0.0.1:8888/callback
   ```
   *Note: This specific callback URI is hardcoded into the local OAuth listener. Punctuation and port number must match exactly.*
5. Check the terms box and click **Save**.
6. On the App overview page, click **Settings**.
7. Copy your **Client ID** and click **Show client secret** to copy your **Client Secret**.

---

## Step 2: Configuration

1. In the root directory of this project, make sure a file named `config.yaml` is present (the launch script will copy it from `config.example.yaml` automatically on first run).
2. Open `config.yaml` with a text editor.
3. Locate the `spotify` section and paste your Client ID and Client Secret:
   ```yaml
   spotify:
     client_id: "PASTE_YOUR_CLIENT_ID_HERE"
     client_secret: "PASTE_YOUR_CLIENT_SECRET_HERE"
     redirect_uri: "http://127.0.0.1:8888/callback"
   ```
4. Save the file.

---

## Step 3: Installation and First Run

1. **Create and Activate a Virtual Environment:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```
2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Launch the Dashboard:**
   Double-click `launch_gui.bat` or run:
   ```bash
   python web_gui_app.py
   ```
4. **Complete Browser Sign-In:**
   * Open http://127.0.0.1:5001 in your web browser.
   * Click **Connect Spotify Account** in the top navigation bar.
   * A new browser window or tab will pop up, asking you to authorize your developer app.
   * Log in and click **Agree** to complete the connection.
   * The popup will close automatically, and the dashboard will begin syncing with your playback.

---

## Directory Structure

```
SonicVectorEQ/
├── data/
│   ├── SAFEEqualiserUserData.csv ← SAFE crowdsourced EQ database
│   ├── test_library.db           ← Preprocessed semantic centroids database
│   └── songs.db                  ← Local SQLite song profile cache (created on run)
├── src/
│   ├── spotify/
│   │   └── service.py            ← Spotify OAuth and API client
│   └── utils/
│       ├── config.py             ← Configuration loader
│       └── llm_client.py         ← Gemini and Local LLM wrapper
├── static/                       ← Frontend CSS, JS, and image assets
├── templates/                    ← Flask dashboard templates
├── .gitignore                    ← Keeps credentials out of Git
├── config.example.yaml           ← Template configuration file
├── config.yaml                   ← Active configuration file (gitignored)
├── requirements.txt              ← Python library dependencies
├── web_gui_app.py                ← Main Flask server and dashboard backend
├── embed_song_predictor.py       ← Semantic centroid weighting and EQ compiler
└── launch_gui.bat                ← Interactive Windows launcher
```

---

## License

This software is provided for personal and research use. The preprocessed SAFE semantic profiles are derived from the crowdsourced SAFE Equaliser Database.
