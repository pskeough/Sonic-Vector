/* ════════════════════════════════════════════════════════════════════════
   tonearm.js — an S-shaped gimballed arm with real kinematics.

   WHY THE MATH MATTERS
   --------------------
   The tonearm is the one part of this scene a viewer can check against memory
   without knowing they are checking. Everyone has watched an arm crawl inward
   across a record. If it pivots around the wrong point, sweeps the wrong arc,
   or tracks at a constant rate, it looks wrong immediately and unaccountably.

   GEOMETRY (a conventional 9" arm)
   --------------------------------
     effective length   L = 230 mm   (pivot → stylus)
     overhang               15 mm     (stylus sits past the spindle)
     pivot → spindle    P = L − overhang = 215 mm
     offset angle           23°       (headshell rotation, for tangency)

   For a stylus sitting at radius r from the spindle, the angle θ at the pivot
   between the pivot→spindle line and the pivot→stylus line is the law of
   cosines on triangle (pivot, spindle, stylus):

       r² = P² + L² − 2PL·cos θ
       θ  = acos( (P² + L² − r²) / (2PL) )

   Over a real playing band that is θ ≈ 38.1° at the 146 mm lead-in down to
   θ ≈ 14.2° at the 57 mm run-out — a total sweep of just under 24°. Small.
   Arms that swing 60° across a record are a very common CG mistake.

   TRACKING RATE
   -------------
   Groove pitch is constant, so the stylus moves inward at a constant RADIAL
   rate, which means θ does NOT change linearly with time. Interpolating the
   angle directly instead of the radius makes the arm visibly loiter at the
   outside and hurry at the middle. Always map progress → radius → θ.
   ════════════════════════════════════════════════════════════════════════ */

import * as THREE from "three";
import { LP } from "./vinylMaps.js";
import { DECK } from "./deck.js";
import { RECORD_PLAY_SURFACE_Y } from "./record.js";

export const ARM = {
  effectiveLength: 230,
  overhang: 15,
  offsetAngleDeg: 23,
  get pivotToSpindle() { return this.effectiveLength - this.overhang; },
  /* Pivot sits rear-right of the platter, at exactly pivotToSpindle from the
     spindle. The x/z split is a styling choice; the magnitude is not. */
  pivotDir: { x: 0.6977, z: -0.7163 },
  /* How high the cueing device lifts the stylus. Real lifters raise 5–8 mm. */
  cueLiftMm: 8,
  /* Radii the stylus actually visits. Lead-in is where the arm is cued down,
     run-out is where it lifts off. */
  leadInRadius: LP.bandOuter,
  runOutRadius: LP.deadwaxOuter,
  /* Parked over the rest, outside the record entirely. */
  restRadius: 170,
  /* How far the stylus tip hangs below the gimbal's axis. OVERWRITTEN at
     construction by measuring the assembled headshell — see the note in the
     constructor. The value here is only a plausible starting figure so that
     anything reading it before a Tonearm exists gets a sane number. */
  stylusDropFromGimbal: 22.2,
};

/* Law of cosines: stylus radius from the spindle → arm rotation at the pivot. */
export function thetaForRadius(r) {
  const P = ARM.pivotToSpindle;
  const L = ARM.effectiveLength;
  const c = (P * P + L * L - r * r) / (2 * P * L);
  return Math.acos(THREE.MathUtils.clamp(c, -1, 1));
}

/* Progress through a track (0→1) → stylus radius. Linear in RADIUS, because
   groove pitch is constant — see the note above. */
export function radiusForProgress(t) {
  const f = THREE.MathUtils.clamp(t, 0, 1);
  return ARM.leadInRadius + (ARM.runOutRadius - ARM.leadInRadius) * f;
}

