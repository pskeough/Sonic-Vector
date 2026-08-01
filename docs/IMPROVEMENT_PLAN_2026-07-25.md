# Sonic Vector: Decision Document

**Date:** 2026-07-25
**Scope:** synthesis of 4 code/research audits, 6 design vectors, and 4 adversarially verified claims.
**Sources:** `C:\AI Coding Projects\Apps\SonicVectorEQ` (app), `C:\Research\SonicEQ` (study). Neither directory was modified.

---

## 1. The reframe

### 1.1 The app is currently a no-op on most tracks, a defect on some, and system-wide on all of them

Three things are simultaneously true right now on this machine:

- **The EQ is not reaching your speakers.** The subkeys of `HKLM\SOFTWARE\EqualizerAPO\Child APOs` are endpoint GUIDs, and all five registered endpoints report DeviceState 4 / 0x20000004 (unplugged). The two active render endpoints carry only the stock Microsoft APO `{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}`. Every `config.txt` write lands on nothing, and the app has no way to know.
- **A stale curve is applied system-wide anyway when it does work.** `C:\Program Files\EqualizerAPO\config\config.txt` was found holding `Preamp: -3.20 dB` with `LSC 120 Hz +7.19 dB` at 15:18, and later `Preamp: -1.20 dB` with a composite peak of **+3.95 dB above the written preamp**, both with the app not running. There is no `atexit`, no signal handler, and no flat-write anywhere in the codebase (`web_gui_app.py:1512-1538`).
- **When it does apply, it clips.** `preamp_gain = round(-0.8 * max(bass_boost, vocal_clarity, airiness), 2)` (`web_gui_app.py:818-819`, verified verbatim, plus three exact duplicates at 1019, 1203, 1445) compensates only for three overlay sliders. It ignores the centroid gains, the style offsets, and the fact that five biquads sum. Two independent agents built the RBJ/EAPO cascade and measured a punchy + `bass_boost` track at **+8.47 dB above unity** (correct preamp: -11.98 dB). Reachable worst case through the UI's own slider ranges is **+20.38 dB @ 1009 Hz** against a preamp slider that floors at -12.

The first bullet means you have possibly never heard this app do what its code says it does. Fix the measurement before you judge the algorithm.

### 1.2 The "crowdsourced ground truth" is fabricated, and the real data cannot rescue the design

F1 confirmed exactly. `generate_mock_dataset()` at `preprocess_safe.py:26-136` fabricates 150 rows from hardcoded per-descriptor means; the SQLite centroids round-trip them (mock warm low shelf 2.5 to centroid 2.312; punchy 3.2 to 3.193; muddy 4.5 to 4.528). Both `data/SAFEEqualiserUserData.csv` and `data/test_library.db` are git-tracked, while `README.md:12` calls the data "crowdsourced". `run_preprocessing()` silently regenerates the mock if the CSV is deleted (`preprocess_safe.py:313-314`).

The important part is what happens if you swap in the real file. In the real 1,700-row corpus (`C:\Research\SonicEQ\data\raw\SAFEEqualiserUserData.csv`, SHA-256 CC43858680115070FFDFA41A1158FCA1344DB2E0096BA73FE68990E90A284AF1), the descriptor counts are:

| profile | real n |
|---|---|
| warm | 532 |
| bright | 504 |
| airy | 6 |
| muddy | 5 |
| presence | 3 |
| punchy | 2 |

The study's own faithful port had to skip four of them: `results/stats/retrieval_arms_results.txt:7` logs `A' profiles with zero TRAIN data (skipped): [airy, muddy, presence, punchy]`. `results/stats/audit_results.txt:49-56` shows exactly **two** descriptors clear the n>=20 floor; the next largest is n=8. And at n=2 an unshrunk centroid is measurably **worse than flat** (warm 4.596 vs 4.141 LSD).

So "use the real SAFE data" deletes two thirds of the taxonomy. The 6-profile ontology is not recoverable from this corpus at any N.

Worse, the taxonomy has the sign backwards on its best-supported concept. Real humans achieve "warm" with a low-mid peak (TRAIN centroid: low shelf +0.53 dB, band1 **+4.76 dB @ ~368 Hz**, high shelf -2.17 dB). That is exactly the region the app's dictionary labels "muddy" (`embed_song_predictor.py:47-49`; muddy centroid band1 +4.53 dB @ 298 Hz). Scored against 419 real warm curves through the study's renderer: **app "muddy" = 3.612 dB median LSD, app "warm" = 3.673 dB**. The app's defect profile models human warmth better than its warm profile does, and `web_gui_app.py:788-792` blends muddy in **additively** (F3 confirmed), so a track tagged dark/heavy/boomy gets its mud boosted.

### 1.3 The descriptor-to-curve link is empirically empty. Stop trying to win there.

Three verified results, all reproduced:

- **Audio does not predict the curve.** Out-of-fold ridge R² of 78 source-audio features against the rendered human curve: **+0.051 (warm), -0.061 (bright)**, against permutation nulls of -0.119 / -0.115 (`results/stats/audio_conditioning_results.txt:6-10, 15-19`). Target-convergence ratio 1.05 / 1.24 means humans *diverge* rather than steering toward a common acoustic target. Note the feature set already contained the bark-band spectrum, so "a better embedding would fix it" is not a live hypothesis for this target.
- **The residual is irreducible disagreement.** Raw dispersion 4.50 / 4.14 dB; residual after removing audio conditioning 4.38 / **4.27** dB. Bright's residual *exceeds* its raw value, consistent with the negative R². Quote the mean-to-centroid estimator consistently; the mean-pairwise estimator on eval clouds is 6.667 / 5.867 dB and D-028 (`DECISION_LOG.md:428-432`) flags the confusion.
- **Doing nothing is competitive on open vocabulary.** Tail medians (`results/stats/method_selection_results.txt:13-19`): A 5.422, A' 5.747, FLAT 5.747, E 5.831, C_AGG 5.938, B 6.117, C 6.285. FLAT beats the trained MLP arm E 70% head-to-head surviving FDR (q=4.346e-04) and beats arm C 67% (q=1.837e-03). FLAT vs the best arm A is p=0.129, not significant.

**Two corrections the orchestrator brief got wrong, and they matter for product decisions.** Arm C is *not* retrieval; it is the LLM-prompted-with-exemplars arm (`EXPERIMENTAL_DESIGN.md:103-109`). The retrieval arm is **A, and A is the best tail arm**. The lesson is "stuffing raw human exemplars into an LLM prompt is the worst thing you can do", not "retrieval is worst". Also, the seven medians are not one sample: A/A'/FLAT/E cover 323 tail descriptors, B/C/C_AGG only 60, so only the 60-descriptor paired head-to-head licenses arm-vs-arm claims.

And the deployed system is already inside this result. `pipeline/11_method_selection.py:6` describes FLAT as "do-nothing EQ, which is what the deployed system emits on 96% of real descriptors". A' fell back to flat on 312 of 325.

Why the floor exists: 79% of tail descriptors (254 of 323) have exactly **one** human curve as gold, and human-to-human centrality is already 4.765 / 4.937 dB. Every tail arm is sitting on the noise floor. That does not mean all methods are equal. It means this corpus cannot adjudicate them, and it forbids claiming any method beats flat on open vocabulary.

### 1.4 The value that does exist is per-listener, and it converges in about ten votes

This is the headline for what to build.

- Everything that reduces human dispersion in this corpus is a **person or context** variable. Expertise: warm spread 5.83 dB (novice) vs 4.14 dB (experienced), a 29% reduction with a 2.18 dB shift in consensus location. Stimulus context: warm spread 8.21 dB (isolated guitar) vs 5.29 dB (full mix), with a 2.62 dB shift in the group mean curve (`results/stats/human_structure_results.txt:14-20`; note the 2.62 is an L2 centroid distance computed at `09_human_structure.py:183`, not the difference of the two spreads). Audio content buys ~0.
- Human practice is a **3 to 4 school mixture**, not one curve. Warm k=3 at shares 16/53/31%; bright k=4 at 18/25/30/27% (`results/stats/human_structure_results.txt:4-12`). The 53% plurality warm school does **no** low-end boost at all; it achieves warmth purely by cutting highs, which is the opposite of the app's +2.31 dB low shelf.
- **Oracle school assignment beats the population centroid by 0.795 dB (warm) and 0.613 dB (bright), against a total descriptor effect over flat of only 0.66 / 1.36 dB.** Knowing which engineer the listener is, is worth roughly as much as knowing what the word means.
- Measured convergence, 120 simulated listeners under exact particle inference with the real SAFE covariance as prior: cold start 7.26 dB coordinate error, 3.17 dB at 5 votes, **1.52 dB at 10**, 0.56 dB at 20. It crosses the 4.5 dB human-disagreement floor at about **4 votes**.

Personalization is not a stretch goal here. It is the only defensible optimization target, and it is cheap.

### 1.5 Loudness is the gate on the entire feature, and it must be measured, not assumed

- **33.0% of the total energy in real human SAFE curves is broadband level, not tone** (level SD 3.04 dB, p5-p95 -3.95 to +5.29 dB).
- The app's own emitted curves differ in pink-weighted loudness by **4.5 dB across profiles after its preamp** (muddy/bass_boost net +2.67 dB, airy/balanced net -1.86 dB), because the preamp is exactly 0.00 for muddy, which sets no overlay.
- Decisively: the ITU-R BS.1770-4 K-weighted broadband gain of a given curve depends on the program spectrum. Measured spread across four plausible spectra: 1.6 dB for the live config, 1.9 dB for the warm centroid, and **7.1 dB for `punchy+bass_boost`** (+0.05 dB under white, +7.16 dB under bass-heavy).

Loudness dominates preference above roughly 0.3 dB. R5 is confirmed verbatim: `APP_PIPELINE_BRAINSTORM.md:118-125` states there is no track-level gold EQ anywhere and that "curve-distance metrics do not exist at track level and must not be faked", leaving blind A/B listening preference as the only legitimate end-to-end endpoint. An analytic, assumed-spectrum level match is off by up to 7 dB exactly where the app boosts hardest.

**Therefore WASAPI loopback capture is a hard prerequisite for the thumbs-up system, not a nice-to-have.** Every vote collected before it lands measures gain, not tone. This does not contradict R1: R1 forecloses predicting the human *curve* from audio; measuring loudness is a different use.

Good news, verified by running it: capture is a `pip install`. On this exact interpreter (CPython 3.14.5), `soundcard` 0.4.6 captured a real (96000, 2) float32 endpoint-loopback array (RMS 0.1085, peak 0.5747), and `proc-tap` 1.1.1 ships a prebuilt `cp314-win_amd64` wheel that isolated **Spotify.exe pid 8872** at RMS 0.145515. No C++/Rust sidecar is needed. Use `blocksize >= 16384`: at 1024 I measured 9 `data discontinuity in recording` warnings per 3 s, at 4096 six, at 16384 zero.

---

## 2. What to fix before anything else

These make current output *wrong*, not merely suboptimal. Roughly two to four days of work total.

| # | Defect | Location | Fix |
|---|---|---|---|
| **F-1** | System EQ never reset on exit or crash. `main()` registers only `close_monitor()`, reached solely via the `finally` of `app.run()`, which never runs when `launch_gui.bat`'s console is closed. | `web_gui_app.py:1512-1538` | `atexit.register(bypass)`, `signal.signal(SIGINT/SIGBREAK/SIGTERM)`, and `win32api.SetConsoleCtrlHandler` for `CTRL_CLOSE_EVENT`/`CTRL_LOGOFF_EVENT`/`CTRL_SHUTDOWN_EVENT` (pywin32 already installed). Also write flat as the very first action at startup. |
| **F-2** | APO not registered on the active render endpoint, so writes do nothing and the app reports success. | new | At startup, get the default render endpoint via `IMMDeviceEnumerator::GetDefaultAudioEndpoint` and test membership in the subkeys of `HKLM\SOFTWARE\EqualizerAPO\Child APOs`. Surface as UI state. Confirm functionally with a -20 dB notch tone plus a loopback read. |
| **F-3** | Preamp compensates the wrong quantity. Measured +8.47 dB above unity. | `web_gui_app.py:817-819`, 1018-1020, 1202-1204, 1444-1446 | Collapse to one function. Evaluate the realized 5-filter cascade on a log grid and set `Preamp = -(max(0, peak_dB)) - 1.0`. The 1.0 dB covers the measured L1-vs-peak excess (+0.68 to +3.80 dB). Measured cost: **557 us per write**. |
| **F-4** | NaN becomes maximum boost. `min(15.0, nan)` returns 15.0 in CPython, so `{"eq":{"low_shelf_gain":"nan"}}` yields +15 dB. Preamp itself is never clamped and is written raw. | `web_gui_app.py:142-153, 162, 1107-1115` | Reject non-finite with HTTP 400 *before* the clamp. Clamp preamp to [-24, 0]. Add a summed-response budget: if peak > 6 dB or L1 > 9 dB, bisect a single scalar over all band gains and show "limited to fit headroom". |
| **F-5** | Config written from 8 call sites across the monitor thread and Flask request threads, outside `state_lock`, with plain `open(path,'w')`. APO opens `FILE_SHARE_READ` and can read truncated content (`FilterEngine.cpp:270-286`). Write-ordering inversion is reproducible by dragging a slider during a track change. | `web_gui_app.py:174-180`; call sites 855, 1058, 1076, 1093, 1117, 1141, 1226, 1476 | One `commit_state(EqState)` behind one module-level lock held across snapshot + render + write. `open(tmp,'w')` then `flush()` then `os.fsync()` then `os.replace()`. `os.replace` **is** detected: the watch mask at `FilterEngine.cpp:553` includes `FILE_NOTIFY_CHANGE_FILE_NAME` with `bWatchSubtree=true`. |
| **F-6** | Gains applied at the wrong frequencies. `synthesize_eq_curve` interpolates all 13 parameters; the web path hardcodes 120/250/1000/3500/10000 Hz at Q 0.71 and copies only the five gains. Punchy's shelf measured at 68.9 Hz is applied at 120 Hz; airy's at 14446 Hz becomes 10 kHz. The CLI path uses the interpolated values, so the two paths emit different EQ from identical weights. | `web_gui_app.py:757-763` vs 788-792, 990-994, 1416-1420; `embed_song_predictor.py:163-177` vs 197-201 | Carry Fc and Q through, or move to curve-space composition (Section 5) which makes the question moot. |
| **F-7** | The preference DB learns the algorithm's own output and then freezes it forever. On every track change the monitor writes `app_state["eq"]` (in auto mode, the machine's own curve) to songs.db; `load_track_eq_from_db` is consulted first and short-circuits, applying no style offsets and recomputing nothing. Style and engine dropdowns become permanent silent no-ops for any track heard once. `/api/eq/reset` zeroes the EQ without touching the DB row, so Reset-then-skip persists flat as a "preference". | `web_gui_app.py:672-687` (verified), 698, 711-736, 952-968, 1121-1142 | Delete the save. Only user-originated events may write preference rows. Verified convenience: `data/songs.db` does not exist on this machine, so there is no poisoned legacy data. This is greenfield. |
| **F-8** | "Muddy" blended additively as a target while the same file subtracts it as a defect twelve lines later. | `web_gui_app.py:788-792` vs 814; `embed_song_predictor.py:47-49` | Add an explicit polarity column (target vs defect) to the lexicon. Until then, remove muddy from the additive blend. |
| **F-9** | The entire explanation surface is dead. `#insightsCard` carries an inline `style="display: none;"` (verified) and `app.js` never references it, so `mixing_reason` (`app.js:417`) and the whole pipeline status list (`app.js:506-549`) render into DOM no user can see, 40 times a minute. | `templates/index.html:279` | Delete the inline style. One line, and it is the difference between an opaque widget and a legible product. |
| **F-10** | HTML injection from crowd-editable Last.fm tags via `innerHTML`, in an origin with unauthenticated control of every local endpoint. | `static/app.js:449-461, 543`; `web_gui_app.py:879` | `textContent` on created elements. |
| **F-11** | Live Last.fm API key hardcoded in git-tracked source and sent over plaintext HTTP. | `lastfm_client.py:33` (verified), 56, 97 | Move to `config.yaml` (already gitignored), switch both URLs to HTTPS, fail loudly when absent. |
| **F-12** | Network calls with no timeout on the monitor thread; token refresh destroys `refresh_token` after ~7 s of failure. | `src/spotify/service.py:365, 368, 371, 251-263` | Retired for free by Section 6. Do not patch; delete. |
| **F-13** | Fabricated data committed as if real, with README claiming "crowdsourced". | `data/SAFEEqualiserUserData.csv`, `data/test_library.db`, `README.md:12` | `git rm` both. Rename any retained copy to `*.MOCK.csv`. Make `run_preprocessing()` fail loudly instead of regenerating. |

