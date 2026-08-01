"""Windows now-playing, straight from the OS. No account, no API key, no network.

Windows keeps a System Media Transport Controls (SMTC) session for every app
that plays media: Spotify's desktop client, a browser tab, VLC, a game. That is
the same data behind the media flyout on the volume keys. Reading it means the
app knows what is playing without anyone signing in to anything, and it works
for sources Spotify's Web API cannot see at all.

Shape of the answer is deliberately identical to the Spotify path's, so the
monitor loop does not care which source it is talking to:

    {track_uri, name, artist, album, image_url, is_playing, is_private_session}

Design notes, from measurements taken on this machine
-----------------------------------------------------
* **Polling, not events.** SMTC does raise MediaPropertiesChanged, and it does
  fire without a message pump. It is still not used here. The callbacks arrive
  on a WinRT threadpool thread that is neither the main thread nor the
  subscriber, they fire exactly twice per track change so they need debouncing,
  and the session object has to be unbound and rebound whenever the current
  session changes. The measured payoff for all that is nothing: notification
  lags true track start by 2-3 s, which is what a 1 s poll costs anyway. A read
  is ~14 ms warm, so polling costs about 1.4% of one core. Simplicity wins.

* **`winrt-Windows.Media` is a separate install.** Without it `playback_type`
  raises AttributeError while every other field works, which fails late and
  reads like a data problem rather than a missing package.

* **Thumbnails are streams, not URLs.** They are read into memory on this
  adapter's own loop and cached, then served by the app over HTTP. Spotify's
  are PNGs around 110-250 KB.

Requires the `winrt-*` packages listed in requirements.txt. Everything degrades
to "unavailable" rather than raising when they are missing, because this whole
source is optional: the app still works with the search box.
"""

import asyncio
import hashlib
import logging
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Import failures are expected and survivable: on a machine without the winrt
# packages the app simply has no Windows source and says so.
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
    )
    from winrt.windows.storage.streams import DataReader as _DataReader
    IMPORT_ERROR: Optional[str] = None
except Exception as e:                                    # pragma: no cover
    _SessionManager = None
    _DataReader = None
    IMPORT_ERROR = f"{type(e).__name__}: {e}"

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
STATUS_PLAYING = 4

# Apps whose "title" is a page title rather than a track title. Not excluded:
# plenty of listening happens on YouTube, and a wrong title costs a flat EQ at
# worst, because Last.fm simply will not have tags for it. They are only
# deprioritised when a real player is also going.
BROWSER_AUMIDS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "brave",
    "opera.exe", "vivaldi.exe", "zen.exe", "msedge", "chrome", "firefox",
}

POLL_SECONDS = 1.0
ART_CACHE_SIZE = 32
# Not every track has a thumbnail: Spotify publishes one for some and not for
# others, and when it does publish one it can lag the title by a second or two.
# So retry a few times, then stop asking rather than firing an async read at a
# null reference once a second for as long as the song lasts.
ART_MAX_ATTEMPTS = 6


