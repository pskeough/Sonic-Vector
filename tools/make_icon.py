"""Generate the Sonic Vector app icon.

Run with:  python tools/make_icon.py

Writes:
    static/favicon.ico            multi-size Windows icon (16 .. 256)
    static/icon-192.png           web app manifest icon
    static/icon-512.png           web app manifest icon
    static/icon-512-maskable.png  manifest "maskable" variant (safe-zone inset)

The curve on the icon is not a drawing of an EQ curve. It is an EQ curve: the
points come from src.dsp.render.render_curve, the same exact-evaluation RBJ
cascade the app writes to Equalizer APO, so the mark is a real filter response
from this project rather than a decorative squiggle.

Two things learned the hard way and encoded here:

* **Plot the correction band, not the full grid.** Over 20 Hz .. 20 kHz the
  shelves turn up hard at both edges and the mark grows spurious hooks in the
  corners. 30 Hz .. 16 kHz is the band the app actually corrects over
  (render.BUDGET_F_LO/HI) and it is also the band that draws cleanly.
* **Render each size independently at 8x.** A 256px icon reduced to 16px turns
  into mush. At 16px the grid has to go and the trace has to be nearly twice as
  heavy in relative terms, which is only expressible per size.

Colours are the console's own: the recessed scope well against a terracotta
trace that warms to amber as it rises, which is the same clay/amber LED pairing
the fascia uses.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFilter                     # noqa: E402

from src.dsp import render                                        # noqa: E402

OUT = ROOT / "static"

# Console palette, straight from static/style.css.
WELL_TOP = (38, 33, 26)
WELL_BOTTOM = (16, 14, 10)
CLAY = (217, 119, 87)
CLAY_LOW = (176, 88, 66)
AMBER = (243, 177, 82)
CREAM = (236, 232, 219)

# The curve the icon shows: a loudness-compensation smile, which is the one EQ
# silhouette a stranger recognises on sight, and which happens to be a preset
# this app ships. Chosen over four candidates because it is the only one that
# still reads as a filter response at 16 px.
#
# Three rules it follows, each learned by drawing the alternatives:
#   * Broad Qs. A narrow band puts a spike in the mark that reads as a glitch.
#   * The third band sits at exactly 0 dB. Any gain there adds a small hook on
#     the treble side that survives downsampling as an unexplained wobble.
#   * Asymmetric. The bass shoulder is taller than the treble one, so the mark
#     has a direction instead of being a symmetric U.
ICON_EQ = {
    "low_shelf_gain": 9.5, "low_shelf_freq": 105.0,
    "first_band_gain": -2.5, "first_band_freq": 400.0, "first_band_q": 0.7,
    "second_band_gain": -5.0, "second_band_freq": 1100.0, "second_band_q": 0.55,
    "third_band_gain": 0.0, "third_band_freq": 5000.0, "third_band_q": 0.7,
    "high_shelf_gain": 7.5, "high_shelf_freq": 9500.0,
}

# Windows uses every one of these; Explorer picks per view mode and the taskbar
# picks per DPI scale, so a missing size is a blurry size.
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def curve_points():
    """The response over the correction band, normalised to 0..1."""
    db = render.render_curve(ICON_EQ)
    band = [(f, v) for f, v in zip(render.FREQ_GRID, db)
            if render.BUDGET_F_LO <= f <= render.BUDGET_F_HI]

    values = [v for _, v in band]
    lo, hi = min(values), max(values)
    span = max(hi - lo, 1e-6)

    n = len(band)
    return [(i / (n - 1), (v - lo) / span) for i, (_, v) in enumerate(band)]


def vertical_gradient(size, top, bottom):
    """A full-canvas vertical ramp, used both as art and through masks."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel((0, y), tuple(
            round(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))
    return grad.resize((size, size), Image.BILINEAR)


