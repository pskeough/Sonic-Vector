/* ════════════════════════════════════════════════════════════════════════
   choreography.js — the state machine that decides what the deck is doing.

   THE LATENCY PROBLEM, AND WHY IT IS ACTUALLY AN OPPORTUNITY
   ---------------------------------------------------------
   The app learns about a track change by polling /api/status every 1.5 s and
   noticing that track_id changed. So the view finds out somewhere between 0
   and 1500 ms late, with no warning. A 4-second record-swap animation started
   at that moment is a 4-second animation that began up to 1.5 s after the
   music already moved on, and it never catches up.

   But the payload also carries progress_ms and duration_ms. That means the
   view can SEE the end of the song coming. When playback passes ~98.5 % the
   changeover is started speculatively — which is not a trick, it is exactly
   what the machine being simulated does. A real tonearm reaches the run-out
   groove and lifts because the record ran out, not because something told it
   the next song had begun. By the time the poll confirms the new track, the
   arm is already up and the platter is already slowing, and the new album art
   has had a second and a half to decode before it is needed.

   That gives two genuinely different changeovers, and the difference is
   legible at a glance:

     NATURAL  the side ran out. Slow, ceremonial, ~4.6 s. Full spin-down,
              the record lifts away, a new one settles, spin-up, a damped cue.
     SKIP     someone pressed next. Urgent, ~2.5 s. The arm snaps up, the
              platter never fully stops, the outgoing record is flicked away.

   Both end the same way: a slow, damped needle drop, because every cueing
   device ever built is viscously damped and that descent is the money shot.
   ════════════════════════════════════════════════════════════════════════ */

import { Ease, Track, tracks, lerp } from "./easing.js";
import { ARM, radiusForProgress } from "./scene/tonearm.js";

/* Scale every key time in a timeline spec. */
function stretch(spec, factor) {
  const out = {};
  for (const [name, keys] of Object.entries(spec)) {
    out[name] = keys.map(k => ({ ...k, t: k.t * factor }));
  }
  return out;
}

export const RPM_33 = 100 / 3;   // 33⅓

/* Playback fraction past which the end of the side is treated as imminent. */
const PREDICT_THRESHOLD = 0.985;

/* Global stretch on both changeover timelines. Every key time below is written
   at 1.0 and scaled by this, so the whole sequence can be paced without
   re-deriving thirty numbers and losing the overlaps between them. */
const SWAP_SPEED = 1.38;

/* How fast the local playback clock is allowed to correct itself toward the
   figure the poll reports, in ms of correction per ms elapsed.

   The clock runs continuously between polls so the tonearm sweeps smoothly;
   each poll then arrives with a slightly different idea of the time. Snapping
   to it makes the arm twitch every 1.5 s, which is exactly what it used to do.
   Bleeding the difference off at 4 % means a half-second disagreement is gone
   within a few seconds and the motion never visibly steps. */
const CLOCK_SLEW = 0.04;

export const State = {
  IDLE: "idle",
  PLAYING: "playing",
  PAUSED: "paused",
  SWAPPING: "swapping",
};

/* ── Natural changeover ───────────────────────────────────────────────────
   Times in ms. The overlaps are deliberate: the platter is already slowing
   while the arm is still returning, and the incoming record starts down before
   the outgoing one is fully gone. Strictly sequential mechanisms look like a
   checklist being executed rather than a machine working. */
