/* ════════════════════════════════════════════════════════════════════════
   stage.js — renderer, environment, light rig, camera rig, post chain.

   THE "BLENDER RENDER" LOOK, DECONSTRUCTED
   ----------------------------------------
   What actually separates a render from realtime output is rarely polygon
   count. It is four things, in this order of importance:

     1. TONE MAPPING. Blender 4.x ships AgX as its default view transform, and
        AgX is most of why modern Blender output looks the way it does — bright
        highlights desaturate toward white instead of clipping to neon, and the
        shadow toe is soft. three.js has AgXToneMapping, so this is free and it
        is the single highest-leverage line in this file.

     2. THE ENVIRONMENT. Renders get their believability from what the surfaces
        reflect, not from their lights. A procedurally-built studio is used
        here instead of RoomEnvironment, because RoomEnvironment is a neutral
        grey box and this deck needs to be reflecting warm cream and amber to
        belong to the same product as the console.

     3. CONTACT AND OCCLUSION. Shadows that actually touch the object.

     4. LENS ARTEFACTS. Bloom, a whisper of chromatic aberration, vignetting
        and grain. Individually invisible; collectively they are the difference
        between "3D on a web page" and "photograph of an object".
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { BokehPass } from "three/addons/postprocessing/BokehPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { Ease, lerp } from "../easing.js";

const WORLD_UP = new THREE.Vector3(0, 1, 0);
const UP_A = new THREE.Vector3();

/* ── Camera poses ─────────────────────────────────────────────────────────
   Scene units are millimetres, so these read as real camera placements: the
   hero shot is a 30 mm-equivalent lens about 700 mm from the deck. */
export const POSES = {
  hero:  { pos: new THREE.Vector3(335, 292, 508), target: new THREE.Vector3(0, 6, 34), fov: 30 },
  /* Straight down, echoing the console's own top-down layout. Switching
     between this and hero is the visual bridge between the two views.

     `up` is given explicitly because this pose is nearly vertical, and lookAt
     builds its basis from cross(up, viewDir). With the default up of +Y that
     cross product is close to degenerate here, so the camera's roll is
     decided by floating-point noise — and the idle drift was enough to swing
     it, tilting the whole deck in frame by several degrees. Pointing up along
     −Z removes the ambiguity and fixes the deck square to the frame. */
  plan:  { pos: new THREE.Vector3(0, 880, 120), target: new THREE.Vector3(0, 0, 6), fov: 27,
           up: new THREE.Vector3(0, 0, -1) },
  /* On the stylus at the lead-in groove, which sits near (125, 75). */
  macro: { pos: new THREE.Vector3(258, 96, 232), target: new THREE.Vector3(116, -4, 66), fov: 24 },
  /* Low and wide, for the record swap: a disc leaving a spindle is far more
     legible in profile than from three-quarters above. */
  swap:  { pos: new THREE.Vector3(168, 104, 606), target: new THREE.Vector3(-10, 46, 0), fov: 35 },
};

/* ── Studio environment ───────────────────────────────────────────────────
   A cube-camera-free environment: emissive planes arranged around the origin,
   rendered once through PMREMGenerator into a prefiltered mipmapped radiance
   map. Costs one render at startup and nothing afterwards.

   The layout is a standard three-point product-shot studio, in the console's
   palette:
     · a large cream softbox overhead-left   (--fascia  #ece8db)  the key
     · a warm amber strip low-right          (--amber   #e9a13b)  the kicker
     · a dim cool panel behind               (--spruce  #3d5a46)  separation
     · a dark floor so the underside of the record does not glow             */
