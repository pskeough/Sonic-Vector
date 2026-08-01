"""Exact frequency-response renderer: EQ parameters -> 256-point dB curve.

Responsibility: this is the app's measuring instrument. The preamp that keeps
the output from clipping, the headroom budget, and the curve drawn in the UI
are all computed from `render_curve` here, so the plot, the safety math, and
the filters Equalizer APO actually runs are provably the same filter.

Ported from the study's Stage 3 renderer (C:\\Research\\SonicEQ\\pipeline\\
03_render.py) with the parameter names adapted to this app's 13-key EQ dict.
The filter math is unchanged, and so are the self-test tolerances.

Rigor rules carried over from the study, and they are not negotiable here
either:
  * Filter math is the RBJ Audio EQ Cookbook, the same formulas Equalizer APO
    uses for PK/LSC/HSC, not an approximation. Shelves use the Q-based form
    (alpha = sin(w0) / (2*Q)); peaking uses A = 10^(dB/40) so the curve peaks
    at exactly `gain` dB.
  * The response is evaluated exactly: H(z) at z = e^{j*2*pi*f/fs} with complex
    arithmetic, per filter, then summed in dB across the cascade. No sampled
    impulse, no FFT, no interpolation.
  * The frequency grid is fixed (256 log-spaced points, 20 Hz .. 20 kHz
    inclusive, fs = 48 kHz). A failing self-test means fix the implementation,
    never widen the tolerance.

Stdlib only, matching the rest of the project.
"""

import cmath
import math
from typing import Dict, List, Tuple

FS = 48000.0          # Equalizer APO's processing rate on this class of device
SHELF_Q = 0.71        # the Q this app writes for LSC/HSC filters
N_POINTS = 256
F_LO, F_HI = 20.0, 20000.0

# Log-spaced measurement grid, endpoints exact: f_i = 20 * 1000^(i/255).
FREQ_GRID: List[float] = [
    F_LO * (F_HI / F_LO) ** (i / (N_POINTS - 1)) for i in range(N_POINTS)
]

# The app's 13-parameter EQ dict.
PARAM_KEYS = [
    "low_shelf_gain", "low_shelf_freq",
    "first_band_gain", "first_band_freq", "first_band_q",
    "second_band_gain", "second_band_freq", "second_band_q",
    "third_band_gain", "third_band_freq", "third_band_q",
    "high_shelf_gain", "high_shelf_freq",
]

GAIN_KEYS = [
    "low_shelf_gain", "first_band_gain", "second_band_gain",
    "third_band_gain", "high_shelf_gain",
]

# --- Headroom budget ------------------------------------------------------
# Per-band ceiling, applied before anything else touches the cascade.
MAX_BAND_GAIN_DB = 6.0
# Ceilings on the *realized* cascade, measured over the audible band where a
# correction is legitimate. Above and below this range we do not correct at
# all, so excursions out there must not drive the limiter.
#
# These cost output level, roughly 1 dB of preamp per 1 dB of peak, because
# the preamp has to make room for the boost. Measured over 126 tag/style
# combinations with the shipped profiles:
#
#   peak cap   median preamp   limiter engaged   median tonal range
#      6 dB       -6.59 dB          45%               5.9 dB
#      5 dB       -6.00 dB          60%               5.3 dB
#      4 dB       -5.01 dB          72%               4.4 dB   <- default
#      3 dB       -4.02 dB          87%               3.5 dB
#      2 dB       -3.02 dB          95%               2.3 dB
#
# 4 dB is the default because the companion study puts the entire descriptor
# effect over flat at 0.66 to 1.36 dB LSD, against 4.4 dB of irreducible
# disagreement between human engineers. Emitting a 6 dB gesture on that
# evidence is not supportable, and it costs 1.6 dB more output level for
# tonal range nobody demonstrated a listener wants. Raise it only if you have
# a reason better than "it sounds more dramatic", because louder always
# sounds better in an unmatched comparison.
MAX_PEAK_DB = 4.0
MAX_SPAN_DB = 7.0
BUDGET_F_LO, BUDGET_F_HI = 30.0, 16000.0