def _norm(text: str) -> str:
    """Normalise for a stable identity key: fold case, strip accents and noise."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    # "(Official Video)", "[Remastered 2011]" and friends are not part of the
    # song's identity, and dropping them keeps one track from being cached
    # under several keys.
    text = re.sub(r"\s*[\(\[]\s*(official|lyric|audio|video|hd|4k|remaster(ed)?|"
                  r"explicit|clean)\b[^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def local_key(artist: str, title: str) -> str:
    """Deterministic per-track identity, since SMTC has no track IDs."""
    return hashlib.sha1(
        f"{_norm(artist)}\x1f{_norm(title)}".encode()).hexdigest()[:16]


class SmtcNowPlaying:
    """Polls the Windows media session and publishes the latest track."""

    name = "windows"

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict[str, Any]] = None
        self._art: "OrderedDict[str, tuple]" = OrderedDict()
        self._art_attempts: Dict[str, int] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._running = False
        self.last_error: Optional[str] = IMPORT_ERROR
        self.generation = 0

    # ---- availability -----------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """True when the WinRT projection imported, so a session can be read."""
        return _SessionManager is not None

    @staticmethod
    def unavailable_reason() -> str:
        if _SessionManager is not None:
            return ""
        return (IMPORT_ERROR or "unknown") + \
            " - install the winrt-* packages in requirements.txt"

    # ---- lifecycle --------------------------------------------------------

    def start(self, timeout: float = 10.0) -> bool:
        """Start the reader thread. Returns True once a first read has landed."""
        if not self.is_available():
            self.last_error = self.unavailable_reason()
            return False
        if self._thread is not None:
            return self._running

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="smtc-nowplaying",
                                        daemon=True)
        self._thread.start()
        started = self._ready.wait(timeout)
        if not started:
            self.last_error = (self.last_error
                               or f"no response from Windows within {timeout:.0f}s")
        return started and self._running

    def stop(self) -> None:
        """Ask the reader to finish.

        The loop is not stopped from here. Calling loop.stop() underneath
        run_until_complete raises "Event loop stopped before Future completed"
        and leaves the coroutine half-run; setting the event and letting _main
        return on its own is the clean shutdown.
        """
        self._stop.set()

    # ---- the contract the monitor loop consumes ---------------------------

    def is_authenticated(self) -> bool:
        """No account exists to authenticate. Running is the whole condition."""
        return self._running

    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Latest known track, or None. Never blocks and never does I/O."""
        with self._lock:
            return dict(self._snapshot) if self._snapshot else None

    def get_art(self, key: str):
        """(bytes, content_type) for a cached thumbnail, or (None, None)."""
        with self._lock:
            entry = self._art.get(key)
            if entry is None:
                return None, None
            self._art.move_to_end(key)
            return entry

    # ---- internals --------------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.exception("Windows now-playing reader stopped")
        finally:
            self._running = False
            self._ready.set()
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        manager = await _SessionManager.request_async()
        self._running = True
        self.last_error = None

        while not self._stop.is_set():
            try:
                await self._poll(manager)
            except Exception as e:
                # A player exiting mid-read throws from the projection. Keep
                # the reader alive; the next poll is 1 s away.
                self.last_error = f"{type(e).__name__}: {e}"
                logger.debug(f"SMTC poll failed: {e}")
            self._ready.set()
            await asyncio.sleep(POLL_SECONDS)

    def _pick_session(self, manager):
        """Choose which of several media sessions is the one that matters.

        Windows reports every app holding a session, so a paused YouTube tab
        sits alongside a playing Spotify. Preference order:
          1. the session Windows considers current, when it is actually playing
          2. any playing session, real players before browsers
          3. the current session, whatever its state (so a pause still shows)
        """
        try:
            sessions = list(manager.get_sessions())
        except Exception:
            sessions = []

        current = None
        try:
            current = manager.get_current_session()
        except Exception:
            pass

        def playing(session) -> bool:
            try:
                return int(session.get_playback_info().playback_status) == STATUS_PLAYING
            except Exception:
                return False

        def is_browser(session) -> bool:
            try:
                return (session.source_app_user_model_id or "").lower() in BROWSER_AUMIDS
            except Exception:
                return False

        if current is not None and playing(current):
            return current

        active = [s for s in sessions if playing(s)]
        if active:
            active.sort(key=is_browser)         # real players first
            return active[0]

        return current or (sessions[0] if sessions else None)

    async def _poll(self, manager) -> None:
        session = self._pick_session(manager)
        if session is None:
            self._publish(None)
            return

        aumid = ""
        try:
            aumid = session.source_app_user_model_id or ""
        except Exception:
            pass

        info = session.get_playback_info()
        status = int(info.playback_status)
        props = await session.try_get_media_properties_async()

        title = (props.title or "").strip()
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()

        if not title:
            # A session with no title is a player that is loaded but idle.
            self._publish(None)
            return

        key = local_key(artist, title)

        # Fetch art once per track, on this loop, never on a callback thread.
        with self._lock:
            have_art = key in self._art
            attempts = self._art_attempts.get(key, 0)
        if not have_art and attempts < ART_MAX_ATTEMPTS:
            with self._lock:
                self._art_attempts[key] = attempts + 1
            await self._load_art(key, props)

        with self._lock:
            has_art = key in self._art

        self._publish({
            "track_uri": f"smtc:local:{key}",
            "name": title,
            "artist": artist,
            "album": album,
            "image_url": f"/api/art/{key}" if has_art else "",
            "is_playing": status == STATUS_PLAYING,
            # SMTC reports a Spotify private session like any other playback,
            # so the whole private-session failure mode simply does not exist
            # on this path.
            "is_private_session": False,
            "source_app": aumid,
            "source": "windows",
        })

    async def _load_art(self, key: str, props) -> None:
        try:
            ref = props.thumbnail
            if ref is None:
                return
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 8_000_000:
                return
            reader = _DataReader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            buf = bytearray(size)
            # read_bytes fills a preallocated buffer; it does not take a count.
            reader.read_bytes(buf)
            content_type = getattr(stream, "content_type", "") or "image/png"
        except Exception as e:
            logger.debug(f"Could not read thumbnail: {e}")
            return

        with self._lock:
            self._art[key] = (bytes(buf), content_type)
            while len(self._art) > ART_CACHE_SIZE:
                evicted, _ = self._art.popitem(last=False)
                self._art_attempts.pop(evicted, None)

    def _publish(self, snapshot: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            before = self._snapshot.get("track_uri") if self._snapshot else None
            after = snapshot.get("track_uri") if snapshot else None
            self._snapshot = snapshot
            if before != after:
                self.generation += 1
                if snapshot:
                    logger.info("Windows now-playing: '%s' by %s (%s)",
                                snapshot["name"], snapshot["artist"] or "unknown",
                                snapshot["source_app"] or "unknown app")
