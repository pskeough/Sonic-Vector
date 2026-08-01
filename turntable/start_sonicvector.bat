@echo off
setlocal
title Sonic Vector

REM ==================================================================
REM  start_sonicvector.bat - brings up both halves, opens the console.
REM
REM    :5001  SonicVectorEQ  (Flask - the EQ engine and the console UI)
REM    :5177  Turntable view (Node  - the 3D deck)
REM
REM  Either can run without the other. The console's TURNTABLE button
REM  probes :5177 before opening a window, and the view falls back to a
REM  demo feed when :5001 is absent - so a half-start degrades rather
REM  than breaks.
REM
REM  Pure ASCII on purpose - see the note at the top of install.bat.
REM ==================================================================

set "PROTO_DIR=%~dp0"
for %%I in ("%PROTO_DIR%..") do set "APP_DIR=%%~fI"
set "CHECK=%PROTO_DIR%tools\portcheck.mjs"

echo.
echo   SONIC VECTOR
echo   ------------------------------------------------
echo.

where node >nul 2>&1
if errorlevel 1 (
    echo   [X] Node.js not found. Run install.bat first.
    echo.
    pause
    exit /b 1
)

REM ---- Turntable view ----------------------------------------------
node "%CHECK%" 5177 >nul 2>&1
if not errorlevel 1 (
    echo   [=] Turntable view already running on 5177.
) else (
    echo   [+] Starting turntable view ...
    start "Sonic Vector - Turntable View Server" /min cmd /c node "%PROTO_DIR%tools\devserver.mjs" 5177
)

REM ---- EQ engine ---------------------------------------------------
node "%CHECK%" 5001 >nul 2>&1
if not errorlevel 1 (
    echo   [=] SonicVectorEQ already running on 5001.
    goto ready
)

if not exist "%APP_DIR%\web_gui_app.py" (
    echo   [!] SonicVectorEQ not found next to this folder:
    echo       %APP_DIR%
    echo       The turntable view will run on its demo feed.
    goto ready
)

echo   [+] Starting SonicVectorEQ ...
REM SONICVECTOR_NO_BROWSER stops the app opening its own tab; this
REM script decides what gets opened and when.
start "Sonic Vector - EQ Engine" /min cmd /c "cd /d "%APP_DIR%" && set SONICVECTOR_NO_BROWSER=1&& set PYTHONIOENCODING=utf-8&& python web_gui_app.py"

set "READY="
for /l %%i in (1,1,60) do call :probe5001
if not defined READY (
    echo   [!] SonicVectorEQ did not come up. The turntable view will
    echo       run on its demo feed. Check the "EQ Engine" window.
)

:ready
echo.
echo   [+] Opening the console ...
start "" "http://localhost:5001"
echo.
echo   Console  http://localhost:5001
echo   Deck     http://localhost:5177   (or the TURNTABLE button)
echo.
echo   Both servers run in their own minimised windows. Close those to
echo   stop them, or use POWER OFF in the console to reset the EQ flat.
echo.
timeout /t 6 >nul
exit /b 0

REM  A subroutine, not an inline block: portcheck's exit code is far
REM  easier to reason about outside a parenthesised for-body.
:probe5001
if defined READY exit /b 0
node "%CHECK%" 5001 >nul 2>&1
if not errorlevel 1 set "READY=1"
exit /b 0