# Preamp margin over the magnitude peak. Broadband material sums with phase,
# so the magnitude peak alone slightly understates true-peak headroom.
PREAMP_MARGIN_DB = 1.0
PREAMP_FLOOR_DB = -24.0

_BUDGET_SLICE = [
    i for i, f in enumerate(FREQ_GRID) if BUDGET_F_LO <= f <= BUDGET_F_HI
]


# ---- RBJ cookbook coefficients (each returns (b0, b1, b2, a0, a1, a2)) ----

def peaking_coeffs(gain_db: float, f0: float, q: float):
    """RBJ peaking EQ. Exact +g/-g inverse pairs at equal f0, Q."""
    a_lin = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / FS
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    b0 = 1.0 + alpha * a_lin
    b1 = -2.0 * cw
    b2 = 1.0 - alpha * a_lin
    a0 = 1.0 + alpha / a_lin
    a1 = -2.0 * cw
    a2 = 1.0 - alpha / a_lin
    return b0, b1, b2, a0, a1, a2


def low_shelf_coeffs(gain_db: float, f0: float, q: float = SHELF_Q):
    """RBJ low shelf, Q-based form (alpha = sin(w0) / (2Q))."""
    a_lin = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / FS
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    sq = 2.0 * math.sqrt(a_lin) * alpha
    b0 = a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cw + sq)
    b1 = 2.0 * a_lin * ((a_lin - 1.0) - (a_lin + 1.0) * cw)
    b2 = a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cw - sq)
    a0 = (a_lin + 1.0) + (a_lin - 1.0) * cw + sq
    a1 = -2.0 * ((a_lin - 1.0) + (a_lin + 1.0) * cw)
    a2 = (a_lin + 1.0) + (a_lin - 1.0) * cw - sq
    return b0, b1, b2, a0, a1, a2


def high_shelf_coeffs(gain_db: float, f0: float, q: float = SHELF_Q):
    """RBJ high shelf, Q-based form (alpha = sin(w0) / (2Q))."""
    a_lin = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / FS
    alpha = math.sin(w0) / (2.0 * q)
    cw = math.cos(w0)
    sq = 2.0 * math.sqrt(a_lin) * alpha
    b0 = a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cw + sq)
    b1 = -2.0 * a_lin * ((a_lin - 1.0) + (a_lin + 1.0) * cw)
    b2 = a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cw - sq)
    a0 = (a_lin + 1.0) - (a_lin - 1.0) * cw + sq
    a1 = 2.0 * ((a_lin - 1.0) - (a_lin + 1.0) * cw)
    a2 = (a_lin + 1.0) - (a_lin - 1.0) * cw - sq
    return b0, b1, b2, a0, a1, a2


# ---- Exact evaluation ----------------------------------------------------

def biquad_mag_db(coeffs, f: float) -> float:
    """|H(e^{j*2*pi*f/fs})| in dB via direct complex evaluation."""
    b0, b1, b2, a0, a1, a2 = coeffs
    z1 = cmath.exp(-1j * 2.0 * math.pi * f / FS)   # z^-1 on the unit circle
    z2 = z1 * z1
    h = (b0 + b1 * z1 + b2 * z2) / (a0 + a1 * z1 + a2 * z2)
    return 20.0 * math.log10(abs(h))


