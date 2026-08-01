/* ════════════════════════════════════════════════════════════════════════
   vinylMaps.js — procedural surface maps for a 12" LP.

   WHY THESE ARE 1-D
   -----------------
   Every surface property of a record varies with RADIUS and (to the eye)
   nothing else. So the maps are 8 px wide and 2048 px tall, sampled with
   v = (r - rHole) / (rEdge - rHole), and repeated around the disc. That is
   ~64 KB instead of ~16 MB for the equivalent 2048² map, and it gives far
   more radial resolution than a square texture could afford.

   WHY THE GROOVES ARE NOT IN THE NORMAL MAP
   -----------------------------------------
   Real groove pitch is roughly 0.1 mm. At 2048 samples across a 130 mm band
   one texel is ~0.063 mm, so individual grooves COULD be drawn — and it looks
   terrible. At any realistic camera distance a screen pixel covers dozens of
   grooves, and the result is a shimmering moiré rosette that reads as a
   rendering bug, not as vinyl.

   The physically honest answer is the one offline renderers use: individual
   grooves are a micro-facet property, not geometry. They are expressed as
   ANISOTROPIC ROUGHNESS — the microfacet distribution is stretched along the
   groove direction, which produces the swept radial sheen that actually makes
   a record look like a record. The normal map is reserved for macro relief
   the eye genuinely resolves:

     · the boundaries between tracks (visible as fine bright rings)
     · the wide, shallow lead-in spiral
     · the run-out / lock groove
     · a very slight dish warp, because no pressing is dead flat

   RADIAL ZONES (real dimensions, in millimetres, radius not diameter)
   -------------------------------------------------------------------
     0.00 –   3.6   spindle hole
     3.6  –  50.0   label (paper — its own mesh, this map just stops)
    50.0  –  57.0   deadwax / run-out land: smooth, near-mirror, matrix etching
    57.0  – 146.0   playing band: grooved, anisotropic
   146.0  – 148.0   lead-in groove: a wide shallow spiral
   148.0  – 151.0   outer land: smooth
   151.0  – 152.0   raised rim
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";

/* Record dimensions in millimetres. The scene works in millimetres throughout
   and the camera is scaled to suit, which keeps every number in this file
   checkable against a real record instead of against an arbitrary unit. */
export const LP = {
  holeRadius: 3.6,
  labelRadius: 50.0,
  deadwaxOuter: 57.0,   // grooved band starts here
  bandOuter: 146.0,     // grooved band ends here
  leadInOuter: 148.0,
  landOuter: 151.0,
  edgeRadius: 152.0,    // 304 mm diameter, i.e. a 12"
  /* A pressing is thicker at the label and at the rim than across the grooved
     band, so stacked records touch only there. It is a small difference but it
     is on the silhouette, and the silhouette is what sells the profile shot. */
  thickBand: 1.55,
  thickLabel: 1.95,
  thickRim: 1.90,
};

const TEX_H = 2048;

/* Texture width = resolution AROUND the disc.

   Almost everything about a record varies with radius and nothing else, which
   is why these maps started 8 px wide. But that makes the surface perfectly
   rotationally symmetric — and combined with an anisotropic sheen that is
   ALSO rotationally symmetric, and therefore fixed in world space, a spinning
   record looked completely static. Only the label appeared to turn, which read
   as the label spinning on a stationary disc.

   Real records are covered in things that break that symmetry: scuffs, hairline
   scratches, a fingerprint, dust. Each one catches the fixed sheen once per
   revolution. That single glint per 1.8 s turn is what tells the eye the disc
   is moving, and it costs one wider texture.

   256 is enough: at the rim that is a sample every ~3.7 mm around, which is
   far finer than any mark drawn here. */
const TEX_W = 256;

/* Normalised radius → texture v. */
const vOf = r => (r - LP.holeRadius) / (LP.edgeRadius - LP.holeRadius);
const rOf = v => LP.holeRadius + v * (LP.edgeRadius - LP.holeRadius);

/* ── Track layout ─────────────────────────────────────────────────────────
   Where the visible rings between songs land. Real sides hold 4–7 tracks of
   uneven length, so an even split would look synthetic. These fractions of the
   playing band are deliberately irregular. */
const TRACK_SPLITS = [0.0, 0.21, 0.37, 0.585, 0.72, 0.885, 1.0];

