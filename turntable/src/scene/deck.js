/* ════════════════════════════════════════════════════════════════════════
   deck.js — the turntable the record sits on.

   The deck is doing the heavy lifting on style. A record on its own is just a
   black disc; it is the plinth and the platter that decide whether this reads
   as the same product as the console. So the materials are lifted straight off
   the console's own vocabulary:

     walnut plinth        --wood-1 #7a5233 / --wood-2 #55351d / --wood-3 #35200f
     cream top plate      --fascia #ece8db, brushed
     silkscreen etching   --etch   #6f6c5e
     amber pilot + strobe --amber-led #f3b152
     terracotta cue lever --clay   #d97757
     dark rubber mat      --well   #1c1914

   Dimensions are millimetres, loosely following a 1970s belt-drive deck of the
   Thorens TD-160 / Pioneer PL-518 school: a wooden plinth with an inset metal
   top plate, rather than the all-metal DJ decks everyone reaches for.
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";
import { LP } from "./vinylMaps.js";

export const DECK = {
  width: 430,
  depth: 355,
  plinthHeight: 62,
  plateInset: 9,        // top plate sits proud of the wood by this much
  /* Larger than the record's 152 mm, as every real deck's platter is. The
     exposed ring is where the strobe dots live, and without it the record
     covers the platter completely and the deck loses the one element that
     makes the rotation readable at a glance. */
  platterRadius: 166,
  platterHeight: 26,
  matRadius: 148,
  spindleRadius: 3.35,  // a shade under the record's 3.6 mm hole
  matThickness: 2.4,
  /* Height of the mat's top surface above the plinth's underside. */
  get platterTop() { return this.plinthHeight + this.plateInset + this.platterHeight; },

  /* WORLD ORIGIN CONVENTION
     The whole scene is placed so that y = 0 is the record's resting plane: the
     top of the mat. Every other module then talks about heights relative to
     where the record sits, instead of each one carrying its own copy of the
     plinth stack-up and drifting out of agreement with the others.

     The top plate is therefore below the origin by the platter and the mat. */
  get topPlateWorldY() { return -(this.platterHeight + this.matThickness); },
  get groupOffsetY() { return -(this.platterTop + this.matThickness); },
};

/* ── Wood ─────────────────────────────────────────────────────────────────
   Walnut, generated rather than photographed so the prototype stays offline.
   The grain is a stack of stretched value noise plus the darker earlywood
   streaks that make walnut recognisable. */
function makeWalnutMaps() {
  const W = 1024, H = 512;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const g = c.getContext("2d");

  g.fillStyle = "#6d4a2e";
  g.fillRect(0, 0, W, H);

  /* Grain lines: long, low-amplitude sinusoids with drifting phase so they
     never look like a repeating wave. */
  for (let i = 0; i < 260; i++) {
    const y0 = Math.random() * H;
    const amp = 4 + Math.random() * 26;
    const freq = 0.004 + Math.random() * 0.010;
    const phase = Math.random() * Math.PI * 2;
    const dark = Math.random();

    g.strokeStyle = dark < 0.30
      ? `rgba(53, 32, 15, ${0.16 + Math.random() * 0.30})`   // --wood-3
      : `rgba(122, 82, 51, ${0.10 + Math.random() * 0.22})`; // --wood-1
    g.lineWidth = 0.6 + Math.random() * 3.4;

    g.beginPath();
    for (let x = 0; x <= W; x += 4) {
      const y = y0 + Math.sin(x * freq + phase) * amp + Math.sin(x * freq * 3.3) * amp * 0.18;
      x === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
    }
    g.stroke();
  }

  /* Pores. Walnut is open-grained and the little dark flecks are most of why
     it reads as walnut rather than as generic brown. */
  for (let i = 0; i < 5200; i++) {
    g.fillStyle = `rgba(40, 24, 11, ${0.10 + Math.random() * 0.28})`;
    const w = 0.6 + Math.random() * 2.6;
    g.fillRect(Math.random() * W, Math.random() * H, w, w * (0.3 + Math.random() * 0.5));
  }

  const map = new THREE.CanvasTexture(c);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;

  /* Roughness derived from the same image: the dark earlywood is more
     absorbent than the pale latewood, so gloss tracks the grain. Deriving it
     from the albedo instead of authoring it separately is what keeps the
     highlight sitting *on* the grain rather than floating over it. */
  const rc = document.createElement("canvas");
  rc.width = W; rc.height = H;
  const rg = rc.getContext("2d");
  rg.drawImage(c, 0, 0);
  const img = rg.getImageData(0, 0, W, H);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const lum = (d[i] * 0.30 + d[i + 1] * 0.59 + d[i + 2] * 0.11) / 255;
    const rough = 0.52 - lum * 0.20;                    // darker grain → rougher
    d[i] = d[i + 1] = d[i + 2] = Math.round(rough * 255);
  }
  rg.putImageData(img, 0, 0);
  const roughnessMap = new THREE.CanvasTexture(rc);
  roughnessMap.wrapS = roughnessMap.wrapT = THREE.RepeatWrapping;

  return { map, roughnessMap };
}