function naturalTimeline() {
  return tracks(stretch({
    /* Arm lifts out of the run-out groove first — this is the trigger for
       everything else, and it is what a real deck does — then, at the very end,
       descends onto the lead-in of the new record.

       Both moves live on ONE channel. They were briefly split across two, with
       the pose taking max(lift, drop); since the lift channel held at 1 for the
       rest of the timeline, that max pinned the arm up and the descent never
       played at all. The needle then flopped down afterwards on the steady-state
       controller's generic easing — the single best beat in the sequence,
       replaced by an exponential approach, and nothing about it looked broken
       enough to notice without logging the values frame by frame. */
    armLift: [
      { t: 0, v: 0 },
      { t: 430, v: 1, ease: Ease.damped },
      { t: 4060, v: 1 },
      /* The money shot: a slow, viscously damped descent onto the lead-in. */
      { t: 4680, v: 0, ease: Ease.damped },
    ],
    armRadius: [
      { t: 0, v: ARM.runOutRadius },
      { t: 430, v: ARM.runOutRadius },
      { t: 1330, v: ARM.restRadius, ease: Ease.standard },
      { t: 3150, v: ARM.restRadius },
      { t: 4060, v: ARM.leadInRadius, ease: Ease.standard },
    ],
    /* Long coast down, long torque-limited spin back up. The platter is the
       heaviest thing on the deck and should behave like it. */
    platterRpm: [
      { t: 300, v: RPM_33 },
      { t: 2500, v: 0, ease: Ease.spinDown },
      { t: 2960, v: 0 },
      { t: 4360, v: RPM_33, ease: Ease.spinUp },
    ],
    /* Outgoing record: rises off the spindle, tilts, then slides away to the
       back-left and out of frame. */
    outLift: [
      { t: 980, v: 0 },
      { t: 1780, v: 34, ease: Ease.easeOutCubic },
      { t: 2420, v: 92, ease: Ease.easeInCubic },
    ],
    outTilt: [
      { t: 980, v: 0 },
      { t: 1780, v: 0.14, ease: Ease.easeOutCubic },
      { t: 2420, v: 0.42, ease: Ease.easeInQuad },
    ],
    outSlide: [
      { t: 1300, v: 0 },
      { t: 2420, v: 420, ease: Ease.easeInCubic },
    ],
    outFade: [
      { t: 1900, v: 1 },
      { t: 2400, v: 0, ease: Ease.easeInQuad },
    ],
    /* Incoming record: descends from above, settles onto the spindle with a
       decaying bounce, tilt levelling out as it lands. */
    inLift: [
      { t: 2080, v: 210 },
      { t: 2980, v: 0, ease: Ease.settle },
    ],
    inTilt: [
      { t: 2080, v: 0.11 },
      { t: 2900, v: 0, ease: Ease.easeOutCubic },
    ],
    inSlide: [
      { t: 2080, v: 150 },
      { t: 2860, v: 0, ease: Ease.easeOutCubic },
    ],
    inFade: [
      { t: 2080, v: 0 },
      { t: 2300, v: 1, ease: Ease.easeOutQuad },
    ],
    /* Camera drops to a low, close angle to watch the swap, then rises back.
       The user asked for the angle to change; this is where it earns its keep,
       because the record leaving the spindle is far more legible in profile
       than from the three-quarter hero view. */
    camBlend: [
      { t: 200, v: 0 },
      { t: 1500, v: 1, ease: Ease.easeInOutCubic },
      { t: 2900, v: 1 },
      { t: 4200, v: 0, ease: Ease.easeInOutCubic },
    ],
    pilot: [
      { t: 0, v: 1 },
      { t: 600, v: 0.25, ease: Ease.easeOutQuad },
      { t: 3000, v: 0.25 },
      { t: 4360, v: 1, ease: Ease.easeInQuad },
    ],
  }, SWAP_SPEED));
}

/* ── Skip ─────────────────────────────────────────────────────────────────
   Same beats, compressed and re-weighted. The platter dips rather than
   stopping, because nobody waits for a full spin-down when they press next. */
