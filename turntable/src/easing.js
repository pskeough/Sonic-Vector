/* ════════════════════════════════════════════════════════════════════════
   easing.js — easing curves and a tiny keyframe evaluator.

   The choreography is the whole point of this view, so the curves are chosen
   per-motion rather than defaulting to ease-in-out everywhere. Machines do not
   move on a single curve: a solenoid snaps, a damped cue lever glides, a
   platter with real rotational inertia coasts, and a record dropped onto a
   spindle settles with a decaying bounce. Using one curve for all of them is
   what makes CG mechanisms feel weightless.
   ════════════════════════════════════════════════════════════════════════ */

export const Ease = {
  linear: t => t,

  /* General-purpose. The 0.32/0.72 control points match the curve the console's
     own CSS uses for panel transitions, so the two views move alike. */
  standard: t => cubicBezier(0.32, 0.72, 0, 1, t),

  /* Damped mechanism: fast off the mark, long settle. This is what a viscous
     cue lift actually does, and getting it right is most of why a needle drop
     feels expensive. */
  damped: t => 1 - Math.pow(1 - t, 3.4),

  /* Solenoid: near-instant, tiny overshoot. For a skip, where the arm is being
     yanked rather than lowered. */
  snap: t => {
    const s = 1.70158;
    const u = t - 1;
    return u * u * ((s + 1) * u + s) + 1;
  },

  /* Rotational inertia spinning up: torque-limited, so it starts slowly and
     asymptotes toward the target rate. */
  spinUp: t => 1 - Math.pow(1 - t, 2.1),

  /* Coasting to a stop. A platter under roughly constant bearing and belt drag
     decelerates roughly linearly, so this is close to linear with a little
     inertia held at the top and a soft arrival at zero.

     NOTE: like every curve here it must run 0 → 1, and the value it returns is
     fed to lerp(runningSpeed, 0, e). The first version of this simplified to
     (1 − t)^2.6, which runs 1 → 0, so the platter accelerated from a standstill
     during the spin-DOWN and braked during the spin-up. It was invisible in the
     source and obvious the moment the rates were logged frame by frame. */
  spinDown: t => t * t * (3 - 2 * t),

  /* Settling onto the spindle: a decaying bounce.

     Rectified on purpose. An un-rectified cosine overshoots past 1, and since
     this drives lerp(dropHeight, 0, e) an overshoot puts the record BELOW the
     platter — it sinks through the mat on first contact. Taking the absolute
     value bounds the curve at 1 and turns the overshoot into what actually
     happens physically: the disc hits, rebounds upward, and settles. */
  settle: t => {
    if (t >= 1) return 1;
    return 1 - Math.exp(-6.2 * t) * Math.abs(Math.cos(t * Math.PI * 3.1));
  },

  easeInQuad: t => t * t,
  easeOutQuad: t => t * (2 - t),
  easeInOutQuad: t => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),
  easeInCubic: t => t * t * t,
  easeOutCubic: t => 1 - Math.pow(1 - t, 3),
  easeInOutCubic: t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
};

/* Newton-solved cubic Bezier, the same formulation CSS uses. Two iterations of
   Newton then a bisection fallback keeps it accurate without a lookup table. */
function cubicBezier(x1, y1, x2, y2, x) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;

  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;

  const sampleX = t => ((ax * t + bx) * t + cx) * t;
  const sampleY = t => ((ay * t + by) * t + cy) * t;
  const slopeX = t => (3 * ax * t + 2 * bx) * t + cx;

  let t = x;
  for (let i = 0; i < 4; i++) {
    const d = slopeX(t);
    if (Math.abs(d) < 1e-6) break;
    const err = sampleX(t) - x;
    if (Math.abs(err) < 1e-6) return sampleY(t);
    t -= err / d;
  }

  let lo = 0, hi = 1;
  t = x;
  for (let i = 0; i < 20; i++) {
    const v = sampleX(t);
    if (Math.abs(v - x) < 1e-6) break;
    v > x ? (hi = t) : (lo = t);
    t = (lo + hi) / 2;
  }
  return sampleY(t);
}

export const lerp = (a, b, t) => a + (b - a) * t;

/* ── Keyframe track ───────────────────────────────────────────────────────
   A channel is a list of { t, v, ease } sorted by time. Evaluating before the
   first key holds the first value; after the last, holds the last. The ease
   named on a key governs the segment ARRIVING at that key, which reads the way
   an animator thinks ("this move eases in").                                */

export class Track {
  constructor(keys) {
    this.keys = keys;
  }

  at(time) {
    const k = this.keys;
    if (time <= k[0].t) return k[0].v;
    if (time >= k[k.length - 1].t) return k[k.length - 1].v;

    for (let i = 1; i < k.length; i++) {
      if (time <= k[i].t) {
        const a = k[i - 1], b = k[i];
        const span = b.t - a.t;
        /* A zero-length segment is a hard cut, not a divide-by-zero. */
        if (span <= 0) return b.v;
        const ease = b.ease || Ease.standard;
        return lerp(a.v, b.v, ease((time - a.t) / span));
      }
    }
    return k[k.length - 1].v;
  }

  get duration() { return this.keys[this.keys.length - 1].t; }
}

/* Build several tracks at once from a plain object of key arrays. */
export function tracks(spec) {
  const out = {};
  for (const [name, keys] of Object.entries(spec)) out[name] = new Track(keys);
  return out;
}
