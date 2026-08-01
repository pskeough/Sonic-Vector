"""End-to-end test of the playback -> mix -> Equalizer APO pipeline.

Run with:  python tests/test_playback_pipeline.py

Drives the real monitor thread against a scripted Spotify player, so every
stage under test is the shipping code: the same track-change detection, the
same centroid matcher, the same headroom budget, the same config.txt writer.
Only two things are substituted, and only because they are network calls:
the Spotify client and the Last.fm tag lookup.

What this pins down, all of which are things that have actually broken here:
  * a new track produces a non-flat curve and writes it to config.txt
  * a different track produces a different curve
  * pause and resume update the UI without disturbing the mix
  * stopping playback does not save the app's own output as a preference
  * a curve the listener shaped IS saved, and is recalled on the next play
  * a private session and unsupported content degrade to a status message
    rather than to a wrong EQ

Stdlib only, no test framework, so it runs anywhere the app runs.
"""

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_gui_app as gui                                          # noqa: E402
from src.dsp import render                                         # noqa: E402

REAL_SLEEP = time.sleep


class FastClock:
    """The monitor sleeps 1.5-2.5 s per pass. Keep real time, shrink the wait."""

    def sleep(self, _seconds):
        REAL_SLEEP(0.01)

    def time(self):
        return time.time()


class ScriptedPlayer:
    """A now-playing source with a fixed sequence of player states.

    Implements the same two methods the real sources do, so it plugs into the
    monitor at exactly the seam the Windows media session and Spotify OAuth
    both use.
    """

    def __init__(self, states):
        self._states = list(states)
        self._i = 0
        self.access_token = "fake-token"

    def is_authenticated(self):
        return True

    def get_current_track(self):
        # Hold on the last state so the monitor keeps a steady world once the
        # script runs out, rather than falling off the end into "stopped".
        state = self._states[min(self._i, len(self._states) - 1)]
        self._i += 1
        return state

    def advance_to(self, index):
        self._i = index


def track(name, artist, uri, playing=True, private=False):
    return {
        "name": name, "artist": artist, "album": f"{name} (Single)",
        "duration_ms": 200000, "progress_ms": 1000, "is_playing": playing,
        "is_private_session": private, "image_url": "",
        "track_uri": uri, "shuffle_state": False, "repeat_state": "off",
    }


class StubLastFm:
    """Deterministic tags, so the assertions are about the mixer, not Last.fm."""

    TAGS = {
        "Bass Cannon": ["punchy", "heavy bass", "trap", "hip-hop"],
        "Candlelight": ["warm", "acoustic", "folk", "mellow"],
    }

    def get_track_tags(self, artist, track_name):
        return list(self.TAGS.get(track_name, ["rock"]))


def gains(state):
    return {k: round(v, 3) for k, v in state["eq"].items() if k.endswith("gain")}


def is_flat(state):
    return all(abs(v) < 0.01 for v in gains(state).values())


