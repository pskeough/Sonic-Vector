# SonicVectorEQ — Turntable View (visual prototype)

A second "now playing" view for SonicVectorEQ: a real-time 3D deck with the
current album spinning on it, and a full record-swap choreography when the
track changes.

**This folder is standalone.** It does not import, read, or write anything in
`SonicVectorEQ/`. The only contact with the real app is one entry added to
`SonicVectorEQ/.claude/launch.json` so the dev server can be started from the
IDE. The app's own `sonicvector` entry is untouched, and no app source file is
modified.

## Install and run

| | |
|---|---|
| **`install.bat`** | one-time setup: checks Python and Node, installs the app's Python deps, verifies Equalizer APO, puts a shortcut on the desktop |
| **`start_sonicvector.bat`** | starts both the EQ engine (:5001) and the turntable view (:5177), then opens the console |
| **`launch_turntable.bat`** | the turntable view on its own, in a Chrome/Edge app window |

The console also gains a **TURNTABLE ▸** button that opens the deck in its own
window. It probes :5177 first, so it never opens a window onto nothing.

### On Equalizer APO

`install.bat` detects it and opens the download page if it is missing, but
deliberately does **not** install it. That installer patches the audio driver
stack, requires a reboot, and asks which playback device to hook — decisions a
setup script has no business making silently on someone's machine. Without it
Sonic Vector still runs and still shows curves; it just will not change what
you hear.

### By hand

```bash
node "C:/AI Coding Projects/Apps/SonicVectorEQ-TurntableProto/tools/devserver.mjs" 5177
```

| Key | |
|---|---|
| `1` `2` `3` `4` | camera — hero, plan, macro, free orbit |
| `n` `p` | next / previous track (demo feed only) |
| `r` | reconnect to SonicVectorEQ |
| `h` | show/hide the prototype tools drawer |

## Live playback

If SonicVectorEQ is running on port 5001 the view uses it automatically; if
not, it falls back to a built-in demo feed and says so in the badge. Press `r`
after starting the app to go live without reloading.

Both paths work, and both are proxied through the dev server:

| | |
|---|---|
| **Spotify** | `album_art` is an absolute `https://i.scdn.co/…` URL |
| **Local playback** | Windows media session; `album_art` is a RELATIVE `/api/art/<hash>` path served by the app itself |
| **No artwork** | a generated house label carrying artist, title and album |

The proxy is not a convenience. Album art becomes a WebGL texture, and a
browser refuses to upload a cross-origin image it considers tainted — so it has
to arrive same-origin. It also means the app needs no CORS headers and
therefore no modification.

### The one thing the app does not send

`/api/status` carries no `progress_ms` or `duration_ms`. Spotify's live values
exist in `src/spotify/service.py` and are simply not forwarded; the SMTC reader
never captures a duration at all.

So the view runs its own playback clock, integrated every frame and nudged
toward the poll rather than snapped to it. That is what makes the tonearm's
inward crawl continuous instead of a 1.5-second staircase. But the DURATION is
a 4-minute assumption, which has two visible consequences:

- The elapsed time is shown without a total, because stating a guessed total as
  fact would be a lie.
- Speculative changeovers are disabled. Predicting the end of a track from a
  guessed length would lift the record off mid-song whenever a track outran the
  guess. Every change therefore takes the SKIP path.

**Forwarding those two fields from `/api/status` is a one-line, additive
change** and would unlock a tonearm sweep genuinely matched to song length plus
the slower ceremonial changeover. It is not done here because the brief was to
leave app code alone.

---

## Approach, and why

Three approaches were weighed: a real-time WebGL scene, pre-rendered Blender
frames played back as sprite sheets, and a zero-dependency CSS/SVG build. A
judged design panel and this prototype both landed on **real-time WebGL
(three.js r185, vendored)**, for one decisive reason: **the album art changes
every song.** Pre-rendered frames cannot carry a live, perspective-correct,
rotating label without reprojecting the art per frame anyway, and CSS cannot
reach the material fidelity the brief asked for.

What makes it read as a render rather than as a web page, in order of how much
each actually contributes:

1. **AgX tone mapping.** Blender 4.x's default view transform, available in
   three.js as `AgXToneMapping`. Highlights desaturate toward white instead of
   clipping to neon; the shadow toe is soft. Single highest-leverage line in the
   codebase.
2. **A built environment, not a light rig.** Reflective surfaces get their
   believability from what they reflect. A studio of emissive panels in the
   console's own palette is built at startup and prefiltered through
   `PMREMGenerator`, so the deck reflects cream and amber rather than generic
   grey. No HDRI download; works offline.
