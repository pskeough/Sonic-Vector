"""Phase 1 exit criterion: killing the app by any means must leave EQ flat.

Run with:  python tests/test_phase1_exit.py

Spawns a child that loads the real app module, points it at a scratch config
file instead of the live Equalizer APO one, writes a deliberately extreme
curve, and then dies in a specific way. The parent then reads the scratch file
and asserts the curve is gone.

Before this phase the only cleanup hook was the `finally` of app.run(), which
does not run when the launcher's console window is closed, so a curve written
for one song stayed applied to every sound on the machine indefinitely.
"""

import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHILD = textwrap.dedent("""
    import sys, os, time, signal
    from pathlib import Path
    sys.path.insert(0, r"{root}")
    import logging; logging.disable(logging.CRITICAL)
    import web_gui_app as gui

    gui.apo_path = Path(r"{cfg}")
    gui._install_exit_handlers()

    # A curve nobody could mistake for flat.
    with gui.state_lock:
        gui.app_state["eq"].update(
            low_shelf_gain=6.0, first_band_gain=-5.0, second_band_gain=5.0,
            third_band_gain=-4.0, high_shelf_gain=6.0)
        gui.app_state["mix"].update(bass_boost=8.0, vocal_clarity=6.0, airiness=6.0)
    gui.commit_state()

    Path(r"{ready}").write_text("ready", encoding="utf-8")

    # Wait for the parent to confirm it has seen the applied curve, so the
    # handler cannot win the race and make the test look like nothing applied.
    go = Path(r"{go}")
    for _ in range(300):
        if go.exists():
            break
        time.sleep(0.1)

    mode = "{mode}"
    if mode == "clean_exit":
        raise SystemExit(0)
    if mode == "uncaught_exception":
        raise RuntimeError("simulated crash")
    time.sleep(30)          # ctrl_break and hard_kill are delivered externally
""")


def gains_in(path: Path):
    """Every Gain value and the Preamp from a config.txt."""
    text = path.read_text(encoding="utf-8")
    gains = [float(g) for g in re.findall(r"Gain\s+(-?\d+\.?\d*)\s*dB", text)]
    pre = re.search(r"Preamp:\s*(-?\d+\.?\d*)\s*dB", text)
    return gains, (float(pre.group(1)) if pre else None)


def run_case(mode: str):
    tmp = Path(tempfile.mkdtemp(prefix="sv-exit-"))
    cfg, ready, go = tmp / "config.txt", tmp / "ready", tmp / "go"
    src = CHILD.format(root=str(ROOT), cfg=str(cfg), ready=str(ready),
                       go=str(go), mode=mode)
    script = tmp / "child.py"
    script.write_text(src, encoding="utf-8")

    # A new process group is required to deliver CTRL_BREAK_EVENT, which is the
    # closest testable analogue of a user pressing Ctrl+C or closing the
    # console. Note that os.kill(pid, SIGINT) on Windows is NOT a signal at
    # all: it calls TerminateProcess, so no handler of any kind can run.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen([sys.executable, str(script)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags)
    # 30 s, not 10. The child imports the whole app (Flask, google-genai, the
    # predictor), which is ~3 s idle but several times that on a loaded machine,
    # so the old budget reported all four exit paths as broken when the only
    # real problem was that Python had not finished starting.
    for _ in range(300):
        if ready.exists():
            break
        time.sleep(0.1)
    else:
        proc.kill()
        return mode, "FAIL", "child never became ready"

    applied, _ = gains_in(cfg)
    if not any(abs(g) > 0.5 for g in applied):
        proc.kill()
        return mode, "FAIL", "child never applied a non-flat curve to begin with"

    go.write_text("go", encoding="utf-8")

    if mode == "ctrl_break":
        import signal as _s
        os.kill(proc.pid, _s.CTRL_BREAK_EVENT)
    elif mode == "hard_kill":
        proc.kill()                       # TerminateProcess: no handler runs

    try:
        proc.wait(timeout=25)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        return mode, "FAIL", "child did not exit"
    time.sleep(0.5)                       # let handlers finish writing

    gains, preamp = gains_in(cfg)
    flat = all(abs(g) < 0.01 for g in gains) and (preamp is not None
                                                  and abs(preamp) < 0.01)
    detail = f"gains={[round(g, 2) for g in gains]} preamp={preamp}"
    return mode, ("PASS" if flat else "FAIL"), detail


def main() -> int:
    cases = ["clean_exit", "uncaught_exception", "ctrl_break", "hard_kill"]
    results = [run_case(mode) for mode in cases]

    print(f"{'exit path':<22}{'result':<8}detail")
    print("-" * 78)
    recoverable_failed = 0
    for mode, status, detail in results:
        print(f"{mode:<22}{status:<8}{detail[:44]}")
        if status == "FAIL" and mode != "hard_kill":
            recoverable_failed += 1

    hard = next(r for r in results if r[0] == "hard_kill")
    print()
    if hard[1] == "FAIL":
        print("NOTE: hard_kill leaves the curve applied. This is expected and "
              "unfixable in-process;\n      SIGKILL runs no handler. The "
              "startup flat-write is the mitigation: the next\n      launch "
              "clears it. A tray app or a watchdog service would close the gap.")
    if recoverable_failed:
        print(f"\nFAIL: {recoverable_failed} recoverable exit path(s) left EQ applied.")
        return 1
    print("\nPASS: every recoverable exit path leaves the system EQ flat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
