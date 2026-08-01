"""Desktop integration: app windows, single-instance, Windows app identity.

Responsibility: everything needed to make a Flask app on localhost behave like
a pinned desktop application rather than a browser tab you have to remember to
open.

Three problems this solves, all of which show up the moment there is an icon on
the taskbar:

1. **Double-click twice.** A pinned launcher gets clicked again while the app is
   already running. Starting a second server just fails on the bound port and
   leaves the listener staring at a dead console. `server_is_running` lets the
   launcher notice and simply raise the existing window instead.

2. **A browser tab is not an app.** Chromium's `--app=` mode opens a chromeless
   window that carries the site's own icon, gets its own taskbar button, and
   does not lose the console among thirty other tabs.

3. **Windows app identity.** Without an explicit AppUserModelID, a windowed
   Python process is grouped under whatever identity the interpreter happens to
   have. Setting one, and stamping the same string on the shortcut, is what
   makes the taskbar treat this as one named application.

Stdlib plus an optional ctypes call into shell32; nothing here is required for
the server to run headless.
"""

import logging
import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

APP_ID = "SonicVector.MasteringConsole"
APP_NAME = "Sonic Vector"

# Chromium builds that support --app=. Edge first: it is present on every
# supported Windows install, so the good path is also the default path.
_BROWSER_CANDIDATES = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def server_is_running(host: str = "127.0.0.1", port: int = 5001,
                      timeout: float = 0.4) -> bool:
    """True when something is already listening, so we do not start a second."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_app_browser() -> str:
    """Path to a Chromium browser that understands --app=, or ""."""
    for raw in _BROWSER_CANDIDATES:
        path = os.path.expandvars(raw)
        if "%" not in path and Path(path).exists():
            return path
    for name in ("msedge", "chrome", "brave"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def open_app_window(url: str, width: int = 1420, height: int = 940) -> str:
    """Open `url` as a chromeless app window. Returns how it was opened.

    Falls back to an ordinary browser tab, which is a downgrade in presentation
    and nothing more: the dashboard is the same either way.
    """
    browser = find_app_browser()
    if browser:
        try:
            subprocess.Popen(
                [browser, f"--app={url}",
                 f"--window-size={width},{height}",
                 # A dedicated profile keeps the app window out of the user's
                 # ordinary session: no extensions injecting into it, no
                 # "restore pages?" prompt, and the window position is
                 # remembered per app rather than per browser.
                 f"--user-data-dir={_profile_dir()}"],
                close_fds=True,
            )
            return f"app window ({Path(browser).stem})"
        except Exception as e:
            logger.warning(f"Could not open an app window with {browser}: {e}")

    webbrowser.open(url)
    return "browser tab"


def _profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache"))
    path = base / "SonicVector" / "AppWindow"
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_app_user_model_id(app_id: str = APP_ID) -> bool:
    """Give this process an explicit Windows application identity."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception as e:
        logger.debug(f"Could not set AppUserModelID: {e}")
        return False