function makeStudioEnvironment(renderer) {
  const env = new THREE.Scene();

  const panel = (color, intensity, w, h, pos, lookAt) => {
    const m = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(color).multiplyScalar(intensity),
        side: THREE.DoubleSide,
      }),
    );
    m.position.copy(pos);
    m.lookAt(lookAt || new THREE.Vector3(0, 0, 0));
    env.add(m);
    return m;
  };

  /* Ambient shell. Without this the shadows go to pure black and every
     dielectric in the scene reads as plastic. */
  const shell = new THREE.Mesh(
    new THREE.SphereGeometry(3000, 24, 16),
    new THREE.MeshBasicMaterial({
      /* Raised well above the token value on purpose. This shell is what the
         disc sees over most of its area, and at --well's own brightness the
         record reflected near-black everywhere the sheen band did not reach.
         A real record in a real room reflects a dim ceiling, never a void. */
      color: new THREE.Color(0x1c1914).multiplyScalar(4.2),   // --well, lifted
      side: THREE.BackSide,
    }),
  );
  env.add(shell);

  /* Intensities are deliberately restrained. A record is a dark mirror: almost
     all of its brightness is reflected, none of it is its own, so the disc
     reports the environment's total energy directly. Push these up and the
     vinyl turns into white plastic — which is exactly what the second test
     render showed when the sheen band was first added at 6.4. */
  /* Key softbox. Sits closer to directly overhead than a pure three-point rig
     would put it, because the PLAN camera looks straight down and a record's
     mirror direction from there is straight up — offset this far enough to one
     side and the top-down view has nothing to reflect and goes flat. */
  panel(0xece8db, 2.3, 2100, 1500, new THREE.Vector3(-330, 1320, 240));
  panel(0xf6f3ea, 0.95, 1300, 900, new THREE.Vector3(880, 700, 620));     // bounce fill
  panel(0xe9a13b, 1.4, 1500, 420,  new THREE.Vector3(700, 120, -820));    // amber kicker
  panel(0x3d5a46, 0.8, 1600, 1000, new THREE.Vector3(-500, 300, -1100));  // cool separation
  panel(0xd97757, 1.1, 700, 380,   new THREE.Vector3(-980, 180, -260));   // terracotta accent

  /* THE SHEEN BAND — the most important panel in the rig, and the one whose
     position is calculated rather than chosen.

     A record is a horizontal mirror. It shows the camera whatever sits in the
     camera's mirror direction: same elevation, opposite azimuth. The hero
     camera is at (335, 292, 508), i.e. azimuth ≈ 57°, elevation ≈ 26°. So the
     disc reflects back-left at ≈ 26° up, and if nothing bright is there the
     record renders as a featureless dark hole no matter how good its material
     is. That is exactly what the first test render showed.

     This panel is a wide, low softbox placed at that reflection: azimuth
     ≈ 230°, elevation ≈ 26°. Its reflection is what the anisotropic groove
     term then smears radially into the swept sheen that reads as vinyl.

     It has to stay NARROW. Widen it into a wall and every direction the disc
     looks in is bright, the sweep loses its edges, and the record goes from
     "black with a highlight" to "uniformly pale". The shape of this rectangle
     is the shape of the highlight. */
  panel(0xf6f3ea, 3.4, 1750, 460, new THREE.Vector3(-1000, 780, -1200));

  /* A second, much dimmer band on the near side, so the disc is not perfectly
     dark on the camera's own side and the sheen has somewhere to fall off to. */
  panel(0xece8db, 1.05, 1700, 420, new THREE.Vector3(560, 430, 1150));

  /* THE HORIZON BAND — a continuous cylinder, not a ring of separate panels.

     It does two jobs. The first is ordinary: vertical surfaces (the platter's
     machined edge, the plinth sides, the counterweight) reflect outward and
     slightly downward and see none of the overhead panels, so without light at
     their own level the aluminium rim renders as a black band.

     The second is the reason it is a cylinder. Anisotropic shading bends the
     record's reflection vector toward the horizon, and how far it bends
     depends on where you are on the disc — so one side of the record ends up
     sampling near or below the horizon while the other samples upward. Any
     variation down there gets painted onto the disc as a hard-edged wedge
     rotating across it. Six discrete panels with gaps between them are exactly
     such a variation, and they were the wedge.

     A single unbroken band of constant radiance has no azimuthal structure for
     the bend to pick up, so the falloff becomes smooth and directionless. */
  const horizon = new THREE.Mesh(
    new THREE.CylinderGeometry(1500, 1500, 900, 64, 1, true),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(0xbfae94).multiplyScalar(0.5),
      side: THREE.BackSide,
    }),
  );
  horizon.position.y = 60;
  env.add(horizon);

  /* And a matching floor-level band below it, so the region the bend reaches
     on the far side is dimmer than the horizon but still continuous with it —
     a gradient, never a step. */
  const underside = new THREE.Mesh(
    new THREE.CylinderGeometry(1500, 1500, 700, 64, 1, true),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(0x6a6053).multiplyScalar(0.5),
      side: THREE.BackSide,
    }),
  );
  underside.position.y = -560;
  env.add(underside);

  /* Floor: dark, so reflections in the record's underside stay grounded — but
     NOT black. Anisotropic shading bends the record's reflection vector toward
     the horizon, and anything that dips below it lands here; against a true
     black floor that shows up as a dead patch on the disc. A very dark warm
     grey keeps it grounded while giving those rays something to return. */
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(4000, 4000),
    new THREE.MeshBasicMaterial({ color: 0x232019 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -400;
  env.add(floor);

  const pmrem = new THREE.PMREMGenerator(renderer);
  pmrem.compileEquirectangularShader();
  const target = pmrem.fromScene(env, 0.04);
  pmrem.dispose();

  env.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  });

  return target.texture;
}