**One correction to an earlier finding:** there is no audible click from the config rewrite. `FilterEngine::setDeviceInfo` sets `transitionLength = sampleRate / 100` (480 samples, 10 ms at 48 kHz) and `FilterConfiguration::doTransition` runs old and new chains in parallel with a raised-cosine mix (`FilterConfiguration.cpp:154-172`). What is real is 1.5 to 4 s of the *previous* track's EQ, a partial-read flutter, and a clean but abrupt 10 ms tonal step.

**One new defect nobody had found:** the notification thread blocks on `loadSemaphore` before every load (`FilterEngine.cpp:598-599`), and that semaphore is released only inside the audio callback when a transition completes (`:406, :445`). **If audio is not flowing, the second and every subsequent config change never loads**, while `apply_and_write_apo()` sets `apo_write_status = "success"` (`web_gui_app.py:198`). Report "paused, EQ will apply when playback resumes" instead of success. This also caps the sustainable reload rate at one per 10 ms of processed audio, which is what makes a stepped ramp feasible.

---

## 3. The new architecture

```
                       ┌───────────────────────────────────────────┐
                       │  WINDOWS SMTC  (winrt-Windows.Media.*)     │
                       │  event-driven, no polling, no accounts     │
                       │  title / artist / album / status / art     │
                       └──────────────┬────────────────────────────┘
                                      │  local_key = sha1(norm(artist)+US+norm(title))
                                      ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  IDENTITY LADDER   (cache-first; steps 0-2 are fully offline)        │
   │   0 local_key cache hit (sub-ms)                                     │
   │   1 SMTC artist+title                                                │
   │   2 title normalizer for browser AUMIDs                              │
   │   3 [OPT] local canonical-MB SQLite  ~1.9 GB  ◄ opt-in only          │
   │   4 [NET] MusicBrainz WS @ 1 req/s ──► recording_mbid                │
   │   5 [NET] start-aligned Chromaprint/AcoustID (last rung)             │
   │   6 give up, emit FLAT, say so                                       │
   └───────────────┬──────────────────────────────────────┬───────────────┘
                   │ mbid                                 │ pid (from SMTC)
                   ▼                                      ▼
   ┌───────────────────────────────┐      ┌──────────────────────────────────┐
   │ TAG JOIN  [NET, optional]     │      │ AUDIO LAYER  (proc-tap / soundcard)│
   │ Last.fm / MB genre, TTL'd     │      │ per-process WASAPI loopback       │
   │ date-stamped, negative-cached │      │ ring buffer 60 s = 23 MB          │
   └───────────────┬───────────────┘      │ Welch band spectrum (0.23 dB @10s)│
                   │                      │ BS.1770 LUFS, true peak, PLR      │
                   ▼                      │ stereo corr, codec cutoff         │
   ┌───────────────────────────────┐      └──────────┬───────────┬───────────┘
   │ DESCRIPTOR PRIOR              │                 │           │
   │ signed lexicon + abstain mass │                 │           │
   │ real-SAFE curve distributions │                 │           │
   │ n-gate: FLAT below n=5        │                 │           │
   │ output: m(f), sd(f), conf c   │                 │           │
   └───────────────┬───────────────┘                 │           │
                   │  c · m(f)                       │ c_meas(f) │ LUFS
                   ▼                                 ▼           │
   ┌──────────────────────────────────────────────────────┐      │
   │  CURVE-SPACE COMPOSER   (256-bin log grid, dB add)   │      │
   │  c_total = c_desc + c_pref + c_style + c_meas        │◄─────┤
   │  projection onto budget: |c|∞ ≤ 6 dB, RMS ≤ 3 dB     │      │
   └───────────────┬──────────────────────────────────────┘      │
                   │                                             │
                   ▼                                             │
   ┌──────────────────────────────────────┐                      │
   │ PARAMETRIC REFIT (residual 0.12-0.16)│                      │
   │ exact preamp from realized cascade   │◄─────────────────────┘
   │ + measured LUFS trim (level match)   │   this closes the loudness gate
   └───────────────┬──────────────────────┘
                   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  OUTPUT STAGE                                                │
   │  registry control plane (HKCU DWORDs) ──► static config.txt   │
   │  Device: scoped | enabled gate | If/Else A/B arms             │
   │  20 ms cosine-eased waypoints | flat-on-exit guaranteed       │
   └───────────────┬──────────────────────────────────────────────┘
                   │
                   ▼   hold-to-compare, level-matched, blind
   ┌──────────────────────────────────────────────────────────────┐
   │  PREFERENCE LEARNER  (the product)                           │
   │  4-dim basis · ideal-point probit · ADF closed form          │
   │  hierarchy: device ▸ global ▸ cluster ▸ artist ▸ track        │
   │  append-only observation log ──► replayable posteriors        │
   └──────────────────────────────────────────────────────────────┘
```

**Optional / offline markers.** Everything from SMTC through the audio layer, the curve composer, the output stage, and the preference learner works with **zero network**. Only tag join, MusicBrainz WS, and AcoustID require the internet, and each must degrade to "unknown, staying flat" rather than blocking. The local MB mirror is an opt-in download, not a default (Section 6). CLAP is optional and sits nowhere on the EQ decision path.

**The structural change.** Today, information flows tags to centroid to file, one shot, with nothing measuring anything and no feedback. In the new shape there are three independent information sources (identity to descriptor prior, audio to measurement, listener to preference), they compose in one common space (256-bin dB curves), and the composition is bounded by an explicit budget. Preference is the only one of the three with real headroom, so it gets the most machinery.

---

## 4. The preference-tuning system

This is the centerpiece and the answer to U3. Build it in the order given; each stage is useful on its own.

### 4.1 Feedback modalities, ranked by measurement

Votes required to reach 2.0 dB coordinate error, each modality scored under its *own* correctly specified generative model (no straw men), 120 simulated listeners, prior = real SAFE covariance:

| modality | votes to 2.0 dB | build order |
|---|---|---|
| A/B duel, active probe | **9** | 3rd |
| Named-axis ("too boomy") | **9** | 3rd, same surface |
| A/B duel, random probe | 10 | (baseline) |
| Implicit slider drags | **14** | **1st** |
| Unary thumbs-up, clean | >40 (2.26 dB at 40) | do not build alone |
| Unary thumbs-up, 30% song-liking confound | never converges (4.63 dB at 40) | do not build |

**The user's literal ask, a bare thumbs-up, is the weakest option on the list.** The mechanism is structural, not incidental. A unary thumb is one bit about whether the ideal point lies inside a ball of *unknown radius* (the acceptance threshold is a nuisance parameter that must be jointly estimated), it carries no signed direction, and it is contaminated by whether the listener likes the *song*. A/B fixes all three at once: both arms are the same song at the same instant so song preference cancels exactly, the response is a signed halfspace cut, and there is no nuisance radius.

**Recommendation: keep the thumb as the affordance, wire it to open a duel.** The user gets the gesture they asked for; the system gets 4x to 6x the information per click.

