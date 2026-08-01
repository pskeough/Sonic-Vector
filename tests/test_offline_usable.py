"""The console must be fully usable with no Spotify account.

Run with:  python tests/test_offline_usable.py

The claim under test is the one the app got wrong: with no Spotify credentials
and no linked account, a track loaded through the search box has to stay loaded.
The monitor thread used to overwrite current_track every 1.5 s with a status
message, so a searched track survived exactly one poll and then took the tags,
the profile weights, Remix, Analyze and the rating buttons down with it.

Also covers the config writer that backs the in-app API-key panel: it must
update the values it is given and leave every comment in config.yaml intact,
because those comments are the only setup instructions a first-time user gets.

Stdlib only, no test framework, so it runs anywhere the app runs.
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils import is_placeholder, set_section_values          # noqa: E402
import web_gui_app as gui                                         # noqa: E402


def check(failures, name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failures.append(f"  {name}{': ' + detail if detail else ''}")


def test_search_survives_the_monitor(failures):
    """A searched track outranks any Spotify status message."""
    searched = {
        "track_id": "abc123", "track_name": "Creep", "artist_name": "Radiohead",
        "album_name": "Pablo Honey", "album_art": "", "genres": ["alt rock"],
        "tags": ["alternative"], "weights": {"presence": 1.0},
        "source": "search", "placeholder": False, "mixing_reason": "",
    }

    with gui.state_lock:
        gui.app_state["current_track"] = dict(searched)
        # Every idle path the monitor can take must leave it alone.
        for headline in ("Spotify Not Connected", "No Active Spotify Playback",
                         "Playback Not Supported", "Spotify private session active"):
            gui._set_idle_track_locked(gui.idle_track(headline, "", ""))
        held = dict(gui.app_state["current_track"])

    check(failures, "search result survives every monitor idle path",
          held["track_name"] == "Creep" and held["source"] == "search",
          f"became {held['track_name']!r}")

    # A track the monitor itself put there is still replaceable, otherwise the
    # panel would freeze on the first status message it ever showed.
    with gui.state_lock:
        gui.app_state["current_track"] = gui.idle_track("No Track Loaded", "", "")
        gui._set_idle_track_locked(gui.idle_track("Playback Not Supported", "", ""))
        replaced = gui.app_state["current_track"]["track_name"]

    check(failures, "monitor may still replace its own placeholder",
          replaced == "Playback Not Supported", f"stuck on {replaced!r}")


def test_placeholders_are_flagged_not_matched(failures):
    """Guards must key off the flag, not off a list of headline strings."""
    check(failures, "flagged placeholder is a placeholder",
          gui._is_placeholder(gui.idle_track("Anything At All", "", "")))
    check(failures, "a real track is not a placeholder",
          not gui._is_placeholder({"track_name": "Creep", "placeholder": False}))
    # A dict written by an older build carries no flag, only the headline.
    check(failures, "legacy headline still recognised",
          gui._is_placeholder({"track_name": "Spotify Account Not Connected"}))


def test_pipeline_tells_the_truth_without_spotify(failures):
    """The signal path used to say "Waiting for stream..." forever."""
    with gui.state_lock:
        gui.app_state["spotify_configured"] = False
        gui.app_state["current_track"] = gui.idle_track("No Track Loaded", "", "")
        gui._refresh_pipeline_locked(auth_ok=False)
        opted_out = dict(gui.app_state["pipeline_status"])

        gui.app_state["spotify_configured"] = True
        gui._refresh_pipeline_locked(auth_ok=False)
        unlinked = dict(gui.app_state["pipeline_status"])

    check(failures, "opting out does not read as a failure",
          "not configured" in opted_out["spotify"].lower(),
          opted_out["spotify"])
    check(failures, "configured-but-unlinked is distinguishable",
          unlinked["spotify"] != opted_out["spotify"],
          f"both say {unlinked['spotify']!r}")
    check(failures, "output stage is reported even with no Spotify",
          opted_out["apo_writer"] != "Not active", opted_out["apo_writer"])


def test_config_writer_keeps_comments(failures):
    """The in-app key panel must not strip config.yaml's instructions."""
    original = (
        "# Sonic Vector Configuration File\n"
        "\n"
        "# Spotify API Credentials\n"
        "# Create an app at https://developer.spotify.com/dashboard\n"
        "spotify:\n"
        '  client_id: "your_spotify_client_id_here"\n'
        '  client_secret: "your_spotify_client_secret_here"\n'
        "\n"
        "# Application Settings\n"
        "app:\n"
        "  polling_interval: 1.5  # seconds between Spotify checks\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        path.write_text(original, encoding="utf-8")

        set_section_values(path, "spotify", {
            "client_id": "abc", "client_secret": "def",
            "redirect_uri": "http://127.0.0.1:8888/callback",
        })
        set_section_values(path, "app", {"polling_interval": "2.0"})
        set_section_values(path, "lastfm", {"api_key": "xyz"})
        text = path.read_text(encoding="utf-8")

        import yaml
        parsed = yaml.safe_load(text)

        check(failures, "values are updated",
              parsed["spotify"]["client_id"] == "abc"
              and parsed["spotify"]["client_secret"] == "def",
              str(parsed["spotify"]))
        check(failures, "a key absent from the section is added",
              parsed["spotify"]["redirect_uri"] == "http://127.0.0.1:8888/callback")
        check(failures, "a section absent from the file is added",
              parsed["lastfm"]["api_key"] == "xyz")
        check(failures, "comments survive",
              text.count("#") == original.count("#"),
              f"{original.count('#')} before, {text.count('#')} after")
        check(failures, "a trailing inline comment survives",
              "# seconds between Spotify checks" in text)
        check(failures, "untouched sections keep their shape",
              "# Create an app at https://developer.spotify.com/dashboard" in text)


def test_single_instance_guard(failures):
    """Detecting a running instance is what stops a double-launch flattening it.

    Two copies share one Equalizer APO config, so a second launch used to write
    flat over the running instance's curve, fail to bind, and then write flat
    again on the way out. main() now refuses early, and this is the check it
    refuses on, so a regression here is silent and costs the listener their EQ.
    """
    import socket
    from src import desktop

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        check(failures, "a bound port is detected as running",
              desktop.server_is_running(port=port))

    # The socket is closed now, so the same port must read as free.
    check(failures, "a free port is detected as not running",
          not desktop.server_is_running(port=port))


def test_placeholder_detection(failures):
    check(failures, "example values are placeholders",
          is_placeholder("your_spotify_client_id_here") and is_placeholder("")
          and is_placeholder(None))
    check(failures, "a real credential is not a placeholder",
          not is_placeholder("79d3622dd85b4c71ae98dd7e3f8588b8"))


def main() -> int:
    print("OFFLINE USABILITY - the console must work with no Spotify account")
    print("-" * 72)

    failures = []
    test_search_survives_the_monitor(failures)
    test_placeholders_are_flagged_not_matched(failures)
    test_pipeline_tells_the_truth_without_spotify(failures)
    test_config_writer_keeps_comments(failures)
    test_single_instance_guard(failures)
    test_placeholder_detection(failures)

    print("-" * 72)
    if failures:
        print(f"FAIL: {len(failures)} violation(s):")
        for f in failures:
            print(f)
        return 1

    print("PASS: search outranks status messages, placeholders are flagged, "
          "the signal path is honest, and config.yaml keeps its comments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
