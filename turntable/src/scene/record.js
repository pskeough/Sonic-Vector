/* ════════════════════════════════════════════════════════════════════════
   record.js — a 12" LP as a lathed solid with an analytic tangent frame.

   GEOMETRY
   --------
   The disc is a LatheGeometry so the thickness profile shows up on the
   silhouette: a record is thicker at the label and at the rim than across the
   grooved band, and that stepped edge is one of the details that reads as
   "real object" the instant the camera drops to a low angle.

   TANGENT FRAME
   -------------
   Anisotropic shading needs to know which way the grooves run. Left to itself,
   three.js derives a tangent frame from UV derivatives in the fragment shader,
   which on a disc means a broken column of pixels at the UV seam and a needless
   per-fragment cost. Since the answer is known in closed form — the grooves run
   circumferentially, everywhere, always — the tangent attribute is written
   directly:

       T = normalize(cross(radialOutward, N)),  w = +1

   which yields bitangent = cross(N, T) * w = radialOutward on both faces, so
   the normal map's green channel perturbs along radius on the top and the
   bottom without a special case.

   UVs
   ---
   v = (r - rHole) / (rEdge - rHole), so the 1-D maps in vinylMaps.js line up
   with real radii. u is the circumferential angle and is unused by those maps
   (they are constant in u), which is exactly why the seam is harmless.
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";
import { LP, vinylMaps } from "./vinylMaps.js";
import { makeHouseLabel } from "../proceduralCover.js";

/* Circumferential subdivision. At 152 mm radius, 256 segments put the
   silhouette error at ~0.01 mm — far below a pixel at any camera distance this
   scene uses — while keeping the disc under ~45k vertices. */
const LATHE_SEGMENTS = 256;

/* WHERE A RECORD SITS
   The disc's geometry is centred on its own mid-plane, so parking the group at
   y = 0 would bury half of it in the mat. A record rests on the mat via its
   thicker label plateau, so the centre sits half a label-thickness up. */
export const RECORD_REST_Y = LP.thickLabel / 2;

/* The height of the grooved playing surface — what the stylus actually touches.
   The tonearm derives its pivot height from this, so a change to the pressing
   thickness moves the arm with it instead of silently leaving the needle
   floating above the record or sunk into it. */
export const RECORD_PLAY_SURFACE_Y = RECORD_REST_Y + LP.thickBand / 2;

/* ── Thickness profile ────────────────────────────────────────────────────
   Half-thickness as a function of radius. Sampled densely where the profile
   actually changes and sparsely across the flats, so the vertex budget goes
   where the curvature is. */
function halfThickness(r) {
  const tLabel = LP.thickLabel / 2;
  const tBand = LP.thickBand / 2;
  const tRim = LP.thickRim / 2;

  if (r <= LP.labelRadius) return tLabel;

  if (r < LP.deadwaxOuter) {
    // Label plateau falls away to the grooved band across the deadwax.
    const t = (r - LP.labelRadius) / (LP.deadwaxOuter - LP.labelRadius);
    return tLabel + (tBand - tLabel) * (t * t * (3 - 2 * t));
  }

  if (r < LP.bandOuter) return tBand;

  if (r < LP.landOuter) {
    // Band rises into the rim ridge.
    const t = (r - LP.bandOuter) / (LP.landOuter - LP.bandOuter);
    return tBand + (tRim - tBand) * (t * t * (3 - 2 * t));
  }

  return tRim;
}

