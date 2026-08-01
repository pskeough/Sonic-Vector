/* ════════════════════════════════════════════════════════════════════════
   proceduralCover.js — generates a plausible record-label image on a canvas.

   Two jobs, and the second is the one that matters:

     1. Test fixtures. The prototype runs offline, so it needs album art that
        exists without a network round trip.
     2. Production fallback. `current_track.album_art` is empty on every
        idle/placeholder payload the backend sends, on Last.fm tracks that
        have no artwork, and on any Spotify track whose CDN fetch fails. A
        blank white disc in those cases would look broken, so the same
        generator ships with the real view.

   Everything is derived from a hash of the album name, so a given album gets
   the same label every time — a cover that reshuffles between polls would be
   worse than no cover at all.

   The palette is drawn from the console's own tokens rather than from
   arbitrary hues, so a fallback label still reads as part of this product:
     --clay #d97757   --amber #e9a13b   --sage #7c8a62
     --fascia #ece8db --ink #262520     --well #1c1914
   ════════════════════════════════════════════════════════════════════════ */

const COVER_SIZE = 1024;

/* FNV-1a. Small, dependency-free, and good enough for picking a palette. */
function hash(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/* Deterministic PRNG seeded off the hash, so layout is stable per album. */
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 4294967296;
  };
}

/* Console-native palettes. Each is [background, ink, accent]. */
const PALETTES = [
  ["#1c1914", "#ece8db", "#d97757"], // well / fascia / clay
  ["#ece8db", "#262520", "#b85c3f"], // fascia / ink / clay-deep
  ["#35200f", "#f3b152", "#7a5233"], // wood-3 / amber-led / wood-1
  ["#26231e", "#a4c076", "#7c8a62"], // desk / sage-led / sage
  ["#d97757", "#1c1914", "#e9a13b"], // clay / well / amber
  ["#3d5a46", "#ece8db", "#a4c076"], // spruce / fascia / sage-led
  ["#17150f", "#e9a13b", "#e06a50"], // desk-2 / amber / red-led
  ["#f1eee4", "#55351d", "#d97757"], // panel / wood-2 / clay
];

export function makeProceduralCover(entry) {
  const key = entry.album_name || entry.track_name || "untitled";
  const seed = hash(key);
  const rand = rng(seed);
  const [bg, ink, accent] = PALETTES[seed % PALETTES.length];

  const c = document.createElement("canvas");
  c.width = c.height = COVER_SIZE;
  const g = c.getContext("2d");
  const S = COVER_SIZE;

  g.fillStyle = bg;
  g.fillRect(0, 0, S, S);

  /* One of four sleeve archetypes. Real sleeves are graphic, not photographic,
     and a label is only ~38 mm across on the record — fine detail is wasted, so
     every archetype is built from large high-contrast shapes that survive at
     the size the disc actually renders them. */
  const archetype = seed % 4;

  if (archetype === 0) {
    // Concentric arcs, off-centre.
    const cx = S * (0.3 + rand() * 0.4);
    const cy = S * (0.3 + rand() * 0.4);
    for (let i = 12; i >= 1; i--) {
      g.beginPath();
      g.arc(cx, cy, (i / 12) * S * 0.75, 0, Math.PI * 2);
      g.fillStyle = i % 2 ? accent : bg;
      g.globalAlpha = 0.35 + (i / 12) * 0.5;
      g.fill();
    }
    g.globalAlpha = 1;
  } else if (archetype === 1) {
    // Hard diagonal split with a colour field.
    g.fillStyle = accent;
    g.beginPath();
    g.moveTo(0, S * (0.2 + rand() * 0.4));
    g.lineTo(S, S * (0.1 + rand() * 0.4));
    g.lineTo(S, S);
    g.lineTo(0, S);
    g.closePath();
    g.fill();
  } else if (archetype === 2) {
    // Bauhaus-ish blocks on a grid.
    const n = 3 + Math.floor(rand() * 2);
    const cell = S / n;
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const r = rand();
        if (r < 0.45) continue;
        g.fillStyle = r < 0.75 ? accent : ink;
        g.globalAlpha = 0.5 + rand() * 0.5;
        if (r > 0.9) {
          g.beginPath();
          g.arc(x * cell + cell / 2, y * cell + cell / 2, cell * 0.42, 0, Math.PI * 2);
          g.fill();
        } else {
          g.fillRect(x * cell, y * cell, cell, cell);
        }
      }
    }
    g.globalAlpha = 1;
  } else {
    // Horizon: a single sun disc over a banded field.
    const horizon = S * (0.5 + rand() * 0.2);
    g.fillStyle = accent;
    g.beginPath();
    g.arc(S / 2, horizon, S * 0.26, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = bg;
    for (let i = 0; i < 9; i++) {
      const yy = horizon + i * (S * 0.028);
      g.globalAlpha = i / 9;
      g.fillRect(0, yy, S, S * 0.014);
    }
    g.globalAlpha = 1;
    g.fillStyle = ink;
    g.globalAlpha = 0.15;
    g.fillRect(0, horizon, S, S - horizon);
    g.globalAlpha = 1;
  }

  /* Type.

     Position is dictated by how this image gets used. On the record it is the
     LABEL, and the label is a circle inscribed in the cover's square — so
     anything within about 15 % of an edge is outside the circle and gets cut.
     The first pass set the type flush to the bottom-left in proper sleeve
     fashion and the record came back reading "ILES DAVIS / of Blue".

     So the block sits inside the inscribed circle, centred horizontally, below
     the middle: clear of the spindle hole at the centre, clear of the crop at
     the rim. */
  g.fillStyle = ink;
  g.textBaseline = "alphabetic";
  g.textAlign = "center";

  /* Chord width of the label circle at the type's height, minus a margin. */
  const safeWidth = S * 0.58;

  const artist = (entry.artist_name || "").toUpperCase();
  g.font = `600 ${Math.round(S * 0.040)}px "Space Grotesk", system-ui, sans-serif`;
  g.letterSpacing = `${Math.round(S * 0.008)}px`;
  g.fillText(fit(g, artist, safeWidth), S * 0.5, S * 0.665);

  const album = entry.album_name || entry.track_name || "";
  g.font = `400 ${Math.round(S * 0.058)}px Lora, Georgia, serif`;
  g.letterSpacing = "0px";
  g.fillText(fit(g, album, safeWidth), S * 0.5, S * 0.745);

  g.textAlign = "left";

  /* A film-grain wash. Flat vector fields look plastic under a specular
     highlight; a little noise gives the label something to catch the light on.

     It has to go through a scratch canvas and drawImage. putImageData does not
     composite — it overwrites the destination pixels wholesale, alpha included
     — so writing the grain straight onto the cover replaces the artwork with a
     sheet of flat grey. */
  const gc = document.createElement("canvas");
  gc.width = gc.height = S;
  const gctx = gc.getContext("2d");
  const grain = gctx.createImageData(S, S);
  const d = grain.data;
  for (let i = 0; i < d.length; i += 4) {
    const v = 128 + (Math.random() - 0.5) * 60;
    d[i] = d[i + 1] = d[i + 2] = v;
    d[i + 3] = 255;
  }
  gctx.putImageData(grain, 0, 0);

  g.globalAlpha = 0.055;
  g.globalCompositeOperation = "overlay";
  g.drawImage(gc, 0, 0);
  g.globalAlpha = 1;
  g.globalCompositeOperation = "source-over";

  return c.toDataURL("image/png");
}