3. **Anisotropic vinyl.** See below.
4. **Lens artefacts.** Depth of field, restrained bloom, transverse chromatic
   aberration, vignette, sensor grain. Individually invisible, collectively the
   difference between "3D on a web page" and "photo of an object".

Everything is vendored (3.2 MB) rather than CDN-loaded, because
`static/style.css` records that the webfont CDN was deliberately removed so the
app needs no network before first paint. A 3D view that silently reintroduced
that dependency would undo the decision.

## The vinyl

The disc is the whole illusion. Three things carry it:

**Grooves are a material property, not geometry.** Real groove pitch is ~0.1 mm.
Drawing individual grooves into a normal map is possible at this texture
resolution and looks terrible — a shimmering moiré rosette that reads as a
rendering bug. Instead the microfacet distribution is stretched, and the normal
map is reserved for macro relief the eye genuinely resolves: track boundaries,
the lead-in spiral, the run-out groove, a shallow dish warp.

**The anisotropy direction is radial, not circumferential.** This is the easiest
thing to get backwards here. In three.js the anisotropy vector selects the axis
whose roughness is raised — `alphaT = mix(roughness², 1, anisotropy²)` — and a
raised roughness spreads the highlight *along* that axis. Brushed metal smears
its highlight perpendicular to the scratches; grooves run circumferentially, so
the highlight smears radially. Set it circumferential and you get a dark disc
with concentric rings. There is an A/B in `shots/10-` and `shots/11-`.

**The sheen does not rotate with the record.** A radially-symmetric anisotropy
field is rotation-invariant, so the highlight stays fixed in world space while
the disc turns — which is exactly what real vinyl does. The consequence is that
a static camera makes the frame look like a still, so the camera carries a slow
idle drift. Small motion, disproportionate effect.

The surface maps are 8 px × 2048 px, indexed by radius only, because every
visual property of a record varies with radius and nothing else. ~64 KB instead
of ~16 MB, with far more radial resolution than a square map could afford. Real
LP dimensions throughout, in millimetres — see `LP` in `scene/vinylMaps.js`.

## The tonearm

Nine-inch arm: 230 mm effective length, 15 mm overhang, 23° offset. Stylus
radius maps to arm rotation by the law of cosines, and **progress maps to radius,
not to angle** — groove pitch is constant, so interpolating the angle makes the
arm loiter at the outside and hurry through the middle. Total sweep across the
playing band is just under 24°; arms that swing 60° are the usual tell.

The pivot tower's height is *measured*, not hardcoded: the assembled headshell's
stylus tip is queried from the scene graph and the tower derived from it, so the
needle lands on the record's grooved surface (mat plane + pressing thickness)
rather than floating above it. Verified: stylus world Y = 1.750 mm, exactly the
grooved surface, at radius 140.1 mm inside the playing band.

## The changeover

The frontend learns about a track change by polling `/api/status` every 1.5 s,
so it finds out up to 1.5 s late with no warning. A 4-second animation started
at that moment never catches up.

But the payload also carries `progress_ms` / `duration_ms`, so the view can
**see the end coming**. Past 98.5 % it starts the changeover speculatively —
which is not a trick, it is what the machine being simulated does. A real
tonearm reaches the run-out and lifts because the record ran out, not because
something announced the next song. By the time the poll confirms, the arm is
already up and the art has had a second and a half to decode.

That yields two changeovers, legibly different:

| | trigger | length | character |
|---|---|---|---|
| **Natural** | run-out groove reached | 4.68 s | full spin-down, record lifts away, new one settles, spin-up, damped cue |
| **Skip** | unpredicted `track_id` change | 2.62 s | arm snaps up, platter dips but never stops, outgoing disc flicked away |

Both end on the same slow, viscously damped needle drop, because every cueing
device ever built is damped and that descent is the money shot.

The camera drops to a low, wide pose during the swap and returns afterward — a
disc leaving a spindle is far more legible in profile than from three-quarters
above.

## Bugs this prototype caught

Worth recording, because each was invisible in the source and only showed up
when values were logged frame by frame or a frame was actually looked at:

- `Ease.spinDown` simplified to `(1−t)^2.6`, which runs 1 → 0. Fed to
  `lerp(speed, 0, e)` the platter **accelerated during spin-down** and braked
  during spin-up.