Do **not** train on skips, replays, or session length. They are strictly worse than unary on the confound axis. The one exception worth adding later is a **system volume change** within 20 s of an EQ engage, which is a clean low-confound signal for the level axis specifically.

### 4.2 The 4-dimensional preference basis

Learning in raw gain space is both under-determined by sparse feedback and over-parameterized relative to what humans do. Centered PCA on level-removed rendered SAFE TRAIN curves gives cumulative variance **41.95 / 75.10 / 88.76 / 94.20%**. (Note: an earlier figure of 91.1% at three dimensions does not reproduce anywhere in either repo; use 88.8% at three and 94.2% at four.) Four numbers reconstruct an arbitrary real human EQ move to about 0.93 dB median LSD, against a 4.14 to 4.50 dB human-to-human floor. Four numbers are indistinguishable from a full human curve at the resolution humans themselves agree to.

The axes, expressed directly as gains on the app's **existing five bands** so no filter-topology change is needed (per one population sigma):

```
A1 tilt      = [+6.94, +5.58, +1.28, -1.92, -1.38, -2.80]   dark ◄──► bright
A2 scoop     = [+1.61, -3.52, -3.39, +0.25, +1.21, +0.95]   mid-forward ◄──► smile
A3 presence  = [+3.29, -0.48, +4.40, +2.51, +0.55, -2.31]
A4 level     = [ 0,     0,     0,     0,     0,    +3.04]
                LSC120  PK250  PK1k   PK3k5  HSC10k Preamp
```

API: `to_gains(x: np.ndarray[4]) -> dict` (`x @ B.T`, then the loudness normalizer overwrites the preamp term) and `to_coords(gains) -> np.ndarray[4]` via `np.linalg.lstsq(B[:5].T, g)` so a manual slider position reads back into coordinates. Sign convention: +A1 = darker. Store `BASIS_VERSION = 1` on every persisted vote; a basis change must never silently reinterpret old votes.

**Two caveats that came out of adversarial verification and that you must respect.**

1. **Spanning is verified, but only in RMS.** Projecting 3000 curves sampled from the app's own six generative profiles onto mean + 4 SAFE PCs gives residual RMS **0.572 dB** overall (presence 0.274, bright 0.391, warm 0.489, muddy 0.546, airy 0.755, punchy 0.793), better than claimed. But worst-case per-frequency residual reaches **4.86 dB at 20 kHz on "airy"** and 4.52 dB at 277 Hz on "muddy". The single worst-spanned direction is the 14 kHz air shelf, precisely the move SAFE engineers rarely made and precisely a consumer-listening direction. State the bound in both norms.

2. **Ship the SAFE basis. Do not ship the SAFE prior covariance at full strength.** A wrong basis costs efficiency, not correctness, because it demonstrably spans the reachable set. A wrong *prior covariance* and a wrong *school mixture* cost correctness in the small-N regime: they enter as bias, not variance, and with per-observation noise at the measured 4.38 / 4.27 dB residual dispersion, a misspecified prior dominates the posterior well past 200 observations. Inflate to `c * Sigma_SAFE` with `c >= 4`, or interpolate toward isotropic, so it acts as a weak regularizer rather than an anchor. Also: bright's best-k=4 sits on the **boundary** of the `k in {1..4}` grid searched at `09_human_structure.py:107`, so the school count for bright is unconverged and must not be transplanted as a fixed 4.

3. **Identifiability requires dither.** Passive thumbs on curves the app itself chose from a 6-profile blend is rank-deficient; the design matrix will not excite all four coordinates and no amount of data re-derives the geometry. The app must inject controlled per-coordinate perturbations (randomized +/- step on each of the 4 basis coefficients) and log the offered coefficient vector alongside every observation.

### 4.3 The model: Bayesian ideal-point probit with closed-form ADF updates

Not Bradley-Terry, not a GP, not a contextual bandit. About 120 lines of numpy, microseconds per update, runs inline in a Flask handler.

**Why not the alternatives.** Bradley-Terry and Thurstone assume utility monotone in item features, which over a bounded gain space drives the estimate to the corner ("more bass is always better"). That is exactly the +12 dB railing the LoRA arm produced (`results/receipts/finetune/rows_d_seed0.jsonl`: bright greedy railed three bands simultaneously at the corpus max). EQ preference has an interior optimum, so the utility must be ideal-point. GP preference learning handles that but is O(N³), has no natural place to inject the SAFE prior, and yields no interpretable per-axis number. Contextual bandits are actively contraindicated by R1: a bandit whose context is spectral features is regressing on noise (R² +0.051 / -0.061).

**Model.** `u(x) = -(x - θ)' A (x - θ) + ε`, `ε ~ N(0, σ_n²)`, `A = diag(1, 1, 1, 0.6)`, `σ_n = 2.0 dB`.

**Exact linearization** (verified numerically to 10 decimal places):

```
u(x_a) - u(x_b) = θ'z - c
    where  z = 2A(x_a - x_b)
           c = x_a'A x_a - x_b'A x_b
    so     P(a beats b) = Φ((θ'z - c)/σ_n)      linear in θ
```

**ADF update** (TrueSkill-style moment match, exact for this likelihood, O(d²) = 16 flops):

```
s²  = z'Σz + σ_n²
m   = μ'z - c
t   = sgn · m / s            sgn = +1 if a won else -1
λ   = φ(t)/Φ(t)
μ'  = μ + sgn · (Σz) · λ / s
Σ'  = Σ - (Σz)(Σz)' · λ(λ + t) / s²
```

**Symmetric-probe simplification.** For `x_a = μ + d`, `x_b = μ - d`: `z = 4Ad`, `c = 4μ'Ad`, so `θ'z - c = 4 d' A (θ - μ)`. The vote is a direct signed measurement of the projection of the current error onto the probe direction. This is what makes probe design trivial.

**Direct observations** (slider drags, volume changes) use a plain conjugate Gaussian update instead:
`Σ' = (Σ⁻¹ + σ_s⁻²I)⁻¹`, `μ' = Σ'(Σ⁻¹μ + σ_s⁻² x_obs)`, with `σ_s ≈ 2.0 dB` for sliders and `≈ 4.0 dB` for volume.

```python
class PrefModel:
    def __init__(self, mu: np.ndarray, Sigma: np.ndarray): ...
    def update_pairwise(self, x_a, x_b, winner) -> None: ...
    def update_direct(self, x_obs, sigma_s) -> None: ...
    def curve(self) -> np.ndarray:      # returns mu, 4 coords
    def uncertainty(self) -> float:     # sqrt(trace(Sigma))
```

### 4.4 Build first: implicit slider capture

Ship this **before** any thumbs-up or A/B UI exists. Highest information per unit of user effort because the denominator is zero: the user is already dragging sliders, and `/api/update_eq` already receives every drag. 14 events to 2.0 dB versus 9 for a deliberate duel, at no marginal user cost, accruing silently from day one. A slider drag is also the least confounded signal available; it is unambiguously a statement about the mix.

In `web_gui_app.py:1098-1118`, after the state write: debounce ~1.5 s of drag inactivity, then `x_obs = basis.to_coords(app_state['eq'])` and `model.update_direct(x_obs, 2.0)`. Gate hard: fire only if the drag settled for >= 1.5 s **and** the track has played >= 10 s **and** the resulting curve is inside the trust region. This must **replace** the inverted loop at `web_gui_app.py:672-687`, not sit beside it.

### 4.5 The A/B duel

A persistent compare strip near the curve card (`templates/index.html:103-118`): `[A] [B] [= no difference]` plus a thumb icon that seeds the duel.

- `POST /api/preference/ab/start` computes probe `d`, forms `x_a = μ+d` and `x_b = μ-d`, renders both through the loudness normalizer so pink-weighted loudness matches within 0.1 dB, randomizes the A/B label **per duel** (not per session), returns an opaque `duel_id`.
- Both arms live in the config simultaneously under `If: readRegDWORD(K,"arm") == 1 / Else: / EndIf:`, so switching is one DWORD write. Measured end-to-end switch budget: registry set, `RegNotifyChangeKeyValue` signal, up to 10 ms coalescing (`FilterEngine.cpp:594-595`), parse, next audio callback, plus APO's own 10 ms raised-cosine crossfade = roughly **15 to 40 ms**. Below the threshold where a switch stops feeling instant, and it is a clean morph.
- Hold **Space** = momentary arm A while held (the correct affordance for tonal comparison). **X** = latch toggle. **V** = vote. Labels hidden until after the vote.
- Record `n_toggles` and `decision_ms`; down-weight fast low-toggle votes by raising `σ_n`. Offer an explicit "no difference" recorded as a tie and used to **calibrate** `σ_n` rather than discarded.
- Add the named-axis fallback ("too boomy" / "too harsh" / "too dull"), measured at 9 votes to 2.0 dB, statistically tied with A/B and far easier to explain. Implement as a coordinate-aligned duel along one axis.

### 4.6 The hierarchy, with device at the top

`θ(context) = θ_global + δ_device + δ_cluster + δ_artist + δ_track`.

**Device belongs at the top and is currently entirely absent** (grep for device/headphone/speaker in `web_gui_app.py` returns only Spotify session strings). Headphones versus speakers versus a laptop is a change in the physical transfer function of 10+ dB at the extremes, which dwarfs every tonal preference the model is trying to learn. A single θ pooled across headphones and desk speakers converges to a compromise wrong for both, and looks like model failure.

Shrinkage weight on a scope's own estimate = `τ²/(τ² + σ²/n)`. With τ = 4 dB and σ = 6 dB: n=1 keeps 30.8% own, n=3 keeps 57.1%, n=10 keeps 81.6%, n=30 keeps 93.0%. Set `τ_device = 4.0`, `τ_cluster = 2.0`, `τ_artist = 1.0`, `τ_track = 0.5` dB. Read: sum posterior means down the chain that have data. Write: update the most specific scope with enough evidence and propagate a shrunk version to the parent.

- **Device key:** enumerate the default render endpoint and key on endpoint GUID + friendly name. Read the friendly name from `HKLM\...\MMDevices\Audio\Render\{guid}\Properties`, value `{a45c254e-df1c-4efd-8020-67d146a850e0},2`. Expose as a visible pill ("Learning for: Sennheiser HD600") with a manual override.
- **Cluster key:** do **not** use raw Last.fm genre strings; the vocabulary is unbounded and most clusters would sit at n=1 forever. Bucket by top-level MusicBrainz genre once the MBID spine lands, or k-means the tag embeddings offline into 12 to 20 buckets.
- Note `lastfm_client.py:143-152` falls back to `artist.getTopTags` whenever a track returns fewer than 3 tags, so the advertised per-track profile is frequently an artist-level profile. Record which source produced the tags and never create a track-scope row from an artist-level fallback.

This is where the "multivariable work together" framing actually lands, and it is the framing the research supports: person-and-context covariates buy 1.3 to 2.9 dB of the ~4.4 dB dispersion, audio content buys ~0.

### 4.7 Active querying, with an honest negative

**Do not build BALD/qEUBO/dueling-bandit machinery.** Measured: active probing reaches 2.0 dB in 9 votes versus 10 for uniformly random directions, and at 40 votes they are identical (0.32 vs 0.30 dB). A 4-dimensional space is covered adequately by random directions. That would be weeks of work for roughly one saved click.

The acquisition is one line, and it is exactly optimal for this likelihood (expected information gain of a symmetric probe is monotone increasing in `d'AΣAd` at fixed `||d||`):

```python
d = probe_db * np.linalg.eigh(A @ Sigma @ A)[1][:, -1]     # probe_db = 3.0
```

3.0 dB puts P(correct answer) near 0.75 to 0.85 under `σ_n = 2.0`. Adapt: if the last 5 votes were decided in under 2 s with no toggling, shrink 20%; if the "no difference" rate exceeds 40%, grow 20%.

**The real lever is how often you ask, not what you ask.** Budget: at most 1 duel per session, at most 3 per day, never in the first 30 s of a track, only when `trace(Σ)` exceeds a floor, only at a natural boundary. Visible "Ask me less" and "Never ask" controls, honored permanently. **Default off**: the app learns silently from slider drags and offers duels only on opt-in or on the thumb.