/* ════════════════════════════════════════════════════════════════════════
   HOUSE LABEL — the fallback when a track has no artwork at all.

   This is not a placeholder and should not look like one. It comes up often
   in normal use: every idle payload the backend sends has an empty album_art,
   Last.fm tracks frequently have none, local playback only has art if the
   media session supplied it, and any CDN fetch can fail. A blank white disc
   in those cases reads as broken.

   So it is designed as an actual record label: a colour field, a printed
   centre, the artist and title set properly, and a house mark round the
   bottom. Three variants, chosen by hashing the ARTIST — so everything by the
   same artist comes up on the same label, the way a real imprint works, and
   the choice is stable across sessions.

   Everything lives inside the circle inscribed in this square, because the
   label crops to that circle when it is mapped onto the record.
   ════════════════════════════════════════════════════════════════════════ */

const HOUSE_VARIANTS = [
  /* field, ink, rule — all straight from the console's tokens. */
  { field: "#d97757", ink: "#241a12", rule: "#8f4630" },   // clay
  { field: "#ece8db", ink: "#262520", rule: "#b85c3f" },   // fascia
  { field: "#3d5a46", ink: "#ece8db", rule: "#a4c076" },   // spruce
];

export function makeHouseLabel(entry) {
  const seed = hash((entry.artist_name || entry.album_name || "unknown").toLowerCase());
  const v = HOUSE_VARIANTS[seed % HOUSE_VARIANTS.length];

  const c = document.createElement("canvas");
  c.width = c.height = COVER_SIZE;
  const g = c.getContext("2d");
  const S = COVER_SIZE;
  const mid = S / 2;

  g.fillStyle = v.field;
  g.fillRect(0, 0, S, S);

  /* Two printed rules, the way almost every label has. The outer one sits just
     inside the crop so it reads as the label's own edge. */
  g.strokeStyle = v.rule;
  g.lineWidth = S * 0.012;
  g.beginPath(); g.arc(mid, mid, S * 0.455, 0, Math.PI * 2); g.stroke();
  g.lineWidth = S * 0.005;
  g.beginPath(); g.arc(mid, mid, S * 0.425, 0, Math.PI * 2); g.stroke();

  /* Centre boss. Real labels leave a clear disc around the spindle hole, and
     it doubles as a quiet background for the type. */
  g.fillStyle = v.ink;
  g.globalAlpha = 0.10;
  g.beginPath(); g.arc(mid, mid, S * 0.30, 0, Math.PI * 2); g.fill();
  g.globalAlpha = 1;

  /* The spindle hole itself. The record's own geometry punches through here,
     so this only has to look right in the ring immediately around it. */
  g.fillStyle = v.ink;
  g.globalAlpha = 0.5;
  g.beginPath(); g.arc(mid, mid, S * 0.036, 0, Math.PI * 2); g.fill();
  g.globalAlpha = 1;

  g.fillStyle = v.ink;
  g.textAlign = "center";
  g.textBaseline = "alphabetic";

  /* Artist above the hole, title below it — the standard arrangement, and it
     keeps both clear of the punched centre. */
  const artist = (entry.artist_name || "").toUpperCase();
  if (artist) {
    g.font = `600 ${Math.round(S * 0.040)}px "Space Grotesk", system-ui, sans-serif`;
    g.letterSpacing = `${Math.round(S * 0.010)}px`;
    g.fillText(fit(g, artist, S * 0.60), mid, S * 0.395);
  }

  const title = entry.track_name || entry.album_name || "";
  if (title) {
    g.letterSpacing = "0px";
    /* Two lines if it will not fit on one — track titles run long, and
       ellipsising "Everything In Its Right Place" to "Everything…" loses the
       one piece of information the label exists to carry. */
    g.font = `400 ${Math.round(S * 0.058)}px Lora, Georgia, serif`;
    const lines = wrap(g, title, S * 0.60, 2);
    lines.forEach((line, i) => g.fillText(line, mid, S * 0.615 + i * S * 0.072));
  }

  const album = entry.album_name || "";
  if (album && album !== title) {
    g.font = `400 ${Math.round(S * 0.028)}px "IBM Plex Mono", Consolas, monospace`;
    g.letterSpacing = `${Math.round(S * 0.004)}px`;
    g.globalAlpha = 0.72;
    g.fillText(fit(g, album.toUpperCase(), S * 0.56), mid, S * 0.755);
    g.globalAlpha = 1;
  }

  /* House mark, curved along the bottom of the label as an imprint's would be. */
  arcText(g, "SONIC VECTOR", mid, mid, S * 0.365, Math.PI * 0.5, {
    font: `500 ${Math.round(S * 0.030)}px "IBM Plex Mono", Consolas, monospace`,
    fill: v.ink,
    alpha: 0.55,
    spread: 0.62,
  });

  g.letterSpacing = "0px";
  g.textAlign = "left";
  return c.toDataURL("image/png");
}