- `Ease.settle` overshot past 1, so the arriving record **sank through the mat**
  on first contact. Rectifying the cosine bounds it at 1 and turns the overshoot
  into a physical rebound.
- Lift and drop were split across two channels combined with `max()`. The lift
  channel held at 1 for the rest of the timeline, so **the needle drop never
  played** — the arm flopped down afterwards on the steady-state controller's
  generic easing.
- `putImageData` does not composite. The film-grain pass **replaced every album
  cover with a sheet of flat grey**.
- `PointsMaterial` ignores a per-vertex `size` attribute, so all dust motes
  rendered identically sized — it looked like snow.
- `ExtrudeGeometry`'s cap UVs flip the tangent frame per triangle, so
  anisotropic shading drew the fan triangulation across the top plate as hard
  diagonal creases.
- Album art was loaded at the label-swap beat and had to complete inside a
  frame; when it did not, **the record arrived wearing a blank white label**. It
  is now requested the moment the track is known.

## Layout

```
index.html              stage + now-playing overlay + harness controls
src/
  main.js               assembly, frame loop, harness wiring
  choreography.js       state machine + the two swap timelines
  easing.js             easing curves + keyframe evaluator
  mockFeed.js           stands in for GET /api/status, faithful 1.5 s poll
  proceduralCover.js    album art fallback + test fixtures
  harness.css           chrome, tokens copied verbatim from the console
  captureRig.js         PROTOTYPE ONLY — offscreen stepping + frame capture
  scene/
    stage.js            renderer, environment, lights, camera rig, post chain
    vinylMaps.js        procedural 1-D surface maps + real LP dimensions
    record.js           lathed disc, analytic tangent frame, label
    deck.js             plinth, top plate, platter, strobe ring, spindle
    tonearm.js          gimbal, S-arm, headshell, kinematics
    dust.js             motes in the key light
tools/devserver.mjs     static server + frame-capture sink (prototype only)
vendor/three/           pinned r185, offline
shots/                  captured frames
```

Harness: `1`/`2`/`3`/`4` switch camera, `n`/`p` change track, `h` hides the rig.
The TIME SCALE slider runs the playback clock hot so the tonearm sweep can be
evaluated without waiting out a five-minute song.

## Integration path

Nothing here is wired into the app yet. When it is:

1. Delete `captureRig.js`, its import in `main.js`, and `tools/devserver.mjs`.
2. Replace `mockFeed.js` with the existing poll in `static/app.js`. The payload
   shape already matches; `choreo.notify(payload)` is the entire interface.
3. Expose `progress_ms` and `duration_ms` on `/api/status`. Both already exist
   in `src/spotify/service.py` and are simply not forwarded. Without them the
   view still works — the tonearm just parks instead of tracking, and every
   changeover takes the SKIP path because nothing can be predicted.
4. Trim `vendor/three/examples/jsm/` to the six addons actually imported.
5. Mount the canvas as a second view behind a toggle, with the existing console
   panel as the other. Pause rendering whenever it is not the active view.

## Revision 2 — changes from feedback

- **Boot failures are now visible.** Module evaluation is one straight line, and
  a throw anywhere in it left the loading veil sitting on whatever step it had
  reached, forever, with no error on screen. It now prints the exception.
- **Strobe ring de-lit.** The platter's amber dots pulsed with the platter rate,
  turning the rim into a ring of blinking lights that pulled the eye off the
  record. They are now what they are on a real deck: machined dimples that
  catch the light and do nothing else.
- **The rotating black wedge.** Took three attempts, and the first two were
  wrong in a way worth recording.

  Attempts 1 and 2 blamed the anisotropic BRDF — plausible, since three.js does
  bend the reflection normal toward the horizon by a varying amount, and
  capping anisotropy *did* shrink the wedge. But shrinking is not fixing, and
  calling the remainder "a soft gradient, acceptable" without measuring it was
  the actual mistake.

  What settled it was ablation with a numeric measure rather than screenshots:
  sample a ring of pixels across the grooved band, then disable one thing at a
  time. **Nothing** brightened the dark arc — not anisotropy, not shadows, not
  the normal map, not the environment. But removing the RectAreaLight dropped
  the *lit* part from 34 to 16 while the dark part barely moved, 17 to 10.

  So nothing was ever darkening the wedge. A record is a near-mirror, and a
  mirror shows you the *shape* of a light: a 620×150 rectangle reflected in the
  disc is a hard-edged bright patch, and everything outside it falls to near
  black. The "wedge" was the complement of that patch.

  Fixed by deleting the area light entirely. All disc lighting now comes from
  the environment and from directional sources, whose specular is a broad soft
  lobe with no edges anywhere. The ambient shell was also lifted well above its
  token value, because a mirror pointed at a black environment renders black.

  The lesson, cheaply bought: an ablation with a number attached found in one
  pass what two rounds of plausible-looking screenshots did not.
