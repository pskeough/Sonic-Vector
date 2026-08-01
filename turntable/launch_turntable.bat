@echo off
setlocal
title Sonic Vector - Turntable View

REM ==================================================================
REM  launch_turntable.bat - opens the turntable view in its own window.
REM
REM  Safe to run repeatedly: if a server is already answering on the
REM  port it is reused rather than fought over.
REM
REM  Nothing here touches the SonicVectorEQ app. If that app is running
REM  on port 5001 the view uses its real now-playing data; if not, it
REM  falls back to a built-in demo feed so it is still usable.
REM
REM  Two things in here look over-cautious and are not:
REM
REM  1. Port checks go through tools\portcheck.mjs instead of an inline
REM     node -e one-liner. The one-liner contained arrow functions, and
REM     cmd.exe reads the ')' in '() => {}' as the end of an enclosing
REM     block - inside the retry loop that closed the loop early and
REM     reported a healthy server as having failed to start.
REM
REM  2. The file is pure ASCII. cmd.exe decodes batch files in the OEM
REM     codepage, so a box-drawing character in a comment can corrupt
REM     the line it sits on. See the note in install.bat.
REM ==================================================================

set "PROTO_DIR=%~dp0"
set "PORT=5177"
set "APP_PORT=5001"
set "URL=http://localhost:%PORT%"
set "SERVER=%PROTO_DIR%tools\devserver.mjs"
set "CHECK=%PROTO_DIR%tools\portcheck.mjs"

echo.
echo   SONIC VECTOR  -  Turntable View
echo   ------------------------------------------------
echo.

where node >nul 2>&1
if errorlevel 1 (
    echo   [X] Node.js was not found on PATH.
    echo       Install it from https://nodejs.org and run this again.
    echo.
    pause
    exit /b 1
)

if not exist "%SERVER%" (
    echo   [X] Cannot find the server script:
    echo       %SERVER%
    echo.
    pause
    exit /b 1
)

REM ---- already serving? --------------------------------------------
node "%CHECK%" %PORT% >nul 2>&1
if not errorlevel 1 (
    echo   [=] Reusing the server already on port %PORT%.
    goto browser
)

echo   [+] Starting the view server on port %PORT% ...
start "SonicVector Turntable Server" /min cmd /c node "%SERVER%" %PORT%

REM Poll rather than sleeping a fixed amount: node starts in well under
REM a second on a warm cache and several seconds on a cold one.
set "READY="
for /l %%i in (1,1,25) do call :probe

if not defined READY goto failed
echo   [+] Server up.

:browser

REM ---- is the real app running? ------------------------------------
node "%CHECK%" %APP_PORT% >nul 2>&1
if not errorlevel 1 (
    echo   [+] SonicVectorEQ found on port %APP_PORT% - using live playback.
) else (
    echo   [i] SonicVectorEQ not running - using the built-in demo feed.
    echo       Start the app, then press R in the view to go live.
)

REM ---- find a Chromium browser for app-window mode ------------------
REM  --app= gives a clean window with no tabs, omnibox or bookmarks bar.
REM  Both Program Files trees are searched plus the ARM one, because on
REM  Windows-on-ARM a browser may be the arm64 build, an x64 build under
REM  emulation, or a per-user install.
set "BROWSER="
for %%P in ("%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%LocalAppData%\Google\Chrome\Application\chrome.exe" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" "%ProgramFiles(Arm)%\Microsoft\Edge\Application\msedge.exe" "%LocalAppData%\Microsoft\Edge\Application\msedge.exe") do if not defined BROWSER if exist "%%~P" set "BROWSER=%%~P"

echo.
if defined BROWSER (
    echo   [+] Opening the view ...
    REM A separate user-data-dir keeps this out of the normal browser
    REM session, so closing it never disturbs open tabs.
    start "" "%BROWSER%" --app=%URL% --window-size=1600,980 --user-data-dir="%LocalAppData%\SonicVectorTurntable\browser" --no-first-run --no-default-browser-check --disable-features=Translate
) else (
    echo   [i] No Chrome or Edge found - opening in the default browser.
    start "" "%URL%"
)

echo.
echo   Keys:  1 2 3 4  camera - hero / plan / macro / free
echo          n  p     next / previous track (demo feed)
echo          r        reconnect to SonicVectorEQ
echo          h        show / hide the tools drawer
echo.
echo   The server runs in the minimised "SonicVector Turntable Server"
echo   window. Close that window to stop it.
echo.
timeout /t 6 >nul
exit /b 0

REM ---- subroutines -------------------------------------------------
REM  Called rather than inlined: portcheck's exit code is far easier to
REM  reason about outside a parenthesised for-body, and this keeps the
REM  loop body free of parentheses entirely.

:probe
if defined READY exit /b 0
node "%CHECK%" %PORT% >nul 2>&1
if not errorlevel 1 set "READY=1"
exit /b 0

:failed
echo.
echo   [X] The server did not come up on port %PORT%.
echo       Running it here so the error is visible - press Ctrl+C to quit.
echo.
REM Run in the foreground rather than telling the user to go and do it
REM themselves: whatever the failure is, it prints right here.
node "%SERVER%" %PORT%
echo.
pause
exit /b 1
