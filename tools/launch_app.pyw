"""Windowed entry point: what the desktop and taskbar shortcuts run.

The .pyw extension is the whole trick on Windows: it is associated with
pythonw.exe, which has no console, so clicking the icon does not flash a black
box and then leave one sitting in the taskbar next to the app.

That removes the only place errors used to go, so everything is mirrored to
data/sonicvector.log, and anything fatal is also shown in a message box.
Failing silently is not an option for a launcher: an icon that does nothing
when clicked is indistinguishable from a broken install.

Clicking the icon while the app is already running raises the existing window
instead of starting a second server, which would bind-fail on port 5001 and
look like a crash.

For a console with live logs (useful when something is wrong), run
launch_gui.bat instead.
"""

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOG_PATH = ROOT / "data" / "sonicvector.log"


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def alert(title: str, message: str) -> None:
    """Say something even though there is no console to say it to."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)   # MB_ICONERROR
    except Exception:
        pass


def main() -> int:
    setup_logging()
    logger = logging.getLogger("launcher")

    from src import desktop

    desktop.set_app_user_model_id()
    port = int(os.environ.get("SONICVECTOR_PORT", "5001"))
    url = f"http://127.0.0.1:{port}"

    # Honour a pre-set SONICVECTOR_NO_BROWSER so this entry point can be run
    # headless, under a service host, or by a test that should not take over
    # the screen. Otherwise the launcher owns the window decision.
    headless = os.environ.get("SONICVECTOR_NO_BROWSER", "").strip() in ("1", "true", "yes")

    if desktop.server_is_running(port=port):
        logger.info("Sonic Vector is already running; opening a window on it.")
        if not headless:
            desktop.open_app_window(url)
        return 0

    # The server owns the browser-opening decision normally. Here the launcher
    # has already decided, so tell the server to keep its hands off and open the
    # window ourselves once the port answers.
    os.environ["SONICVECTOR_NO_BROWSER"] = "1"

    try:
        import web_gui_app
    except Exception as e:
        logger.exception("Failed to import the app")
        alert("Sonic Vector could not start",
              f"{e}\n\nFull details: {LOG_PATH}")
        return 1

    def open_when_ready() -> None:
        # Poll rather than sleep a fixed time: cold starts on a loaded machine
        # take several seconds, and opening the window early shows a browser
        # error page that people reasonably read as "the app is broken".
        for _ in range(120):
            if desktop.server_is_running(port=port):
                how = desktop.open_app_window(url)
                logger.info(f"Dashboard opened as {how}: {url}")
                return
            time.sleep(0.25)
        logger.error("Server did not come up within 30s; opening anyway.")
        desktop.open_app_window(url)

    if not headless:
        threading.Thread(target=open_when_ready, daemon=True).start()
    else:
        logger.info(f"Headless start; dashboard at {url}")

    try:
        web_gui_app.main()
    except SystemExit:
        raise
    except Exception as e:
        logger.exception("Sonic Vector exited with an error")
        alert("Sonic Vector stopped unexpectedly",
              f"{e}\n\nFull details: {LOG_PATH}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
