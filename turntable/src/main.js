/* ════════════════════════════════════════════════════════════════════════
   main.js — assembles the stage, drives the frame loop, wires the harness.

   The only integration surface that matters is `feed.subscribe(...)` near the
   bottom: it takes exactly the payload shape GET /api/status already returns.
   Swapping the mock feed for a real fetch is a one-line change, which is the
   point — nothing in this prototype reaches into the running app.
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";
import { Stage, POSES } from "./scene/stage.js";
import { Deck } from "./scene/deck.js";
import { Record, RECORD_REST_Y } from "./scene/record.js";
import { Tonearm, ARM } from "./scene/tonearm.js";
import { Dust } from "./scene/dust.js";
import { MockFeed } from "./mockFeed.js";
import { LiveFeed, appReachable } from "./liveFeed.js";
import { Choreographer, State, RPM_33 } from "./choreography.js";
import { installCaptureRig } from "./captureRig.js";

const boot = document.getElementById("boot");
const bootText = document.getElementById("bootText");

/* Surface a boot failure instead of hanging on the loading veil.

   Module evaluation is one long straight line — build the stage, the deck, the
   records, the arm — and if any step throws, the veil simply sits there
   forever showing whatever step it had reached. There is no error, no blank
   page, nothing to react to; it just looks like a slow load that never ends.
   This turns that into a legible message on screen. */
function bootFailed(err) {
  console.error(err);
  boot.classList.remove("gone");
  boot.style.display = "";
  boot.classList.add("failed");

  /* Built as nodes rather than innerHTML: the message is an exception string,
     which can carry a URL or arbitrary text from a failed fetch, and there is
     no reason to hand that to the HTML parser. */
  bootText.textContent = "";
  const head = document.createElement("b");
  head.textContent = "Could not start the view.";
  const detail = document.createElement("div");
  detail.className = "boot-detail";
  detail.textContent = String((err && err.message) || err);
  const hint = document.createElement("div");
  hint.className = "boot-hint";
  hint.textContent = "Full trace is in the console — Ctrl+Shift+J.";
  bootText.append(head, detail, hint);
}
addEventListener("error", e => bootFailed(e.error || e.message));
addEventListener("unhandledrejection", e => bootFailed(e.reason));

/* ── Build ───────────────────────────────────────────────────────────── */

bootText.textContent = "Initialising renderer…";
const stage = new Stage(document.getElementById("stage"));

bootText.textContent = "Building deck…";
const deck = new Deck(stage.renderer);
deck.setEnvironment(stage.environment);
stage.scene.add(deck.group);

bootText.textContent = "Pressing records…";
/* Two discs, ping-ponged. One record that animates off, retextures and comes
   back would be simpler, but it forbids the shot that makes the swap read: the
   outgoing disc sliding away while the incoming one is already descending.
   The heavy assets — the vinyl surface maps — are cached and shared, so the
   second instance costs a mesh and a label material, not a second texture set. */
const records = [new Record(stage.renderer), new Record(stage.renderer)];
const holders = records.map(r => {
  /* An outer holder carries lift/tilt/slide so the Record can own rotation.y
     for spin without the two fighting over the same Euler. */
  const holder = new THREE.Group();
  holder.add(r.group);
  stage.scene.add(holder);
  return holder;
});
let onPlatter = 0;    // index of the record currently on the spindle

bootText.textContent = "Aligning tonearm…";
const tonearm = new Tonearm();
tonearm.setEnvironment(stage.environment);
stage.scene.add(tonearm.group);

const dust = new Dust();
stage.scene.add(dust.points);

/* ── Data source ──────────────────────────────────────────────────────────
   Two feeds, one contract. The live feed polls the running SonicVectorEQ app
   through the dev server's proxy; the demo feed fabricates the same payload so
   the view is usable with nothing else running. Both are constructed up front
   and only one is started, so switching between them is instant. */
const mockFeed = new MockFeed();
const liveFeed = new LiveFeed({
  onConnectionChange: (ok, detail) => {
    setSourceBadge(ok ? "live" : "waiting", detail);
    /* Losing the app mid-session does NOT silently swap to demo data. A record
       that keeps spinning through a backend outage is lying about the state of
       the world; the badge goes amber and the deck idles out on the next poll. */
  },
});