/* ── Brushed aluminium ────────────────────────────────────────────────────
   Linear brushing for the top plate, circular brushing for the platter. Both
   are anisotropic; the difference in brush direction is the whole reason the
   platter and the plate read as different pieces of metal despite sharing a
   colour. */
function makeBrushedMaps({ circular }) {
  const S = 1024;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");

  g.fillStyle = "#8f8d83";
  g.fillRect(0, 0, S, S);

  g.lineWidth = 1;
  for (let i = 0; i < 9000; i++) {
    const shade = 118 + Math.random() * 62;
    g.strokeStyle = `rgba(${shade},${shade},${shade - 4},${0.05 + Math.random() * 0.13})`;
    if (circular) {
      const r = Math.random() * S * 0.72;
      const a0 = Math.random() * Math.PI * 2;
      g.beginPath();
      g.arc(S / 2, S / 2, r, a0, a0 + 0.02 + Math.random() * 0.4);
      g.stroke();
    } else {
      const y = Math.random() * S;
      g.beginPath();
      g.moveTo(0, y);
      g.lineTo(S, y + (Math.random() - 0.5) * 2);
      g.stroke();
    }
  }

  const roughnessMap = new THREE.CanvasTexture(c);
  roughnessMap.wrapS = roughnessMap.wrapT = THREE.RepeatWrapping;
  return { roughnessMap };
}

/* ── Silkscreen ───────────────────────────────────────────────────────────
   The etched legends on the top plate. This is a small thing that does a large
   amount of work: a blank metal plate looks unfinished, and real decks are
   covered in tiny type. Uses the console's own three faces. */
function makeLegendTexture() {
  const W = 2048, H = 1700;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const g = c.getContext("2d");

  g.fillStyle = "#ece8db";
  g.fillRect(0, 0, W, H);

  const etch = "#6f6c5e";
  const faint = "rgba(111,108,94,0.55)";

  /* Maker's mark, front-left. */
  g.fillStyle = "#262520";
  g.font = '600 44px "Space Grotesk", system-ui, sans-serif';
  g.letterSpacing = "10px";
  g.fillText("SONIC VECTOR", 96, H - 96);

  g.fillStyle = faint;
  g.font = '400 24px "IBM Plex Mono", Consolas, monospace';
  g.letterSpacing = "6px";
  g.fillText("SEMANTIC MASTERING CONSOLE  ·  MK II", 100, H - 56);

  /* Speed legend, front-right. */
  g.fillStyle = etch;
  g.font = '500 30px "IBM Plex Mono", Consolas, monospace';
  g.letterSpacing = "5px";
  g.fillText("33⅓", W - 430, H - 100);
  g.fillText("45", W - 300, H - 100);

  g.fillStyle = faint;
  g.font = '400 21px "IBM Plex Mono", Consolas, monospace';
  g.fillText("RPM", W - 200, H - 100);

  /* Pitch scale, a little ruler of ticks. */
  g.strokeStyle = faint;
  g.lineWidth = 2;
  for (let i = 0; i <= 16; i++) {
    const x = W - 430 + i * 16;
    const long = i % 4 === 0;
    g.beginPath();
    g.moveTo(x, H - 190);
    g.lineTo(x, H - 190 + (long ? 20 : 11));
    g.stroke();
  }
  g.fillStyle = faint;
  g.font = '400 17px "IBM Plex Mono", Consolas, monospace';
  g.letterSpacing = "2px";
  g.fillText("PITCH  −8 · 0 · +8", W - 434, H - 208);

  /* A hairline border, as silkscreened plates always have. */
  g.strokeStyle = "rgba(111,108,94,0.30)";
  g.lineWidth = 3;
  g.strokeRect(52, 52, W - 104, H - 104);

  const map = new THREE.CanvasTexture(c);
  map.colorSpace = THREE.SRGBColorSpace;
  return map;
}

