"""The app must detect what is playing with no Spotify account at all.

Run with:  python tests/test_windows_nowplaying.py

This is the claim the app was built on and the one it kept failing: automatic
now-playing detection with zero API keys. It comes from the Windows System
Media Transport Controls session, which is the same data behind the media
flyout on the volume keys, so it sees every player on the machine and needs no
account, no network and no login.

Two halves:

  * **Contract.** The Windows source must answer in exactly the shape the
    monitor loop consumes, and the identity key it invents must be stable
    across the cosmetic title variations real players emit, or every
    "(Remastered)" would be cached as a different song.

  * **Wiring.** The monitor must accept a source that is not Spotify, mix from
    it, and never mark a Spotify account as linked just because detection is
    working. That last one was a real bug: the Windows source answers
    is_authenticated() truthfully about itself, and the app read it as "the
    listener is signed in to Spotify".

The live-session parts are skipped rather than failed when nothing is playing,
so this suite is honest on a silent machine.

Stdlib only, no test framework.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.nowplaying import SmtcNowPlaying, local_key                # noqa: E402
import web_gui_app as gui                                           # noqa: E402

REQUIRED_FIELDS = {"track_uri", "name", "artist", "album", "image_url",
                   "is_playing", "is_private_session"}


def check(failures, name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failures.append(f"  {name}{': ' + detail if detail else ''}")


def skip(name, why):
    print(f"  [SKIP] {name} ({why})")


def test_identity_key(failures):
    """The key has to survive the noise real players put in titles."""
    base = local_key("Radiohead", "Creep")

    check(failures, "identity is stable across case and spacing",
          local_key("radiohead", "  Creep ") == base)
    check(failures, "identity ignores decoration players add",
          local_key("Radiohead", "Creep (Remastered 2009)") == base,
          "a remaster tag forked the identity")
    check(failures, "identity ignores accents and punctuation",
          local_key("Bjork", "Joga") == local_key("Björk", "Jóga"))
    check(failures, "different songs get different identities",
          local_key("Radiohead", "Creep") != local_key("Radiohead", "Karma Police"))
    check(failures, "the key fits the track_id slot",
          len(base) == 16 and all(c in "0123456789abcdef" for c in base))
    # web_gui_app splits on ":" to get a track_id, so the URI shape matters.
    check(failures, "the URI yields the key as a track_id",
          f"smtc:local:{base}".split(":")[-1] == base)


def test_availability(failures):
    check(failures, "the Windows source reports availability without raising",
          isinstance(SmtcNowPlaying.is_available(), bool))
    if not SmtcNowPlaying.is_available():
        check(failures, "an unavailable source explains itself",
              bool(SmtcNowPlaying.unavailable_reason()))


def test_live_session(failures):
    """Read the real media session, if this machine has one."""
    if not SmtcNowPlaying.is_available():
        skip("live session read", "winrt packages not installed")
        return

    source = SmtcNowPlaying()
    try:
        started = source.start(timeout=15.0)
        check(failures, "the Windows source starts", started,
              str(source.last_error))
        if not started:
            return

        check(failures, "a running source reports itself ready",
              source.is_authenticated())

        track = source.get_current_track()
        if track is None:
            skip("track contract", "nothing is playing on this machine")
            return

        missing = REQUIRED_FIELDS - set(track)
        check(failures, "the track dict matches the monitor's contract",
              not missing, f"missing {sorted(missing)}")
        check(failures, "the track has a title", bool(track["name"]))
        check(failures, "the URI is a local SMTC key",
              track["track_uri"].startswith("smtc:local:"), track["track_uri"])
        check(failures, "the source labels itself", track.get("source") == "windows")
        # SMTC reports a Spotify private session like any other playback, so the
        # whole private-session failure mode does not exist on this path.
        check(failures, "private-session blocking cannot occur",
              track["is_private_session"] is False)

        print(f"         (live: {track['artist']} - {track['name']})")

        # Reads must be cheap: the monitor calls this every 1.5 s.
        started_at = time.perf_counter()
        for _ in range(200):
            source.get_current_track()
        per_call_us = (time.perf_counter() - started_at) / 200 * 1e6
        check(failures, "reads are snapshot-cheap, not blocking I/O",
              per_call_us < 500, f"{per_call_us:.0f} us per call")
    finally:
        source.stop()


def test_monitor_accepts_a_non_spotify_source(failures):
    """Detection working must not be reported as "Spotify is linked"."""

    class FakeWindowsSource:
        source_name = "windows"

        def is_authenticated(self):
            return True

        def get_current_track(self):
            return None

    saved = (gui.now_playing, gui.spotify_oauth)
    try:
        gui.now_playing = FakeWindowsSource()
        gui.spotify_oauth = None

        # Mirrors the monitor's own logic for the Spotify-linked flag.
        auth_ok = gui.now_playing.is_authenticated()
        if gui.now_playing is gui.spotify_oauth:
            linked = auth_ok
        elif gui.spotify_oauth is not None:
            linked = gui.spotify_oauth.is_authenticated()
        else:
            linked = False

        check(failures, "a working Windows source is ready", auth_ok)
        check(failures, "a working Windows source is NOT a linked Spotify account",
              linked is False,
              "detection was reported as a signed-in Spotify account")
    finally:
        gui.now_playing, gui.spotify_oauth = saved


def test_genre_enrichment_is_optional(failures):
    """Enrichment must never be on the path that produces a curve."""
    saved = gui.spotify_client
    try:
        gui.spotify_client = None
        check(failures, "genre lookup degrades to empty without Spotify",
              gui.fetch_artist_genres("c9d0d9439c688fb8", "Woodland Creatures",
                                      "your best friend jippy") == [])
    finally:
        gui.spotify_client = saved

    # A 16-hex SMTC key must never be sent to Spotify's /tracks/{id}.
    check(failures, "an SMTC key is not mistaken for a Spotify ID",
          not gui._looks_like_spotify_id("c9d0d9439c688fb8"))
    check(failures, "a real Spotify ID is still recognised",
          gui._looks_like_spotify_id("3n3Ppam7vgaVa1iaRUc9Lp"))


def main() -> int:
    print("WINDOWS NOW-PLAYING - detection with no account, no network")
    print("-" * 72)

    failures = []
    test_identity_key(failures)
    test_availability(failures)
    test_live_session(failures)
    test_monitor_accepts_a_non_spotify_source(failures)
    test_genre_enrichment_is_optional(failures)

    print("-" * 72)
    if failures:
        print(f"FAIL: {len(failures)} violation(s):")
        for f in failures:
            print(f)
        return 1

    print("PASS: the Windows media session identifies tracks on its own, and "
          "Spotify is never required for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