export class Tonearm {
  constructor() {
    this.group = new THREE.Group();

    const pivot = new THREE.Vector3(
      ARM.pivotDir.x * ARM.pivotToSpindle,
      0,
      ARM.pivotDir.z * ARM.pivotToSpindle,
    );
    this.pivotPosition = pivot.clone();

    /* World heading from pivot to spindle, in three.js's rotation.y convention
       (a point at local +X rotated by a lands at (cos a, 0, −sin a)). */
    const dx = -pivot.x / ARM.pivotToSpindle;
    const dz = -pivot.z / ARM.pivotToSpindle;
    this.baseYaw = Math.atan2(-dz, dx);

    /* Slightly blue-shifted rather than neutral. A pure-white metal tint in a
       warm environment comes back looking like brass, which is what the first
       render produced; biasing the albedo cool lets it read as chrome under an
       amber kicker without desaturating the rig. */
    const chrome = new THREE.MeshPhysicalMaterial({
      color: 0xd8dce0, roughness: 0.22, metalness: 0.95, envMapIntensity: 0.95,
    });
    const satin = new THREE.MeshPhysicalMaterial({
      color: 0x8e9195, roughness: 0.45, metalness: 0.8, envMapIntensity: 0.85,
    });
    const black = new THREE.MeshPhysicalMaterial({
      color: 0x17150f, roughness: 0.42, metalness: 0.25, envMapIntensity: 0.7,
    });
    const clay = new THREE.MeshPhysicalMaterial({
      color: 0xd97757, roughness: 0.36, metalness: 0.1, // --clay, on the cue lever
      clearcoat: 0.25, clearcoatRoughness: 0.45,
    });
    this.materials = { chrome, satin, black, clay };

    /* ── Yaw group: rotates about vertical, sweeping the arm across ────
       Its height is not known yet: it depends on where the stylus tip ends up
       once the headshell is assembled, which is measured below rather than
       guessed at. */
    this.yaw = new THREE.Group();
    this.yaw.position.copy(pivot);
    this.group.add(this.yaw);

    /* ── Lift group: rotates about local +Z, raising the stylus ───────── */
    this.lift = new THREE.Group();
    this.yaw.add(this.lift);

    /* ── Arm tube ────────────────────────────────────────────────────── */
    /* An S-bend. On a real arm the S exists to give the headshell its offset
       angle without a bent headshell; here it also happens to be the silhouette
       everyone recognises as "hi-fi turntable" rather than "record player". */
    const L = ARM.effectiveLength;
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(14, 0, 0),
      new THREE.Vector3(L * 0.26, 0.4, -7.5),
      new THREE.Vector3(L * 0.52, 0.6, -2.0),
      new THREE.Vector3(L * 0.74, 0.3, 8.5),
      new THREE.Vector3(L * 0.885, 0, 12.0),
    ]);
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 96, 4.2, 20, false), chrome);
    tube.castShadow = true;
    this.lift.add(tube);

    /* Counterweight, behind the pivot. Its mass is the reason the arm balances,
       and visually it is the thing that makes the arm look like it has weight
       rather than being a wire. */
    const cwStub = new THREE.Mesh(new THREE.CylinderGeometry(3.2, 3.2, 44, 20), satin);
    cwStub.rotation.z = Math.PI / 2;
    cwStub.position.x = -22;
    this.lift.add(cwStub);

    const cw = new THREE.Mesh(new THREE.CylinderGeometry(17, 17, 26, 40), black);
    cw.rotation.z = Math.PI / 2;
    cw.position.x = -44;
    cw.castShadow = true;
    this.lift.add(cw);

    /* Tracking-force dial: a thin chrome ring on the counterweight. */
    const dial = new THREE.Mesh(new THREE.TorusGeometry(17.3, 1.1, 8, 36), chrome);
    dial.rotation.y = Math.PI / 2;
    dial.position.x = -34;
    this.lift.add(dial);

    /* ── Headshell ───────────────────────────────────────────────────── */
    const head = new THREE.Group();
    const end = curve.getPoint(1);
    head.position.copy(end);
    /* The offset angle. Rotating the headshell — not the whole arm — is what
       makes the cartridge sit closer to tangent with the groove. */
    head.rotation.y = THREE.MathUtils.degToRad(ARM.offsetAngleDeg);
    this.lift.add(head);

    /* Bayonet collar — the twist-lock every removable headshell uses. */
    const collar = new THREE.Mesh(new THREE.CylinderGeometry(5.2, 5.6, 8, 28), satin);
    collar.rotation.z = Math.PI / 2;
    collar.position.x = 1;
    head.add(collar);

    /* The headshell proper.

       The first pass built this as three axis-aligned boxes and it read as
       three separate objects floating near each other rather than as one
       machined part. Two things fix that: everything sits inside a single
       group that is TILTED nose-down, the way a real headshell is so the
       cartridge meets the record square; and the parts overlap rather than
       merely abut, so there are no seams for the eye to read as gaps. */
    const shellGroup = new THREE.Group();
    shellGroup.position.set(6, -1.5, 0);
    shellGroup.rotation.z = -0.13;
    head.add(shellGroup);

    /* THE HEADSHELL AND CARTRIDGE

       This assembly is under the macro camera and gets looked at closely, and
       the first two attempts both failed the same way: a stack of hard-edged
       axis-aligned boxes reads as a pile of blocks, not as a precision part.
       What fixes it is not more geometry, it is the right details —

         · every visible edge is chamfered, because nothing machined has a
           sharp corner and the thin bright line along a chamfer is most of
           what says "metal part" at this distance;
         · the cartridge is a WEDGE that tapers down toward the nose, which is
           the silhouette every moving-magnet cartridge actually has;
         · the parts overlap and share edges rather than merely touching;
         · two mounting screws, because they are always there and their absence
           is felt even when nobody could tell you what is missing.            */

    /* Headshell plate: tapered, with a rolled front lip. */
    const shellShape = new THREE.Shape();
    shellShape.moveTo(0, -6.8);
    shellShape.lineTo(20, -5.0);
    shellShape.lineTo(24.5, -3.4);
    shellShape.lineTo(25.6, -1.6);
    shellShape.lineTo(25.6, 1.6);
    shellShape.lineTo(24.5, 3.4);
    shellShape.lineTo(20, 5.0);
    shellShape.lineTo(0, 6.8);
    shellShape.closePath();

    const plateGeom = new THREE.ExtrudeGeometry(shellShape, {
      depth: 2.2, bevelEnabled: true,
      bevelSize: 0.5, bevelThickness: 0.45, bevelSegments: 3, curveSegments: 3,
    });
    /* Extrude runs along +Z; stand the plate up so its thickness is vertical. */
    plateGeom.rotateX(-Math.PI / 2);

    const plate = new THREE.Mesh(plateGeom, satin);
    plate.position.set(0, -2.2, 0);
    plate.castShadow = true;
    shellGroup.add(plate);

    /* Finger lift: a thin blade swept up and back off the shell's edge. */
    const liftShape = new THREE.Shape();
    liftShape.moveTo(0, 0);
    liftShape.lineTo(7.5, 0);
    liftShape.lineTo(9.0, 5.4);
    liftShape.lineTo(1.4, 6.2);
    liftShape.closePath();
    const liftGeom = new THREE.ExtrudeGeometry(liftShape, {
      depth: 1.1, bevelEnabled: true, bevelSize: 0.22, bevelThickness: 0.2, bevelSegments: 2,
    });
    const finger = new THREE.Mesh(liftGeom, satin);
    finger.position.set(17.5, -1.4, 4.6);
    finger.rotation.set(-0.35, 0, -0.12);
    shellGroup.add(finger);

    /* Cartridge body — a wedge, tapering down toward the nose. Built as a
       lathe-free extrusion in profile so the taper is on the silhouette. */
    const bodyProfile = new THREE.Shape();
    bodyProfile.moveTo(0, 0);            // rear top, up against the shell
    bodyProfile.lineTo(16.5, 0);         // front top
    bodyProfile.lineTo(17.4, -2.2);      // nose chamfer
    bodyProfile.lineTo(15.6, -8.4);      // nose bottom — the taper
    bodyProfile.lineTo(2.0, -9.6);       // rear bottom
    bodyProfile.lineTo(0, -7.4);
    bodyProfile.closePath();

    const bodyGeom = new THREE.ExtrudeGeometry(bodyProfile, {
      depth: 10.4, bevelEnabled: true,
      bevelSize: 0.5, bevelThickness: 0.4, bevelSegments: 3, curveSegments: 3,
    });
    bodyGeom.translate(0, 0, -5.2);      // centre it across the shell

    /* Muted rather than the console's full-strength terracotta. At this size a
       saturated block of --clay reads as bright plastic; knocking the chroma
       back and dropping the clearcoat lets it read as a moulded body that
       happens to be the product's accent colour. */
    const cartMat = new THREE.MeshPhysicalMaterial({
      color: 0xa8543a,
      roughness: 0.48,
      metalness: 0.05,
      clearcoat: 0.22,
      clearcoatRoughness: 0.42,
    });
    this.materials.cartridge = cartMat;

    const cart = new THREE.Mesh(bodyGeom, cartMat);
    cart.position.set(6.0, -3.4, 0);
    cart.castShadow = true;
    shellGroup.add(cart);

    /* Dark front face, set slightly proud, with the cantilever emerging from
       it. Almost every cartridge presents a dark nose and it is what stops the
       body reading as one solid coloured lump. */
    const nose = new THREE.Mesh(new THREE.BoxGeometry(2.0, 6.4, 9.0), black);
    nose.position.set(22.3, -8.6, 0);
    nose.rotation.z = -0.08;
    shellGroup.add(nose);

    /* Two mounting screws through the shell into the body. */
    for (const z of [-3.6, 3.6]) {
      const screw = new THREE.Mesh(new THREE.CylinderGeometry(0.85, 0.85, 1.1, 12), chrome);
      screw.position.set(10.5, -0.6, z);
      shellGroup.add(screw);
    }

    /* ── The needle ───────────────────────────────────────────────────
       Cantilever, tip and the contact marker as ONE assembly built along a
       single local axis.

       The previous version placed a rotated cylinder and a cone at two
       separately-chosen world positions and rotations, and matching the end of
       a rotated cylinder to the base of a cone by eye does not work: it left a
       visible gap, so the tip read as floating free of the arm. Here the
       cantilever hangs straight down the group's own −Y from the origin, the
       tip is placed at exactly its far end, and the rake is applied once to the
       whole group. The joint cannot drift because nothing computes it twice.

       The radii match at the joint as well — the cantilever's bottom radius IS
       the cone's base radius — so there is no step where they meet either. */
    const needle = new THREE.Group();
    const CANT_LENGTH = 7.8;
    const JOINT_RADIUS = 0.17;
    const TIP_LENGTH = 0.9;

    const cantilever = new THREE.Mesh(
      new THREE.CylinderGeometry(0.44, JOINT_RADIUS, CANT_LENGTH, 14),
      chrome,
    );
    cantilever.position.y = -CANT_LENGTH / 2;
    needle.add(cantilever);

    /* The stylus. About a third of a millimetre of diamond in reality — far
       too small to model honestly, so it is drawn just large enough to catch a
       specular glint at the contact point, which is where the eye goes during
       a needle drop. Seated flush on the cantilever's end. */
    const stylus = new THREE.Mesh(
      new THREE.ConeGeometry(JOINT_RADIUS, TIP_LENGTH, 12),
      chrome,
    );
    stylus.position.y = -CANT_LENGTH - TIP_LENGTH / 2;
    stylus.rotation.z = Math.PI;         // apex downward
    needle.add(stylus);

    /* Contact point: the cone's apex, and therefore always exactly where the
       needle actually touches, whatever the assembly above is changed to. */
    this.stylusTip = new THREE.Object3D();
    this.stylusTip.position.y = -CANT_LENGTH - TIP_LENGTH;
    needle.add(this.stylusTip);

    /* Emerge from inside the nose, raked forward, so the cantilever's top is
       buried in the cartridge rather than butting against it. */
    needle.position.set(21.2, -7.2, 0);
    needle.rotation.z = 0.40;
    shellGroup.add(needle);

    /* (The contact marker is created with the needle assembly above, at the
       cone's apex, so there is nothing to place here.) */

    /* ── Tower height, MEASURED ───────────────────────────────────────
       The stylus tip is now four nested transforms deep — head offset, offset
       angle, shellGroup offset, shellGroup tilt — so how far it hangs below
       the gimbal is a consequence of the headshell design, not a number anyone
       can sensibly hand-maintain. The first pass hardcoded 18.6 mm and the
       rebuilt headshell moved the real figure to about 22, which put the needle
       visibly floating above the record.

       So ask the scene graph. The transform from the lift group down to the tip
       is well-defined before anything is added to a scene, and taking it
       relative to the lift group cancels out every ancestor — including the
       tower height that is about to be derived from it. */
    this.lift.updateMatrixWorld(true);
    this.stylusTip.updateMatrixWorld(true);
    const tipInLift = new THREE.Vector3().setFromMatrixPosition(
      new THREE.Matrix4()
        .copy(this.lift.matrixWorld).invert()
        .multiply(this.stylusTip.matrixWorld),
    );
    ARM.stylusDropFromGimbal = -tipInLift.y;

    /* The assembly is parented at the top plate, which sits below the mat plane
       by the platter and the mat. The tower must raise the gimbal so a level
       arm puts the stylus on the record's GROOVED SURFACE — the mat plane plus
       the pressing's own thickness, not the mat plane itself. */
    const towerHeight =
      RECORD_PLAY_SURFACE_Y + ARM.stylusDropFromGimbal - DECK.topPlateWorldY;
    this.yaw.position.setY(towerHeight);

    /* ── Pivot tower ─────────────────────────────────────────────────── */
    const tower = new THREE.Group();
    tower.position.copy(pivot);

    const base = new THREE.Mesh(new THREE.CylinderGeometry(21, 24, 9, 40), satin);
    base.position.y = 4.5;
    base.castShadow = true;
    tower.add(base);

    const post = new THREE.Mesh(new THREE.CylinderGeometry(9.5, 11, towerHeight - 9, 32), chrome);
    post.position.y = 9 + (towerHeight - 9) / 2;
    post.castShadow = true;
    tower.add(post);

    /* Gimbal yoke: the visible cue that this arm can move in two axes. */
    const yoke = new THREE.Mesh(new THREE.TorusGeometry(11, 2.4, 12, 32, Math.PI), chrome);
    yoke.position.y = towerHeight;
    yoke.rotation.y = Math.PI / 2;
    tower.add(yoke);

    this.group.add(tower);

    /* ── Arm rest ────────────────────────────────────────────────────── */
    const rest = new THREE.Group();
    const restAngle = this.baseYaw + thetaForRadius(ARM.restRadius);
    rest.position.set(
      pivot.x + Math.cos(restAngle) * (L - 26),
      0,
      pivot.z - Math.sin(restAngle) * (L - 26),
    );
    const restPost = new THREE.Mesh(new THREE.CylinderGeometry(5, 6.5, 30, 24), satin);
    restPost.position.y = 15;
    restPost.castShadow = true;
    rest.add(restPost);
    const cradle = new THREE.Mesh(new THREE.TorusGeometry(7, 2.6, 10, 24, Math.PI), black);
    cradle.position.y = 31;
    cradle.rotation.set(Math.PI / 2, 0, restAngle);
    rest.add(cradle);
    this.group.add(rest);

    /* ── Cue lever ───────────────────────────────────────────────────── */
    const cueBase = new THREE.Mesh(new THREE.BoxGeometry(16, 7, 22), satin);
    cueBase.position.set(pivot.x - 44, 3.5, pivot.z + 20);
    this.group.add(cueBase);

    this.cueLever = new THREE.Mesh(new THREE.BoxGeometry(7, 5, 26), clay);
    this.cueLever.position.set(pivot.x - 44, 9, pivot.z + 26);
    this.group.add(this.cueLever);

    /* Sit the assembly on the deck's top plate. */
    this.group.position.y = DECK.topPlateWorldY;

    this.currentRadius = ARM.restRadius;
    this.lifted = 1;   // 0 = down on the record, 1 = fully raised
    this.applyPose();
  }

  /* Set the stylus radius (mm from the spindle) and the lift amount (0..1). */
  setPose(radius, lifted) {
    this.currentRadius = radius;
    this.lifted = lifted;
    this.applyPose();
  }

  applyPose() {
    this.yaw.rotation.y = this.baseYaw + thetaForRadius(this.currentRadius);
    /* Rotating about +Z takes local +X toward +Y, so a positive angle raises
       the far end of the arm. asin because the lift is specified as a height at
       the stylus, not as an angle. */
    this.lift.rotation.z = Math.asin(
      (ARM.cueLiftMm * this.lifted) / ARM.effectiveLength,
    );
    /* The cue lever throws with the lift, because on a real deck it is the
       lever that causes it. */
    this.cueLever.rotation.x = -0.42 * this.lifted;
  }

  /* World-space position of the stylus tip, for the contact shadow and for the
     dust motes that get kicked up on a needle drop. */
  worldStylus(target) {
    this.stylusTip.getWorldPosition(target);
    return target;
  }

  setEnvironment(env) {
    for (const m of Object.values(this.materials)) m.envMap = env;
  }
}