### 4.8 Cold start and safety

- **Prior:** a Gaussian mixture over the SAFE schools with the study's own shares as mixing weights, represented with ~2000 particles for the first ~8 votes, then collapsed to the single Gaussian once one component passes 0.7 posterior weight. Measured benefit: +0.41 / +0.45 / +0.55 dB at votes 1/2/3, +0.15 dB by vote 12. Worth taking because it is free, not as a headline. Remember to flatten the covariance per 4.2.
- **Quick tune:** an optional 60-second first-run of 5 back-to-back duels on a track the user picks. Takes error from 7.26 to about 3.17 dB, below the human-disagreement floor, in one minute. This is the one place where batching queries is justified, and it is a far better first-run experience than 13 setup steps.
- **Trust region:** clamp `||x||_A <= r_max` with `r_max = 3.0 + 6.0*(1 - mean(post_sd)/mean(prior_sd))`, capped at 9.0 dB. A new user can be moved 3 dB; a user with 30 votes can be moved 9. Two lines, and the single most important overfitting guard.
- **Drift:** EWMA (α=0.1) of vote surprisal `-log P(winner)`. If it exceeds its running baseline by >0.3 nats over 10 votes, inflate Σ by 1.5x and log visibly ("re-learning your preferences").
- **Reset and pin:** `POST /api/preference/reset {scope}` for scope in {track, artist, cluster, device, global, all}, each confirmed. A "pin" that freezes a scope against further updates.
- **Never train on:** the model's own output, any LLM-arm curve, or eval-protocol votes. Record a `source` column so this is auditable rather than conventional.

### 4.9 Proving it works: the within-app blind holdout

R5 means the app cannot score itself against any external truth. Preference is the target, so the only valid instrument is a blind listening comparison, and the product owes the user one.

**Protocol.** n level-matched blind 2AFC trials on tracks the user picks, between two of {personalized θ, FLAT, generic centroid}. Randomize arm-to-label per trial. Unlimited toggling. Force a choice or an explicit tie. **Eval votes must not update the model** (`is_eval=1`, skip the ADF update), or the test is circular.

**Statistic.** Exact binomial sign test against p=0.5; report wins/n, Clopper-Pearson 95% CI, one-sided p. Power: at true p=0.70 you need n=50 for 0.78 power, n=60 for 0.84; at p=0.65 you need ~100; at p=0.80, n=30 suffices. Fixed-n at 50 to 60 is a big ask of one person, so offer an SPRT (α=0.05, β=0.20, H1: p=0.70): **expected stopping n = 23.2 when the effect is real, 15.4 when it is not**, a single listening session.

**Report all three contrasts**, including the embarrassing ones: personalized vs flat (the claim), personalized vs generic (the value of personalization specifically, the cleanest test of this whole feature), and generic vs flat (which the research predicts lands near 50/50 on open vocabulary). Show the CI, refuse a verdict below n=20. Add a **sanity arm**: personalized vs personalized, identical curves. The user should score ~50%. Systematically non-50% means the level matching or crossfade is leaking a cue and the whole dataset is suspect.

### 4.10 The store

Append-only observations plus derived posterior snapshots. The posterior becomes a pure function of the log, so a basis change, a re-tuned `σ_n`, or a bug fix can be **replayed** rather than losing the user's history. The current schema (`web_gui_app.py:208-232`) stores only latest state and destroys the raw evidence on write; `local_play_count` and `last_played_at` at lines 230-231 are declared and never written.

```sql
-- data/preferences.db,  PRAGMA journal_mode=WAL, single writer thread

CREATE TABLE observations (
  obs_id           INTEGER PRIMARY KEY,
  ts               TIMESTAMP NOT NULL,
  kind             TEXT NOT NULL CHECK(kind IN ('pairwise','direct','axis','tie')),
  source           TEXT NOT NULL CHECK(source IN ('ab_duel','slider','axis_button','volume','quick_tune')),
  is_eval          INTEGER NOT NULL DEFAULT 0,
  basis_version    INTEGER NOT NULL,
  device_key       TEXT,
  cluster_key      TEXT,
  artist_key       TEXT,
  track_key        TEXT,
  mbid             TEXT,
  x_a              BLOB,          -- float64[4], offered coefficient vector A
  x_b              BLOB,          -- float64[4], offered coefficient vector B
  x_obs            BLOB,          -- float64[4], for kind='direct'
  winner           TEXT CHECK(winner IN ('a','b','tie')),
  probe_db         REAL,
  loudness_match_db REAL,         -- measured, not assumed
  match_source     TEXT CHECK(match_source IN ('measured','assumed')),
  n_toggles        INTEGER,
  decision_ms      INTEGER,
  sigma_n          REAL,
  app_version      TEXT
);
CREATE INDEX idx_obs_device ON observations(device_key, ts);
CREATE INDEX idx_obs_track  ON observations(track_key);

CREATE TABLE posteriors (
  scope_type    TEXT NOT NULL,     -- device|global|cluster|artist|track
  scope_key     TEXT NOT NULL,
  basis_version INTEGER NOT NULL,
  mu            BLOB NOT NULL,     -- float64[4]
  sigma         BLOB NOT NULL,     -- float64[4][4]
  n_obs         INTEGER NOT NULL,
  updated_at    TIMESTAMP NOT NULL,
  pinned        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (scope_type, scope_key, basis_version)
);

CREATE TABLE eval_runs (
  run_id      INTEGER PRIMARY KEY,
  started_at  TIMESTAMP, ended_at TIMESTAMP,
  protocol    TEXT,               -- 'sprt' | 'fixed_n'
  arm_a       TEXT, arm_b TEXT,   -- personalized|flat|generic
  n_trials    INTEGER, n_wins_a INTEGER,
  p_value     REAL, ci_lo REAL, ci_hi REAL,
  stopped_by  TEXT
);

CREATE TABLE devices (
  device_key TEXT PRIMARY KEY, friendly_name TEXT, kind TEXT,
  first_seen TIMESTAMP, last_seen TIMESTAMP
);

CREATE TABLE measurements (
  track_key TEXT, mbid TEXT, fs INTEGER, integrated_seconds REAL,
  band_edges_hash TEXT, band_db BLOB,   -- 25 float32, level-removed
  lufs_i REAL, dbtp REAL, plr REAL, psr REAL,
  r_broadband REAL, r_low REAL, codec_cutoff_hz REAL,
  centroid_hz REAL, flatness REAL, measured_at TIMESTAMP
);
```

**Export** `my_sound_profile.json`: `{schema_version, basis_version, exported_at, app_version, devices[], posteriors[], observation_count, sha256}`. Export posteriors by default; raw observations only on an explicit "include full history" toggle, since the observation log is a listening-history record. Import validates `basis_version` and refuses a mismatch rather than silently reinterpreting coordinates.

Give the profile a human-readable summary generated from μ: *"Warmer than average (tilt +2.1 dB), slight presence lift, level -0.4 dB."* That string is the whole feature made legible.

---

## 5. Algorithm replacement

### 5.1 What comes out

Delete `sonic_dictionary` (`embed_song_predictor.py:36-65`), `calculate_similarity_weights` (`:84-134`), the four copies of the "smart blending" overlay block, `get_ai_predicted_eq` (`web_gui_app.py:406-437`), `generate_mock_dataset`, the ±5 dB outlier rule, and `run_daemon_loop` (`embed_song_predictor.py:291-374`, unreachable because nothing in the repo ever writes `../.spotify_cache`; `SpotifyService` only deletes it).

Rename `embed_song_predictor.py`. F2 confirmed: `match_count = len(song_words.intersection(vocab))` at line 106, no vectors anywhere, and the filename plus its "cosine-style text similarity" docstring at line 85 is the origin of the UI's false "Vector Similarity Centroids" claim.

### 5.2 Do not substitute embeddings. This was measured and it fails.

The obvious fix (embed the tags and the SAFE vocabulary) does not work. `all-MiniLM-L6-v2`, the same model Arm A used, scores leave-one-out kNN **sign accuracy of 52.6% (k=1), 55.3% (k=5), 50.0% (k=7)** on a 38-word labelled audio lexicon. That is chance. Its nearest neighbours are antonyms: dark to bright cos 0.590, thick to thin 0.816, fat to thin 0.608, dull to sharp 0.593, airy to boxy 0.558. Centring on the warm-bright contrast axis does not fix it (mellow lands on the bright side at -0.067, crisp on the warm side at +0.034); an audio-framed template makes it worse, ranking "british" above every real sonic word.

Distributional embeddings collapse antonyms because antonyms share contexts. Shipping this gives confident **wrong-sign** EQ, strictly worse than today's matcher, which never inverts and only misses.

This also explains Arm A rather than contradicting it: raw cosines sit at 0.277 ± 0.107 for every tag including "favorites" (0.350) and "seen live" (0.249), so Arm A is a near-fixed 50/50 warm+bright average, an accidental shrink-toward-flat. Which is why FLAT vs A is -0.112 dB, p=0.1294, failing FDR.

Embeddings keep exactly one job: an out-of-vocabulary **domain gate**, never polarity. In-lexicon 1-NN cosine median 0.541 vs noise-tag 0.323, so a threshold at 0.43 flags "plausibly sonic, unknown", which routes to abstain and logs the term for lexicon expansion.

### 5.3 The replacement

**Step 1: signed lexicon with explicit abstain mass.** ~200 audio terms with columns `term | axis | sign | magnitude_prior_dB | polarity∈{target,defect}`. Polarity is the correct fix for F3: "muddy", "boxy", "boomy", "harsh", "sibilant", "tinny" are defect names, so the human typing them is describing what they *removed*. SAFE cannot adjudicate this (muddy n=5, where shrinkage makes the centroid nearly flat regardless of sign), so it must be a declared column, not an inferred one. Genre tags abstain by default: SAFE contains zero evidence linking genre to a tonal target, so genre-to-curve is learnable only from the user's own votes.

```
w_d = Σ_matched_tags (tag_conf × magnitude_prior)
a   = 1 / (1 + Σ_d w_d / w_0)                       # abstain mass, w_0 ≈ 1.0
curve = (1 - a) · Σ_d w_d λ_d m_d(f) / Σ_d w_d
```

The key change is that weights are **never renormalized to 1.0**. Today a track whose only match is "rock" gets `presence = 1.0` and the entire centroid (+5.88 dB @ 1 kHz), identical in magnitude to a track with ten corroborating tags.

**Step 2: descriptor priors as curve distributions, not parameter centroids.** Store `{n, m(f) 256 floats, sd(f) 256 floats, λ, level_offset_dB}` per descriptor, built from the real CSV with only the audit's a-priori rules (`01_audit.py:39` blocklist {test, 1, my} removes 183; drop 44 exact dups; 1,473 usable). Render every submission through the RBJ instrument and take the **per-bin median**, which is outlier-robust without a rejection rule.

Curve space beats parameter space, measured: 5-fold CV over 817 real curves gives warm 3.335 (curve-median) vs 3.463 (param-centroid), bright 3.450 vs 3.546. The param-space centroid sits 0.85 to 0.98 dB RMSE from the curve-space target, **larger than the entire descriptor effect over flat for warm** (0.66 dB). Parameter averaging smears frequencies and Qs across submissions that put the same gesture in different bands.

Do **not** use `preprocess_safe.py:261-270`. Its ±5 dB rule keeps 239/1700 rows (14%) and exactly **zero** for muddy/airy/boomy/deep, then line 270 silently falls back to the *unfiltered* mean for those, putting two estimators in one table. At 4.50 dB raw dispersion it is a ±1.1σ window per band compounded over five bands: it discards the genuine disagreement the research says is irreducible. The ±15 dB gate at line 211 is dead code (the SAFE plugin clamps at ±12).

