"""Pre-flight check: report what will and will not work before the server starts.

Run by launch_gui.bat, and useful on its own:

    python tools/preflight.py

Exit codes:
    0  safe to start (possibly with reduced functionality)
    1  refuse to start (the DSP renderer self-test failed, so any curve the app
       emitted could clip without the preamp knowing about it)

Output is deliberately plain ASCII. The Windows console is cp1252 by default
and printing a check mark or an emoji there raises UnicodeEncodeError, which
would turn an informational script into a crash.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LINE = "-" * 76


def say(status: str, text: str) -> None:
    print(f"  [{status:^7}] {text}")


def check_python() -> bool:
    ok = sys.version_info >= (3, 9)
    say("OK" if ok else "FAIL",
        f"Python {sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro}" + ("" if ok else "  (3.9 or newer required)"))
    return ok


def check_deps() -> bool:
    missing = []
    for module, package in [("flask", "flask"), ("yaml", "pyyaml"),
                            ("requests", "requests")]:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        say("MISSING", "Python packages: " + ", ".join(missing))
        say("ACTION", "pip install -r requirements.txt")
        return False
    say("OK", "Python dependencies present")
    return True


def check_renderer() -> bool:
    from src.dsp import render
    report, ok = render.run_selftests()
    if ok:
        say("OK", "DSP renderer self-test (8 tests) passed")
    else:
        say("FAIL", "DSP renderer self-test FAILED - refusing to start")
        print(report)
    return ok


def check_centroids() -> bool:
    db = ROOT / "data" / "test_library.db"
    if not db.exists():
        say("MISSING", "data/test_library.db not built")
        say("ACTION", "python preprocess_safe.py --synthetic")
        return False
    try:
        import sqlite3
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT descriptor, sample_count FROM semantic_eq_centroids"
            ).fetchall()
    except Exception as e:
        say("WARN", f"could not read centroid database: {e}")
        return False
    if not rows:
        say("WARN", "centroid database is empty")
        return False
    say("OK", f"{len(rows)} EQ profiles loaded "
              f"({', '.join(r[0] for r in sorted(rows))})")
    return True


def check_apo() -> bool:
    from src.dsp import apo
    status = apo.probe_apo_status()
    state = status["state"]
    if state == "apo_ready":
        say("OK", status["detail"])
        return True
    if state == "apo_not_installed":
        say("BLOCKED", "Equalizer APO is not installed.")
        say("ACTION", "https://sourceforge.net/projects/equalizerapo/")
    elif state == "apo_no_active_endpoint":
        active = ", ".join(e["name"] for e in status["endpoints"]) or "none"
        say("BLOCKED", "Equalizer APO is installed but is NOT enabled on the "
                       "output you are using.")
        say("", f"Active outputs: {active}")
        say("ACTION", "Run Equalizer APO's Configurator as administrator, tick "
                      "the output you")
        say("", "listen through, then restart Windows Audio.")
        say("", "Until then the app runs, but nothing it does is audible.")
    else:
        say("WARN", "Could not determine Equalizer APO status.")
    return False


def check_now_playing() -> None:
    """Can the app tell what is playing without anyone signing in to anything?"""
    from src.nowplaying import SmtcNowPlaying

    if SmtcNowPlaying.is_available():
        say("OK", "Windows media session available (automatic now-playing, "
                  "no account)")
        say("", "Detects any player on this PC: Spotify's app, a browser,")
        say("", "VLC, games. Nothing to sign in to.")
    else:
        say("OPT-OUT", "Windows media session unavailable. Automatic detection is off.")
        say("", SmtcNowPlaying.unavailable_reason())
        say("ACTION", "pip install -r requirements.txt")


def check_optional_services() -> None:
    """Spotify and Last.fm are enrichment, not requirements."""
    import yaml
    from src.utils import is_placeholder

    cfg_path = ROOT / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            say("WARN", f"config.yaml could not be parsed: {e}")

    def configured(section: str, key: str) -> bool:
        return not is_placeholder((cfg.get(section) or {}).get(key))

    if configured("spotify", "client_id") and configured("spotify", "client_secret"):
        say("OK", "Spotify credentials found (genre enrichment, optional sync)")
    else:
        say("OPT-OUT", "No Spotify credentials. Genre enrichment is off; tags")
        say("", "still come from Last.fm and detection from Windows.")
        say("", "To add them, click API KEYS in the dashboard. No restart.")

    if configured("lastfm", "api_key"):
        say("OK", "Last.fm key found (tag lookups available)")
    else:
        say("OPT-OUT", "No Last.fm key. Tag lookups are off, so tag-matched "
                       "profiles stay flat.")


def main() -> int:
    print(LINE)
    print("  Sonic Vector - pre-flight")
    print(LINE)

    hard_ok = True
    hard_ok &= check_python()
    deps_ok = check_deps()
    hard_ok &= deps_ok

    if deps_ok:
        # These import project modules, so they need the dependencies present.
        if not check_renderer():
            print(LINE)
            return 1
        check_centroids()
        print()
        check_apo()
        print()
        check_now_playing()
        print()
        check_optional_services()

    print(LINE)
    if not hard_ok:
        print("  Cannot start. Resolve the items marked FAIL or MISSING above.")
        print(LINE)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
