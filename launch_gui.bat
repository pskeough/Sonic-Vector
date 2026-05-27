@echo off
title Sonic Vector — Dashboard Launcher
color 0B

echo ============================================================================
echo                      Sonic Vector Dashboard Launcher
echo ============================================================================
echo.
echo Sonic Vector requires Spotify Developer Credentials to track active playback.
echo.
echo SETUP INSTRUCTIONS:
echo 1. Go to: https://developer.spotify.com/dashboard and log in.
echo 2. Click "Create app", name it "Sonic Vector", and select "Web API".
echo 3. Add this EXACT Redirect URI in the app settings:
echo    http://127.0.0.1:8888/callback
echo 4. Save the app, copy your "Client ID" and "Client Secret".
echo 5. Open "config.yaml" in this directory and paste them in the spotify section.
echo.
echo ============================================================================
echo.

:: Check if config.yaml exists
if not exist "config.yaml" (
    echo [ERROR] config.yaml not found. Creating it from example...
    copy config.example.yaml config.yaml > nul
    echo [ACTION] Please edit config.yaml and insert your Spotify credentials before proceeding.
    pause
    exit /b
)

:: Quick check for placeholder values in config.yaml
findstr /C:"your_spotify_client_id_here" config.yaml > nul
if %errorlevel% equ 0 (
    echo [WARNING] Default placeholder detected in config.yaml!
    echo Please open config.yaml and replace "your_spotify_client_id_here"
    echo with your actual Spotify Developer Client ID.
    echo.
    choice /M "Would you like to open config.yaml now"
    if %errorlevel% equ 1 (
        start notepad config.yaml
        echo Edit the file, save it, and then run this launcher again.
        pause
        exit /b
    )
)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Check and activate virtual environment
if exist "venv\Scripts\activate.bat" (
    echo [OK] Found local virtual environment. Activating...
    call "venv\Scripts\activate.bat"
) else if exist "..\venv\Scripts\activate.bat" (
    echo [OK] Found virtual environment in parent directory. Activating...
    call "..\venv\Scripts\activate.bat"
) else (
    echo [INFO] Virtual environment not detected. Running with system Python.
    echo        If dependencies are missing, run: pip install -r requirements.txt
)

echo.
echo [INFO] Starting Sonic Vector Web Dashboard...
echo [INFO] 1. Open http://127.0.0.1:5001 in your browser.
echo [INFO] 2. Click "Connect Spotify Account" in the top bar.
echo [INFO] 3. Approve the connection in the browser popup window to sync.
echo.
echo [INFO] Close this window or press Ctrl+C to stop the server.
echo.

python web_gui_app.py

echo.
echo [INFO] Dashboard server stopped.
pause