def render_curve(params: Dict[str, float]) -> List[float]:
    """13-key parameter dict -> 256 cascade magnitudes in dB.

    Cascade order: low shelf -> peaking 1 -> peaking 2 -> peaking 3 ->
    high shelf. Magnitudes multiply, so dB add; the order does not affect the
    magnitude response but is fixed here so the code reads the way the config
    file does.
    """
    missing = [k for k in PARAM_KEYS if k not in params]
    if missing:
        raise KeyError(f"params missing required keys: {missing}")
    chain = [
        low_shelf_coeffs(params["low_shelf_gain"], params["low_shelf_freq"]),
        peaking_coeffs(params["first_band_gain"], params["first_band_freq"],
                       params["first_band_q"]),
        peaking_coeffs(params["second_band_gain"], params["second_band_freq"],
                       params["second_band_q"]),
        peaking_coeffs(params["third_band_gain"], params["third_band_freq"],
                       params["third_band_q"]),
        high_shelf_coeffs(params["high_shelf_gain"], params["high_shelf_freq"]),
    ]
    return [sum(biquad_mag_db(c, f) for c in chain) for f in FREQ_GRID]


# ---- Headroom -------------------------------------------------------------

def peak_db(params: Dict[str, float]) -> float:
    """Highest point of the realized cascade, over the full grid."""
    return max(render_curve(params))


def _budget_stats(response: List[float]) -> Tuple[float, float]:
    """(peak, peak-to-peak span) over the correction band only."""
    band = [response[i] for i in _BUDGET_SLICE]
    return max(band), max(band) - min(band)


def required_preamp_db(params: Dict[str, float],
                       margin_db: float = PREAMP_MARGIN_DB) -> float:
    """Preamp that keeps the realized cascade from clipping, in dB (<= 0).

    This is the whole point of the module. The previous implementation
    compensated only three UI overlay sliders and ignored the band gains
    entirely, which let the composite response reach +8 dB above unity.
    """
    peak = peak_db(params)
    if peak <= 0.0:
        # A cut-only or flat curve has nothing to make room for. Charging the
        # margin anyway cost a needless 1 dB of output on every flat setting,
        # which is exactly the "why does this sound worse" trap.
        return 0.0
    return max(PREAMP_FLOOR_DB, min(0.0, -peak - margin_db))


def apply_headroom_budget(
    params: Dict[str, float],
) -> Tuple[Dict[str, float], bool, float]:
    """Clamp per-band gains, then scale the whole curve to fit the budget.

    Returns (params, was_limited, scale). Scaling is a single scalar applied
    to every band gain so the curve keeps its shape and only loses magnitude,
    which is the least surprising thing to do to someone's chosen tone.
    """
    out = dict(params)
    limited = False

    for k in GAIN_KEYS:
        g = float(out[k])
        clamped = max(-MAX_BAND_GAIN_DB, min(MAX_BAND_GAIN_DB, g))
        if clamped != g:
            limited = True
        out[k] = clamped

    def fits(scale: float) -> bool:
        trial = dict(out)
        for key in GAIN_KEYS:
            trial[key] = out[key] * scale
        peak, span = _budget_stats(render_curve(trial))
        return peak <= MAX_PEAK_DB and span <= MAX_SPAN_DB

    if fits(1.0):
        return out, limited, 1.0

    # Largest feasible scale, by bisection. 40 iterations puts the answer well
    # inside float noise, and each evaluation is ~1280 complex divides.
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if fits(mid):
            lo = mid
        else:
            hi = mid

    for k in GAIN_KEYS:
        out[k] = out[k] * lo
    return out, True, lo


def pink_weighted_mean_db(params: Dict[str, float]) -> float:
    """Average gain of the cascade, weighted per octave rather than per hertz.

    Used to level-match the hold-to-compare bypass against the processed
    signal, so the louder arm does not simply win. The grid is log-spaced, so
    a plain mean over it already weights every octave equally, which is the
    pink-noise assumption.

    This is an *analytic* match against an assumed spectrum, not a measurement
    of the actual program. It is off by up to several dB on spectrally extreme
    material, which is why the UI labels it "assumed" until real loudness
    measurement lands.
    """
    curve = render_curve(params)
    return sum(curve) / len(curve)