/* Rounded-box helper. Real objects have no perfectly sharp edges, and the thin
   highlight along a rounded corner is most of what sells solidity. */
function roundedBox(w, h, d, r, seg = 4) {
  const shape = new THREE.Shape();
  const x = w / 2, z = d / 2;
  shape.moveTo(-x + r, -z);
  shape.lineTo(x - r, -z);
  shape.quadraticCurveTo(x, -z, x, -z + r);
  shape.lineTo(x, z - r);
  shape.quadraticCurveTo(x, z, x - r, z);
  shape.lineTo(-x + r, z);
  shape.quadraticCurveTo(-x, z, -x, z - r);
  shape.lineTo(-x, -z + r);
  shape.quadraticCurveTo(-x, -z, -x + r, -z);

  const bevel = Math.min(2.0, h * 0.14);
  const geom = new THREE.ExtrudeGeometry(shape, {
    depth: h - bevel * 2,
    bevelEnabled: true,
    bevelSize: bevel,
    bevelThickness: bevel,
    bevelSegments: seg,
    curveSegments: 12,
  });
  /* Extrude builds along +Z; stand it up so height runs along +Y. */
  geom.rotateX(-Math.PI / 2);
  geom.translate(0, bevel, 0);
  return geom;
}

export class Deck {
  constructor(renderer) {
    this.group = new THREE.Group();
    const maxAniso = renderer.capabilities.getMaxAnisotropy();

    /* ── Plinth ──────────────────────────────────────────────────────── */
    const walnut = makeWalnutMaps();
    walnut.map.anisotropy = maxAniso;
    walnut.map.repeat.set(2.2, 1.8);
    walnut.roughnessMap.repeat.copy(walnut.map.repeat);

    const plinthMat = new THREE.MeshPhysicalMaterial({
      map: walnut.map,
      roughnessMap: walnut.roughnessMap,
      roughness: 1.0,
      metalness: 0.0,
      /* Satin lacquer over open-pore walnut. The clearcoat is what gives the
         plinth its soft sheen without turning the wood itself glossy. */
      clearcoat: 0.30,
      clearcoatRoughness: 0.45,
      envMapIntensity: 0.85,
    });

    const plinth = new THREE.Mesh(
      roundedBox(DECK.width, DECK.plinthHeight, DECK.depth, 7),
      plinthMat,
    );
    plinth.castShadow = true;
    plinth.receiveShadow = true;
    this.group.add(plinth);

    /* ── Top plate ───────────────────────────────────────────────────── */
    const brushedFlat = makeBrushedMaps({ circular: false });
    brushedFlat.roughnessMap.anisotropy = maxAniso;
    brushedFlat.roughnessMap.repeat.set(1, 1);

    const legend = makeLegendTexture();
    legend.anisotropy = maxAniso;

    /* The plate is two meshes, and the split is not cosmetic.

       ExtrudeGeometry triangulates its cap as a fan over the outline and gives
       it UVs in raw model space. Anisotropic shading derives its tangent frame
       from UV derivatives, so on that cap the tangent flips along every
       triangle boundary and the fan becomes visible as hard diagonal creases
       across the metal. Putting the brushed, anisotropic, silkscreened surface
       on a plain PlaneGeometry — regular UVs, one consistent tangent frame —
       removes the artefact instead of hiding it. The extruded body underneath
       keeps the rounded edge on the silhouette and is shaded plainly. */
    const plateBodyMat = new THREE.MeshPhysicalMaterial({
      color: 0xdedacb,
      roughnessMap: brushedFlat.roughnessMap,
      roughness: 0.56,
      metalness: 0.52,
      envMapIntensity: 1.15,
    });

    const plate = new THREE.Mesh(
      roundedBox(DECK.width - 16, DECK.plateInset, DECK.depth - 16, 5),
      plateBodyMat,
    );
    plate.position.y = DECK.plinthHeight;
    plate.castShadow = true;
    plate.receiveShadow = true;
    this.group.add(plate);

    this.plateMat = new THREE.MeshPhysicalMaterial({
      map: legend,
      roughnessMap: brushedFlat.roughnessMap,
      color: 0xffffff,
      roughness: 0.52,
      metalness: 0.52,
      /* Brushing runs along the plate's long axis, so the highlight smears
         left-to-right the way it does on a real fascia. */
      anisotropy: 0.8,
      anisotropyRotation: 0,
      envMapIntensity: 1.2,
    });

    const face = new THREE.Mesh(
      new THREE.PlaneGeometry(DECK.width - 26, DECK.depth - 26, 1, 1),
      this.plateMat,
    );
    face.rotation.x = -Math.PI / 2;
    face.position.y = DECK.plinthHeight + DECK.plateInset + 0.05;
    face.receiveShadow = true;
    this.group.add(face);

    /* ── Platter ─────────────────────────────────────────────────────── */
    const brushedRound = makeBrushedMaps({ circular: true });
    brushedRound.roughnessMap.anisotropy = maxAniso;

    /* Metalness is deliberately well under 1. A fully metallic surface has no
       diffuse term at all — it is purely a mirror — so the platter's vertical
       machined edge, which faces outward into a dark room and sees none of the
       overhead panels, rendered as a solid black band. Real anodised aluminium
       is not a perfect conductor either; backing off metalness gives the rim
       enough diffuse to stay legible without making it look like plastic. */
    this.platterMat = new THREE.MeshPhysicalMaterial({
      color: 0xcac6b8,
      roughnessMap: brushedRound.roughnessMap,
      roughness: 0.50,
      metalness: 0.50,
      anisotropy: 0.9,
      envMapIntensity: 1.25,
    });

    this.platter = new THREE.Group();
    this.platter.position.y = DECK.plinthHeight + DECK.plateInset;

    const platterBody = new THREE.Mesh(
      new THREE.CylinderGeometry(DECK.platterRadius, DECK.platterRadius - 3, DECK.platterHeight, 192, 1, false),
      this.platterMat,
    );
    platterBody.position.y = DECK.platterHeight / 2;
    platterBody.castShadow = true;
    platterBody.receiveShadow = true;
    this.platter.add(platterBody);

    /* Strobe dimples.

       On a real deck these are machined pits in the platter edge, lit by a
       mains-frequency neon so they appear to stand still at the correct speed.
       They were emissive amber here, pulsing with the platter rate — which
       turned the platter rim into a ring of blinking lights that pulled the
       eye straight off the record. They are now what they are on the deck
       itself: shallow machined marks that catch the light as they pass and do
       nothing else. */
    this.strobeMat = new THREE.MeshStandardMaterial({
      color: 0x9a9384,
      roughness: 0.34,
      metalness: 0.55,
    });

    const STROBE_COUNT = 92;
    const dot = new THREE.BoxGeometry(1.5, 0.6, 3.6);
    const strobes = new THREE.InstancedMesh(dot, this.strobeMat, STROBE_COUNT);
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const s = new THREE.Vector3(1, 1, 1);
    for (let i = 0; i < STROBE_COUNT; i++) {
      const a = (i / STROBE_COUNT) * Math.PI * 2;
      const r = DECK.platterRadius - 4.5;
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), -a);
      m.compose(new THREE.Vector3(Math.cos(a) * r, DECK.platterHeight - 0.2, Math.sin(a) * r), q, s);
      strobes.setMatrixAt(i, m);
    }
    strobes.instanceMatrix.needsUpdate = true;
    this.platter.add(strobes);

    /* Mat. Dark ribbed rubber, the same value as the console's recessed wells,
       so the record has something to sit against that is not simply black. */
    const matMat = new THREE.MeshPhysicalMaterial({
      color: 0x1c1914,               // --well
      roughness: 0.86,
      metalness: 0.0,
      sheen: 0.35,
      sheenRoughness: 0.8,
      sheenColor: new THREE.Color(0x4a4437),
      envMapIntensity: 0.55,
    });
    const mat = new THREE.Mesh(
      new THREE.CylinderGeometry(DECK.matRadius, DECK.matRadius, DECK.matThickness, 160),
      matMat,
    );
    mat.position.y = DECK.platterHeight + DECK.matThickness / 2;
    mat.receiveShadow = true;
    this.platter.add(mat);

    this.group.add(this.platter);

    /* ── Spindle ─────────────────────────────────────────────────────── */
    const chrome = new THREE.MeshPhysicalMaterial({
      color: 0xe6e4dd,
      roughness: 0.20,
      metalness: 0.95,
      envMapIntensity: 1.1,
    });
    const spindle = new THREE.Mesh(
      new THREE.CylinderGeometry(DECK.spindleRadius, DECK.spindleRadius, 16, 48),
      chrome,
    );
    spindle.position.y = DECK.plinthHeight + DECK.plateInset + DECK.platterHeight + 9;
    spindle.castShadow = true;
    this.group.add(spindle);
    this.chrome = chrome;

    /* Chamfered spindle tip, so a record can be lowered onto it convincingly. */
    const tip = new THREE.Mesh(new THREE.ConeGeometry(DECK.spindleRadius, 2.6, 48), chrome);
    tip.position.y = spindle.position.y + 8 + 1.3;
    this.group.add(tip);

    /* ── Pilot lamp ──────────────────────────────────────────────────── */
    this.pilotMat = new THREE.MeshStandardMaterial({
      color: 0x3a2c15,
      emissive: new THREE.Color(0xf3b152),
      emissiveIntensity: 0.4,
      roughness: 0.28,
    });
    const pilot = new THREE.Mesh(new THREE.CylinderGeometry(4.2, 4.2, 1.6, 32), this.pilotMat);
    pilot.position.set(-DECK.width / 2 + 42, DECK.plinthHeight + DECK.plateInset + 0.4, DECK.depth / 2 - 40);
    this.group.add(pilot);

    /* A real point light under the lamp, so it actually spills onto the plate
       instead of being a flat emissive dot pretending to glow. */
    this.pilotLight = new THREE.PointLight(0xf3b152, 0, 90, 2);
    this.pilotLight.position.copy(pilot.position).setY(pilot.position.y + 6);
    this.group.add(this.pilotLight);

    /* ── Feet ────────────────────────────────────────────────────────── */
    const footMat = new THREE.MeshPhysicalMaterial({
      color: 0x14120d, roughness: 0.78, metalness: 0.1,
    });
    for (const [sx, sz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const foot = new THREE.Mesh(new THREE.CylinderGeometry(15, 17, 12, 28), footMat);
      foot.position.set(sx * (DECK.width / 2 - 40), -6, sz * (DECK.depth / 2 - 38));
      foot.castShadow = true;
      this.group.add(foot);
    }

    /* Drop the whole deck so the mat's surface sits at world y = 0. */
    this.group.position.y = DECK.groupOffsetY;

    this.platterAngle = 0;
    this.platterRpm = 0;
  }

  /* The platter and the record are driven from the same rate but tracked as
     separate angles: during a swap the record is off the platter, and slaving
     one to the other would drag the platter around with it. */
  update(dt, { pilotLevel }) {
    this.platterAngle += (this.platterRpm / 60) * Math.PI * 2 * dt;
    if (this.platterAngle > Math.PI * 2) this.platterAngle -= Math.PI * 2;
    this.platter.rotation.y = this.platterAngle;

    /* The pilot is the deck's only lamp now. Kept low and steady — it marks
       power rather than demanding attention. */
    this.pilotMat.emissiveIntensity = 0.2 + pilotLevel * 0.75;
    this.pilotLight.intensity = pilotLevel * 320;
  }

  setEnvironment(env) {
    for (const m of [this.platterMat, this.chrome]) m.envMap = env;
  }
}