Resulting warm target (level-normalized, refit residual 0.161 dB): `LSC -0.66 dB @ 284 Hz | PK +3.42 @ 226 Hz Q0.40 | PK -1.11 @ 962 Hz Q1.25 | PK -1.34 @ 3474 Hz Q0.47 | HSC -2.45 @ 7660 Hz`, peak +2.99 dB @ 317 Hz. Scored against 419 real warm curves: **proposed 3.549, app warm 3.673, app muddy 3.612, flat 4.124**.

**Step 3: shrinkage, with the correction that came out of verification.** The tempting move is empirical Bayes, `λ(n) = τ²/(τ² + σ²/n)`. Adversarial verification killed the calibration: subsampling warm and bright probes `σ²/n` only and carries **zero** information about `τ²`, which must be estimated from **two** descriptor means (1 degree of freedom, 95% CI on τ spanning 0.45x to 32x). Worse, warm and bright are the corpus's most spectrally opposed pair (high-shelf means -2.65 vs +3.94 dB), so a 2-point τ̂ is inflated by construction: τ̂ = 4.66 dB against σ̂ = 4.50 dB gives λ(1)=0.52, λ(3)=0.76. Inflated τ means **under**-shrinkage, meaning the app **over**-acts, emitting half of one anonymous engineer's DAW move at up to ±6 dB on a stereo master bus. That is the dominant risk and it does not fail safe.

Per-descriptor tail dispersion cannot be measured at all: `tail_eval.jsonl` holds 452 entries across 323 descriptors, 254 of them singletons, max n=7, total within-descriptor df = 129. The blocker is degrees of freedom, not protocol.

**Recommendation: ship a hard n-gate, not an EB schedule.** Emit FLAT for any descriptor with n < 5 (which is 312 of 323 tail descriptors), and use a conservatively small fixed τ above the gate. And **state in code that the shrinkage target is literally 0 dB per band, never the corpus grand mean**: the pooled warm+bright low-shelf mean is -1.52 dB, so shrinking a +2 dB true move toward it produces a negative result, wrong sign, produced by the "safe" direction.

Budget the cost honestly: shrink-to-flat costs about 0.33 dB of median tail LSD (A 5.422 vs FLAT 5.747, n.s.) and loses the 35 of 323 descriptors where retrieval beats flat by more than 1 dB. That is a real price, and it is the right one to pay until per-user data exists.

**Step 4: compose in curve space under a deviation budget.**

```
c_total(f) = c_desc(f) + c_pref(f) + c_style(f) + c_meas(f)      on the 256-bin log grid
if max|c_total| > L∞ (6.0 dB):  scale by L∞ / max|c_total|
if RMS(c_total) > 3.0 dB:       scale accordingly
```

dB addition is exactly right because cascaded filter magnitudes multiply. A projection preserves the *shape* of the compromise and only scales magnitude, which a listener can reason about; a priority order would let one source silently erase another. Measured RMS for reference: warm target 1.72 dB, bright 2.46 dB, a 50/50 blend 0.80 dB, so a 3 dB budget binds only when user preference is stacked on top.

**Step 5: parametric refit as the final rendering step.** `scipy.optimize.least_squares` over `[gain, log10 f, Q] × 5` with bounds (LSC 30 to 300 Hz, PK1 80 to 600, PK2 300 to 2000, PK3 1500 to 9000, HSC 4 to 16 kHz, Q 0.4 to 2.0), warm-started from the previous track's parameters. Residual RMSE **0.116 dB (bright), 0.161 dB (warm)**, about 30x below the 4.3 dB human noise floor. The 5-band form is a lossless *renderer*; it was only ever wrong as a *prediction target*.

For the measured-correction path specifically, a linear solve is enough and 10,000x cheaper: precompute the 256×8 basis of each filter's +1 dB response, precompute `A = [W@B ; sqrt(λ)I]`, and `np.linalg.lstsq`. Measured **162 us per solve** versus 1.2 to 2.8 s for full nonlinear optimization, for a weighted-residual difference of 0.09 dB at a 4 dB peak-to-peak target, far below audibility. Linearity error of the RBJ dB response in gain: 0.057 dB at 1 dB pk-pk, 0.114 at 2, 0.228 at 4, 0.458 at 8.

**Step 6: exact preamp and level normalization.** `Preamp = -(max over grid of the fitted cascade) - 1.0 dB`. Measured on the proposed targets: warm -3.49, bright -4.15, 50/50 blend -1.72, warm + user tilt -5.91 dB. Subtract `mean_f m(f)` from every stored descriptor curve at table-build time and record it as `level_offset_dB`. Honest cost to state up front: level-normalizing *raises* raw LSD against un-normalized human curves for bright (3.730 vs 3.558) because human curves genuinely carry level. In the level-normalized metric it wins decisively (2.888 vs 3.454 bright, 2.854 vs 3.466 warm). Pick the level-normalized metric, write down why, never mix the two.

**Step 7: vendor the renderer.** Copy `C:\Research\SonicEQ\pipeline\03_render.py` into the app as the one source of truth. It is stdlib-only (cmath/math), implements the RBJ cookbook exactly, evaluates H(z) on the unit circle with no FFT approximation, and carries a six-test ALL PASS receipt (`results/stats/render_validation.txt`: FLAT max |dB| 0.000000; +6 dB @ 1 kHz reads 5.9967; inverse cancellation residual 4.16e-15 dB). Its `SHELF_Q = 0.71` and `fs = 48000` already match what the app writes for LSC/HSC. Wire it to four call sites: preamp, budget projection, refit objective, and the UI plot endpoint (serve the rendered 256 points rather than reimplementing filter math in JS).

One caveat to carry in code: LSD is **unweighted** RMSE on a log grid, so 20 to 40 Hz counts as much as 2 to 4 kHz. The ERB/Bark-weighted variant named at `EXPERIMENTAL_DESIGN.md:164-165` was never run; there is no receipt for it in `results/stats/`. Every dB number in this corpus is perceptually unweighted. Do not inherit that in the product: weight W at 1.0 in 40 Hz to 16 kHz, 0.25 below 40 Hz, 0.35 above 16 kHz, times `1/sqrt(1+(200/f)²)`.

**Protocol guard:** `data/splits/eval_set.jsonl` is read by `06_metrics.py` and nothing else. Any app-side scoring must fit and evaluate on `train.jsonl` or the raw CSV only, or it burns the paper's frozen test set.

### 5.4 The optional layer: CLAP as a within-track differential critic

Not required, and explicitly **not** on the EQ decision path. But it was measured and it works in one specific form.

Zero-shot CLAP over the SAFE vocabulary fails as the research proposed it: across 30 level-matched cells, the raw cosine for "warm" spans 0.2019 across content but only 0.0160 across EQ, a 12.6x content dominance (bright 7.2x, muddy 15.3x, airy 10.8x). Absolute CLAP tells you the instrument, not the tone.

Differencing two curves on the **same track** cancels content exactly. On a monotone shelf-gain ladder (-9 to +9 dB, RMS-matched): Spearman **rho = -1.000** on both music contents for high-shelf vs the warm-bright axis, **+1.000** for a 300 Hz Q1.2 peak, 12 of 14 expected-sign checks correct. Report `delta_k(c) = axis_k(c) - axis_k(unprocessed)`.

Recipe, all measured: `laion/clap-htsat-unfused` via `transformers` native `ClapModel` (transformers 5.14.1 has it; do **not** `pip install laion_clap`). Unwrap `.pooler_output`; the kwarg is `audio=`, not `audios=`. Paired-antonym engineering-description prompts, never bare adjectives (worth ~7x on the dominance ratio). **Feed exactly 480,000 samples**: `ClapFeatureExtractor` defaults to `truncation='rand_trunc'`, so anything longer is randomly cropped and two candidate curves get compared on two different 10-second windows. Ship the audio tower only: 28.2M of 153.5M params, **56.6 MB fp16**, 0.36 GB VRAM, 92 ms/clip on the 5060 Ti, 430 ms/clip on CPU at 4 threads (8 threads is slower). Precompute the text embeddings at build time.

Reject `laion/larger_clap_music_and_speech`: it is **directionally inverted** on this task (a +10 dB low-shelf / -10 dB high-shelf treatment lowers its warm-minus-bright margin on all three contents), 2x slower, and 744 MB.

---

## 6. Dropping Spotify

### 6.1 SMTC verdict: verified working, and the research document's code is wrong

Two agents independently ran the spike that `APP_PIPELINE_BRAINSTORM.md:151-160` left unchecked.

- **`winsdk` is dead and will not install here.** Last PyPI release 1.0.0b10 (2023-08-11), wheels stop at cp311, and `pip index versions winsdk` returns "No matching distribution found" on CPython 3.14.5 / win_amd64. Following `APP_PIPELINE_BRAINSTORM.md:27-36` as written fails immediately.
- **The live projection works.** `winrt-Windows.Media.Control`, `winrt-Windows.Media`, `winrt-Windows.Foundation`, `winrt-Windows.Foundation.Collections`, `winrt-Windows.Storage.Streams`, all 3.2.1 with cp314 wheels, 2.3 MB total. Live read off Spotify.exe: title "My Dirty Desire", artist "Pale Jay", album "Bewilderment", playback status, position 0:00:24.577, thumbnail present.
- **Measured:** `request_async()` 449.9 ms cold; `try_get_media_properties_async()` 14.4 ms warm, ~350 ms cold. Events fire with **no message pump at all** (16 TimelinePropertiesChanged in 75 s of pure `asyncio.sleep`). `MediaPropertiesChanged` fires exactly **twice** per track change, so a 1.0 s debounce is mandatory. Notification lags true track start by 2 to 3 s, comparable to the current polling, so this is a dependency win, not a latency win.
- **Packaging trap:** `winrt-Windows.Media` is a *separate required* install. Without it `playback_info.playback_type` raises `AttributeError` while every other field works, failing late and confusingly. Namespace packages are not transitively pulled.
- **Threading:** callbacks arrive on a WinRT threadpool thread that is neither main nor the subscribing thread (measured: main 16676, subscriber 26972, callbacks 24856/552). Handlers must do only `loop.call_soon_threadsafe(...)`; every shared structure needs a lock.
- **Folklore killed:** dropping the Python reference to an event handler does **not** kill the subscription. Both dropped-ref and held-ref arms fired 12/12 after `gc.collect()`. PyWinRT holds a strong ref.

### 6.2 The migration is one line

`web_gui_app.py:530` (`track_info = spotify_oauth.get_current_track()`) is the **only** now-playing call. The consumed contract at `web_gui_app.py:612-618` is just `{track_uri, name, artist, album, image_url, is_playing, is_private_session}`. Because line 613 does `track_uri.split(":")[-1]`, returning `smtc:local:<sha1_16>` yields a stable track_id with zero other edits.

```python
track_uri = f"smtc:local:{sha1(norm(artist) + chr(31) + norm(title)).hexdigest()[:16]}"
```

A tested prototype exists at `...\scratchpad\smtc\smtc_source.py`. One teardown nit to fix when porting: `stop()` stops the loop under `run_until_complete` and raises "Event loop stopped before Future completed"; set a stop event and let `_main` return instead.

Keep a 5 s `asyncio.sleep` watchdog as a safety net for a player that dies without an event.

**Album art:** SMTC returns an `IRandomAccessStreamReference`, not a URL, so `image_url` breaks. Measured working: `stream = await ref.open_read_async(); reader = DataReader(stream.get_input_stream_at(0)); await reader.load_async(stream.size); reader.read_bytes(buf)`. Spotify returns PNG at 112 to 168 KB. Cache per `local_key` in a 32-entry LRU and serve `GET /api/art/<local_key>` with an ETag. Do the read on the adapter's own loop, never on the callback thread.

### 6.3 What deleting the OAuth stack buys for free