def alpha_ramp(size, top_alpha, bottom_alpha):
    ramp = Image.new("L", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        ramp.putpixel((0, y), round(top_alpha + (bottom_alpha - top_alpha) * t))
    return ramp.resize((size, size), Image.BILINEAR)


def render_icon(px: int, inset_ratio: float = 0.0) -> Image.Image:
    """One icon at one size, rendered at 8x and reduced.

    inset_ratio shrinks the artwork inside the canvas for the manifest's
    "maskable" variant: Android and Chrome crop maskable icons to a circle, so
    everything meaningful has to sit inside the middle 80%.
    """
    ss = 8
    size = px * ss
    detail = px >= 32          # below this, the grid is noise, not information

    trace_w = size * (0.088 if px < 24 else 0.062 if px < 48 else 0.046)
    corner = size * 0.215

    art = vertical_gradient(size, WELL_TOP, WELL_BOTTOM).convert("RGBA")

    # Plot box. Generous, so the curve is the icon rather than a detail in it,
    # but inset enough that the trace never collides with a rounded corner.
    pad_x = size * 0.135
    pad_y = size * (0.215 if detail else 0.185)
    box_w = size - 2 * pad_x
    box_h = size - 2 * pad_y
    floor_y = size - pad_y * 0.45

    pts = curve_points()
    xy = [(pad_x + fx * box_w, pad_y + (1.0 - fy) * box_h) for fx, fy in pts]

    if detail:
        grid = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grid)
        for frac in (0.25, 0.5, 0.75):
            x = pad_x + frac * box_w
            gd.line([(x, pad_y * 0.62), (x, floor_y)],
                    fill=CREAM + (30,), width=max(1, int(size * 0.0045)))
        art.alpha_composite(grid)

    # Filled area under the trace. Painted as a clay ramp through a polygon
    # mask so it fades toward the floor instead of sitting there as a slab.
    fill_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(fill_mask).polygon(
        xy + [(xy[-1][0], floor_y), (xy[0][0], floor_y)], fill=255)
    fill_mask = Image.composite(alpha_ramp(size, 132, 8),
                                Image.new("L", (size, size), 0), fill_mask)
    art.paste(Image.new("RGB", (size, size), CLAY), (0, 0), fill_mask)

    # Bloom under the trace: a blurred copy of the stroke, not a radial blob,
    # so the light follows the curve the way it does on the scope.
    stroke_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(stroke_mask).line(xy, fill=255, width=int(trace_w),
                                     joint="curve")
    for x, y in (xy[0], xy[-1]):
        r = trace_w / 2
        ImageDraw.Draw(stroke_mask).ellipse([x - r, y - r, x + r, y + r], fill=255)

    bloom = stroke_mask.filter(ImageFilter.GaussianBlur(trace_w * 1.15))
    art.paste(Image.new("RGB", (size, size), CLAY), (0, 0),
              bloom.point(lambda a: int(a * 0.62)))

    # The trace, painted through its own mask with a vertical clay-to-amber
    # ramp: the crest reads as the hot part of the curve, which is exactly what
    # the amber LEDs on the console mean.
    trace_grad = Image.new("RGB", (size, size))
    trace_grad.paste(vertical_gradient(size, AMBER, CLAY_LOW), (0, 0))
    art.paste(trace_grad, (0, 0), stroke_mask)

    # Rounded panel with a lit top edge, the way the fascia catches light.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=corner, fill=255)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(art, (0, 0), mask)

    if detail:
        bevel = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(bevel).rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=corner,
            outline=CREAM + (40,), width=max(2, int(size * 0.010)))
        canvas.alpha_composite(bevel)

    if inset_ratio > 0:
        inner = int(size * (1 - inset_ratio))
        shrunk = canvas.resize((inner, inner), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(shrunk, ((size - inner) // 2, (size - inner) // 2), shrunk)

    return canvas.resize((px, px), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    frames = [render_icon(px) for px in ICO_SIZES]
    ico = OUT / "favicon.ico"
    # Passing the per-size frames as append_images is what makes Pillow store
    # each one as authored; sizes= alone would rescale a single source and
    # discard the per-size tuning above.
    frames[-1].save(ico, format="ICO",
                    sizes=[(p, p) for p in ICO_SIZES],
                    append_images=frames[:-1])
    print(f"  wrote {ico.relative_to(ROOT)}  ({', '.join(str(p) for p in ICO_SIZES)})")

    for px in (192, 512):
        p = OUT / f"icon-{px}.png"
        render_icon(px).save(p, format="PNG")
        print(f"  wrote {p.relative_to(ROOT)}")

    p = OUT / "icon-512-maskable.png"
    render_icon(512, inset_ratio=0.22).save(p, format="PNG")
    print(f"  wrote {p.relative_to(ROOT)}  (maskable safe zone)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
