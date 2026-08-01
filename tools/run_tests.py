"""Run every test suite in this project and report one verdict.

Run with:  python tools/run_tests.py
           python tools/run_tests.py -v      (show each suite's own output)

There is no test framework here on purpose: each suite is a plain script that
returns 0 or 1 and prints its own reasoning, so they run anywhere the app runs
and need nothing installed. This is just the thing that runs all of them.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SUITES = [
    ("headroom safety", "tests/test_phase1_headroom.py",
     "no tag/style combination can exceed 0 dBFS"),
    ("exit paths", "tests/test_phase1_exit.py",
     "every recoverable exit leaves the system EQ flat"),
    ("offline usability", "tests/test_offline_usable.py",
     "the console works with no Spotify account"),
    ("windows now-playing", "tests/test_windows_nowplaying.py",
     "tracks are detected with no account and no network"),
    ("playback pipeline", "tests/test_playback_pipeline.py",
     "tracks are detected, mixed, written, and remembered"),
    ("DSP renderer", "src/dsp/render.py",
     "the measuring instrument agrees with the filter maths"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show the full output of each suite")
    args = parser.parse_args()

    print("=" * 76)
    print("  Sonic Vector - full test run")
    print("=" * 76)

    failed = []
    for name, path, claim in SUITES:
        script = ROOT / path
        if not script.exists():
            print(f"  [ SKIP ] {name:<20} {path} is missing")
            continue

        started = time.time()
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                              capture_output=not args.verbose, text=True)
        elapsed = time.time() - started
        ok = proc.returncode == 0
        print(f"  [{'  OK  ' if ok else ' FAIL '}] {name:<20} {claim}  ({elapsed:.1f}s)")

        if not ok:
            failed.append(name)
            if not args.verbose:
                tail = (proc.stdout or "").strip().splitlines()[-12:]
                for line in tail:
                    print(f"           {line}")

    print("=" * 76)
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
        print("  Re-run with -v for the full output.")
        return 1
    print("  All suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