let activeFeed = null;
let unsubscribe = null;

function useFeed(feed) {
  if (unsubscribe) unsubscribe();
  if (activeFeed) activeFeed.stop();
  activeFeed = feed;
  unsubscribe = feed.subscribe(payload => {
    warmArt(payload.current_track);
    choreo.notify(payload);
  });
  feed.start();
  /* The demo controls only mean anything against the demo feed — the transport
     cannot skip a track inside somebody's Spotify session. */
  const demo = feed === mockFeed;
  for (const id of ["btnPrev", "btnPlay", "btnNext", "btnIdle", "seek", "scale"]) {
    const el = document.getElementById(id);
    if (el) el.disabled = !demo;
  }
  document.getElementById("btnLive").classList.toggle("on", !demo);
  document.getElementById("btnDemo").classList.toggle("on", demo);
  setSourceBadge(demo ? "demo" : "live");
}

const choreo = new Choreographer();

/* Directions the discs travel during a swap. The outgoing record leaves toward
   the back-left, away from the tonearm, and the incoming one arrives from the
   front-right — so the two never cross the arm and never cross each other. */
const OUT_DIR = new THREE.Vector3(-0.62, 0, -0.78).normalize();
const IN_DIR = new THREE.Vector3(0.70, 0, 0.71).normalize();
const UP = new THREE.Vector3(0, 1, 0);

/* Tilt about the axis perpendicular to travel, so a disc tips in the direction
   it is moving rather than rolling around an arbitrary axis. */
const OUT_TILT_AXIS = new THREE.Vector3().crossVectors(UP, OUT_DIR).normalize();
const IN_TILT_AXIS = new THREE.Vector3().crossVectors(UP, IN_DIR).normalize();

/* Apply a whole pose to both discs. Factored out so the capture rig can drive
   exactly the same code path as the live frame loop — a screenshot taken
   through a different code path is not evidence about the real view. */
function poseRecords(pose) {
  poseHolder(holders[onPlatter], pose.inRecord, IN_DIR, IN_TILT_AXIS,
    pose.inRecord.opacity, records[onPlatter]);
  poseHolder(holders[1 - onPlatter], pose.outRecord, OUT_DIR, OUT_TILT_AXIS,
    pose.outRecord.opacity, records[1 - onPlatter]);
}

function poseHolder(holder, motion, dir, axis, opacity, record) {
  holder.position.set(
    dir.x * motion.slide,
    RECORD_REST_Y + motion.lift,
    dir.z * motion.slide,
  );
  holder.quaternion.setFromAxisAngle(axis, motion.tilt);

  const visible = opacity > 0.004;
  holder.visible = visible;
  if (!visible) return;

  /* Only pay for blending while a disc is actually mid-fade. A permanently
     transparent record would be sorted every frame and would stop writing
     depth, which the DOF pass reads — so it would quietly blur wrong. */
  const fading = opacity < 0.996;
  for (const m of [record.vinylMaterial, record.labelMaterial]) {
    if (m.transparent !== fading) {
      m.transparent = fading;
      m.depthWrite = !fading;
      m.needsUpdate = true;
    }
    m.opacity = opacity;
  }
}

/* ── Now-playing readout ─────────────────────────────────────────────── */

const el = {
  np: document.getElementById("np"),
  src: document.getElementById("npSrc"),
  title: document.getElementById("npTitle"),
  artist: document.getElementById("npArtist"),
  album: document.getElementById("npAlbum"),
  time: document.getElementById("npTime"),
  lamp: document.getElementById("lamp"),
  lampLabel: document.getElementById("lampLabel"),
};

const mmss = ms => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

function paintReadout(track) {
  if (!track || !track.track_name) {
    el.src.textContent = "SRC · IDLE";
    el.title.textContent = "No Track Loaded";
    el.artist.textContent = "Waiting for playback";
    el.album.textContent = "";
    el.time.textContent = "0:00 / 0:00";
    return;
  }
  el.src.textContent = `SRC · ${(track.source || "idle").toUpperCase()}`;
  el.title.textContent = track.track_name;
  el.artist.textContent = track.artist_name || "";
  el.album.textContent = track.album_name || "";
}

