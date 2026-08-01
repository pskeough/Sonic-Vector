/* ════════════════════════════════════════════════════════════════════════
   captureRig.js — offscreen stepping and frame capture.

   PROTOTYPE TOOLING ONLY. Delete this file and its one import from main.js
   before any of this is integrated.

   It exists because this view has to be judged by eye, and the two obvious
   ways to look at it both fail here:

     · requestAnimationFrame does not run when the host pane is not
       compositing, so the scene simply never advances.
     · The default drawing buffer is not preserved, so there is nothing left
       to read back after a frame is presented.

   So the rig drives the simulation synchronously at a fixed timestep, renders
   at a fixed resolution independent of the window, reads the framebuffer back
   with gl.readPixels inside the same task, and POSTs the PNG to the dev
   server, which writes it to shots/.

   A fixed timestep also makes captures reproducible: "the frame 2100 ms into a
   swap" is an exact thing, not whatever the frame pacing happened to land on.
   ════════════════════════════════════════════════════════════════════════ */

const W = 1440, H = 810;

export function installCaptureRig(ctx) {
  const { stage, choreo, tonearm, deck, records, dust, onPlatterRef } = ctx;

  const resize = (w = W, h = H) => {
    stage.renderer.setPixelRatio(1);
    stage.renderer.setSize(w, h, false);
    stage.composer.setPixelRatio(1);
    stage.composer.setSize(w, h);
    stage.camera.aspect = w / h;
    stage.camera.updateProjectionMatrix();
  };

  /* Deliberately NOT called at install time. Doing so pinned the canvas to the
     capture resolution the moment the page loaded, so the view rendered at
     1440x810 inside whatever size window the user actually had. The rig sizes
     the canvas when a capture starts, and resume() hands it back. */

  /* One simulation tick. Mirrors main.js's frame() exactly — if the two ever
     diverge, captures stop being evidence about the real view. */
  const step = (dtMs = 16.7) => {
    /* Stand the live frame loop down on first use, and only on first use.
       Both loops advance the same choreographer, so leaving rAF running during
       a capture puts every frame ahead of its nominal time — it threw the first
       swap capture out by ~500 ms. Setting this at install time instead would
       mean simply opening the page left it frozen. */
    window.__capturePaused = true;

    const dt = dtMs / 1000;
    const pose = choreo.update(dtMs);
    tonearm.setPose(pose.armRadius, pose.armLift);
    const on = onPlatterRef();
    records[on].rpm = pose.recordRpm;
    records[1 - on].rpm = pose.recordRpm * 0.55;
    records[0].update(dt);
    records[1].update(dt);
    ctx.poseRecords(pose);
    deck.platterRpm = pose.platterRpm;
    deck.update(dt, { pilotLevel: pose.pilot });
    dust.update(dt);
    stage.updateCamera(dt, pose.camBlend);
    stage.render(dt);
    return pose;
  };

  const shot = async name => {
    /* Re-render immediately before reading back, in this same task.

       The context is created with preserveDrawingBuffer: false, so the moment
       control returns to the browser the drawing buffer's contents become
       undefined — and reading it after an await returns garbage, which showed
       up as a completely white PNG rather than as any kind of error. Rendering
       here makes the capture independent of whatever happened before it. */
    stage.render(0);

    const gl = stage.renderer.getContext();
    const w = stage.canvas.width, h = stage.canvas.height;
    const px = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);

    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g2 = c.getContext("2d");
    const img = g2.createImageData(w, h);
    /* GL's origin is bottom-left, the canvas's is top-left. */
    for (let y = 0; y < h; y++) {
      const s = (h - 1 - y) * w * 4;
      img.data.set(px.subarray(s, s + w * 4), y * w * 4);
    }
    g2.putImageData(img, 0, 0);

    const res = await fetch("/shot", {
      method: "POST",
      body: JSON.stringify({ name, data: c.toDataURL("image/png") }),
    });
    return res.json();
  };

  /* Run `ms` of simulation, then capture. */
  const at = async (name, ms, dt = 16.7) => {
    resize();
    const n = Math.max(1, Math.round(ms / dt));
    for (let i = 0; i < n; i++) step(dt);
    return shot(name);
  };

  window.__cap = { step, shot, at, resize, W, H,
    /* Hand control back to the live frame loop after a capture session. */
    resume: () => {
      window.__capturePaused = false;
      stage.resize();
    } };
  return window.__cap;
}
