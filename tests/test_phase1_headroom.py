"""Phase 1 exit criterion: the app must never emit a clipping curve.

Run with:  python tests/test_phase1_headroom.py

The claim under test is narrow and checkable: for every tag set the matcher can
produce, under every sound style, the composite magnitude response plus the
written preamp must not exceed 0 dBFS. Before this phase it exceeded it by up
to 8.5 dB on ordinary material, because the preamp was derived from three UI
overlay sliders and ignored the band gains entirely.

Stdlib only, no test framework, so it runs anywhere the app runs.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.dsp import apo, render                                  # noqa: E402
from embed_song_predictor import SemanticEQPredictor             # noqa: E402
import web_gui_app as gui                                        # noqa: E402


# Tag sets chosen to exercise every profile and every combination that drives
# the overlay sliders hardest, plus the degenerate no-match case.
TAG_SETS = [
    ["punchy", "kick", "drums", "hip-hop", "trap", "heavy bass"],
    ["warm", "acoustic", "folk", "mellow", "intimate"],
    ["bright", "crisp", "synth", "edm", "electronic", "dance"],
    ["airy", "ambient", "dreamy", "reverb", "chill", "space"],
    ["presence", "vocal", "singer", "rock", "alternative"],
    ["muddy", "boxy", "boomy", "dark", "heavy"],
    ["punchy", "warm", "bass", "funk", "groove"],
    ["bright", "airy", "sparkle", "classical", "instrumental"],
    ["warm", "muddy", "dark", "vintage", "analog"],
    ["punchy", "presence", "rock", "metal", "grunge"],
    ["punchy", "bright", "house", "techno", "club", "dance"],
    ["warm", "presence", "jazz", "blues", "singer-songwriter"],
    ["airy", "warm", "lo-fi", "chillout", "psychedelic"],
    ["punchy", "airy", "drum and bass", "dubstep"],
    ["bright", "presence", "pop", "disco", "synthpop"],
    ["muddy", "punchy", "heavy", "kick", "boomy"],
    ["indie", "alternative rock", "guitar"],
    ["r&b", "rap", "beat", "snare"],
    ["classical", "instrumental", "open", "breathable"],
    [],                                     # no tags at all -> must stay flat
    ["gibberish", "seen live", "favorite songs 2019"],   # real Last.fm noise
]

# Overlay/user-trim extremes reachable through the UI's own slider ranges.
MIX_EXTREMES = [
    {"preamp_gain": 0.0, "strength": 1.0, "bass_boost": 8.0,
     "vocal_clarity": 6.0, "airiness": 6.0},
    {"preamp_gain": 0.0, "strength": 2.0, "bass_boost": 8.0,
     "vocal_clarity": 6.0, "airiness": 6.0},
    {"preamp_gain": -12.0, "strength": 2.0, "bass_boost": -8.0,
     "vocal_clarity": -6.0, "airiness": -6.0},
]


def composite_peak(eq: dict, mix: dict):
    """Reproduce exactly what commit_state() emits, and measure it."""
    composite = gui._composite_eq(eq, mix)
    budgeted, limited, scale = render.apply_headroom_budget(composite)
    user_trim = max(-12.0, min(0.0, float(mix.get("preamp_gain", 0.0))))
    preamp = max(render.PREAMP_FLOOR_DB,
                 render.required_preamp_db(budgeted) + user_trim)
    peak = max(render.render_curve(budgeted)) + preamp
    return peak, preamp, limited, scale


def main() -> int:
    predictor = SemanticEQPredictor(db_path=str(ROOT / "data" / "test_library.db"))
    if not predictor.centroids:
        print("FAIL: no centroids loaded. Run: python preprocess_safe.py --synthetic")
        return 1

    styles = list(gui.STYLE_OFFSETS)
    failures = []
    limited_count = 0
    checks = 0
    worst = (-99.0, None)

    for tags in TAG_SETS:
        weights = predictor.calculate_similarity_weights(tags)
        interpolated = predictor.synthesize_eq_curve(weights)
        for style in styles:
            eq = gui.blend_curve(interpolated, style)
            mixes = [gui.dynamic_overlays(weights)] + MIX_EXTREMES
            for mix in mixes:
                peak, preamp, limited, scale = composite_peak(eq, mix)
                checks += 1
                limited_count += bool(limited)
                if peak > worst[0]:
                    worst = (peak, (tags[:2], style, round(preamp, 2)))
                # Floating point on a 256-point grid; 1e-6 is noise, not slack.
                if peak > 1e-6:
                    failures.append(
                        f"  {peak:+6.2f} dBFS  style={style:<10} "
                        f"preamp={preamp:6.2f}  tags={tags[:3]}")

    print(f"Checked {checks} curve/style/mix combinations "
          f"across {len(TAG_SETS)} tag sets and {len(styles)} styles.")
    print(f"Headroom limiter engaged on {limited_count} of them.")
    print(f"Worst observed output peak: {worst[0]:+.4f} dBFS  ({worst[1]})")

    # The muddy-as-defect fix, asserted rather than assumed.
    muddy_w = predictor.calculate_similarity_weights(["muddy", "boomy", "dark"])
    muddy_curve = predictor.synthesize_eq_curve(muddy_w)
    if muddy_w.get("muddy", 0.0) <= 0.0:
        failures.append("  muddy tags did not match the muddy profile at all")
    elif muddy_curve["low_shelf_gain"] > 0.5:
        failures.append(
            f"  muddy-tagged track still boosts the low shelf "
            f"({muddy_curve['low_shelf_gain']:+.2f} dB)")

    # Flat must survive a round trip through the whole output stage.
    flat_peak = max(render.render_curve(apo.FLAT_EQ))
    if abs(flat_peak) > 0.01:
        failures.append(f"  flat EQ is not flat ({flat_peak:+.4f} dB)")

    if failures:
        print(f"\nFAIL: {len(failures)} violation(s):")
        for f in failures[:25]:
            print(f)
        if len(failures) > 25:
            print(f"  ... and {len(failures) - 25} more")
        return 1

    print("\nPASS: no combination exceeds 0 dBFS; muddy is corrective; flat is flat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