function trackGapRadii() {
  const inner = LP.deadwaxOuter;
  const outer = LP.bandOuter;
  /* Note the reversal: a record plays from the OUTSIDE IN, so split 0 is at
     the outer edge. Getting this backwards puts the gaps in the wrong places
     relative to the tonearm and nobody could say why it looked wrong. */
  return TRACK_SPLITS.map(f => outer - f * (outer - inner));
}

export const TRACK_GAPS = trackGapRadii();

/* ── Shared 1-D canvas helper ─────────────────────────────────────────── */

function makeStrip(writeRow) {
  const c = document.createElement("canvas");
  c.width = TEX_W;
  c.height = TEX_H;
  const g = c.getContext("2d", { willReadFrequently: true });
  const img = g.createImageData(TEX_W, TEX_H);
  const d = img.data;

  for (let y = 0; y < TEX_H; y++) {
    /* y=0 is the top of the canvas, which is v=1 once the texture is flipped
       by three.js. Sample v accordingly so radius increases downward in the
       source data and the flip lands it the right way up. */
    const v = 1 - y / (TEX_H - 1);
    /* The row is still computed once and repeated across the width — every
       property here really is radius-only. Azimuthal detail is added
       afterwards, on top, by addSurfaceMarks. */
    const rgba = writeRow(rOf(v), v, y);
    for (let x = 0; x < TEX_W; x++) {
      const i = (y * TEX_W + x) * 4;
      d[i] = rgba[0]; d[i + 1] = rgba[1]; d[i + 2] = rgba[2]; d[i + 3] = rgba[3];
    }
  }
  g.putImageData(img, 0, 0);
  return c;
}

/* ── Surface marks ────────────────────────────────────────────────────────
   Draws wear onto a roughness strip, in the strip's own space: x is the angle
   around the disc (0…2π across the full width, wrapping), y is radius.

   Everything is drawn SMOOTHER than the surrounding groove, not rougher. That
   is how vinyl damage actually reads — a scuff polishes the groove walls flat,
   so it catches light where the modulated groove around it scatters. Drawing
   marks darker/rougher makes them read as dirt sitting on top rather than as
   damage to the surface.

   Kept sparse on purpose. This is meant to be a well-kept record that turns
   visibly, not a thrashed one.                                               */
function addSurfaceMarks(canvas, seed) {
  const g = canvas.getContext("2d", { willReadFrequently: true });
  const rand = rng(seed);

  const yOf = r => (1 - vOf(r)) * (TEX_H - 1);
  const bandTop = yOf(LP.bandOuter);
  const bandBottom = yOf(LP.deadwaxOuter);

  g.save();
  /* multiply darkens, and this canvas is a ROUGHNESS map, so darker = smoother
     = shinier. That is the right direction: a scuff burnishes the groove flat,
     and it glints where the modulated groove around it scatters. */
  g.globalCompositeOperation = "multiply";

  /* Hairline arcs: the commonest mark on a played record, from sliding it in
     and out of an inner sleeve. They run WITH the groove, so they are long in
     x and only a texel or two tall. */
  for (let i = 0; i < 14; i++) {
    const y = bandTop + rand() * (bandBottom - bandTop);
    const x0 = rand() * TEX_W;
    const len = TEX_W * (0.06 + rand() * 0.30);
    const dark = 0.55 + rand() * 0.3;

    g.strokeStyle = `rgba(${Math.round(dark * 255)},${Math.round(dark * 255)},${Math.round(dark * 255)},0.85)`;
    g.lineWidth = 0.8 + rand() * 1.4;
    g.beginPath();
    g.moveTo(x0, y);
    g.lineTo(x0 + len, y + (rand() - 0.5) * 5);
    g.stroke();
    /* Wrap: a mark running off the right edge continues at the left, because
       x is an angle. Without this the seam is a visible vertical join. */
    if (x0 + len > TEX_W) {
      g.beginPath();
      g.moveTo(x0 + len - TEX_W - len, y);
      g.lineTo(x0 + len - TEX_W, y + (rand() - 0.5) * 5);
      g.stroke();
    }
  }

  /* A couple of radial scuffs — from a stylus dropped carelessly, or a sleeve
     dragged across. Short in x, tall in y, and the most visible of the lot
     because they cut across the groove direction. */
  for (let i = 0; i < 3; i++) {
    const x = rand() * TEX_W;
    const y0 = bandTop + rand() * (bandBottom - bandTop) * 0.7;
    const h = (bandBottom - bandTop) * (0.05 + rand() * 0.14);
    g.strokeStyle = "rgba(150,150,150,0.55)";
    g.lineWidth = 0.9 + rand() * 1.1;
    g.beginPath();
    g.moveTo(x, y0);
    g.lineTo(x + (rand() - 0.5) * 7, y0 + h);
    g.stroke();
  }

  /* Dust and specks, across the whole grooved band. */
  for (let i = 0; i < 260; i++) {
    const x = rand() * TEX_W;
    const y = bandTop + rand() * (bandBottom - bandTop);
    const s = 0.6 + rand() * 1.5;
    const v = 170 + Math.floor(rand() * 60);
    g.fillStyle = `rgba(${v},${v},${v},0.5)`;
    g.fillRect(x, y, s, s);
  }

  g.restore();
  return canvas;
}

