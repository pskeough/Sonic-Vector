@echo off
setlocal enabledelayedexpansion
title Sonic Vector

:: Move to the script's own directory FIRST. Everything below touches files
:: relative to it, and the working directory is not the script directory when
:: the launcher is started from a shortcut, from "Run as administrator", or
:: from another folder.
cd /d "%~dp0"

echo ============================================================================
echo                                Sonic Vector
echo ============================================================================
echo.

:: ---------------------------------------------------------------- Python ---
where python >nul 2>&1
if errorlevel 1 (
    echo   [FAIL] Python was not found on your PATH.
    echo          Install Python 3.9 or newer and tick "Add python.exe to PATH".
    echo          https://www.python.org/downloads/windows/
    echo.
    pause
    exit /b 1
)

:: ------------------------------------------------------------ Virtualenv ---
if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
    echo   [ OK  ] Virtual environment activated.
) else if exist "..\venv\Scripts\activate.bat" (
    call "..\venv\Scripts\activate.bat"
    echo   [ OK  ] Virtual environment activated ^(parent directory^).
) else (
    echo   [INFO ] No virtual environment found. Using system Python.
)

:: ---------------------------------------------------------------- Config ---
:: config.yaml is gitignored, so a fresh clone will not have one. Create it and
:: carry on: nothing in it is required to start.
if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy /y "config.example.yaml" "config.yaml" >nul
        echo   [INFO ] Created config.yaml from the example template.
    ) else (
        echo   [WARN ] Neither config.yaml nor config.example.yaml is present.
    )
)

:: ------------------------------------------------------------- Centroids ---
if not exist "data\test_library.db" (
    echo   [INFO ] Building EQ profile database ^(first run^)...
    python preprocess_safe.py --synthetic >nul 2>&1
    if errorlevel 1 (
        echo   [WARN ] Profile build failed. Run: python preprocess_safe.py --synthetic
    )
)

:: ------------------------------------------------------------------ Icon ---
:: Generated rather than committed as a binary, so it can be re-tuned in one
:: place. Missing on a fresh clone, and the shortcut installer needs it.
if not exist "static\favicon.ico" (
    echo   [INFO ] Generating the app icon ^(first run^)...
    python tools\make_icon.py >nul 2>&1
    if errorlevel 1 echo   [WARN ] Icon build failed. Run: python tools\make_icon.py
)

echo.

:: -------------------------------------------------------------- Preflight --
:: Reports what will and will not work, and refuses to start only if the DSP
:: renderer self-test fails, since every headroom guarantee depends on it.
python tools\preflight.py
set "PREFLIGHT=!errorlevel!"

if not "!PREFLIGHT!"=="0" (
    echo.
    echo   Start aborted. Fix the items above and run this launcher again.
    echo.
    pause
    exit /b 1
)

:: Honour an override so the banner never advertises the wrong address.
if "%SONICVECTOR_PORT%"=="" set "SONICVECTOR_PORT=5001"

echo.
echo   Dashboard:  http://127.0.0.1:%SONICVECTOR_PORT%
echo   Stop:       close this window, or press Ctrl+C
echo.

:: Mention the shortcut installer once, and only while there is no shortcut to
:: install. Repeating it every launch would just be noise.
if not exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Sonic Vector.lnk" (
    echo   Tip: run  python tools\install_shortcut.py  to add a desktop and
    echo        Start Menu icon you can pin to the taskbar.
    echo.
)

echo ============================================================================
echo.

python web_gui_app.py
set "EXITCODE=!errorlevel!"

echo.
if not "!EXITCODE!"=="0" (
    echo   [FAIL] Sonic Vector exited with code !EXITCODE!.
    echo          Scroll up for the traceback.
) else (
    echo   Sonic Vector stopped. The system EQ has been reset to flat.
)
echo.
pause
endlocal