- **Changeover slowed** ~38 %, via a single `SWAP_SPEED` factor rather than
  thirty hand-edited key times.
- **Tonearm drift is now continuous.** See the playback clock above.
- **The spin reads.** The disc was rotationally symmetric and so was its sheen,
  so a spinning record looked completely static apart from the label. It now
  carries asymmetric wear — hairline scuffs, radial marks, dust — each of which
  glints once per revolution, plus the sub-millimetre dish warp every real
  pressing has.
- **Less shiny overall.** Exposure, bloom, every light, and the clearcoat on
  vinyl, wood, chrome and the cartridge all pulled back.
- **UI moved to a 36 px bottom bar** carrying only the camera and source
  choices. Everything else is prototype scaffolding and folds into a drawer
  that is closed by default, so nothing sits over the deck.
- **Cartridge rebuilt** as a chamfered tapered wedge with a proper nose,
  cantilever, finger lift and mounting screws, instead of stacked boxes.

## Revision 3

**The rotating black wedge — actual root cause, after three wrong ones.**

The clue that solved it was "it eclipses on and off", which I had not tested
for. Periodic means something periodic, and the only per-revolution thing in
the scene was the dish warp I had added to sell the rotation.

0.55 mm of warp over a 152 mm radius is a tilt of 0.2°, which sounds far too
small to matter. It is not: a mirror doubles angles, so that becomes 0.4° of
swing in the reflected direction. The environment is bright in one band and
dark elsewhere, so the swing walks the boundary between them across a large
part of the disc — and because a dish warp makes the surface normal vary
*linearly* with position, that boundary is a straight radial line. A hard-edged
dark wedge, sweeping round once per revolution, switching on and off.

Measured, camera frozen, disc stepped through a full turn:

| | mean brightness across the grooved band |
|---|---|
| warp on | 33 → 39, varying with angle |
| warp off | **33 at every angle** |

Warp disabled. The rotation cue is carried by the asymmetric surface marks
instead, which is the better mechanism anyway — a real record reads as spinning
because of its scuffs, not because you can watch it wobble. The residual ~7 %
shimmer per revolution is those marks, and is the intended effect.

The two earlier "fixes" were not wasted — the RectAreaLight really was
producing a hard-edged reflected rectangle, and the six-panel environment
really did have gaps for a bent normal to catch. Both are still gone. But
neither was what the user was actually looking at.

**Batch files are now pure ASCII, and that was a real bug too.**

`install.bat` failed outright on first run: fragments of lines executed as
commands, and an AMD64 machine reported as ARM64. Cause: cmd.exe decodes batch
files in the console's **OEM codepage**, not UTF-8. The box-drawing characters
I had used for section rules — inside comments — became multi-byte garbage that
split lines mid-token, breaking the caret continuations and the architecture
test. All three scripts are now ASCII-only with CRLF endings, long commands on
single lines, and are verified by running them under `chcp 437`.

**ARM64 support.** The installer reports the machine's true architecture (via
`PROCESSOR_ARCHITEW6432`, so a 32-bit shell under emulation cannot fool it) and
splits the dependency install: core packages, which are pure Python, from
`pywin32` and the `winrt-*` family, which are compiled wheels not published for
every Python/architecture pair. A failure in the second group is a warning, not
a dead install — the app still runs and still applies EQ, it just cannot see
audio playing outside Spotify. Browser search paths cover both Program Files
trees plus the ARM one. Equalizer APO's ARM64 availability is **not** asserted
either way, because I have not verified it.

## Known gaps

- Not yet verified running live at framerate: the host Browser pane is not
  compositing in this environment, so every frame here was rendered and read
  back through an offscreen capture rig at a fixed timestep. Frame cost on the
  target machine is unmeasured.
- No scratches or surface flaws. Because the sheen is fixed and the disc turns,
  a few non-radial marks in the roughness map would each glint once per
  revolution — a strong "alive" cue, and cheap. It needs a 2-D overlay map,
  which the current 1-D scheme does not have.
- The headshell is close but still the weakest part in macro.
- No `prefers-reduced-motion` path for the 3D view yet.
- The record's underside is untextured in all but silhouette.
