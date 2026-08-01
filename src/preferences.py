"""Listener preference capture.

Responsibility: record what the listener thought of a mix, in a coordinate
space that a preference model can actually learn from later.

Why this exists in this shape
-----------------------------
The companion study (C:\\Research\\SonicEQ) found that the descriptor-to-curve
mapping is close to unlearnable in general: out-of-fold R^2 of audio features
against the human EQ curve is +0.051 (warm) and -0.061 (bright), and the
residual dispersion between engineers (4.38 dB) is almost the whole raw
dispersion (4.50 dB). What DOES reduce that spread is knowing which person you
are dealing with. Per-listener preference is therefore not a nice-to-have on
top of the algorithm; it is the only target the evidence supports.

So votes are worth capturing from the first day the button exists, even though
the model that consumes them is not built yet. This module is the capture half.

Honest limitation, recorded here so nobody forgets it
----------------------------------------------------
A bare thumbs-up is the WEAKEST feedback modality available. It is one bit
about a ~13-dimensional setting, it carries no direction (too bright? too dull?
it cannot say), and it is confounded with whether the listener likes the SONG.
Simulation put a clean unary thumb at >40 votes to converge, and a thumb with a
30% song-liking confound at never. A level-matched A/B duel gets there in ~9,
because both arms are the same song at the same instant so song preference
cancels, and the answer is a signed direction rather than a radius.

The vote is stored with `kind` so the A/B observations can live in the same
table when that surface lands, and so unary votes can be down-weighted rather
than trusted equally.

Stdlib only.
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASIS_VERSION = 1

# Components are [low_shelf, band1, band2, band3, high_shelf, preamp] in dB.
# Axes derived from a centred PCA of the level-removed real SAFE curves; four
# of them reconstruct an arbitrary human EQ move to about 0.93 dB median error,
# against a 4.14-4.50 dB floor of disagreement between the humans themselves.
# In other words, four numbers are indistinguishable from a full curve at the
# resolution the source data can actually resolve.
BASIS: Dict[str, List[float]] = {
    "tilt":     [+6.94, +5.58, +1.28, -1.92, -1.38, -2.80],
    "scoop":    [+1.61, -3.52, -3.39, +0.25, +1.21, +0.95],
    "presence": [+3.29, -0.48, +4.40, +2.51, +0.55, -2.31],
    "level":    [0.0, 0.0, 0.0, 0.0, 0.0, +3.04],
}
AXES = ["tilt", "scoop", "presence", "level"]

GAIN_ORDER = [
    "low_shelf_gain", "first_band_gain", "second_band_gain",
    "third_band_gain", "high_shelf_gain",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    obs_id        INTEGER PRIMARY KEY,
    ts            REAL    NOT NULL,
    kind          TEXT    NOT NULL CHECK(kind IN ('unary','pairwise','axis','direct')),
    source        TEXT    NOT NULL,
    verdict       TEXT    CHECK(verdict IN ('up','down','a','b','tie')),
    basis_version INTEGER NOT NULL,
    -- context
    track_key     TEXT,
    track_name    TEXT,
    artist_name   TEXT,
    sound_style   TEXT,
    engine        TEXT,
    tags_json     TEXT,
    weights_json  TEXT,
    -- what was actually playing through the filters at the moment of the vote
    gains_json    TEXT NOT NULL,
    preamp_db     REAL,
    coords_json   TEXT NOT NULL,
    limited       INTEGER NOT NULL DEFAULT 0,
    -- provenance
    is_eval       INTEGER NOT NULL DEFAULT 0,
    app_version   TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_track ON observations(track_key, ts);
CREATE INDEX IF NOT EXISTS idx_obs_kind  ON observations(kind, ts);
"""


def project(gains: Dict[str, float], preamp_db: float = 0.0) -> Dict[str, float]:
    """Least-squares coordinates of a curve in the 4-axis preference basis.

    Solves min |B x - g| over the six-component vector, via the normal
    equations. Four unknowns, so a plain Gaussian elimination is plenty and it
    keeps this module dependency-free.
    """
    target = [float(gains.get(k, 0.0)) for k in GAIN_ORDER] + [float(preamp_db)]
    cols = [BASIS[a] for a in AXES]

    n = len(AXES)
    ata = [[sum(cols[i][k] * cols[j][k] for k in range(6)) for j in range(n)]
           for i in range(n)]
    atb = [sum(cols[i][k] * target[k] for k in range(6)) for i in range(n)]

    # Gaussian elimination with partial pivoting.
    m = [row[:] + [atb[i]] for i, row in enumerate(ata)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return {a: 0.0 for a in AXES}
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]

    return {AXES[i]: round(m[i][n] / m[i][i], 4) for i in range(n)}


class PreferenceStore:
    """Append-only observation log.

    Append-only on purpose: the posterior of any model fitted to this becomes a
    pure function of the log, so a change to the basis, the noise model, or a
    bug fix can be replayed rather than losing the listener's history. The
    previous per-track EQ table stored only the latest state and destroyed the
    evidence on every write.
    """

    def __init__(self, db_path: str = "data/preferences.db"):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def record_unary(
        self,
        verdict: str,
        eq: Dict[str, float],
        preamp_db: float,
        track: Optional[Dict[str, Any]] = None,
        sound_style: str = "",
        engine: str = "",
        limited: bool = False,
        app_version: str = "",
    ) -> Dict[str, Any]:
        """Store one thumbs-up / thumbs-down against the curve now playing."""
        if verdict not in ("up", "down"):
            raise ValueError(f"verdict must be 'up' or 'down', got {verdict!r}")

        track = track or {}
        gains = {k: float(eq.get(k, 0.0)) for k in GAIN_ORDER}
        coords = project(gains, preamp_db)

        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO observations (
                       ts, kind, source, verdict, basis_version,
                       track_key, track_name, artist_name, sound_style, engine,
                       tags_json, weights_json, gains_json, preamp_db,
                       coords_json, limited, is_eval, app_version
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(), "unary", "thumb", verdict, BASIS_VERSION,
                    track.get("track_id") or None,
                    track.get("track_name") or None,
                    track.get("artist_name") or None,
                    sound_style, engine,
                    json.dumps(track.get("tags") or []),
                    json.dumps(track.get("weights") or {}),
                    json.dumps(gains), float(preamp_db),
                    json.dumps(coords), int(bool(limited)), 0, app_version,
                ),
            )
            obs_id = cur.lastrowid
            conn.commit()

        logger.info(
            "Preference recorded: %s for '%s' (coords %s)",
            verdict, track.get("track_name") or "unknown",
            ", ".join(f"{a}={coords[a]:+.2f}" for a in AXES),
        )
        return {"obs_id": obs_id, "coords": coords, "verdict": verdict}

    def summary(self) -> Dict[str, Any]:
        """Counts and the running mean coordinate of approved mixes."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT verdict, coords_json FROM observations WHERE kind='unary'"
            ).fetchall()

        up = [json.loads(r["coords_json"]) for r in rows if r["verdict"] == "up"]
        down = sum(1 for r in rows if r["verdict"] == "down")

        mean = {a: 0.0 for a in AXES}
        if up:
            for a in AXES:
                mean[a] = round(sum(c.get(a, 0.0) for c in up) / len(up), 3)

        return {
            "total": len(rows),
            "up": len(up),
            "down": down,
            "mean_approved_coords": mean,
            "basis_version": BASIS_VERSION,
            # Straight from the simulation: a unary thumb needs 40+ votes to say
            # much, and is unreliable if song-liking leaks into it. Surfacing
            # the number keeps the UI from implying more precision than exists.
            "votes_for_signal": 40,
        }