function texFrom(canvas, colorSpace = THREE.NoColorSpace) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.ClampToEdgeWrapping;
  t.colorSpace = colorSpace;
  t.generateMipmaps = true;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.magFilter = THREE.LinearFilter;
  return t;
}

/* Deterministic PRNG, so the wear pattern on the disc is the same every run.
   Randomising it per session would mean the record's scratches rearranged
   themselves every time the view opened. */
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 4294967296;
  };
}

/* Smooth 0→1 across [a,b]; returns 0 below a and 1 above b. */
const smooth = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

/* Distance to the nearest track boundary, in millimetres. */
function gapProximity(r) {
  let best = Infinity;
  for (const gr of TRACK_GAPS) best = Math.min(best, Math.abs(r - gr));
  return best;
}

/* Deterministic 1-D value noise over radius, in millimetres.

   Programme-material variation has to be IRREGULAR. Summed sines were the
   first attempt and they produced a set of evenly-spaced concentric rings
   marching across the disc — an interference pattern, not a record. Real
   groove modulation has no period, so neither does this. */
function hash1(n) {
  const s = Math.sin(n * 127.1) * 43758.5453;
  return s - Math.floor(s);
}

function noise1(x) {
  const i = Math.floor(x);
  const f = x - i;
  const u = f * f * (3 - 2 * f);
  return (hash1(i) * (1 - u) + hash1(i + 1) * u) * 2 - 1;
}

/* Two octaves at millimetre and sub-millimetre scale. The fine octave exists
   to be averaged away by mipmapping at distance while still breaking up the
   surface in close-ups. */
function grooveNoise(r) {
  return noise1(r * 0.72) * 0.62 + noise1(r * 3.9) * 0.28 + noise1(r * 17.3) * 0.10;
}

/* ── Roughness ────────────────────────────────────────────────────────────
   The single most important map. Vinyl's lands are near-mirror; the grooved
   band is much rougher because the microfacets are cut into it. The contrast
   between the two is the cue that says "this surface has grooves" even when no
   individual groove is visible. */
export function makeRoughnessMap() {
  const canvas = makeStrip(r => {
    let rough;

    if (r < LP.labelRadius) {
      rough = 0.85;                                   // paper, though the label mesh covers this
    } else if (r < LP.deadwaxOuter) {
      /* Deadwax: the smoothest part of the record. This is the mirror ring
         between the label and the first groove, and it is what produces the
         bright sliver of reflected light next to the label. */
      rough = 0.045 + 0.02 * smooth(LP.labelRadius, LP.deadwaxOuter, r);
    } else if (r < LP.bandOuter) {
      /* Grooved band. The contrast between this and the mirror lands either
         side is the cue that says "grooves" even when no groove is resolvable.
         Outer tracks on a real LP are cut hotter than inner ones, so the band
         is not uniform. */
      const t = (r - LP.deadwaxOuter) / (LP.bandOuter - LP.deadwaxOuter);
      rough = 0.34 + 0.05 * t;

      /* Programme material: irregular, not periodic. See grooveNoise. */
      rough += 0.038 * grooveNoise(r);

      /* Track boundaries: a thin band of unmodulated, therefore smoother,
         groove. This is the bright ring you see between songs. */
      const gp = gapProximity(r);
      rough -= 0.20 * (1 - smooth(0.0, 0.9, gp));
    } else if (r < LP.leadInOuter) {
      rough = 0.13;                                   // lead-in: wide spiral, smoother
    } else if (r < LP.landOuter) {
      rough = 0.06;                                   // outer land: mirror
    } else {
      rough = 0.09;                                   // rim, slightly scuffed by handling
    }

    /* Edge wear. Records get picked up by the rim, so the outermost couple of
       millimetres are always duller than the land behind them. */
    rough += 0.10 * smooth(LP.landOuter, LP.edgeRadius, r);

    const v = Math.round(Math.min(1, Math.max(0, rough)) * 255);
    return [v, v, v, 255];
  });

  /* Wear goes on last, over the radial base. This is the only map with
     azimuthal detail, and it is what makes the disc read as turning. */
  addSurfaceMarks(canvas, 0x5eed1a);

  return texFrom(canvas);
}