These are defects **of that subsystem** and they retire rather than needing individual repair: the refresh path that destroys `refresh_token` after ~7 s of failure (`service.py:251-263`); the callback server that blocks forever on `handle_request()` and permanently wedges the Connect button with port 8888 still bound (`:169-174`, `web_gui_app.py:1242-1263`); three `requests` calls with no timeout on the monitor thread (`:365, 368, 371`); unbounded 429 recursion (`:375-379`); the non-atomic concurrent `_save_tokens` torn write (`:115-133`); the PKCE-vs-client_secret inconsistency (`:183-189` vs `:221-226`); the missing OAuth `state` parameter (`:154-161`); the duplicate shadowed `get_queue` (`:412` and `:737`); a leftover DEBUG print (`:384-385`); and `SpotifyAPIClient`'s token that never refreshes so genre enrichment silently dies after 3600 s (`spotify_client.py:96-97`).

Onboarding drops from ~13 steps across 3 accounts to **zero** for now-playing. The private-session branch (`web_gui_app.py:595-610`) becomes dead code, because SMTC reports Spotify private sessions normally. That is a real user-facing win.

Sequence: Phase A ship a config flag `now_playing_source: smtc|spotify` with both live. Phase B move genre enrichment to MBID joins. Phase C delete.

### 6.4 Identification fallback ladder, honestly ranked

| rung | method | offline | verdict |
|---|---|---|---|
| 0 | `local_key` cache | yes | sub-ms, covers repeat plays, which for a personal EQ app is most plays |
| 1 | SMTC artist + title | yes | **covers ~90%.** Spotify desktop populates title/artist/album correctly. `genres` is empty and `album_track_count` is 0. |
| 2 | Title normalizer for browser AUMIDs | yes | ~60 lines. Strip "(Official Video)", "[HD]", "(Lyrics)", " - Topic", trailing " - YouTube". Split on first " - ". Gate on `aumid` in the browser set and on `playback_type == Music` so podcasts and videos are skipped rather than EQ'd. |
| 3 | Local canonical-MusicBrainz SQLite | yes | **opt-in only, see below** |
| 4 | MusicBrainz web service, 1 req/s | no | **the right default** |
| 5 | Start-aligned Chromaprint / AcoustID | no | last rung, minority of sessions |
| 6 | Give up, emit FLAT, say so | yes | a first-class state, not an error |

**The local MusicBrainz mirror: measured, and it is bigger than anyone assumed.** HTTP HEAD confirms the compressed canonical dump at exactly 2,320,377,487 bytes. The zstd frame stores no content size and there is no seek table, so row counts genuinely could not be read from metadata. A 40 MB ranged GET plus prefix decompression yields the exact tar member header: `canonical_musicbrainz_data.csv = 7,519,259,059 bytes` (7.52 GB), full tar ~10.2 GB at the measured 4.3812x ratio. Mean row width over 815,149 real parsed rows is 225.4 bytes, giving **~33.4M rows, not the ~23M assumed** (cross-checked: MusicBrainz reports 39,605,986 recordings, and the canonical dump is the on-release subset, so 84% is coherent). Real SQLite builds from those rows, projected:

| schema | bytes/row | projected |
|---|---|---|
| all 10 columns + index | 268.70 | **8.97 GB** |
| lean TEXT PK, mbid BLOB16, score, WITHOUT ROWID | 56.75 | **1.90 GB** |
| lean TEXT + index | 99.37 | 3.32 GB |
| 64-bit hash PK, no readable text at all | 30.16 | 1.01 GB |

No schema preserving the artist/title strings lands under ~1.9 GB. Dedup does not help (distinct/total = 1.0000). Build time is a non-issue (41 s decompress at 246 MB/s, ~2 min insert, 5 to 15 min index).

**Therefore: default to the MusicBrainz web service with an MBID-keyed SQLite cache.** Its 1 req/s limit is entirely adequate for one lookup per track change. Offer the ~1.9 GB mirror as a genuine power-user opt-in stated at its real size with a progress bar. And correct the framing: "offline-first" cannot be guaranteed for arbitrary tracks at any acceptable disk cost. What **is** achievable offline is a warm cache of everything already played, which for a personal EQ app converges fast and is the honest promise.

**Negative caching is the piece the research omitted and the app needs most.** `next_retry_at = now + min(30 days, 2^attempts hours)`, or an unresolvable track re-queries MusicBrainz on every play.

**Correction to R4 on fingerprinting.** `APP_PIPELINE_BRAINSTORM.md:48-52` says fingerprinting "works on ~15-30 s excerpts". That is wrong for mid-track loopback. Chromaprint's own README scopes it to full-file identification and long-stream monitoring, calls itself "not a general purpose audio fingerprinting solution", analyses roughly the first two minutes, and AcoustID states it cannot identify short snippets. The index is aligned to file start, so a capture at 2:30 will not match. This is not Shazam; landmark hashing handles arbitrary offsets, Chromaprint does not. The saving grace is that SMTC gives a track-change event, which is exactly what makes **start-aligned** capture possible. Ship the prebuilt `chromaprint-fpcalc-1.6.0-windows-x86_64.zip` (1.73 MB) as a subprocess to keep LGPL at arm's length. Note the AcoustID web service is free for non-commercial use only.

### 6.5 The "lightweight local model reads search results" idea: no, and it is not close

Four reasons, one decisive.

1. **It solves a non-problem.** SMTC already returns clean artist/title/album for Spotify desktop. Measured, on this machine, today.
2. **It does not solve the real problem.** Where identity genuinely fails, a one-hour DJ mix, no amount of search-result reading tells you what is playing at minute 34. Only fingerprinting can.
3. **It inverts U4.** It is *more* network-dependent than what it replaces, violating `APP_PIPELINE_BRAINSTORM.md:147` ("every online box optional"), adds seconds of scraping latency, and depends on SERP HTML that breaks without notice.
4. **The research already ran this experiment.** Arm C, an LLM given exemplars in context, is the worst tail arm at median LSD 6.285; FLAT beats it 67% head-to-head surviving FDR at q=1.837e-03. And C_AGG proved models simply relay their context: median context-pull +1.00 for all six models, cloud dispersion **0.000 dB**. This is a documented negative result, not an untried idea.

Build the 60-line title normalizer instead. If you want a local model budget, spend it on CLAP, which is local, offline, needs no scraping, and targets a gap that actually exists.

**Related: delete the existing LLM engine.** It is in the worst available configuration. Given a centroid it is a no-op relay with added latency; given exemplars it gets significantly worse; given nothing it emits textbook consensus at 10% of human dispersion. It also asks for gains at 60/250/1000/4000/12000 Hz (`web_gui_app.py:418-422`) which are applied at 120 Hz **as a shelf**, 250, 1000, 3500, 10000 (`:757-763`), so it is evaluated against a filter set it was never told about. And one transient failure permanently flips the user's engine selection globally (`:776-779`), while `provider: local` silently falls back to Google Gemini on a 10 s timeout, uploading the user's track and artist with no consent prompt (`src/utils/llm_client.py:150, 156-192`).

If an LLM is kept anywhere, restrict it to **one offline job with human review**: proposing polarity and magnitude labels for new lexicon terms. That use is not contradicted by the research. The memorization probe (verbatim recall 0/6, statistical recall 0/6) shows models encode written doctrine rather than the behavioral corpus, and written doctrine is exactly the right authority for "is boxy a defect word" while being exactly the wrong authority for "how many dB".

---

## 7. Product and experience

### 7.1 Output stage: stop overwriting a text file, start configuring a driver

**Two corrections to earlier findings.** (a) The app never needed admin: `Get-Acl 'C:\Program Files\EqualizerAPO\config'` shows an explicit `BUILTIN\Users : FullControl` ACE that the APO installer grants. Admin is required only to *install* APO. The banner at `templates/index.html:25` and `static/app.js:328` telling users to run `launch_isolated_gui.bat` as Administrator is wrong twice: wrong file (it does not exist in the repo) and wrong diagnosis. (b) APO already crossfades, so there is no click.

**Recommended: a registry control plane.** `readRegDWORD(key, value)` calls `engine->watchRegistryKey(key)` (`parser/RegistryFunctions.cpp`), and the notification thread arms `RegNotifyChangeKeyValue(..., REG_NOTIFY_CHANGE_LAST_SET, ...)` on every such key (`FilterEngine.cpp:563-569`). Combined with backtick inline expressions this gives a static config.txt whose gains are read from HKCU, driven by unelevated `RegSetValueEx`, with no file writes on the hot path ever.

```
Eval: K = "HKEY_CURRENT_USER\\Software\\SonicVector"
If: readRegDWORD(K,"enabled") == 1
Device: `readRegString(K,"device")`
Stage: post-mix
Preamp: `readRegDWORD(K,"preamp_cdb")/100` dB
Filter 1: ON LSC Fc `readRegDWORD(K,"f0")` Hz Gain `readRegDWORD(K,"g0_cdb")/100` dB Q `readRegDWORD(K,"q0_m")/1000`
...
EndIf:
```