function profilePoints() {
  /* The rim is a half-round: an arc of radius h = half the rim thickness,
     centred at (edgeRadius - h, 0). It therefore meets the top flat tangentially
     at r = edgeRadius - h and reaches its widest point, r = edgeRadius, exactly
     at y = 0. Rolling the edge rather than chamfering it is what gives the disc
     a continuous bright rim-light instead of a hard specular line. */
  const h = halfThickness(LP.edgeRadius);
  const rimStart = LP.edgeRadius - h;

  const top = [];
  const push = r => top.push(new THREE.Vector2(r, halfThickness(r)));

  /* Sampled densely where the profile actually bends, sparsely across flats. */
  const stops = [
    [LP.holeRadius, LP.labelRadius, 8],
    [LP.labelRadius, LP.deadwaxOuter, 18],
    [LP.deadwaxOuter, LP.bandOuter, 22],
    [LP.bandOuter, LP.leadInOuter, 6],
    [LP.leadInOuter, LP.landOuter, 8],
    [LP.landOuter, rimStart, 6],
  ];
  for (const [a, b, n] of stops) {
    for (let i = 0; i < n; i++) push(a + ((b - a) * i) / n);
  }
  push(rimStart);

  const pts = top.slice();

  /* Rim arc, from the top flat around to the bottom flat. Endpoints are
     omitted because the flats already supply them. */
  const ROLL = 16;
  for (let i = 1; i < ROLL; i++) {
    const a = (i / ROLL) * Math.PI;
    pts.push(new THREE.Vector2(rimStart + Math.sin(a) * h, Math.cos(a) * h));
  }

  /* Underside: the top profile mirrored, walked back inward. */
  for (let i = top.length - 1; i >= 0; i--) {
    pts.push(new THREE.Vector2(top[i].x, -top[i].y));
  }

  return pts;
}

/* Rewrite UVs and attach the analytic tangent attribute. */
function fitFrame(geom) {
  const pos = geom.attributes.position;
  const nrm = geom.attributes.normal;
  const count = pos.count;

  const uv = new Float32Array(count * 2);
  const tan = new Float32Array(count * 4);

  const span = LP.edgeRadius - LP.holeRadius;
  const radial = new THREE.Vector3();
  const normal = new THREE.Vector3();
  const tangent = new THREE.Vector3();

  for (let i = 0; i < count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const r = Math.hypot(x, z);

    uv[i * 2] = (Math.atan2(z, x) / (Math.PI * 2)) + 0.5;
    uv[i * 2 + 1] = THREE.MathUtils.clamp((r - LP.holeRadius) / span, 0, 1);

    /* Circumferential unit vector, used directly on the rim wall where the
       radial direction and the normal are parallel and the cross product
       collapses. */
    const circum = r > 1e-6
      ? tangent.set(-z / r, 0, x / r)
      : tangent.set(0, 0, 1);

    radial.set(r > 1e-6 ? x / r : 1, 0, r > 1e-6 ? z / r : 0);
    normal.set(nrm.getX(i), nrm.getY(i), nrm.getZ(i));

    const t = new THREE.Vector3().crossVectors(radial, normal);
    if (t.lengthSq() < 1e-8) t.copy(circum); else t.normalize();

    tan[i * 4] = t.x; tan[i * 4 + 1] = t.y; tan[i * 4 + 2] = t.z; tan[i * 4 + 3] = 1;
  }

  geom.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  geom.setAttribute("tangent", new THREE.BufferAttribute(tan, 4));
  return geom;
}

/* ── Label ────────────────────────────────────────────────────────────────
   Its own mesh with planar UVs, because the album art has to map as a square
   and the vinyl's radial UVs cannot express that. Sits a hair proud of the
   plateau, as a glued paper label does. */
function makeLabelGeometry() {
  const g = new THREE.RingGeometry(LP.holeRadius, LP.labelRadius, 128, 1);
  const pos = g.attributes.position;
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i++) {
    /* Square projection: the art fills the label's bounding box, so the disc
       crops the corners off the cover exactly as a real label does. */
    uv[i * 2] = pos.getX(i) / (LP.labelRadius * 2) + 0.5;
    uv[i * 2 + 1] = pos.getY(i) / (LP.labelRadius * 2) + 0.5;
  }
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return g;
}