/* ── Normal ───────────────────────────────────────────────────────────────
   Tangent-space. The record's tangent frame is supplied analytically by
   record.js (tangent = circumferential, bitangent = radial), so the GREEN
   channel here perturbs the normal along the RADIAL direction — which is the
   only direction macro relief varies in. Red stays neutral. */
export function makeNormalMap() {
  /* Build a height profile first, then differentiate it. Differentiating a
     height field is the only way to get slopes that are actually consistent
     with each other; hand-authoring the slopes directly always produces relief
     that lights incorrectly from one side. */
  const height = new Float32Array(TEX_H);

  for (let y = 0; y < TEX_H; y++) {
    const v = 1 - y / (TEX_H - 1);
    const r = rOf(v);
    let h = 0;

    if (r >= LP.deadwaxOuter && r <= LP.bandOuter) {
      /* Track boundaries sit a hair proud of the modulated groove either side. */
      const gp = gapProximity(r);
      h += 0.055 * (1 - smooth(0.0, 1.1, gp));
    }

    if (r > LP.bandOuter && r < LP.leadInOuter) {
      /* Lead-in: a genuinely wide spiral, ~4 turns over 2 mm, and one of the
         few places on a record where the naked eye resolves individual turns. */
      const t = (r - LP.bandOuter) / (LP.leadInOuter - LP.bandOuter);
      h += 0.16 * Math.sin(t * Math.PI * 8);
    }

    if (r > LP.labelRadius && r < LP.deadwaxOuter) {
      /* Run-out groove: one deep, isolated ring just outside the label. */
      const rg = Math.abs(r - (LP.labelRadius + 3.2));
      h -= 0.20 * Math.exp(-(rg * rg) / 0.12);
    }

    /* A very shallow dish across the whole disc. No pressing is flat, and a
       dead-flat record reads as CG immediately — the give-away is that the
       specular highlight runs perfectly straight. */
    h += 0.04 * Math.cos((r / LP.edgeRadius) * Math.PI * 1.4);

    height[y] = h;
  }

  /* Central difference → slope → tangent-space normal. */
  const canvas = makeStrip((r, v, y) => {
    const hPrev = height[Math.max(0, y - 1)];
    const hNext = height[Math.min(TEX_H - 1, y + 1)];
    /* dv in millimetres, so the slope is a real gradient rather than an
       arbitrary one that would change if the texture height changed. */
    const dr = (2 / (TEX_H - 1)) * (LP.edgeRadius - LP.holeRadius);
    const slope = (hNext - hPrev) / dr;

    /* n = normalize(0, 1, -slope) in (tangent, bitangent, normal) terms, packed
       so green carries the radial component. */
    const inv = 1 / Math.sqrt(1 + slope * slope);
    const ny = -slope * inv;
    const nz = inv;

    return [
      128,                                   // no circumferential relief
      Math.round((ny * 0.5 + 0.5) * 255),
      Math.round((nz * 0.5 + 0.5) * 255),
      255,
    ];
  });

  return texFrom(canvas);
}