Store every scalar as a signed integer in centi-units (`readRegDWORD` returns `(int)value`, so negative dB works via two's complement). Python side needs the `ctypes.c_uint32(...).value` round-trip because `winreg` rejects negative ints for `REG_DWORD`.

**Spike this before building on it.** The mechanism is verified by source reading of Equalizer APO 1.4.2 only, never executed. Confirm backticks are accepted inside a `Filter` Gain field on 1.4.2. **Fallback if the spike fails:** `os.replace()` of an `Include:`d file inside the config directory, which is atomic and *is* detected (the watch mask includes `FILE_NOTIFY_CHANGE_FILE_NAME` with `bWatchSubtree=true`). The included file must live under `ConfigPath` or the watcher never sees it.

**Capabilities the app currently ignores entirely:** `Device:` (scope the EQ to headphones only, which is the whole headphones-vs-speakers problem), `Stage:` (`pre-mix`/`post-mix`), `If:/Eval:` (A/B arms in one file), `Filter: ON IIR Order 2 Coefficients b0 b1 b2 a0 a1 a2`, `Copy:`, `Convolution:`, `LoudnessCorrection:`.

**Scope the EQ.** Today it applies to games, Discord, Zoom, YouTube and Windows notification sounds with a curve chosen for a Last.fm-tagged song, and it persists after exit. Emit `Device:` for the calibrated endpoint and wrap everything in `If: readRegDWORD(K,"enabled") == 1`, set to 0 whenever the tracked player is not playing. This is free at runtime: `FilterConfiguration::isEmpty()` returns `filterCount == 0` and `FilterEngine.cpp:376` then skips de-interleaving entirely.

**Emit IIR coefficients.** `IIRFilterFactory.cpp` parses exactly `(order+1)*2` numbers, b's first. The sign convention is verified in `IIRFilter.cpp`'s constructor (`a[i] = -coefficients[...]/a0`), which is standard: scipy `sos` rows drop in unchanged, in double precision. Design one `design(bands) -> sos` in Python, serialize it, and evaluate the same `sos` for the UI plot. Then the plot, the preamp math, and the runtime are provably the same filter.

### 7.2 Transitions

Track change: interpolate over ~300 ms as 15 cosine-eased waypoints written 20 ms apart, `g_k = g_start + (g_end - g_start)·0.5(1 - cos(πk/N))`. A/B toggles and slider drags: one step.

**20 ms is a hard floor, not taste.** The notification thread blocks on `loadSemaphore` before each load and it is released only when a transition completes in the audio callback, so closer waypoints queue rather than land. Cap step size at 0.5 dB per band per waypoint (below the level JND). One caveat: reloaded filters get `initialize()` with zeroed delay lines, so each waypoint restarts a settling transient. For a 120 Hz Q=0.71 shelf the pole radius is 0.989, decaying to 1% in ~8.6 ms, which the raised-cosine fade covers almost exactly. Below ~60 Hz it does not, so keep the low shelf corner at or above 60 Hz or use 4 to 6 widely spaced waypoints when the sub-bass delta is large.

Run the ramp on a dedicated timer thread owning the commit lock, cancellable so a skip mid-ramp retargets rather than queues.

### 7.3 Settling policy: measure continuously, decide once

Four states. `SILENT` (no non-silent packets for 3 s, hold and do not write) to `ACQUIRING` (0 to 25 s, keep the previous curve or flat, accumulate the Welch estimate, show a progress affordance) to `SETTLED` (write once, freeze for the track) to `TRANSITION`.

Three arguments for settle-once: audible EQ motion reads as a fault; the sparse intro is *supposed* to sound sparse and a per-moment corrector will "fix" the composer; and decisively, you cannot A/B a moving target, so every vote collected under a time-varying curve is uninterpretable.

Exactly three legitimate mid-track moves: a Windows volume change (extrinsic, expected, trigger on `IAudioEndpointVolumeCallback`), an output device change, and a slow safety limiter (per-band leaky integrator, <= 0.5 dB/s slew, ±1.5 dB total authority, active only to pull back a band driving true peak over the ceiling). Hard rule: **no parameter changes while an A/B vote is pending.**

Note this inverts current behavior, which writes the new curve 1.5 to 4+ s into a track: simultaneously too early to have measured anything, and exactly when the listener is most attentive.

### 7.4 Headroom and safety

- Preamp from the realized cascade plus 1.0 dB margin (Section 2, F-3).
- Budget: per-band |g| <= 6 dB hard; realized cascade peak-to-peak <= 9 dB over 30 Hz to 16 kHz; never correct below 35 Hz or above 14 kHz; total boost capped so `Preamp >= -6 dB`.
- **Gates from measurement, not targets:** if PLR < 8 dB the master is already limited to the ceiling, so constrain to cut-only (all gains <= 0). If low-band stereo correlation r < 0.5, cap low-shelf boost at +1.5 dB; decorrelated bass is where summed headroom explodes and where a boost destroys mono compatibility. Detect the codec cut-off (first band more than 12 dB below the 8 to 10 kHz average) and zero the correction target above it, or a 16 kHz Ogg brick wall reads as -30 dB and demands a +6 dB shelf that boosts only hiss.
- Multiply final gains by `min(1, integrated_seconds/25)` so correction ramps in with evidence.
- Flat on every exit path, plus a Run-key entry that sets `enabled=0` at logon as belt and braces.
- A permanent visible Bypass in the UI and the tray menu.

State the honest cost: this rule costs output level (a +6 dB curve means a -7 dB preamp), which is why the budget must be low and why the honest framing of this product is **tonal shaping, not loudness**.

### 7.5 Packaging

`pystray` tray icon owning process lifetime. Serve with **waitress**, not `app.run()`: the werkzeug dev server is not a shipping target, and it is threaded by default, which is what creates the concurrent-writer problem in the first place. Bind to port 0, read the assigned port, require a per-launch token so a page from another origin cannot drive the local API. `pywebview` as the default window with "open in browser" as fallback.

**Nuitka standalone, not PyInstaller onefile.** Onefile extracts the runtime to `%TEMP%` on every launch: slow start, and a well-known antivirus false-positive pattern for an app that also touches audio configuration. Inno Setup chains `EqualizerAPO64-1.4.2.exe /S`. Signing: Azure Artifact Signing at $9.99/month Basic, now open to individual developers, versus $300 to $500/year plus a hardware token for a traditional EV cert. Unsigned, SmartScreen blocks it, and "click More info then Run anyway" is not a first-run experience.

The tray app never needs elevation.

### 7.6 Onboarding

Once identification moves to SMTC, the only hard dependency is APO, and APO's cost is almost entirely the manual DeviceSelector step and the reboot. Both are avoidable.

- **Register APO onto the endpoint programmatically** from the elevated installer: DeviceSelector writes the APO CLSIDs into `HKLM\...\MMDevices\Audio\Render\{guid}\FxProperties` (both paths and the PKEY GUIDs `{d04e05a6-594b-4fb6-a80d-01af5eed7d1d}` and `{b3f8fa53-0004-438e-9003-51a46e139bfc}` appear in its string table).
- **Skip the reboot.** The reboot exists so the audio service re-reads the registration. `Restart-Service audiosrv -Force` achieves it in about two seconds. Warn that audio will drop briefly and let the user decline into a reboot.
- **Verify, do not assume.** APO ships a self-test (the strings `EqualizerAPODeviceTest` and `DeviceTestPipeName` in both the DLL and DeviceSelector.exe are a named-pipe handshake). Fall back to playing a 1 kHz tone with a -20 dB notch armed and confirming attenuation via loopback.
- Then: headphones or speakers (sets the per-endpoint profile), one 20-second excerpt, three forced-choice A/B trials to seed the prior. That doubles as the moment the user first hears the product do anything.

### 7.7 Error taxonomy and the A/B affordance

Delete the `display:none` first. Then define states explicitly:

- **OUTPUT**: `apo_missing | apo_not_on_this_endpoint | endpoint_changed | engaged | bypassed_by_user | bypassed_no_playback | stalled`. That last is the `loadSemaphore` defect: say "paused, EQ will apply when playback resumes" rather than reporting success.
- **IDENTIFY**: `no_player | player_running_not_playing | identified | unidentified`.
- **TAGS**: `ok | artist_level_fallback | rate_limited | unavailable`. The artist-level case is currently invisible and is frequently the actual behavior.
- **CURVE**: `corpus_centroid | flat_insufficient_evidence | user_profile | manual`. **Distinguish "flat because I know nothing" from "flat because flat is right."** Per the research that is the common case, not the edge case, and it is exactly the moment a listener's input is worth most.

Fix in the same pass: the banner dismiss button (`index.html:27` adds `hidden`, and `app.js:324` removes it within 1.5 s), the two blocking `alert()` calls (`app.js:654, 737`), the `.live-indicator-dot` that blinks identically whether the server is streaming or dead (`style.css:616-629`, zero JS binding), and the sliders that snap back mid-drag because a poll response overwrites them before the debounced POST lands (`app.js:297, 386-402, 750` vs `web_gui_app.py:1103-1105`; fix with a pointerdown/pointerup interaction guard and a monotonic state version, not a shorter interval).

Also fix the UI's false claims: `index.html:49` "Semantic Text Similarity EQ", `:58` "Vector Similarity Centroids", `:317` "Semantic Profile Vectors", `README.md:12/92/116` "crowdsourced SAFE Equaliser Database", and the `mixing_reason` templates at `web_gui_app.py:797, 998, 1182`. Drop "muddy" from the displayed profile bars or give it corrective treatment; `app.js:470-485` currently renders it as a glowing achievement bar with a percentage.

**Positioning that survives the evidence:** *"Sonic Vector learns the EQ you prefer and applies it automatically while you listen. It starts from what mastering engineers actually did, not from what the internet says they should, and it proves the difference with an instant, loudness-matched A/B."* The claims that survive: on descriptors with real corpus support a data-derived centroid beats flat (warm 3.469 vs 4.124 dB, bright 3.550 vs 4.816 dB); human practice is a 3 to 4 school mixture so personalization has genuine variance to capture; person-level covariates buy 1.3 to 2.9 dB while audio content buys ~0.

---

## 8. Sequenced roadmap

### Phase 1: Make it correct and honest (3 to 4 days)

**Goal:** the app stops producing wrong output, stops clipping, stops persisting after exit, and the listener can hear what it is doing.

**Deliverables**
1. Vendor `03_render.py` as `src/dsp/render.py`; run its six-test self-check at startup and refuse to start on failure.
2. One `commit_state()` behind one lock, tmp + fsync + `os.replace`. Delete the four duplicated overlay blocks and the second APO writer in `embed_song_predictor.py:180-209`.
3. Exact preamp from the realized cascade plus 1.0 dB, plus the peak/L1 budget with visible "limited to fit headroom".
4. Non-finite rejection and preamp clamping at `/api/update_eq`.
5. `atexit` + `SIGINT/SIGBREAK/SIGTERM` + `SetConsoleCtrlHandler` flat-write; flat-write at startup.
6. APO endpoint registration check with a real UI state.
7. Delete `save_track_eq_to_db` from the track-change path (F-7) and the recall short-circuit.
8. Carry interpolated Fc/Q through, or set the low shelf to the corpus value; either way the two code paths must agree.
9. Remove muddy from the additive blend.
10. Un-hide `#insightsCard`; replace the plot's invented magnitudes with server-rendered points including preamp; escape tag badges.
11. Hold-to-compare bypass button (analytic level match for now, labelled `assumed`).
12. `git rm` the mock CSV and DB; make preprocessing fail loudly; fix the Last.fm key and HTTPS.

**Exit criterion:** across 20 sampled tag sets and all 5 styles, the measured composite response peak is <= 0.0 dBFS with the written preamp applied; killing the process by any means leaves `config.txt` flat; the user can hold a key and hear processed versus unprocessed on demand; the UI explains which curve it chose and why.

### Phase 2: See and hear the real thing (1.5 to 2 weeks)

**Goal:** drop Spotify, start measuring the actual audio, and make comparison valid.

**Deliverables**
1. `SmtcNowPlaying` adapter behind `now_playing_source: smtc|spotify`, then flip the default, then delete `src/spotify/`.
2. Local art endpoint; title normalizer; `playback_type` gating so podcasts and videos are skipped.
3. Identity spine SQLite with negative caching; MusicBrainz WS at 1 req/s; MBID-keyed tag joins with TTL.
4. `proc-tap` per-process loopback capture on its own thread with a 60 s ring buffer (23 MB), `blocksize >= 16384`.
5. Measurement module: Welch 25-band level-removed spectrum (`nperseg=8192`, median averaging), BS.1770 integrated LUFS, true peak on neighbourhoods only, PLR/PSR, stereo correlation, codec cutoff.
6. **Measured** level match: closed-form `dLUFS` from the measured spectrum and the exact cascade, verified empirically by measuring the same 20 s passage with EQ on versus bypassed and asserting |delta| < 0.3 LU. Surface as a self-test result ("level match verified: 0.1 LU").
7. Settling state machine with 20 ms ramp waypoints.

**Exit criterion:** the app identifies and EQs a track with Spotify OAuth uninstalled and no Spotify developer account present; a measured A/B pair differs by < 0.3 LU on real program material for the `punchy+bass_boost` case that previously spread 7.1 dB; the EQ does not engage on a Discord call.

### Phase 3: The preference system (3 to 4 weeks)

**Goal:** the app learns you, and can show you that it did.

**Deliverables**
1. `preference/basis.py` (4-dim basis, re-derived on the corrected PCA, with the >10 kHz worst-case residual documented), `preference/model.py` (ADF probit), `data/preferences.db`.
2. Implicit slider capture wired first, with the 1.5 s debounce and the hard gates.
3. A/B duel surface: hold-to-compare, blind mode, randomized labels per duel, tie option, `n_toggles`/`decision_ms` recorded, thumb icon as the entry point.
4. Named-axis fallback buttons.
5. Hierarchy with device at the top; device picker pill with manual override; per-endpoint profiles.
6. Cold start: flattened school-mixture prior with particles, trust region, drift EWMA, scoped reset and pin.
7. Dither injection on offered coefficient vectors so the design matrix reaches full rank in all four directions.
8. New descriptor prior: real-SAFE curve distributions, signed lexicon with abstain mass, hard n-gate at n < 5, shrinkage target literally 0 dB, curve-space composition under the deviation budget, parametric refit.
9. Within-app blind holdout with SPRT and all three contrasts plus the identical-arms sanity check.
10. Profile export/import with the human-readable summary line.

**Exit criterion:** a real user reaches `trace(Σ)` below the convergence floor within 15 combined signals on one device; the blind holdout runs end to end and returns a verdict with a CI; the identical-arms sanity arm scores within [0.35, 0.65] at n=20 (if it does not, the level matching is leaking a cue and Phase 2 is not done).

### Phase 4: Ship it (3 to 4 weeks)

**Goal:** something you can hand to another person.

**Deliverables**
1. Registry control plane (after its spike) or the `Include:` fallback; `Device:` scoping; `If/Else` A/B arms in one config; `enabled` gate driven by playback state.
2. Tray app, waitress, port 0, per-launch token, `pywebview`.
3. Nuitka standalone, Inno Setup chaining the APO installer, Azure Artifact Signing.
4. First-run wizard: programmatic endpoint registration, `Restart-Service audiosrv` instead of reboot, functional verification via notch tone plus loopback, headphones/speakers question, 60-second quick tune.
5. Full error taxonomy surface; accessibility pass (labels on the ten unlabelled sliders, `:focus-visible`, `prefers-reduced-motion`, `prefers-color-scheme`, header `flex-wrap`); self-host the two fonts; add the missing favicon.
6. Optional: local MB mirror as an explicit opt-in at its real ~1.9 GB; CLAP differential critic as a UI explanation layer.

**Exit criterion:** a clean Windows 11 machine goes from downloaded installer to audibly EQ'd music in under 3 minutes with no developer accounts, no manual registry editing, and no reboot; SmartScreen does not warn.

---

## 9. Open questions for you

1. **Cut-only or boost-allowed by default?** A +6 dB boost curve costs -7 dB of preamp and therefore -7 dB of output level. Cut-only preserves headroom and never clips but makes everything quieter in a different way and cannot add air. Which do you want as the shipped default, and do you want the PLR gate to switch it automatically on already-limited masters?

2. **Should the EQ ever touch non-music audio?** The `enabled` gate can restrict processing to the tracked player only. That is almost certainly right, but it means your YouTube, games, and calls get nothing, and some people want a permanent house curve. Player-only, always-on, or per-endpoint choice?

3. **How much are you willing to be asked?** My recommendation is duels default **off**, learning silently from slider drags, offering a duel only on opt-in or on the thumb. The alternative converges roughly 35% faster but interrupts music. Where do you want that dial?

4. **Do you want the app to be able to tell you flat wins?** The blind holdout can return "for you, on your library, flat beat the personalized curve." That is a defensible and possibly likely outcome given `method_selection_results.txt`. Shipping it is the honest choice and the trust-building one. Shipping it also means the product can publicly fail. Confirm you want that.

5. **Personal tool or shippable product?** Phase 4 is 3 to 4 weeks and about $120/year of signing, and it changes nothing about how the app sounds for you. If this is yours alone, Phases 1 to 3 plus a shortcut is the whole job, and I would skip the installer, the signing, and the onboarding wizard entirely.

6. **Registry control plane: worth a day of spike?** It retires six defects at the root and makes the A/B switch a single DWORD write, but it is verified only by reading Equalizer APO's source and could fail on a backtick-inside-a-Filter-field parsing quirk. The `Include:` + `os.replace` fallback is fully verified and about 80% as good. Do I spend a day proving the better one, or take the safe one now?

---

## 10. Explicitly rejected

Each of these sounds good and the evidence kills it. Do not re-propose them.

**Modeling and data**

- **Training any model to predict the human EQ curve from audio.** Out-of-fold R² = +0.051 (warm) / -0.061 (bright) against permutation nulls of -0.119 / -0.115, with a target-convergence ratio of 1.05 / 1.24 meaning humans *diverge*. The feature set already contained the bark-band spectrum, so "a richer embedding fixes it" is not a live hypothesis. (`audio_conditioning_results.txt:6-19`)
- **Shipping Arm E** (frozen MiniLM to 64-unit MLP), which `TAG_ACQUISITION_2026.md:54-58` and `APP_PIPELINE_BRAINSTORM.md:140-141` both nominate as the app's local predictor. It is the one arm with a surviving **negative** result: FLAT beats it 70% head-to-head, q=4.346e-04. It was trained on 817 rows spanning exactly two distinct descriptors and can only learn two conditional means. The research's own recommendation is contradicted by the research's own results.
- **Assuming the LoRA arms proved anything.** There is no `arm_d.jsonl` and no `arm_f.jsonl`. `d_seed0` was a 24-example, 3-step smoke run; the 4 surviving generations rail three bands simultaneously at the +12 dB corpus max. `f_seed0` peaked at 32.41 GB VRAM on a 17.1 GB card. `d_seed1` hung at step 30 and `models/lora/d_seed1` is an empty directory. The fine-tuning axis is **untested**, not won.
- **Sentence-embedding the tags.** kNN sign accuracy 52.6% / 55.3% / 50.0% at k=1/5/7 on a labelled 38-word audio lexicon. Antonyms are nearest neighbours (thick to thin cos 0.816). Would ship confident wrong-sign EQ.
- **Softmax over the descriptor vocabulary with a temperature.** With only two anchors above the floor and every tag scoring 0.277 ± 0.107 to both, temperature only controls how fast you collapse onto a coin flip. At T=0.05 "crisp" lands 54% warm and "mellow" 63% bright, both backwards.
- **Filtering SAFE to "instrument == Mix".** That taxonomy exists only in the mock generator (`preprocess_safe.py:79`). The real column has 119 distinct values, 724 blanks (48%), and is contaminated with stimulus labels (guitar 123, jazz1 34, metal1 34), which `DECISION_LOG.md:236-237` explicitly calls stimulus-label noise. F5's substance stands and is better supported by the guitar-versus-mix dispersion contrast.
- **`preprocess_safe.py`'s ±5 dB outlier rule.** Keeps 14% of real rows, zero for muddy/airy/boomy/deep, then silently falls back to the unfiltered mean for those, mixing two estimators in one table.
- **Per-bin, per-band pooled, and 3-PC empirical-Bayes shrinkage variants.** All measured within 0.01 to 0.08 dB of a single global scalar and often worse. Complexity with no benefit. (And per Section 5.3, ship the n-gate rather than any EB schedule.)
- **MERT as a feature source for a trained head.** No text tower means no zero-shot, and the supervision to train a head does not exist: two descriptors above the floor, no track-level gold anywhere. Revisit only after ~10³ level-matched pairwise votes exist, at which point the target is a ranking loss, not curve regression.
- **`essentia` / `essentia-tensorflow`.** `pip index versions` returns "No matching distribution found" on Python 3.14.5 / win_amd64. It has never shipped official Windows wheels. Putting it in `requirements.txt` fails for every Windows user.
- **Using the SAFE audio-feature CSV as a source of playback target curves.** Stem-level pre/post pairs from DAW sessions, not finished masters.

**Signal path**

- **Per-moment dynamic or multiband adaptive EQ.** Audible EQ motion reads as a fault; the arrangement's dynamics are intentional; and you cannot A/B a moving target, so every vote collected under one is uninterpretable.
- **Applying a Harman in-room or over-ear target to the program spectrum.** Category error. Harman curves specify the *transducer plus room* transfer function at the ear. Total-at-ear = program × transducer, so a Harman-shaped correction of the *program* is meaningless without the transducer response. Legitimate only as a separate static per-device stage if the user names their headphone.
- **Closed-loop adaptive correction** (measure post-EQ, adjust, re-measure). Unnecessary: open-loop de-EQ inversion is algebraically exact. Measured recovery error through a cascade spanning -2.40 to +7.15 dB: mean -0.0003 dB, RMS 0.0093 dB. There is no loop to destabilize.
- **Assuming from documentation whether WASAPI loopback taps before or after the APO chain.** It varies by Windows build, endpoint, and whether APO installed as LFX/GFX or SFX/MFX/EFX. Replace with a 20 s runtime probe: measured per-band Welch SD is 0.230 dB at a 10 s window, so a +6 dB probe has 26:1 SNR.
- **The nonlinear parametric fit online.** 1.2 to 2.8 s versus 162 us for the precomputed linear solve, for 0.09 dB of residual improvement at a 4 dB target. Offline refinement only.
- **APO's `GraphicEQ` as the primary emission format.** FFT-based minimum-phase FIR: more CPU, added latency, and its response is defined by interpolation between nodes on a linear grid, so making the plot and preamp agree exactly is harder. Keep it as an option for the measured-correction path only.
- **APO's built-in `LoudnessCorrection:` for the A/B level match.** It is an ISO-226 equal-loudness compensator for quiet listening. It changes tone as well as level, which is exactly what must be held constant between arms. Good future feature, wrong tool here.
- **Continuous true-peak metering.** 1834 ms per 20 s of stereo, 9% of one core in realtime. Restrict to neighbourhoods above -3 dBFS.
- **Beat/tempo/onset analysis.** No bearing on tonal correction, and a librosa-class dependency.
- **Treating the per-band ±15 dB clamp as headroom protection.** It clamps each band before summation, so five bands can each pass at +15 dB, and it returns 15.0 for NaN.

**Identity and models**

- **`winsdk` for SMTC**, as prescribed by `APP_PIPELINE_BRAINSTORM.md:30-36`. No cp314 wheel exists. `pywinrt` does not resolve as a package name either.
- **A local LLM reading web search results to identify tracks.** See Section 6.5.
- **A full local MusicBrainz Postgres mirror.** `mbdump.tar.bz2` is 7.4 GB compressed and `mbdump-edit.tar.bz2` alone is 15 GB, plus a running Postgres. Even the canonical dump yields a 1.9 GB SQLite minimum for any schema that keeps the strings, so it cannot be a casual default download.
- **`laion/larger_clap_music_and_speech`.** Measured **directionally inverted** on the exact task: an unambiguously darker treatment lowers its warm-minus-bright margin on all three contents. Also 2x slower and 744 MB.
- **The `laion_clap` pip package.** `transformers` 5.14.1 ships native `ClapModel`; the wrapper adds nothing and carries torchlibrosa/librosa deps.
- **Bare-adjective and "This audio is X" CLAP prompts**, and **background-prompt normalization alone**, and **feeding 20 to 30 s excerpts** (random-cropped by `rand_trunc`, verified to produce different mel tensors on identical input), and **the mel-domain shortcut as the final scorer** (sign agreement only 3/5), and **shipping the CLAP text tower** (125.3M of 153.5M params for a fixed prompt list), and **fp16 for speed** (92.9 vs 91.9 ms, use it for VRAM only), and **8 CPU threads** (measured slower than 4).

**Product**

- **A VST plugin for APO to host.** APO 1.4 implements VST **2.4** only, and Steinberg withdrew VST2 SDK licensing in 2018. A legal blocker, not an engineering one.
- **A virtual audio device driver.** WHQL attestation, an EV cert, driver maintenance across Windows updates, and the user must manually switch default playback device and lose per-device behavior. Disproportionate when APO already gives a crossfading, double-precision, zero-added-latency chain for free.
- **Loopback capture plus re-render through a VST3 host.** Doubles latency, needs a virtual sink anyway, creates a feedback path. Loopback is a *measurement* tap, not a processing path.
- **Redirecting APO's `ConfigPath` to `%LOCALAPPDATA%`.** Unnecessary (`BUILTIN\Users` already has FullControl) and it breaks Peace, FxSound, and any other APO frontend.
- **Running the app elevated**, which `templates/index.html:25` currently instructs. Wrong diagnosis, and it means a Flask server plus a browser popup running as Administrator.
- **PyInstaller onefile.** `%TEMP%` extraction on every launch, a known AV false-positive pattern.
- **Building BALD / qEUBO / dueling-bandit active learning.** Measured worth: 1 vote out of 10, and zero by vote 40. Weeks of work for one click.
- **Bradley-Terry, Thurstone, GP preference learning, and contextual bandits** as the preference model. Monotone-utility models drive to the corner (which is what the LoRA arm did); GPs are O(N³) with no place for the prior and no interpretable output; bandits on spectral context regress on noise per R1.
- **Training on skips, replays, or session length.** Strictly worse than a unary thumb on the confound axis, and a clean unary already never converges under a 30% song-liking confound.
- **A bare thumbs-up as the measurement.** Keep it as the gesture, wire it to a duel. See 4.1.
- **Chasing the last milliseconds of A/B switch latency.** Measured 15 to 40 ms end to end, dominated by APO's own 10 ms coalescing and 10 ms crossfade, neither reachable from outside. Already below the perceptual threshold. The 7 dB loudness error is where the effort belongs.