function skipTimeline() {
  return tracks(stretch({
    armLift: [
      { t: 0, v: 0 },
      { t: 170, v: 1, ease: Ease.snap },
      { t: 2040, v: 1 },
      /* NOT compressed to match the rest. A cue lever is damped whether or not
         you are in a hurry, and holding this beat at full length is what keeps
         the skip from feeling like the animation was fast-forwarded. */
      { t: 2620, v: 0, ease: Ease.damped },
    ],
    armRadius: [
      { t: 0, v: ARM.runOutRadius },
      { t: 170, v: ARM.runOutRadius },
      { t: 690, v: ARM.restRadius, ease: Ease.standard },
      { t: 1420, v: ARM.restRadius },
      { t: 2040, v: ARM.leadInRadius, ease: Ease.standard },
    ],
    platterRpm: [
      { t: 0, v: RPM_33 },
      { t: 640, v: 11, ease: Ease.easeOutQuad },
      { t: 900, v: 11 },
      { t: 1720, v: RPM_33, ease: Ease.spinUp },
    ],
    outLift: [
      { t: 360, v: 0 },
      { t: 760, v: 40, ease: Ease.easeOutCubic },
      { t: 1180, v: 96, ease: Ease.easeInCubic },
    ],
    outTilt: [
      { t: 360, v: 0 },
      { t: 760, v: 0.24, ease: Ease.easeOutCubic },
      { t: 1180, v: 0.62, ease: Ease.easeInQuad },
    ],
    outSlide: [
      { t: 520, v: 0 },
      { t: 1180, v: 460, ease: Ease.easeInCubic },
    ],
    outFade: [
      { t: 880, v: 1 },
      { t: 1160, v: 0, ease: Ease.easeInQuad },
    ],
    inLift: [
      { t: 940, v: 190 },
      { t: 1600, v: 0, ease: Ease.settle },
    ],
    inTilt: [
      { t: 940, v: 0.16 },
      { t: 1540, v: 0, ease: Ease.easeOutCubic },
    ],
    inSlide: [
      { t: 940, v: 175 },
      { t: 1520, v: 0, ease: Ease.easeOutCubic },
    ],
    inFade: [
      { t: 940, v: 0 },
      { t: 1120, v: 1, ease: Ease.easeOutQuad },
    ],
    camBlend: [
      { t: 60, v: 0 },
      { t: 700, v: 1, ease: Ease.easeInOutCubic },
      { t: 1500, v: 1 },
      { t: 2200, v: 0, ease: Ease.easeInOutCubic },
    ],
    pilot: [
      { t: 0, v: 1 },
      { t: 260, v: 0.4, ease: Ease.easeOutQuad },
      { t: 1500, v: 0.4 },
      { t: 2100, v: 1, ease: Ease.easeInQuad },
    ],
  }, SWAP_SPEED));
}

/* The point in each timeline at which the incoming record adopts the new album
   art. It has to be after the outgoing record is out of frame and before the
   incoming one fades in, or the art visibly pops. */
const LABEL_SWAP_AT = { natural: 1980, skip: 900 };

export class Choreographer {
  constructor() {
    this.state = State.IDLE;
    this.track = null;          // the track currently ON the platter
    this.pending = null;        // a track the poll reported mid-swap
    this.timeline = null;
    this.variant = "natural";
    this.clock = 0;
    this.labelSwapped = false;
    this.needleDropped = false;
    this.predicted = false;

    /* Steady-state values, held between swaps and eased toward smoothly so
       that a pause or resume outside a swap still has weight. */
    this.armRadius = ARM.restRadius;
    this.armLift = 1;
    this.platterRpm = 0;
    this.pilot = 0;

    /* Local playback clock, in ms.

       The tonearm's position is a function of how far into the track playback
       is, and the only source for that arrives on a 1.5 s poll. Reading it
       straight off the payload means the arm's target is a staircase: it holds
       still for a second and a half, jumps, holds again. Even smoothed, the
       jump is visible, and it is the whole reason the sweep looked mechanical
       rather than like an arm crawling through a groove.

       So the clock is integrated every FRAME here, and each poll only nudges
       it — see CLOCK_SLEW. The result is genuinely continuous motion at
       whatever framerate the view is running. */
    this.clockMs = 0;
    this.clockDurationMs = 0;
    this.clockEstimated = true;

    this.handlers = {
      prepareArt: [], labelSwap: [], needleDown: [], swapStart: [], swapEnd: [],
    };
  }

  on(event, fn) { this.handlers[event].push(fn); return this; }
  emit(event, arg) { for (const fn of this.handlers[event]) fn(arg); }

