@echo off
setlocal EnableDelayedExpansion
title Sonic Vector - Install

REM ==================================================================
REM  install.bat - one-time setup for Sonic Vector + the turntable view.
REM
REM  Checks prerequisites, installs Python dependencies, verifies that
REM  Equalizer APO is present, and puts a shortcut on the desktop.
REM
REM  THIS FILE IS DELIBERATELY PURE ASCII. cmd.exe decodes batch files
REM  in the console's OEM codepage, not UTF-8, so any multi-byte
REM  character - even inside a REM comment - gets misdecoded and can
REM  split the line it sits on. An earlier version used box-drawing
REM  characters for section rules; the mangling broke the caret line
REM  continuations and the architecture test, which then reported an
REM  AMD64 machine as ARM64 and ran fragments of lines as commands.
REM  Keep every byte in this file below 0x80.
REM
REM  Long commands are kept on ONE line for the same reason: caret
REM  continuations are the first thing to break when a line is corrupted.
REM
REM  Runs on x64 and on Windows-on-ARM. The dependency install is split
REM  in two because of that: the core packages are pure Python and work
REM  anywhere, while the Windows-media-session packages (pywin32 and the
REM  winrt-* family) are compiled wheels whose availability varies by
REM  architecture and Python version. Those are OPTIONAL - without them
REM  the app loses local-playback detection but Spotify still works - so
REM  a failure there is a warning, not a dead install.
REM
REM  It does NOT install Equalizer APO. That installer modifies the
REM  audio driver stack, needs a reboot, and requires you to choose
REM  which output device to hook.
REM ==================================================================

set "PROTO_DIR=%~dp0"
for %%I in ("%PROTO_DIR%..") do set "APP_DIR=%%~fI"

REM PROCESSOR_ARCHITECTURE reports the architecture of THIS process,
REM which is x86 when a 32-bit shell runs under emulation.
REM PROCESSOR_ARCHITEW6432 holds the machine's real architecture then.
set "ARCH=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "ARCH=%PROCESSOR_ARCHITEW6432%"
set "IS_ARM="
if /i "%ARCH%"=="ARM64" set "IS_ARM=1"

echo.
echo   SONIC VECTOR  -  Install
echo   ==========================================================
echo.
echo   Machine : Windows %ARCH%
echo   View    : %PROTO_DIR%
echo   App     : %APP_DIR%
echo.

set "FAIL="

REM ---- 1. Python ---------------------------------------------------
echo   [1/6] Python
where python >nul 2>&1
if errorlevel 1 (
    echo         [X] not found on PATH.
    if defined IS_ARM echo             On ARM64 use the "Windows installer (ARM64)" build.
    echo             Get it from https://www.python.org/downloads/windows/
    echo             Tick "Add python.exe to PATH" during setup.
    set "FAIL=1"
) else (
    for /f "tokens=2" %%V in ('python --version 2^>^&1') do set "PYVER=%%V"
    for /f %%A in ('python -c "import platform;print(platform.machine())"') do set "PYARCH=%%A"
    echo         [OK] Python !PYVER! on !PYARCH!
)

REM ---- 2. Node -----------------------------------------------------
echo   [2/6] Node.js  (runs the turntable view)
where node >nul 2>&1
if errorlevel 1 (
    echo         [X] not found on PATH.
    if defined IS_ARM echo             On ARM64 take the arm64 .msi.
    echo             Get it from https://nodejs.org/en/download
    set "FAIL=1"
) else (
    for /f %%V in ('node --version') do set "NODEVER=%%V"
    for /f %%A in ('node -p "process.arch"') do set "NODEARCH=%%A"
    echo         [OK] Node !NODEVER! on !NODEARCH!
)

if defined FAIL goto missing

REM ---- 3. Core Python packages -------------------------------------
REM  Pure Python plus Pillow. These must succeed for the app to run.
echo   [3/6] Core Python packages
python -m pip install --quiet --disable-pip-version-check "flask>=3.0.0" "pyyaml>=6.0" "requests>=2.31.0" "pillow>=10.0.0" "google-genai>=0.1.0"
if errorlevel 1 (
    echo         [X] failed. Run this to see the error in full:
    echo             python -m pip install flask pyyaml requests pillow google-genai
    goto problem
)
echo         [OK] installed

