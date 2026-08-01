/* ════════════════════════════════════════════════════════════════════════
   dust.js — motes drifting in the key light.

   Cheap, and out of proportion to its cost. Dust does three things at once:
   it puts something in the volume of air between the camera and the deck,
   which is what stops a 3D scene reading as objects pasted on a backdrop; it
   makes the key light legible as a beam rather than as an assumption; and it
   gives the frame a low level of motion during long stretches where nothing
   is happening but a record turning.

   Drawn additively with depth writes off, so motes never occlude one another
   and never punch holes in the depth buffer that the DOF pass would then blur
   incorrectly.
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";

const COUNT = 420;

/* A soft radial falloff sprite, generated rather than loaded. Squared falloff
   keeps the core tight and the halo faint, which is what an out-of-focus
   speck actually looks like. */
function makeMoteTexture() {
  const S = 64;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
  grad.addColorStop(0.0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,246,228,0.55)");
  grad.addColorStop(1.0, "rgba(255,240,210,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class Dust {
  constructor() {
    const positions = new Float32Array(COUNT * 3);
    const scales = new Float32Array(COUNT);
    this.velocity = new Float32Array(COUNT * 3);
    /* The drift each mote returns to. puff() perturbs `velocity`; without a
       resting value to relax back toward, a mote kicked upward would rise out
       of the scene and never be recycled. */
    this.baseVelocity = new Float32Array(COUNT * 3);
    this.phase = new Float32Array(COUNT);

    /* Bias the volume toward the key light's side and keep it above the deck,
       so motes read as being lit by the beam rather than scattered at random. */
    for (let i = 0; i < COUNT; i++) {
      positions[i * 3] = (Math.random() - 0.62) * 900;
      positions[i * 3 + 1] = Math.random() * 460 - 40;
      positions[i * 3 + 2] = (Math.random() - 0.4) * 780;

      /* Sizes follow a strong power law: a few large near motes, many tiny
         far ones. A uniform distribution looks like snow — which is what the
         first test render looked like, so the exponent is steep and the cap is
         low deliberately. */
      scales[i] = 0.5 + Math.pow(Math.random(), 4.5) * 4.2;

      this.baseVelocity[i * 3] = (Math.random() - 0.5) * 3.4;
      this.baseVelocity[i * 3 + 1] = -1.2 - Math.random() * 2.6;   // settling, slowly
      this.baseVelocity[i * 3 + 2] = (Math.random() - 0.5) * 3.4;
      this.velocity[i * 3] = this.baseVelocity[i * 3];
      this.velocity[i * 3 + 1] = this.baseVelocity[i * 3 + 1];
      this.velocity[i * 3 + 2] = this.baseVelocity[i * 3 + 2];
      this.phase[i] = Math.random() * Math.PI * 2;
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("aScale", new THREE.BufferAttribute(scales, 1));

    this.material = new THREE.PointsMaterial({
      map: makeMoteTexture(),
      color: 0xf3e2c4,
      size: 3,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.30,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });

    /* PointsMaterial has no per-point size — it reads the material's single
       `size` uniform and ignores any attribute, so the size buffer above would
       otherwise be inert and every mote would render identically sized. Two
       lines of injection is much cheaper than reimplementing the material. */
    this.material.onBeforeCompile = shader => {
      shader.vertexShader = shader.vertexShader
        .replace("void main() {", "attribute float aScale;\nvoid main() {")
        .replace("gl_PointSize = size;", "gl_PointSize = size * aScale;");
    };

    this.points = new THREE.Points(geom, this.material);
    this.points.frustumCulled = false;
    this.positions = positions;
    this.time = 0;
  }

  update(dt) {
    this.time += dt;
    const p = this.positions;

    /* Air drag, framerate-independent. Anything puff() flung outward bleeds
       its energy off over roughly half a second and rejoins the ambient drift. */
    const relax = 1 - Math.exp(-2.4 * dt);

    for (let i = 0; i < COUNT; i++) {
      const i3 = i * 3;
      for (let k = 0; k < 3; k++) {
        this.velocity[i3 + k] += (this.baseVelocity[i3 + k] - this.velocity[i3 + k]) * relax;
      }

      /* Brownian wander on top of the settling drift. Pure linear fall looks
         like rain; the lateral wobble is what makes it read as dust in air. */
      const wob = Math.sin(this.time * 0.6 + this.phase[i]);
      p[i3] += (this.velocity[i3] + wob * 2.1) * dt;
      p[i3 + 1] += this.velocity[i3 + 1] * dt;
      p[i3 + 2] += (this.velocity[i3 + 2] + Math.cos(this.time * 0.5 + this.phase[i]) * 1.8) * dt;

      /* Recycle out of the bottom and back in at the top. */
      if (p[i3 + 1] < -60) {
        p[i3] = (Math.random() - 0.62) * 900;
        p[i3 + 1] = 420;
        p[i3 + 2] = (Math.random() - 0.4) * 780;
      }
    }

    this.points.geometry.attributes.position.needsUpdate = true;
  }

  /* A puff kicked up where the stylus meets the record. Rather than spawning
     new particles — which would mean a second buffer and a lifetime system for
     one half-second event — the nearest motes are simply teleported to the
     contact point and given an outward push. */
  puff(at) {
    const p = this.positions;
    let moved = 0;
    for (let i = 0; i < COUNT && moved < 26; i++) {
      const i3 = i * 3;
      if (p[i3 + 1] > 260) {
        const a = Math.random() * Math.PI * 2;
        const r = 4 + Math.random() * 26;
        p[i3] = at.x + Math.cos(a) * r;
        p[i3 + 1] = at.y + Math.random() * 12;
        p[i3 + 2] = at.z + Math.sin(a) * r;
        this.velocity[i3] = Math.cos(a) * (16 + Math.random() * 26);
        this.velocity[i3 + 1] = 10 + Math.random() * 22;
        this.velocity[i3 + 2] = Math.sin(a) * (16 + Math.random() * 26);
        moved++;
      }
    }
  }

  setVisible(on) { this.points.visible = on; }
}
