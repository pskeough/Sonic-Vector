"""Create Start Menu and Desktop shortcuts for Sonic Vector.

Run with:  python tools/install_shortcut.py
Remove:    python tools/install_shortcut.py --uninstall

Why a Start Menu entry and not a direct taskbar pin: Windows 11 removed
programmatic taskbar pinning for ordinary applications. There is no supported
API a script can call to put an arbitrary shortcut on the taskbar, and the
LayoutModification.xml route only applies at first sign-in for a new profile.
What *does* work, and works reliably, is pinning from the Start Menu, so this
script puts a proper entry there and tells you the one click that finishes it.

The shortcut points at tools/launch_app.pyw through pythonw.exe, so clicking it
opens the dashboard with no console window attached.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.desktop import APP_ID, APP_NAME                          # noqa: E402

SHORTCUT_NAME = f"{APP_NAME}.lnk"
DESCRIPTION = "Adaptive EQ and real-time mastering console"


def pythonw_path() -> Path:
    """The windowed interpreter next to whichever python is running us."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; is this Windows?")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop_dir() -> Path:
    """The real Desktop, which is not always %USERPROFILE%\\Desktop.

    OneDrive's "back up your folders" moves it, and hardcoding the classic path
    silently drops the shortcut somewhere the user will never look.
    """
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_DESKTOPDIRECTORY = 0x0010, SHGFP_TYPE_CURRENT = 0
        if ctypes.windll.shell32.SHGetFolderPathW(0, 0x0010, 0, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Desktop"


def write_shortcut(path: Path, target: Path, args: str, icon: Path,
                   workdir: Path) -> None:
    from win32com.client import Dispatch

    shell = Dispatch("WScript.Shell")
    link = shell.CreateShortCut(str(path))
    link.TargetPath = str(target)
    link.Arguments = args
    link.WorkingDirectory = str(workdir)
    link.IconLocation = f"{icon},0"
    link.Description = DESCRIPTION
    link.WindowStyle = 1                       # normal
    link.save()

    _stamp_app_id(path)


def _stamp_app_id(path: Path) -> None:
    """Write System.AppUserModel.ID onto the .lnk.

    Without it, Windows derives taskbar identity from the target executable, so
    every shortcut that launches pythonw.exe collides into one button labelled
    for the interpreter. Best-effort: the shortcut is perfectly usable if the
    property store is unavailable.
    """
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32comext.shell import shellcon      # GPS_* live here, not pscon

        store = propsys.SHGetPropertyStoreFromParsingName(
            str(path), None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID,
                       propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
        store.Commit()
    except Exception as e:
        print(f"  note: could not stamp AppUserModelID ({e}). Shortcut still works.")


def install() -> int:
    icon = ROOT / "static" / "favicon.ico"
    launcher = ROOT / "tools" / "launch_app.pyw"

    if not icon.exists():
        print("  [FAIL] static/favicon.ico is missing. Run: python tools/make_icon.py")
        return 1
    if not launcher.exists():
        print("  [FAIL] tools/launch_app.pyw is missing.")
        return 1

    target = pythonw_path()
    if target.name != "pythonw.exe":
        print("  [WARN] pythonw.exe was not found next to this interpreter, so "
              "a console window will appear when the shortcut is used.")

    made = []
    for folder in (start_menu_dir(), desktop_dir()):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / SHORTCUT_NAME
            write_shortcut(path, target, f'"{launcher}"', icon, ROOT)
            made.append(path)
            print(f"  [ OK  ] {path}")
        except Exception as e:
            print(f"  [FAIL] {folder}: {e}")

    if not made:
        return 1

    print()
    print("  To pin it to the taskbar:")
    print("    Open Start, type 'Sonic Vector', right-click the result,")
    print("    then choose 'Pin to taskbar'.")
    print()
    print("  Windows 11 has no supported way for a script to pin on your")
    print("  behalf, so that click is yours. Everything else is done.")
    return 0


def uninstall() -> int:
    removed = 0
    for folder in (start_menu_dir(), desktop_dir()):
        path = folder / SHORTCUT_NAME
        if path.exists():
            try:
                path.unlink()
                print(f"  removed {path}")
                removed += 1
            except Exception as e:
                print(f"  [FAIL] {path}: {e}")
    if not removed:
        print("  nothing to remove")
    print("  If it is pinned to the taskbar, unpin it there as well.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the shortcuts instead of creating them")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("  This installer is Windows-only.")
        return 1

    print("-" * 76)
    print(f"  {APP_NAME} - desktop shortcuts")
    print("-" * 76)
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    raise SystemExit(main())