  /* ── Poll ingress ─────────────────────────────────────────────────────
     Called with every /api/status payload. Everything the state machine keys
     off is decided here; update() only interpolates. */
  notify(payload) {
    const t = payload.current_track;
    const isLive = Boolean(t && t.track_name) && t.placeholder !== true;

    if (!isLive) {
      /* Backend has no track: idle out. If a swap is in flight, let it finish
         and land on an empty platter rather than snapping. */
      this.pending = null;
      if (this.state !== State.SWAPPING) {
        this.track = null;
        this.state = State.IDLE;
      }
      return;
    }

    const changed = !this.track || this.track.track_id !== t.track_id;

    if (this.state === State.SWAPPING) {
      /* Mid-swap. If this is the track we speculatively started for, adopt it
         quietly — the animation is already doing the right thing.

         The art request goes out NOW, not at the label-swap beat. The incoming
         disc is invisible until its fade begins, so there is a free window to
         get the texture uploaded; asking for it at the moment it has to be
         visible means a texture load has to complete inside a single frame,
         and when it does not the record arrives wearing a blank white label. */
      if (changed) {
        this.pending = t;
        this.emit("prepareArt", t);
      }
      return;
    }

    if (changed) {
      /* New track: the arm belongs back at the lead-in, so the clock restarts
         rather than slewing across from wherever the last one had reached. */
      this.clockMs = 0;
      this.clockDurationMs = Number(t.duration_ms) || 0;
      this.clockEstimated = t.duration_estimated !== false;

      if (this.track === null) {
        /* First track of the session, or coming back from idle. There is
           nothing to remove, so run the arrival half only. */
        this.track = t;
        this.beginSwap("natural", { arrivalOnly: true });
      } else {
        /* An unpredicted change: someone pressed next. */
        this.pending = t;
        this.beginSwap("skip");
      }
      /* After beginSwap, so the listener sees the disc slot the swap has
         already switched to. */
      this.emit("prepareArt", t);
      return;
    }

    /* Same track. Update playback position and decide whether the end of the
       side is close enough to start the changeover on our own. */
    this.track = t;
    this.state = t.is_playing ? State.PLAYING : State.PAUSED;
    this.syncClock(t);

    /* Speculative changeover, but ONLY on a real duration.

       When the app does not report one, liveFeed substitutes an estimate so
       the tonearm has something to track. Predicting off that estimate would
       mean lifting the record off mid-song whenever a track outran the guess
       and putting it straight back — a spectacular, entirely self-inflicted
       glitch. Without a real duration every change simply takes the SKIP path,
       which is what an unforeseen change is anyway. */
    if (t.is_playing && !this.clockEstimated && this.clockDurationMs > 0) {
      if (this.clockFraction() >= PREDICT_THRESHOLD && !this.predicted) {
        this.predicted = true;
        this.beginSwap("natural");
      }
    }
  }

  /* Reconcile the local clock with what the poll just reported. */
  syncClock(t) {
    this.clockDurationMs = Number(t.duration_ms) || 0;
    this.clockEstimated = t.duration_estimated !== false;

    const reported = Number(t.progress_ms) || 0;
    const drift = reported - this.clockMs;

    /* A big disagreement means a seek, a fresh track or a resume after the tab
       was hidden — none of which should be eased through, because the arm
       genuinely is somewhere else now. Small ones are just clock jitter and get
       bled off gradually so the sweep never visibly steps. */
    if (Math.abs(drift) > 4000) this.clockMs = reported;
    else this.clockMs += drift * CLOCK_SLEW;
  }

  /* Fraction through the track, from the locally-integrated clock. */
  clockFraction() {
    if (!(this.clockDurationMs > 0)) return 0;
    return Math.min(1, Math.max(0, this.clockMs / this.clockDurationMs));
  }

  beginSwap(variant, opts = {}) {
    this.variant = variant;
    this.timeline = variant === "skip" ? skipTimeline() : naturalTimeline();
    this.arrivalOnly = Boolean(opts.arrivalOnly);
    /* An arrival-only swap starts partway in, at the moment the label changes,
       so there is no phantom record being lifted off an empty platter. */
    this.clock = this.arrivalOnly ? LABEL_SWAP_AT[variant] - 1 : 0;
    this.state = State.SWAPPING;
    this.labelSwapped = false;
    this.needleDropped = false;
    this.emit("swapStart", { variant, arrivalOnly: this.arrivalOnly });
  }

  /* Longest key time across every channel — when the swap is over. */
  timelineDuration() {
    let d = 0;
    for (const tr of Object.values(this.timeline)) d = Math.max(d, tr.duration);
    return d;
  }

  update(dtMs) {
    if (this.state === State.SWAPPING) return this.updateSwap(dtMs);
    return this.updateSteady(dtMs);
  }