def response_points(params: Dict[str, float],
                    preamp_db: float = 0.0) -> List[Dict[str, float]]:
    """Curve for the UI plot, including the preamp, as [{f, db}, ...].

    The UI must never compute its own magnitudes. If the plot and the emitted
    filter can disagree, they eventually will.
    """
    curve = render_curve(params)
    return [{"f": round(f, 2), "db": round(db + preamp_db, 4)}
            for f, db in zip(FREQ_GRID, curve)]


# ---- Self-tests (tolerances are part of the spec; never widen them) -------

def _params(**overrides) -> Dict[str, float]:
    """All-flat baseline: every gain 0 dB at plausible neutral frequencies."""
    p = {
        "low_shelf_gain": 0.0, "low_shelf_freq": 100.0,
        "first_band_gain": 0.0, "first_band_freq": 250.0, "first_band_q": 1.0,
        "second_band_gain": 0.0, "second_band_freq": 1000.0, "second_band_q": 1.0,
        "third_band_gain": 0.0, "third_band_freq": 4000.0, "third_band_q": 1.0,
        "high_shelf_gain": 0.0, "high_shelf_freq": 10000.0,
    }
    p.update(overrides)
    return p


def _nearest_idx(f: float) -> int:
    return min(range(N_POINTS), key=lambda i: abs(FREQ_GRID[i] - f))