REM ---- 4. Optional: local playback detection -----------------------
REM  pywin32 and the winrt-* family are compiled extensions. They are
REM  what lets the app read the Windows media session, i.e. detect what
REM  is playing outside Spotify. If no wheel exists for this Python and
REM  architecture pair, everything else still works.
echo   [4/6] Local playback detection (optional)
python -m pip install --quiet --disable-pip-version-check "winrt-runtime>=3.2.1" "winrt-Windows.Media.Control>=3.2.1" "winrt-Windows.Media>=3.2.1" "winrt-Windows.Foundation>=3.2.1" "winrt-Windows.Foundation.Collections>=3.2.1" "winrt-Windows.Storage.Streams>=3.2.1" "pywin32>=306" >nul 2>&1
if errorlevel 1 (
    echo         [!] no wheels for Python !PYVER! on %ARCH%.
    echo.
    echo             These are compiled packages and are not published for
    echo             every Python and architecture pair. Sonic Vector will
    echo             still run and still apply EQ; it just will not pick up
    echo             audio playing outside Spotify.
    echo.
    echo             The usual fix is a different Python minor version. To
    echo             see which package is missing a wheel, run:
    echo               python -m pip install winrt-runtime pywin32
) else (
    echo         [OK] installed - Windows media session detection available
)

REM ---- 5. Equalizer APO --------------------------------------------
REM  Sonic Vector applies EQ by writing Equalizer APO's config file, so
REM  without it the app runs and shows curves but changes no audio.
echo   [5/6] Equalizer APO
set "APO_DIR="
for %%P in ("%ProgramFiles%\EqualizerAPO" "%ProgramFiles(x86)%\EqualizerAPO" "C:\Program Files\EqualizerAPO") do if not defined APO_DIR if exist "%%~P\config\config.txt" set "APO_DIR=%%~P"

if defined APO_DIR (
    echo         [OK] found at !APO_DIR!
    goto shortcut
)

echo         [!] not installed.
echo.
echo             Sonic Vector applies EQ by writing Equalizer APO's config
echo             file. Without it the app still runs and still shows you
echo             curves, but nothing you hear will change.
if defined IS_ARM echo.
if defined IS_ARM echo             NOTE: this machine is ARM64. Equalizer APO is a
if defined IS_ARM echo             driver-level audio component and this script has
if defined IS_ARM echo             not verified that an ARM64 build exists - check
if defined IS_ARM echo             the project page before assuming it installs.
echo.
echo             This script will not install it for you: the installer
echo             patches the audio driver stack, needs a reboot, and asks
echo             which playback device to hook. That is your call.
echo.
choice /c YN /n /m "            Open the download page now? [Y/N] "
if not errorlevel 2 start "" "https://sourceforge.net/projects/equalizerapo/"

:shortcut
REM ---- 6. Shortcut -------------------------------------------------
echo   [6/6] Desktop shortcut
set "LNK=%USERPROFILE%\Desktop\Sonic Vector.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%'); $s.TargetPath='%PROTO_DIR%start_sonicvector.bat'; $s.WorkingDirectory='%PROTO_DIR%'; $s.IconLocation='%APP_DIR%\static\favicon.ico'; $s.Description='Sonic Vector - adaptive EQ and turntable view'; $s.Save()" >nul 2>&1
if exist "%LNK%" (echo         [OK] "Sonic Vector" on your desktop) else (echo         [!] could not create it - launch from start_sonicvector.bat)

echo.
echo   ==========================================================
echo   Done. Start it from the desktop shortcut, or with
echo   start_sonicvector.bat in this folder.
echo.
pause
exit /b 0

:missing
echo.
echo   Install the missing prerequisites above, then run this again.
echo.
pause
exit /b 1

:problem
echo.
pause
exit /b 1