def wait_for(predicate, timeout=6.0, what=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        REAL_SLEEP(0.02)
    return False


def config_gains(path):
    import re
    text = path.read_text(encoding="utf-8")
    return [float(g) for g in re.findall(r"Gain\s+(-?\d+\.?\d*)\s*dB", text)]


def run(failures):
    tmp = Path(tempfile.mkdtemp(prefix="sv-pipeline-"))
    cfg = tmp / "config.txt"

    # Point every side effect at the temp directory: the real Equalizer APO
    # config and the real songs.db must not be touched by a test run.
    gui.apo_path = cfg
    gui.PROJECT_ROOT = tmp
    (tmp / "data").mkdir(parents=True, exist_ok=True)

    gui.lastfm = StubLastFm()
    gui.spotify_client = None
    gui.predictor = gui.SemanticEQPredictor(db_path=str(ROOT / "data" / "test_library.db"))
    gui.time = FastClock()

    with gui.state_lock:
        gui.app_state["ai_engine"] = "similarity"
        gui.app_state["sound_style"] = "balanced"
        gui.app_state["mode"] = "auto"
        gui.app_state["spotify_configured"] = True
        gui.app_state["current_track"] = gui.idle_track("No Track Loaded", "", "")

    script = [
        track("Bass Cannon", "Test Artist", "spotify:track:AAAAAAAAAAAAAAAAAAAAAA"),
    ]
    player = ScriptedPlayer(script)
    # now_playing is the seam the monitor reads; spotify_oauth is only the
    # Spotify implementation of it and is deliberately absent here, so this
    # exercises the same path the Windows media session takes.
    gui.now_playing = player
    gui.spotify_oauth = None

    gui.active_monitoring = True
    thread = threading.Thread(target=gui.monitor_spotify_playback, daemon=True)
    thread.start()

    def check(name, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            failures.append(f"  {name}{': ' + detail if detail else ''}")

    try:
        # 1. A new track is detected, tagged, matched and applied.
        ok = wait_for(lambda: gui.app_state["current_track"].get("track_id")
                      == "AAAAAAAAAAAAAAAAAAAAAA")
        check("new track is detected", ok,
              f"still {gui.app_state['current_track']['track_name']!r}")

        # The monitor publishes current_track under the state lock and only
        # then calls commit_state(), so track_id appears before the filter is
        # written. Sampling here without waiting reads a half-applied world.
        wrote = wait_for(lambda: cfg.exists() and gui.app_state["output"]["response"])
        check("the mix is committed after the track is published", wrote)

        with gui.state_lock:
            snapshot = {k: dict(v) if isinstance(v, dict) else v
                        for k, v in gui.app_state.items()}
        first_gains = gains(snapshot)

        check("track metadata is published",
              snapshot["current_track"]["track_name"] == "Bass Cannon"
              and snapshot["current_track"]["source"] == "spotify"
              and snapshot["current_track"]["placeholder"] is False)
        check("tags are harvested",
              "punchy" in snapshot["current_track"]["tags"])
        check("profile weights are produced",
              any(w > 0 for w in snapshot["current_track"]["weights"].values()),
              str(snapshot["current_track"]["weights"]))
        check("a non-flat curve is produced", not is_flat(snapshot),
              str(first_gains))
        check("bass-tagged track lifts the low shelf",
              first_gains["low_shelf_gain"] > 0.5, str(first_gains))
        check("the curve reaches Equalizer APO",
              cfg.exists() and any(abs(g) > 0.1 for g in config_gains(cfg)))
        # headroom_peak_db is measured on the budgeted cascade with the preamp
        # folded in, which is the quantity the guarantee is about. Recomputing
        # it from the raw composite would skip apply_headroom_budget and test
        # something the app never emits.
        check("the emitted curve respects the headroom budget",
              snapshot["output"]["headroom_peak_db"] <= 0.01,
              f"peak {snapshot['output']['headroom_peak_db']} dBFS")

        # 2. Pause is reflected without disturbing the mix.
        player._states.append(track("Bass Cannon", "Test Artist",
                                    "spotify:track:AAAAAAAAAAAAAAAAAAAAAA",
                                    playing=False))
        ok = wait_for(lambda: gui.app_state["current_track"].get("is_playing") is False)
        check("pause is reflected in the UI", ok)
        check("pausing does not change the curve", gains(gui.app_state) == first_gains,
              f"{gains(gui.app_state)} != {first_gains}")

        # 3. A different track produces a different curve.
        player._states.append(track("Candlelight", "Test Artist",
                                    "spotify:track:BBBBBBBBBBBBBBBBBBBBBB"))
        ok = wait_for(lambda: gui.app_state["current_track"].get("track_id")
                      == "BBBBBBBBBBBBBBBBBBBBBB")
        check("track change is detected", ok)
        second_gains = gains(gui.app_state)
        check("a different track gets a different curve",
              second_gains != first_gains, f"both {second_gains}")
        check("warm-tagged track is not brighter than the bass track",
              second_gains["high_shelf_gain"] <= first_gains["high_shelf_gain"] + 0.01,
              f"warm {second_gains['high_shelf_gain']} vs punchy "
              f"{first_gains['high_shelf_gain']}")

        # 4. Auto mode must not store the app's own output as a preference.
        songs_db = tmp / "data" / "songs.db"
        player._states.append(None)          # playback stopped
        ok = wait_for(lambda: gui._is_placeholder(gui.app_state["current_track"]))
        check("stopping playback shows a status message", ok,
              gui.app_state["current_track"]["track_name"])
        check("an unedited auto mix is NOT saved as a preference",
              not songs_db.exists(),
              "songs.db was written for a curve the listener never touched")

        # 5. A curve the listener shaped IS saved, and recalled next time.
        player._states.append(track("Candlelight", "Test Artist",
                                    "spotify:track:BBBBBBBBBBBBBBBBBBBBBB"))
        ok = wait_for(lambda: gui.app_state["current_track"].get("track_id")
                      == "BBBBBBBBBBBBBBBBBBBBBB")
        check("playback resumes onto the same track", ok)

        with gui.state_lock:
            gui.app_state["eq"]["second_band_gain"] = -4.25
        gui.mark_user_edit()
        gui.commit_state()

        player._states.append(track("Bass Cannon", "Test Artist",
                                    "spotify:track:AAAAAAAAAAAAAAAAAAAAAA"))
        ok = wait_for(lambda: gui.app_state["current_track"].get("track_id")
                      == "AAAAAAAAAAAAAAAAAAAAAA")
        check("switching away from an edited track works", ok)

        recalled = gui.load_track_eq_from_db("BBBBBBBBBBBBBBBBBBBBBB")
        check("a listener-edited curve IS saved",
              recalled is not None
              and abs(recalled["eq"]["second_band_gain"] - (-4.25)) < 0.01,
              str(recalled))

        # 6. Degenerate player states do not produce a wrong EQ.
        player._states.append(track("Anything", "Anyone",
                                    "spotify:track:CCCCCCCCCCCCCCCCCCCCCC",
                                    private=True))
        ok = wait_for(lambda: "private session"
                      in gui.app_state["current_track"]["track_name"].lower())
        check("private session is reported, not mixed", ok,
              gui.app_state["current_track"]["track_name"])

        player._states.append({**track("Unknown", "", ""), "track_uri": ""})
        ok = wait_for(lambda: gui.app_state["current_track"]["track_name"]
                      == "Playback Not Supported")
        check("unsupported content is reported, not mixed", ok,
              gui.app_state["current_track"]["track_name"])

    finally:
        gui.active_monitoring = False
        REAL_SLEEP(0.1)
        gui.time = time
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("PLAYBACK PIPELINE - detect, mix, write, remember")
    print("-" * 72)
    failures = []
    run(failures)
    print("-" * 72)

    if failures:
        print(f"FAIL: {len(failures)} violation(s):")
        for f in failures:
            print(f)
        return 1

    print("PASS: tracks are detected, mixed, written to APO, and only the "
          "listener's own edits are remembered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