export class Record {
  constructor(renderer) {
    this.group = new THREE.Group();
    const maps = vinylMaps();

    /* Anisotropic filtering matters more here than anywhere else in the scene:
       the record is a large, nearly-flat surface viewed at grazing angles, the
       exact case where trilinear filtering smears the roughness map into mush
       and the sheen disappears. */
    const maxAniso = renderer.capabilities.getMaxAnisotropy();
    for (const t of Object.values(maps)) t.anisotropy = maxAniso;

    this.vinylMaterial = new THREE.MeshPhysicalMaterial({
      map: maps.albedo,
      roughnessMap: maps.roughness,
      normalMap: maps.normal,
      normalScale: new THREE.Vector2(1, 1),
      metalness: 0.0,
      roughness: 1.0,          // scaled by the map
      /* Vinyl is a dielectric with a genuinely high IOR (~1.55) and a bright,
         tight specular. Leaving this at the 0.5 default is why most attempts
         at a CG record look like black felt. */
      reflectivity: 0.50,
      ior: 1.55,
      /* The anisotropic term. Strength comes from the map's blue channel;
         direction is pinned to +tangent, i.e. circumferential. */
      anisotropy: 1.0,
      anisotropyRotation: 0.0,
      anisotropyMap: maps.anisotropy,
      /* A whisper of clearcoat for the polished lands. Vinyl really does have
         a thin smooth skin over a rougher substrate — but clearcoat adds a
         second, full-strength specular lobe on top of the base one, and at
         0.35 that second lobe was bright enough to wash the disc out to pale
         grey regardless of how dark its albedo was. */
      clearcoat: 0.05,
      clearcoatRoughness: 0.20,
      envMapIntensity: 1.6,
    });

    const geom = fitFrame(new THREE.LatheGeometry(profilePoints(), LATHE_SEGMENTS));
    this.vinyl = new THREE.Mesh(geom, this.vinylMaterial);
    this.vinyl.castShadow = true;
    this.vinyl.receiveShadow = true;
    this.group.add(this.vinyl);

    /* Label front + back. A blank white texture until album art arrives, so a
       missing cover degrades to a plain label instead of an untextured mesh. */
    this.labelTexture = null;
    this.labelMaterial = new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      roughness: 0.74,
      metalness: 0.0,
      clearcoat: 0.06,
      clearcoatRoughness: 0.6,
      envMapIntensity: 0.9,
    });

    const labelGeom = makeLabelGeometry();
    const y = LP.thickLabel / 2 + 0.03;

    this.labelFront = new THREE.Mesh(labelGeom, this.labelMaterial);
    this.labelFront.rotation.x = -Math.PI / 2;
    this.labelFront.position.y = y;
    this.labelFront.receiveShadow = true;
    this.group.add(this.labelFront);

    this.labelBack = new THREE.Mesh(labelGeom, this.labelMaterial);
    this.labelBack.rotation.x = Math.PI / 2;
    this.labelBack.position.y = -y;
    this.group.add(this.labelBack);

    /* Spin state. Angle is integrated rather than derived from a clock so that
       spin-up and spin-down ramps are continuous — snapping the angle to
       elapsed*rpm would make the platter jump every time the rate changed. */
    this.angle = 0;
    this.rpm = 0;

    /* Dish warp — DISABLED, and this is the interesting part.

       No pressing is flat, so tilting the disc a fraction of a degree once per
       revolution is physically right and was added to sell the rotation. At
       0.55 mm over a 152 mm radius that is a tilt of about 0.2°, which sounds
       far too small to see. It is not.

       A record is a near-mirror, and a mirror doubles angles: 0.2° of surface
       tilt swings the reflected direction by 0.4°. The environment is bright
       in one band and dark elsewhere, so that swing walks the boundary between
       them across a large part of the disc — and because a dish warp makes the
       surface normal vary LINEARLY with position, that boundary is a straight
       radial line. The result was a hard-edged dark wedge sweeping round the
       record once per revolution and appearing to switch on and off.

       Measured: sweeping the disc through a full turn with the camera frozen
       moved mean brightness across the grooved band between 33 and 39 with the
       warp on, and held it at exactly 33 at every angle with it off.

       The rotation cue is carried instead by the asymmetric surface marks in
       vinylMaps.js, which is the better mechanism anyway — a real record reads
       as spinning because of its scuffs, not because you can see it wobble.

       Left as a field rather than deleted: with a genuinely smooth environment
       (a real HDRI, or a much larger diffuse source) there is no boundary for
       the tilt to sweep, and this could come back. */
    this.warpAmplitude = 0;                 // mm at the rim
    this.warpPhase = Math.random() * Math.PI * 2;
  }

  /* Swap the album art.

     `track` is used to generate a house label whenever real artwork is
     unavailable — no album_art on the payload, a dead URL, a CORS refusal, a
     404 from the app's SMTC art cache. Falling back to a BLANK disc was the
     first design and it is wrong: the no-art case is common, not exceptional,
     and a blank white label reads as a bug rather than as a record.

     Returns a promise so the choreography can wait for the texture to decode
     before dropping a record onto the platter. A label that pops in half a
     second late is the most obvious tell that this is a web page. */
  setAlbumArt(url, track = {}) {
    const applyFallback = resolve => {
      this.applyLabelTexture(makeHouseLabel(track), () => resolve(false));
    };

    return new Promise(resolve => {
      if (!url) { applyFallback(resolve); return; }

      const loader = new THREE.TextureLoader();
      /* Album art may be a remote https URL on Spotify's CDN. The dev server
         proxies it to keep it same-origin, but set this anyway so the loader
         still works if the view is ever served alongside the app and asked to
         load a CDN URL directly. */
      loader.setCrossOrigin("anonymous");
      loader.load(
        url,
        tex => { this.adoptTexture(tex); resolve(true); },
        undefined,
        () => applyFallback(resolve),
      );
    });
  }

  /* Load a data URI as the label, used for generated fallbacks. */
  applyLabelTexture(dataUri, done) {
    new THREE.TextureLoader().load(
      dataUri,
      tex => { this.adoptTexture(tex); done(); },
      undefined,
      () => {
        /* Even the generated label failed to decode, which should not happen.
           A plain cream disc is the last resort. */
        this.labelMaterial.map = null;
        this.labelMaterial.color.set(0xe8e4d8);
        this.labelMaterial.needsUpdate = true;
        done();
      },
    );
  }

  adoptTexture(tex) {
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 8;
    const old = this.labelMaterial.map;
    this.labelMaterial.map = tex;
    this.labelMaterial.color.set(0xffffff);
    this.labelMaterial.needsUpdate = true;
    if (old) old.dispose();
    this.labelTexture = tex;
  }

  update(dt) {
    this.angle += (this.rpm / 60) * Math.PI * 2 * dt;
    if (this.angle > Math.PI * 2) this.angle -= Math.PI * 2;
    this.group.rotation.y = this.angle;

    /* Tilt the disc very slightly, with the tilt axis fixed in WORLD space
       while the record turns underneath it. That is what a warp does: the high
       point of the dish travels around with the vinyl, so from a fixed camera
       the rim rises and falls once per revolution.

       Expressed as a small rotation about x and z rather than by deforming the
       mesh — the amplitude is well under a millimetre over a 152 mm radius, so
       a rigid tilt is indistinguishable from the real bend and costs nothing. */
    const tilt = this.warpAmplitude / LP.edgeRadius;
    const a = this.angle + this.warpPhase;
    this.group.rotation.x = Math.sin(a) * tilt;
    this.group.rotation.z = Math.cos(a) * tilt * 0.6;
  }

  dispose() {
    this.vinyl.geometry.dispose();
    this.vinylMaterial.dispose();
    this.labelFront.geometry.dispose();
    this.labelMaterial.dispose();
    if (this.labelTexture) this.labelTexture.dispose();
  }
}