/* ── Anisotropy ───────────────────────────────────────────────────────────
   three.js reads anisotropy strength from the BLUE channel and direction from
   red/green, as a 2-D vector in tangent space remapped from [0,1] to [-1,1].

   THE DIRECTION IS RADIAL, NOT CIRCUMFERENTIAL — and getting this backwards is
   the easiest mistake to make here, because "the grooves run circumferentially"
   makes circumferential feel like the obvious answer.

   Look at what the direction vector actually selects. In three.js the vector
   picks the axis whose roughness is RAISED toward 1:

       material.alphaT = mix( roughness², 1.0, anisotropy² )

   A raised roughness along an axis spreads the specular lobe ALONG that axis.
   Now think about brushed metal: scratches running one way make the normal
   vary across them, so the highlight smears PERPENDICULAR to the scratches.
   Grooves run circumferentially, therefore the highlight smears radially,
   therefore the rough axis — the direction vector — is radial.

   Set it circumferential and the highlight wraps into a bright concentric
   ring. Set it radially and you get the swept double-lobed bowtie that reads
   instantly as vinyl.

   record.js supplies an analytic tangent frame in which the BITANGENT is the
   outward radial direction, so radial is the vector (0, +1). */
export function makeAnisotropyMap() {
  const canvas = makeStrip(r => {
    let strength;

    /* STRENGTH IS CAPPED WELL BELOW 1, and that is not a taste decision.

       three.js approximates anisotropic environment reflection by bending the
       shading normal along the anisotropy axis:

           bentNormal = cross(bitangent, viewDir)
           bentNormal = normalize(cross(bentNormal, bitangent))
           bentNormal = mix(bentNormal, normal, f(anisotropy, roughness))

       At full strength on a large horizontal surface that bend is severe enough
       to tip the reflection vector BELOW the horizon over roughly half the
       disc, where it samples the dark floor and the record renders as a hard-
       edged wedge of pure black rotating across it. It reads as a rendering
       bug because it is one.

       0.5 keeps the swept sheen and stays inside the approximation's usable
       range. If this is ever ported to a renderer with a real anisotropic GGX
       IBL, it can go back up.

       This cap is only half the fix, and it was NOT the half that mattered —
       capping alone still left a visible eighth of the disc dark. The bend has
       to have somewhere uniform to land, which is what the continuous horizon
       cylinder in stage.js provides. Removing either one brings the wedge
       back.                                                                  */
    const MAX = 0.5;

    if (r < LP.deadwaxOuter) {
      strength = 0.0;
    } else if (r < LP.bandOuter) {
      strength = MAX;
      /* Track gaps are less modulated, so their anisotropy is weaker — the
         boundary rings read as slightly glassier than the music either side. */
      strength -= MAX * 0.45 * (1 - smooth(0.0, 0.8, gapProximity(r)));
    } else if (r < LP.leadInOuter) {
      strength = MAX * 0.55;
    } else {
      strength = 0.0;
    }

    return [
      128,                                             // direction x =  0
      255,                                             // direction y = +1 → radial
      Math.round(Math.min(1, Math.max(0, strength)) * 255),
      255,
    ];
  });

  return texFrom(canvas);
}

/* ── Albedo tint ──────────────────────────────────────────────────────────
   Vinyl is not #000. It is a very dark warm grey-brown with a faint bloom of
   scattered light in the grooved band, because light that enters the groove
   bounces off both walls before coming back. Pure black kills every bit of
   form in the disc and is the fastest way to make it look like a hole. */
export function makeAlbedoMap() {
  const canvas = makeStrip(r => {
    /* Base carbon-loaded PVC, sampled off photographs of black vinyl. */
    let R = 13, G = 12, B = 14;

    if (r >= LP.deadwaxOuter && r <= LP.bandOuter) {
      /* Multiple scattering inside the groove lifts the band very slightly. */
      const lift = 7 * (1 - smooth(0.0, 1.2, gapProximity(r)));
      R += 5 + lift; G += 5 + lift; B += 6 + lift;
    }

    if (r > LP.landOuter) {
      /* Handling haze on the rim. */
      R += 6; G += 6; B += 6;
    }

    return [R, G, B, 255];
  });

  return texFrom(canvas, THREE.SRGBColorSpace);
}

/* Build every map once and share the result. These are pure functions of the
   constants above, so a second caller would only be paying to recompute an
   identical texture. */
let cached = null;
export function vinylMaps() {
  if (!cached) {
    cached = {
      albedo: makeAlbedoMap(),
      roughness: makeRoughnessMap(),
      normal: makeNormalMap(),
      anisotropy: makeAnisotropyMap(),
    };
  }
  return cached;
}