/* ── Choreography hooks ──────────────────────────────────────────────── */

choreo.on("swapStart", ({ arrivalOnly }) => {
  if (arrivalOnly) return;
  /* The incoming disc is the other slot. It is hidden until the timeline
     fades it in, so preparing it early is free. */
  onPlatter = 1 - onPlatter;
});

/* Fires as soon as the incoming track is known — which is well before it has
   to be seen, since the arriving disc is invisible until its fade begins. */
let artAppliedFor = null;
choreo.on("prepareArt", async track => {
  artAppliedFor = track ? track.track_id : null;
  await records[onPlatter].setAlbumArt(track ? track.album_art : "", track || {});
});

choreo.on("labelSwap", async track => {
  /* This fires at the moment the outgoing record is out of frame and before
     the incoming one is visible, so any texture change is never seen. In the
     normal case prepareArt has already done the work and this is a no-op; the
     guard covers the speculative path, where the changeover was started on the
     run-out groove and no new track was ever reported. */
  const id = track ? track.track_id : null;
  if (id !== artAppliedFor) {
    artAppliedFor = id;
    await records[onPlatter].setAlbumArt(track ? track.album_art : "", track || {});
  }

  el.np.style.opacity = "0";
  setTimeout(() => {
    paintReadout(track);
    el.np.style.opacity = "1";
  }, 180);
});

choreo.on("needleDown", () => {
  const p = new THREE.Vector3();
  tonearm.worldStylus(p);
  dust.puff(p);
});

/* ── Frame loop ──────────────────────────────────────────────────────── */

let last = performance.now();
let frames = 0, fpsAccum = 0, fps = 0;
let hidden = false;

const stats = {
  box: document.getElementById("stats"),
  fps: document.getElementById("stFps"),
  draw: document.getElementById("stDraw"),
  tri: document.getElementById("stTri"),
  state: document.getElementById("stState"),
};

const LAMP_FOR = {
  [State.PLAYING]: ["playing", "PLAYING"],
  [State.PAUSED]: ["idle", "CUED"],
  [State.SWAPPING]: ["cueing", "CHANGING"],
  [State.IDLE]: ["idle", "STOPPED"],
};

function frame(now) {
  requestAnimationFrame(frame);

  /* Clamped so that returning from a background tab — where rAF may not have
     fired for minutes — does not integrate one enormous step and fling the
     records across the scene. */
  const dtMs = Math.min(now - last, 100);
  last = now;
  const dt = dtMs / 1000;

  /* Prototype only. The capture rig steps the simulation by hand at a fixed
     timestep; if this loop keeps running alongside it, both advance the same
     choreographer and captured frames land at the wrong point in the timeline.
     That is not hypothetical — it put the first swap capture ~500 ms out. */
  if (window.__capturePaused) return;

  /* The Page Visibility API is the single biggest power saving available: this
     view may be left open all day, and a hidden tab has no business running a
     full post chain. rAF is already throttled when hidden in most browsers,
     but not in every window state, so this is belt and braces. */
  if (hidden) return;

  const pose = choreo.update(dtMs);

  tonearm.setPose(pose.armRadius, pose.armLift);

  records[onPlatter].rpm = pose.recordRpm;
  records[1 - onPlatter].rpm = pose.recordRpm * 0.55;   // the disc being removed coasts
  records[0].update(dt);
  records[1].update(dt);

  poseRecords(pose);

  deck.platterRpm = pose.platterRpm;
  deck.update(dt, { pilotLevel: pose.pilot });

  dust.update(dt);

  stage.updateCamera(dt, pose.camBlend);
  stage.render(dt);

  /* Readout clock.

     When the duration is an estimate — which it is on every live track, since
     /api/status does not forward duration_ms — only the elapsed time is shown.
     Printing "0:43 / 4:00" against a guessed 4:00 states a fact the view does
     not have. The elapsed figure is real, so that is what gets displayed. */
  const t = choreo.track;
  if (t && t.track_name) {
    /* Read from the choreographer's own clock, not the payload — that is the
       one advancing every frame, and it is the one the tonearm is following. */
    el.time.textContent = choreo.clockEstimated
      ? mmss(choreo.clockMs)
      : `${mmss(choreo.clockMs)} / ${mmss(choreo.clockDurationMs)}`;
  }

  const [lampState, lampLabel] = LAMP_FOR[pose.state] || ["idle", "—"];
  if (el.lamp.dataset.state !== lampState) {
    el.lamp.dataset.state = lampState;
    el.lampLabel.textContent = lampLabel;
  }

  frames++;
  fpsAccum += dt;
  if (fpsAccum >= 0.5) {
    fps = Math.round(frames / fpsAccum);
    frames = 0; fpsAccum = 0;
    if (!stats.box.hidden) {
      const info = stage.renderer.info;
      stats.fps.textContent = fps;
      stats.draw.textContent = info.render.calls;
      stats.tri.textContent = info.render.triangles.toLocaleString();
      stats.state.textContent = pose.variant ? `${pose.state}:${pose.variant}` : pose.state;
    }
  }
}

