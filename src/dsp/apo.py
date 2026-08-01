"""Equalizer APO output stage: config generation, atomic writes, and whether
APO is actually going to hear us.

Responsibility: everything that touches Equalizer APO lives here. Two things
this module exists to get right, both of which the app previously got wrong:

1. **Atomic writes.** APO opens config.txt with FILE_SHARE_READ and re-reads
   it on change, so a plain `open(path, 'w')` can be read mid-truncation. We
   write a temp file in the same directory, fsync it, then `os.replace`, which
   is atomic on NTFS. APO's watch mask includes FILE_NOTIFY_CHANGE_FILE_NAME
   with bWatchSubtree, so the rename is detected.

2. **Telling the truth about whether the EQ is reaching the speakers.** A
   successful file write means nothing if APO is not registered on the output
   the user is listening through. This machine is a live example: Equalizer
   APO is installed, but none of its registered endpoints is an active render
   endpoint, so every config write was inert while the UI reported success.
   `probe_apo_status()` checks that and the app surfaces it.

Registry access here is strictly read-only. Registering an APO on an endpoint
changes system audio configuration and is the user's call, made through
Equalizer APO's own Configurator.
"""

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import winreg
except ImportError:                                    # non-Windows dev boxes
    winreg = None                                      # type: ignore

logger = logging.getLogger(__name__)

APO_ROOT_KEY = r"SOFTWARE\EqualizerAPO"
APO_CHILD_APOS_KEY = r"SOFTWARE\EqualizerAPO\Child APOs"
MMDEV_RENDER_KEY = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
)
# PKEY_Device_FriendlyName, in the registry's "{guid},pid" spelling.
FRIENDLY_NAME_VALUE = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
DEVICE_STATE_ACTIVE = 1

SHELF_Q = 0.71

# The canonical do-nothing curve. Written at startup and on every exit path.
FLAT_EQ: Dict[str, float] = {
    "low_shelf_gain": 0.0, "low_shelf_freq": 120.0,
    "first_band_gain": 0.0, "first_band_freq": 250.0, "first_band_q": 0.71,
    "second_band_gain": 0.0, "second_band_freq": 1000.0, "second_band_q": 0.71,
    "third_band_gain": 0.0, "third_band_freq": 3500.0, "third_band_q": 0.71,
    "high_shelf_gain": 0.0, "high_shelf_freq": 10000.0,
}


# ---- Config generation ---------------------------------------------------

def build_config_text(eq: Dict[str, float], preamp_db: float,
                      note: str = "") -> str:
    """Render the 13-parameter EQ dict as an Equalizer APO config.txt."""
    lines = [
        "# Equalizer APO configuration written by Sonic Vector",
        f"# Last updated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if note:
        lines.append(f"# {note}")
    lines += [
        "",
        f"Preamp: {preamp_db:.2f} dB",
        "",
        "# 5-band parametric filter array",
        f"Filter 1: ON LSC Fc {eq['low_shelf_freq']:.0f} Hz Gain "
        f"{eq['low_shelf_gain']:.2f} dB Q {SHELF_Q:.2f}",
        f"Filter 2: ON PK Fc {eq['first_band_freq']:.0f} Hz Gain "
        f"{eq['first_band_gain']:.2f} dB Q {eq['first_band_q']:.2f}",
        f"Filter 3: ON PK Fc {eq['second_band_freq']:.0f} Hz Gain "
        f"{eq['second_band_gain']:.2f} dB Q {eq['second_band_q']:.2f}",
        f"Filter 4: ON PK Fc {eq['third_band_freq']:.0f} Hz Gain "
        f"{eq['third_band_gain']:.2f} dB Q {eq['third_band_q']:.2f}",
        f"Filter 5: ON HSC Fc {eq['high_shelf_freq']:.0f} Hz Gain "
        f"{eq['high_shelf_gain']:.2f} dB Q {SHELF_Q:.2f}",
        "",
    ]
    return "\n".join(lines)


def write_config_atomic(path: Path, text: str) -> str:
    """Write config.txt so APO can never observe a partial file.

    Returns one of: "ok", "path_missing", "denied", "error".
    """
    try:
        if not path.parent.exists():
            return "path_missing"
        # Same directory, so os.replace stays on one volume and is atomic.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".sonicvector-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return "ok"
    except PermissionError:
        return "denied"
    except Exception as e:
        logger.error(f"Failed to write Equalizer APO config at {path}: {e}")
        return "error"


# ---- "Is APO actually going to hear us?" ---------------------------------

def _open_hklm(subkey: str):
    return winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, subkey, 0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    )