def run_selftests() -> Tuple[str, bool]:
    """Run all unit tests. Returns (report_text, all_passed)."""
    lines: List[str] = []
    results: List[bool] = []

    def check(name, expected, actual, passed):
        results.append(passed)
        lines.append(f"[{name}]")
        lines.append(f"  expected : {expected}")
        lines.append(f"  actual   : {actual}")
        lines.append(f"  {'PASS' if passed else 'FAIL'}")
        lines.append("")

    lines.append("RENDER VALIDATION - measuring-instrument self-test")
    lines.append(f"fs = {FS:.0f} Hz | grid = {N_POINTS} log points "
                 f"{F_LO:.0f}..{F_HI:.0f} Hz | shelf Q = {SHELF_Q}")
    lines.append("")

    # 1. FLAT: all gains 0 -> identity response everywhere.
    resp = render_curve(_params())
    worst = max(abs(v) for v in resp)
    check("FLAT", "max |dB| over grid < 0.01",
          f"max |dB| = {worst:.6f}", worst < 0.01)

    # 2. PEAK: +6 dB @ 1 kHz Q=1 on band 2 only.
    resp = render_curve(_params(second_band_gain=6.0, second_band_freq=1000.0,
                                second_band_q=1.0))
    i1k = _nearest_idx(1000.0)
    at_peak, at_lo, at_hi = resp[i1k], resp[0], resp[-1]
    check("PEAK @1kHz",
          f"grid point nearest 1 kHz ({FREQ_GRID[i1k]:.1f} Hz) within 0.05 dB "
          "of +6; 20 Hz and 20 kHz within 0.1 dB of 0",
          f"peak = {at_peak:.4f} dB; 20 Hz = {at_lo:.4f} dB; "
          f"20 kHz = {at_hi:.4f} dB",
          abs(at_peak - 6.0) < 0.05 and abs(at_lo) < 0.1 and abs(at_hi) < 0.1)

    # 3. LOW SHELF: +6 dB @ 100 Hz.
    resp = render_curve(_params(low_shelf_gain=6.0, low_shelf_freq=100.0))
    at20 = resp[0]
    at10k = resp[_nearest_idx(10000.0)]
    check("LOW SHELF +6@100Hz",
          "20 Hz within 0.3 dB of +6; 10 kHz within 0.1 dB of 0",
          f"20 Hz = {at20:.4f} dB; 10 kHz = {at10k:.4f} dB",
          abs(at20 - 6.0) < 0.3 and abs(at10k) < 0.1)

    # 4. HIGH SHELF: -6 dB @ 10 kHz.
    resp = render_curve(_params(high_shelf_gain=-6.0, high_shelf_freq=10000.0))
    at20k = resp[-1]
    at100 = resp[_nearest_idx(100.0)]
    check("HIGH SHELF -6@10kHz",
          "20 kHz within 0.5 dB of -6; 100 Hz within 0.1 dB of 0",
          f"20 kHz = {at20k:.4f} dB; 100 Hz = {at100:.4f} dB",
          abs(at20k - (-6.0)) < 0.5 and abs(at100) < 0.1)

    # 5. INVERSE CANCELLATION: RBJ peaking +g and -g at equal f,Q are exact
    #    inverses (the +g numerator IS the -g denominator), so dB sums to 0.
    up = render_curve(_params(first_band_gain=6.0, first_band_freq=500.0,
                              first_band_q=2.0))
    dn = render_curve(_params(first_band_gain=-6.0, first_band_freq=500.0,
                              first_band_q=2.0))
    worst = max(abs(u + d) for u, d in zip(up, dn))
    check("INVERSE CANCELLATION",
          "max |sum of +6/-6 dB curves| < 0.05",
          f"max |dB| = {worst:.2e}", worst < 0.05)

    # 6. GRID SANITY.
    increasing = all(b > a for a, b in zip(FREQ_GRID, FREQ_GRID[1:]))
    ok = (len(FREQ_GRID) == N_POINTS and increasing
          and abs(FREQ_GRID[0] - F_LO) <= 1e-6 * F_LO
          and abs(FREQ_GRID[-1] - F_HI) <= 1e-6 * F_HI)
    check("GRID SANITY",
          "256 points, strictly increasing, 20.0 .. 20000.0 (rel tol 1e-6)",
          f"n = {len(FREQ_GRID)}; increasing = {increasing}; "
          f"first = {FREQ_GRID[0]!r}; last = {FREQ_GRID[-1]!r}", ok)

    # 7. PREAMP SUFFICIENCY. The headroom guarantee, stated as a test: after
    #    the budget and the preamp, nothing on the grid may exceed 0 dB.
    aggressive = _params(
        low_shelf_gain=12.0, low_shelf_freq=60.0,
        first_band_gain=9.0, first_band_freq=200.0, first_band_q=0.8,
        second_band_gain=8.0, second_band_freq=1000.0, second_band_q=1.0,
        third_band_gain=7.0, third_band_freq=3500.0, third_band_q=1.0,
        high_shelf_gain=10.0, high_shelf_freq=10000.0,
    )
    budgeted, _, _ = apply_headroom_budget(aggressive)
    pre = required_preamp_db(budgeted)
    worst = max(v + pre for v in render_curve(budgeted))
    check("PREAMP SUFFICIENCY",
          "max (response + preamp) <= 0.0 dB for a deliberately extreme curve",
          f"preamp = {pre:.2f} dB; max = {worst:.4f} dB", worst <= 0.0)

    # 8. BUDGET ENFORCEMENT.
    _, limited, scale = apply_headroom_budget(aggressive)
    peak, span = _budget_stats(render_curve(budgeted))
    check("BUDGET ENFORCEMENT",
          f"extreme curve reported limited; peak <= {MAX_PEAK_DB} and "
          f"span <= {MAX_SPAN_DB} dB over {BUDGET_F_LO:.0f}..{BUDGET_F_HI:.0f} Hz",
          f"limited = {limited}; scale = {scale:.4f}; peak = {peak:.3f}; "
          f"span = {span:.3f}",
          limited and peak <= MAX_PEAK_DB + 1e-6 and span <= MAX_SPAN_DB + 1e-6)

    all_passed = all(results)
    n_fail = results.count(False)
    lines.append("ALL PASS" if all_passed
                 else f"FAILURES: {n_fail} of {len(results)} tests failed")
    return "\n".join(lines), all_passed


if __name__ == "__main__":
    report, ok = run_selftests()
    print(report)
    raise SystemExit(0 if ok else 1)