document.addEventListener("visibilitychange", () => {
  hidden = document.hidden;
  if (!hidden) last = performance.now();
});

/* ── Harness wiring (prototype only) ─────────────────────────────────── */

const $ = id => document.getElementById(id);

$("btnNext").onclick = () => mockFeed.next();
$("btnPrev").onclick = () => mockFeed.previous();
$("btnIdle").onclick = () => { mockFeed.stop(); mockFeed.setIdle(); };
$("btnPlay").onclick = e => {
  const playing = !mockFeed.playing;
  mockFeed.setPlaying(playing);
  e.target.textContent = playing ? "PAUSE" : "PLAY";
};

$("seek").oninput = e => {
  const f = e.target.value / 1000;
  mockFeed.seekFraction(f);
  $("seekVal").textContent = `${(f * 100).toFixed(1)}%`;
  mockFeed.emit();
};

$("scale").oninput = e => {
  mockFeed.timeScale = Number(e.target.value);
  $("scaleVal").textContent = `${e.target.value}×`;
};

/* ── Source switching ────────────────────────────────────────────────── */

const srcBadge = $("srcBadge");
const srcLabel = $("srcLabel");
const srcDetail = $("srcDetail");

function setSourceBadge(state, detail) {
  srcBadge.dataset.state = state;
  srcLabel.textContent =
    state === "live" ? "LIVE · SONICVECTOR"
    : state === "demo" ? "DEMO FEED"
    : "WAITING FOR APP";
  srcDetail.textContent = detail || (
    state === "demo" ? "app not connected — press R to retry" : ""
  );
}

$("btnLive").onclick = async () => {
  setSourceBadge("waiting", "connecting…");
  if (await appReachable()) {
    useFeed(liveFeed);
  } else {
    setSourceBadge("demo", "SonicVectorEQ not reachable on :5001");
    useFeed(mockFeed);
  }
};
$("btnDemo").onclick = () => useFeed(mockFeed);

for (const [id, name] of [["camHero", "hero"], ["camTop", "plan"], ["camMacro", "macro"]]) {
  $(id).onclick = () => {
    stage.setPose(name);
    for (const other of ["camHero", "camTop", "camMacro", "camFree"]) {
      $(other).classList.toggle("on", other === id);
    }
  };
}
$("camFree").onclick = () => {
  stage.setFreeLook(!stage.freeLook.active);
  $("camFree").classList.toggle("on", stage.freeLook.active);
};
$("camHero").classList.add("on");

$("optPost").onchange = e => stage.setPostEnabled(e.target.checked);
$("optGrain").onchange = e => stage.setGrain(e.target.checked);
$("optDust").onchange = e => dust.setVisible(e.target.checked);
$("optStats").onchange = e => { stats.box.hidden = !e.target.checked; };

$("barMore").onclick = () => {
  const drawer = $("drawer");
  drawer.hidden = !drawer.hidden;
  $("barMore").classList.toggle("on", !drawer.hidden);
};