def _subkey_names(subkey: str) -> List[str]:
    names: List[str] = []
    try:
        with _open_hklm(subkey) as k:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return names


def _endpoint_info(guid: str) -> Optional[Dict[str, Any]]:
    """(state, friendly name, has FxProperties) for one render endpoint."""
    base = f"{MMDEV_RENDER_KEY}\\{guid}"
    try:
        with _open_hklm(base) as k:
            state, _ = winreg.QueryValueEx(k, "DeviceState")
    except OSError:
        return None

    name = guid
    try:
        with _open_hklm(f"{base}\\Properties") as k:
            name = str(winreg.QueryValueEx(k, FRIENDLY_NAME_VALUE)[0])
    except OSError:
        pass

    has_fx = False
    try:
        with _open_hklm(f"{base}\\FxProperties"):
            has_fx = True
    except OSError:
        pass

    return {
        "guid": guid,
        "name": name,
        "state": int(state),
        "active": int(state) == DEVICE_STATE_ACTIVE,
        "has_fx_properties": has_fx,
    }


def probe_apo_status() -> Dict[str, Any]:
    """Report whether Equalizer APO will process the user's audio.

    Equalizer APO's Configurator records every endpoint it has been enabled on
    as a subkey of HKLM\\SOFTWARE\\EqualizerAPO\\Child APOs, so we can answer
    this from APO's own bookkeeping rather than guessing at APO CLSIDs.

    Returns a dict with a `state` in:
      unsupported            not Windows / no registry access
      apo_not_installed      Equalizer APO is not installed
      apo_no_active_endpoint APO is installed but enabled on nothing you are
                             currently playing through, so config writes are
                             inert. This is the case the app used to report as
                             success.
      apo_ready              APO is enabled on at least one active output
    """
    if winreg is None:
        return {"state": "unsupported", "endpoints": [],
                "detail": "Registry access is unavailable on this platform."}

    try:
        with _open_hklm(APO_ROOT_KEY):
            pass
    except OSError:
        return {"state": "apo_not_installed", "endpoints": [],
                "detail": "Equalizer APO is not installed. Without it this "
                          "app cannot change what you hear."}

    registered = {g.lower() for g in _subkey_names(APO_CHILD_APOS_KEY)}
    endpoints = [
        info for info in (
            _endpoint_info(g) for g in _subkey_names(MMDEV_RENDER_KEY)
        ) if info
    ]
    for ep in endpoints:
        ep["apo_registered"] = ep["guid"].lower() in registered

    active = [e for e in endpoints if e["active"]]
    active_with_apo = [e for e in active if e["apo_registered"]]

    if active_with_apo:
        names = ", ".join(e["name"] for e in active_with_apo)
        return {
            "state": "apo_ready",
            "endpoints": active,
            "detail": f"Equalizer APO is active on: {names}.",
        }

    active_names = ", ".join(e["name"] for e in active) or "none detected"
    return {
        "state": "apo_no_active_endpoint",
        "endpoints": active,
        "detail": (
            "Equalizer APO is installed but is not enabled on any output you "
            f"are currently using ({active_names}). Until you enable it, "
            "nothing this app does will change what you hear. Open Equalizer "
            "APO's Configurator as administrator, tick the output you listen "
            "through, and restart the audio service."
        ),
    }