  updateSwap(dtMs) {
    this.clock += dtMs;
    const T = this.timeline;
    const t = this.clock;

    if (!this.labelSwapped && t >= LABEL_SWAP_AT[this.variant]) {
      this.labelSwapped = true;
      /* Adopt whatever the poll has given us by now. If nothing arrived — the
         prediction fired but the track did not actually change — the same
         record comes back down, which reads as the side being restarted rather
         than as a glitch. */
      if (this.pending) {
        this.track = this.pending;
        this.pending = null;
      }
      this.emit("labelSwap", this.track);
    }

    const lift = T.armLift.at(t);

    /* Needle contact: the lift channel reaching zero on its way DOWN, i.e. in
       the back half of the timeline. Guarding on the second half matters —
       the channel is also zero at t = 0, before the arm has lifted at all. */
    if (!this.needleDropped && lift <= 0.001 && t > this.timelineDuration() * 0.5) {
      this.needleDropped = true;
      this.emit("needleDown", this.track);
    }

    const pose = {
      state: State.SWAPPING,
      variant: this.variant,
      armRadius: T.armRadius.at(t),
      armLift: lift,
      platterRpm: T.platterRpm.at(t),
      recordRpm: T.platterRpm.at(t),
      outRecord: {
        lift: T.outLift.at(t),
        tilt: T.outTilt.at(t),
        slide: T.outSlide.at(t),
        opacity: this.arrivalOnly ? 0 : T.outFade.at(t),
      },
      inRecord: {
        lift: T.inLift.at(t),
        tilt: T.inTilt.at(t),
        slide: T.inSlide.at(t),
        opacity: T.inFade.at(t),
      },
      camBlend: T.camBlend.at(t),
      pilot: T.pilot.at(t),
    };

    if (t >= this.timelineDuration()) {
      /* Hand the interpolated values to the steady-state controller so the
         first frame after the swap does not jump. */
      this.armRadius = pose.armRadius;
      this.armLift = pose.armLift;
      this.platterRpm = pose.platterRpm;
      this.pilot = pose.pilot;
      this.predicted = false;
      this.state = this.track
        ? (this.track.is_playing ? State.PLAYING : State.PAUSED)
        : State.IDLE;
      this.emit("swapEnd", this.track);
    }

    return pose;
  }

  updateSteady(dtMs) {
    const dt = dtMs / 1000;
    const t = this.track;

    let targetRadius = ARM.restRadius;
    let targetLift = 1;
    let targetRpm = 0;
    let targetPilot = 0;

    if (this.state === State.PLAYING && t) {
      /* Advance the local clock by this frame. This is what makes the arm's
         inward crawl continuous rather than a 1.5 s staircase. */
      this.clockMs += dtMs;
      targetRadius = radiusForProgress(this.clockFraction());
      targetLift = 0;
      targetRpm = RPM_33;
      targetPilot = 1;
    } else if (this.state === State.PAUSED && t) {
      /* Paused: cue up and let the platter coast to a stop, which is what
         happens when you touch the cue lever on a real deck. Holding the arm
         where it is means resuming picks up in the same groove — so the clock
         does NOT advance here. */
      targetRadius = radiusForProgress(this.clockFraction());
      targetLift = 1;
      targetRpm = 0;
      targetPilot = 0.3;
    }

    /* Exponential approach, framerate-independent. Rates differ per channel
       because the parts have different inertia: the platter is slow, the cue
       lift is quick, the arm's lateral sweep is somewhere between.

       The arm's rate is high now that its target moves continuously. It was
       low to smooth over the poll staircase — which meant the arm was always
       lagging its own target and the lag was what you actually saw. With the
       clock integrated per frame, the smoothing is only needed to absorb a
       seek. */
    const approach = (cur, target, perSecond) =>
      lerp(cur, target, 1 - Math.exp(-perSecond * dt));

    this.armRadius = approach(this.armRadius, targetRadius, 7.5);
    this.armLift = approach(this.armLift, targetLift, 4.5);
    this.platterRpm = approach(this.platterRpm, targetRpm, 1.15);
    this.pilot = approach(this.pilot, targetPilot, 5.0);

    return {
      state: this.state,
      variant: null,
      armRadius: this.armRadius,
      armLift: this.armLift,
      platterRpm: this.platterRpm,
      recordRpm: this.platterRpm,
      outRecord: { lift: 0, tilt: 0, slide: 0, opacity: 0 },
      inRecord: { lift: 0, tilt: 0, slide: 0, opacity: this.track ? 1 : 0 },
      camBlend: 0,
      pilot: this.pilot,
    };
  }
}