/* Drag to orbit, wheel to dolly — only meaningful in FREE. */
let dragging = false, lastX = 0, lastY = 0;
stage.canvas.addEventListener("pointerdown", e => {
  dragging = true; lastX = e.clientX; lastY = e.clientY;
  stage.canvas.setPointerCapture(e.pointerId);
});
stage.canvas.addEventListener("pointermove", e => {
  if (!dragging || !stage.freeLook.active) return;
  stage.orbit(e.clientX - lastX, e.clientY - lastY);
  lastX = e.clientX; lastY = e.clientY;
});
stage.canvas.addEventListener("pointerup", e => {
  dragging = false;
  stage.canvas.releasePointerCapture(e.pointerId);
});
stage.canvas.addEventListener("wheel", e => {
  if (!stage.freeLook.active) return;
  e.preventDefault();
  stage.zoom(-e.deltaY);
}, { passive: false });

addEventListener("keydown", e => {
  if (e.key === "n" || e.key === "ArrowRight") mockFeed.next();
  if (e.key === "p" || e.key === "ArrowLeft") mockFeed.previous();
  if (e.key === "1") $("camHero").click();
  if (e.key === "2") $("camTop").click();
  if (e.key === "3") $("camMacro").click();
  if (e.key === "4") $("camFree").click();
  if (e.key === "h") $("barMore").click();
  if (e.key === "r") $("btnLive").click();
});

/* ── Go ──────────────────────────────────────────────────────────────── */

/* Warm the album art the instant a new track is seen, rather than waiting for
   the choreography to ask for it at the label-swap beat.

   The swap only reaches that beat about two seconds in, which is normally
   plenty — but "normally" is doing real work there. The art is a cross-origin
   fetch from Spotify's CDN, and if it is slow, or the machine has just woken,
   or the swap is the fast SKIP variant where the beat lands at 900 ms, the
   record comes down wearing a blank white label. Kicking off the decode here
   costs nothing and moves the deadline as early as it can possibly be. */
let warmedTrackId = null;
function warmArt(track) {
  if (!track || !track.album_art || track.track_id === warmedTrackId) return;
  warmedTrackId = track.track_id;
  const img = new Image();
  img.crossOrigin = "anonymous";
  /* Decode off the main thread where supported, so the eventual texture upload
     is a straight copy and never stalls a frame mid-animation. */
  img.onload = () => { if (img.decode) img.decode().catch(() => {}); };
  img.src = track.album_art;
}

(async () => {
  bootText.textContent = "Looking for SonicVectorEQ…";
  const live = await appReachable();

  /* Prime the first record before the first frame so the opening arrival has
     something to land with. On the live path the app may legitimately have no
     track loaded, in which case this is a house label — which is exactly what
     the deck should be wearing while it waits. */
  const priming = live
    ? { track_id: "", track_name: "", artist_name: "", album_name: "", album_art: "" }
    : mockFeed.currentPayload().current_track;
  await records[onPlatter].setAlbumArt(priming.album_art, priming);
  artAppliedFor = priming.track_id;
  paintReadout(priming);

  bootText.textContent = "Compiling shaders…";
  /* Force the shader compile now rather than on the first visible frame. This
     scene compiles a lot of MeshPhysicalMaterial permutations — anisotropy,
     clearcoat, sheen — and doing it under the boot veil turns a one-second
     freeze into a one-second loading state. */
  stage.renderer.compile(stage.scene, stage.camera);

  useFeed(live ? liveFeed : mockFeed);
  if (!live) setSourceBadge("demo", "SonicVectorEQ not reachable on :5001");

  requestAnimationFrame(frame);
  requestAnimationFrame(() => {
    boot.classList.add("gone");
    setTimeout(() => { boot.style.display = "none"; }, 600);
  });
})();

/* Handy for poking at the scene from the console while tuning. */
window.__proto = {
  stage, deck, records, tonearm, dust, choreo, POSES, ARM, RPM_33,
  mockFeed, liveFeed, useFeed,
  get feed() { return activeFeed; },
};

/* Prototype-only. See captureRig.js — delete this import and call before any
   of this is integrated into the real app. */
installCaptureRig({
  stage, choreo, tonearm, deck, records, dust,
  poseRecords,
  onPlatterRef: () => onPlatter,
});