/* Set a string around a circular arc centred on `centreAngle`.

   Two things here are easy to get wrong and were both wrong first time, in a
   way that is only visible once rendered:

   ROTATION. Canvas angles run clockwise with +Y downward, and a glyph's "up" is
   local −Y. Rotating by `a + π/2` points that up-vector radially OUTWARD, which
   is right for text across the top of a circle and upside down across the
   bottom. `a − π/2` points it toward the centre, which is how record labels and
   rubber stamps set their bottom text — upright to a reader looking at the
   label the right way up.

   DIRECTION. Along the bottom arc, INCREASING the angle moves left across the
   canvas. Stepping forward through the string therefore lays it out
   right-to-left, i.e. mirrored. The index has to walk the angle backwards. */
function arcText(g, text, cx, cy, radius, centreAngle, opts) {
  g.save();
  g.font = opts.font;
  g.fillStyle = opts.fill;
  g.globalAlpha = opts.alpha ?? 1;
  g.textAlign = "center";
  g.textBaseline = "middle";

  const step = opts.spread / Math.max(1, text.length - 1);
  const start = centreAngle + opts.spread / 2;

  for (let i = 0; i < text.length; i++) {
    const a = start - i * step;
    g.save();
    g.translate(cx + Math.cos(a) * radius, cy + Math.sin(a) * radius);
    g.rotate(a - Math.PI / 2);
    g.fillText(text[i], 0, 0);
    g.restore();
  }
  g.restore();
}

/* Greedy wrap to at most `maxLines`. If words are left over, the final line is
   ellipsised — a long title is better truncated than silently dropped. */
function wrap(g, text, maxWidth, maxLines) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";

  for (let i = 0; i < words.length; i++) {
    const candidate = line ? `${line} ${words[i]}` : words[i];

    if (g.measureText(candidate).width <= maxWidth) {
      line = candidate;
      continue;
    }

    if (lines.length === maxLines - 1) {
      /* Out of lines with words remaining: everything left goes on this one
         and gets trimmed to fit. */
      line = candidate;
      for (let j = i + 1; j < words.length; j++) line += ` ${words[j]}`;
      break;
    }

    if (line) lines.push(line);
    line = words[i];
  }

  if (line) lines.push(line);
  return lines.slice(0, maxLines).map(l => fit(g, l, maxWidth));
}

/* Trim a string with an ellipsis until it fits a pixel width. */
function fit(g, text, maxWidth) {
  if (g.measureText(text).width <= maxWidth) return text;
  let t = text;
  while (t.length > 1 && g.measureText(t + "…").width > maxWidth) {
    t = t.slice(0, -1);
  }
  return t + "…";
}