/* ── Finish pass ──────────────────────────────────────────────────────────
   Vignette, grain, and chromatic aberration in a single fullscreen pass.
   Three separate passes would mean three fullscreen blits for three effects
   that each cost about four instructions. Runs after OutputPass, i.e. in
   display space, which is where lens and sensor artefacts belong.           */
const FinishShader = {
  uniforms: {
    tDiffuse:   { value: null },
    uTime:      { value: 0 },
    uGrain:     { value: 0.045 },
    uVignette:  { value: 0.62 },
    uAberration:{ value: 0.0016 },
  },
  vertexShader: /* glsl */`
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */`
    uniform sampler2D tDiffuse;
    uniform float uTime;
    uniform float uGrain;
    uniform float uVignette;
    uniform float uAberration;
    varying vec2 vUv;

    // Hash-based value noise. Cheaper than a texture lookup and it never tiles.
    float hash(vec2 p) {
      p = fract(p * vec2(443.897, 441.423));
      p += dot(p, p.yx + 19.19);
      return fract((p.x + p.y) * p.x);
    }

    void main() {
      vec2 uv = vUv;
      vec2 centred = uv - 0.5;
      float r2 = dot(centred, centred);

      // Transverse chromatic aberration: zero at the optical axis, growing
      // with the square of image height, which is how a real lens behaves.
      // A constant offset across the frame reads as a broken video codec.
      vec2 offset = centred * r2 * uAberration * 8.0;
      vec3 col;
      col.r = texture2D(tDiffuse, uv + offset).r;
      col.g = texture2D(tDiffuse, uv).g;
      col.b = texture2D(tDiffuse, uv - offset).b;

      // Vignette. Smooth and shallow — deep vignettes look like an Instagram
      // filter rather than a lens.
      float vig = smoothstep(0.95, 0.18, r2 * uVignette * 2.6);
      col *= mix(0.72, 1.0, vig);

      // Sensor grain, animated. Scaled by luminance so it lives in the
      // midtones and shadows and does not speckle the highlights.
      float n = hash(uv * 900.0 + fract(uTime) * 137.0) - 0.5;
      float lum = dot(col, vec3(0.299, 0.587, 0.114));
      col += n * uGrain * (1.0 - smoothstep(0.55, 1.0, lum));

      gl_FragColor = vec4(col, 1.0);
    }
  `,
};

export class Stage {
  constructor(canvas) {
    this.canvas = canvas;

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,          // the composer's multisampled target handles this
      powerPreference: "high-performance",
      stencil: false,
    });
    /* Capped at 2. Beyond that the cost is quadratic and the benefit is nil on
       a desktop display, and this view may be left running for hours. */
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    /* See the header: this line is doing more for the look than anything else
       in the file. Blender 4.x's default view transform, in a browser. */
    this.renderer.toneMapping = THREE.AgXToneMapping;
    this.renderer.toneMappingExposure = 0.95;
    this.renderer.shadowMap.enabled = true;
    /* PCFSoft is deprecated in current three.js and silently falls back to PCF,
       so ask for PCF directly rather than logging a warning every frame. The
       softness the scene needs comes from the shadow radius and normalBias
       below, which are tuned for millimetre units. */
    this.renderer.shadowMap.type = THREE.PCFShadowMap;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x17150f);   // --desk-2
    /* A little atmospheric falloff so the deck sits in space rather than
       floating on a flat backdrop. Distances are in mm. */
    this.scene.fog = new THREE.Fog(0x17150f, 900, 2600);

    this.environment = makeStudioEnvironment(this.renderer);
    this.scene.environment = this.environment;
    this.scene.environmentIntensity = 1.0;

    this.camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 10, 4000);
    this.camera.position.copy(POSES.hero.pos);
    this.camera.lookAt(POSES.hero.target);

    this.buildLights();
    this.buildComposer();
    this.buildBackdrop();

    /* Camera rig state. `base` and `blend` let the choreography cross-fade
       between two named poses without the harness and the animation fighting
       over camera ownership. */
    this.poseA = "hero";
    this.poseB = "swap";
    this.blend = 0;
    this.freeLook = { active: false, yaw: 0, pitch: 0, dolly: 0 };
    this.drift = 0;
    this.currentTarget = POSES.hero.target.clone();

    addEventListener("resize", () => this.resize());
  }

  buildLights() {
    /* Key. Warm, from upper-left-front. This is the light that casts the
       record's shadow onto the plinth and draws the specular sliver down the
       tonearm, so it is the only one that gets a shadow map. */
    /* Carries most of the disc's highlight now that the area light is gone. A
   directional source's specular is a broad soft lobe with no edges — which
   is the whole reason it can do this job and a rectangle could not. */
    this.key = new THREE.DirectionalLight(0xfff1dc, 3.0);
    this.key.position.set(-520, 780, 430);
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(2048, 2048);
    /* Tight ortho frustum around the deck. A default frustum over a
       430 mm object wastes almost all of the shadow map's resolution and is
       the usual reason web shadows look chunky. */
    const s = 340;
    this.key.shadow.camera.left = -s;
    this.key.shadow.camera.right = s;
    this.key.shadow.camera.top = s;
    this.key.shadow.camera.bottom = -s;
    this.key.shadow.camera.near = 200;
    this.key.shadow.camera.far = 1800;
    this.key.shadow.bias = -0.0006;
    this.key.shadow.normalBias = 1.2;      // mm — scene scale, not the usual 0.02
    this.key.shadow.radius = 2.2;
    this.scene.add(this.key);
    this.scene.add(this.key.target);

    /* Amber kicker from behind-right, grazing the record. A low, raking light
       is what makes the anisotropic groove sheen visible at all — a highlight
       needs a light near the horizon to stretch. */
    this.kick = new THREE.DirectionalLight(0xf3b152, 2.6);
    this.kick.position.set(690, 165, -640);
    this.scene.add(this.kick);

    /* Cool fill from the left, very low, purely to keep shadow detail alive. */
    this.fill = new THREE.DirectionalLight(0xbcd0d8, 0.32);
    this.fill.position.set(-680, 210, -420);
    this.scene.add(this.fill);

    /* A soft overhead practical, tinted cream, that also lights the label. */
    this.top = new THREE.SpotLight(0xece8db, 165000, 1600, Math.PI / 7, 0.75, 2);
    this.top.position.set(-60, 900, 190);
    this.scene.add(this.top);
    this.scene.add(this.top.target);

    /* THERE IS NO RECT AREA LIGHT, AND THAT IS THE POINT.

       There was one here, sitting where the environment's sheen band sits, on
       the theory that an analytically-evaluated area source keeps a crisp
       edge the prefiltered environment map cannot. It does — and that is
       exactly what was wrong with it.

       A record is a near-mirror. A mirror shows you the SHAPE of a light, so a
       620x150 rectangle reflected in the disc is a hard-edged bright patch
       covering part of it, and everything outside that patch falls away to
       near black. Read from the dark side, that is a black wedge with a
       straight edge lying across the record — which is precisely what it
       looked like, and it survived every material change thrown at it because
       it was never a material problem.

       Ablation made it unambiguous: removing this light dropped the lit part
       of the disc from 34 to 16 while the dark part barely moved from 17 to
       10. Nothing was darkening the wedge; the rest of the disc was being
       brightened around it.

       All disc lighting now comes from the environment, which is PMREM-
       prefiltered and therefore has no hard edges anywhere — the highlight
       becomes a gradient that falls off smoothly instead of stopping. */
  }

  /* A large, softly-lit ground plane so the deck casts onto something and the
     frame has a floor rather than fading into void. */
  buildBackdrop() {
    const mat = new THREE.MeshPhysicalMaterial({
      color: 0x1a1712,
      roughness: 0.82,
      metalness: 0.0,
      envMapIntensity: 0.5,
    });
    const floor = new THREE.Mesh(new THREE.CircleGeometry(2200, 96), mat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -110;
    floor.receiveShadow = true;
    this.scene.add(floor);
  }

  buildComposer() {
    const size = new THREE.Vector2();
    this.renderer.getSize(size);
    const dpr = this.renderer.getPixelRatio();

    /* Multisampled render target. With a composer in the chain the renderer's
       own `antialias` flag does nothing, and this scene is full of long thin
       chrome edges — the tonearm, the spindle, the record's rim — which are
       exactly what aliases worst. */
    const rt = new THREE.WebGLRenderTarget(size.x * dpr, size.y * dpr, {
      type: THREE.HalfFloatType,
      samples: 4,
    });

    this.composer = new EffectComposer(this.renderer, rt);
    this.composer.setPixelRatio(dpr);
    this.composer.setSize(size.x, size.y);

    this.renderPass = new RenderPass(this.scene, this.camera);
    this.composer.addPass(this.renderPass);

    /* Depth of field. Kept very shallow: enough that the far corner of the
       plinth softens, not so much that the record goes mushy. Focus distance
       is updated per-frame to track whatever the camera is looking at. */
    this.bokeh = new BokehPass(this.scene, this.camera, {
      focus: 640,
      aperture: 0.000018,
      maxblur: 0.006,
    });
    this.composer.addPass(this.bokeh);

    /* Bloom. It runs BEFORE OutputPass, i.e. on linear HDR values, so the
       threshold is in scene-referred units and not in the 0–1 of the final
       image. 0.86 sounds high and is not: the cream top plate sits well above
       1.0 in linear, so it bloomed as hard as the LEDs and haloed the whole
       deck. Only the LEDs, the strobe dots and the hottest chrome specular
       should get through. */
    this.bloom = new UnrealBloomPass(new THREE.Vector2(size.x, size.y), 0.15, 0.5, 2.4);
    this.composer.addPass(this.bloom);

    /* Tone mapping + colour space conversion. Everything above this point is
       working in linear HDR; everything below is in display space. */
    this.composer.addPass(new OutputPass());

    this.finish = new ShaderPass(FinishShader);
    this.composer.addPass(this.finish);

    this.postEnabled = true;
  }

  setPostEnabled(on) {
    this.postEnabled = on;
    this.bokeh.enabled = on;
    this.bloom.enabled = on;
    this.finish.enabled = on;
  }

  setGrain(on)  { this.finish.uniforms.uGrain.value = on ? 0.045 : 0.0; }

  /* ── Camera ───────────────────────────────────────────────────────────
     The choreography owns `blend`; the harness owns which two poses are being
     blended. Free-look overrides both by orbiting whatever pose is active. */
  setPose(name) {
    if (!POSES[name]) return;
    this.poseA = name;
    this.freeLook.active = false;
  }

  setFreeLook(on) { this.freeLook.active = on; }

  orbit(dx, dy) {
    this.freeLook.yaw -= dx * 0.0045;
    this.freeLook.pitch = THREE.MathUtils.clamp(
      this.freeLook.pitch - dy * 0.0035, -0.32, 1.36,
    );
  }

  zoom(delta) {
    this.freeLook.dolly = THREE.MathUtils.clamp(this.freeLook.dolly + delta * 0.0009, -0.45, 0.9);
  }

  updateCamera(dt, blend) {
    this.blend = blend;
    this.drift += dt;

    const a = POSES[this.poseA];
    const b = POSES[this.poseB];
    const k = Ease.easeInOutCubic(THREE.MathUtils.clamp(blend, 0, 1));

    const pos = a.pos.clone().lerp(b.pos, k);
    const target = a.target.clone().lerp(b.target, k);
    const fov = lerp(a.fov, b.fov, k);

    /* Idle drift. The anisotropic sheen on the record is rotationally
       symmetric, so spinning the disc does NOT move the highlight — real vinyl
       behaves the same way. Without a slowly moving camera the sheen is frozen
       and the whole frame looks like a still. This is a small motion doing a
       disproportionate amount of work. */
    const bob = Math.sin(this.drift * 0.21) * 7.5;
    const sway = Math.cos(this.drift * 0.147) * 11.0;
    pos.x += sway;
    pos.y += bob;
    pos.z += Math.sin(this.drift * 0.11) * 6.0;

    if (this.freeLook.active) {
      const radius = pos.length() * (1 - this.freeLook.dolly);
      const baseYaw = Math.atan2(pos.x, pos.z);
      const basePitch = Math.asin(THREE.MathUtils.clamp(pos.y / pos.length(), -1, 1));
      const yaw = baseYaw + this.freeLook.yaw;
      const pitch = THREE.MathUtils.clamp(basePitch + this.freeLook.pitch, 0.04, 1.42);
      pos.set(
        Math.sin(yaw) * Math.cos(pitch) * radius,
        Math.sin(pitch) * radius,
        Math.cos(yaw) * Math.cos(pitch) * radius,
      );
    }

    this.camera.position.copy(pos);
    this.currentTarget.lerp(target, 1 - Math.exp(-6 * dt));
    /* Interpolate the up vector too, so a blend into or out of the near-vertical
       plan pose does not snap the camera's roll partway through. */
    UP_A.copy(a.up || WORLD_UP);
    this.camera.up.copy(UP_A).lerp(b.up || WORLD_UP, k).normalize();
    this.camera.lookAt(this.currentTarget);
    if (Math.abs(this.camera.fov - fov) > 0.01) {
      this.camera.fov = fov;
      this.camera.updateProjectionMatrix();
    }

    /* Keep the plane of focus on whatever the camera is aimed at, so the
       record stays sharp through every pose change. */
    if (this.bokeh.enabled) {
      this.bokeh.uniforms.focus.value = this.camera.position.distanceTo(this.currentTarget);
    }

    this.key.target.position.copy(this.currentTarget);
    this.top.target.position.set(0, 0, 0);
  }

  render(dt) {
    this.finish.uniforms.uTime.value += dt;
    if (this.postEnabled) this.composer.render(dt);
    else this.renderer.render(this.scene, this.camera);
  }

  resize() {
    const w = window.innerWidth, h = window.innerHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(w, h);
  }
}
