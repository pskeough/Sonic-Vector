/**
 * Sonic Vector - frontend application engine and response plotter.
 *
 * Responsibility: Handles DOM binding, throttled REST communication, plotting of
 * the backend's realized frequency response, hold-to-compare bypass, output-stage
 * reporting (Equalizer APO reachability, preamp, headroom), live playback indication, state-based banner overlays and popup Spotify OAuth integration.
 *
 * The curve on screen is never computed in the browser. /api/status carries
 * output.response: the realized RBJ biquad cascade the backend wrote to
 * Equalizer APO, preamp already folded in. The plot and the emitted filter
 * therefore cannot drift apart, which the old client-side bell approximation
 * could not promise.
 */

// Global State holding active filter coefficients
let activeEq = {
    low_shelf_gain: 0.0,   low_shelf_freq: 120.0,
    first_band_gain: 0.0,  first_band_freq: 250.0,  first_band_q: 0.71,
    second_band_gain: 0.0, second_band_freq: 1000.0, second_band_q: 0.71,
    third_band_gain: 0.0,  third_band_freq: 3500.0, third_band_q: 0.71,
    high_shelf_gain: 0.0,  high_shelf_freq: 10000.0
};

let activeMix = {
    preamp_gain: 0.0,
    strength: 1.0,
    bass_boost: 0.0,
    vocal_clarity: 0.0,
    airiness: 0.0
};

// The emitted response, straight from the backend: [{f, db}, ...], 256 points,
// log-spaced 20 Hz .. 20 kHz, preamp included.
let responsePoints = [];

let outputState = {
    preamp_db: 0.0,
    limited: false,
    limit_scale: 1.0,
    bypassed: false
};

let appMode = "auto";
let updateTimer = null;
// True while /api/eq/redo is in flight, so the 1.5 s status poll cannot
// re-enable the button underneath the request.
let redoInFlight = false;

// Hold-to-compare state. `bypassDesired` is what the listener is asking for
// right now; `outputState.bypassed` is what the backend has confirmed.
let bypassDesired = false;
let bypassInFlight = false;
// Polled state is ignored briefly after a toggle: a status response that left
// the server before the toggle would otherwise flap the curve back.
let bypassSettleUntil = 0;

// Canvas elements
const canvas = document.getElementById("eqCanvas");
const ctx = canvas.getContext("2d");
const canvasContainer = document.getElementById("canvasContainer");

// Slider DOM handles
const sliders = {
    low_shelf: document.getElementById("slider_low_shelf"),
    mid_1: document.getElementById("slider_mid_1"),
    mid_2: document.getElementById("slider_mid_2"),
    mid_3: document.getElementById("slider_mid_3"),
    high_shelf: document.getElementById("slider_high_shelf")
};

const sliderVals = {
    low_shelf: document.getElementById("val_low_shelf"),
    mid_1: document.getElementById("val_mid_1"),
    mid_2: document.getElementById("val_mid_2"),
    mid_3: document.getElementById("val_mid_3"),
    high_shelf: document.getElementById("val_high_shelf")
};

// Mix Sliders handles
const mixSliders = {
    preamp: document.getElementById("slider_mix_preamp"),
    strength: document.getElementById("slider_mix_strength"),
    bass: document.getElementById("slider_mix_bass"),
    vocal: document.getElementById("slider_mix_vocal"),
    air: document.getElementById("slider_mix_air")
};

const mixVals = {
    preamp: document.getElementById("val_mix_preamp"),
    strength: document.getElementById("val_mix_strength"),
    bass: document.getElementById("val_mix_bass"),
    vocal: document.getElementById("val_mix_vocal"),
    air: document.getElementById("val_mix_air")
};

// Navigation / Header handles
const syncModeToggle = document.getElementById("syncModeToggle");
const resetEqBtn = document.getElementById("resetEqBtn");
const consoleModeTag = document.getElementById("consoleModeTag");
const aiEngineSelect = document.getElementById("aiEngineSelect");
const aiEngineOption = document.getElementById("aiEngineOption");
const soundStyleSelect = document.getElementById("soundStyleSelect");
const eqError = document.getElementById("eqError");

// Curve panel handles
const compareBtn = document.getElementById("compareBtn");
const bypassChip = document.getElementById("bypassChip");
const preampReadout = document.getElementById("preampReadout");
const limitReadout = document.getElementById("limitReadout");

// Banners handles
const adminWarning = document.getElementById("adminWarning");
const adminWarningTitle = document.getElementById("adminWarningTitle");
const adminWarningText = document.getElementById("adminWarningText");
const adminWarningClose = document.getElementById("adminWarningClose");
const apoStatusBanner = document.getElementById("apoStatusBanner");
const apoStatusDetail = document.getElementById("apoStatusDetail");
const apoRecheckBtn = document.getElementById("apoRecheckBtn");
const spotifyBanner = document.getElementById("spotifyBanner");
const spotifyBannerText = document.getElementById("spotifyBannerText");
const spotifyBannerClose = document.getElementById("spotifyBannerClose");
const connectSpotifyBtn = document.getElementById("connectSpotifyBtn");
const disconnectSpotifyBtn = document.getElementById("disconnectSpotifyBtn");
const spotifySetupBtn = document.getElementById("spotifySetupBtn");
const spotifySetupPanel = document.getElementById("spotifySetupPanel");
const spotifySetupCancel = document.getElementById("spotifySetupCancel");
const spotifySetupSave = document.getElementById("spotifySetupSave");
const spotifySetupMsg = document.getElementById("spotifySetupMsg");
const spotifyClientId = document.getElementById("spotifyClientId");
const spotifyClientSecret = document.getElementById("spotifyClientSecret");
const spotifyRedirectUri = document.getElementById("spotifyRedirectUri");
const redoMixBtn = document.getElementById("redoMixBtn");

// Playback details DOM
const trackTitle = document.getElementById("trackTitle");
const trackArtist = document.getElementById("trackArtist");
const trackAlbum = document.getElementById("trackAlbum");
const trackSourceTag = document.getElementById("trackSourceTag");
const trackArt = document.getElementById("trackArt");
const vinylSpindle = document.getElementById("vinylSpindle");
const liveDot = document.querySelector(".live-dot");

/**
 * The header dot is the only thing on the page that says whether audio is
 * actually moving. It used to be styled but never bound to anything, so it
 * looked identical whether the stream was live or the server was dead.
 */
function setLive(isLive) {
    if (liveDot) liveDot.classList.toggle("on", isLive);
    if (vinylSpindle) vinylSpindle.classList.toggle("playing", isLive);
}
const tagsList = document.getElementById("tagsList");
const weightsList = document.getElementById("weightsList");
const weightsCard = document.getElementById("weightsCard");
const mixingReason = document.getElementById("mixingReason");
const pipelineStatusList = document.getElementById("pipelineStatusList");

// Listener rating
const voteUpBtn = document.getElementById("voteUpBtn");
const voteDownBtn = document.getElementById("voteDownBtn");
const voteCount = document.getElementById("voteCount");
const voteNote = document.getElementById("voteNote");
let votedTrackKey = null;

// Search Form
const searchOverrideForm = document.getElementById("searchOverrideForm");
const searchQuery = document.getElementById("searchQuery");


// --- CANVAS INITIALIZATION & RESOLUTION FIX ---
function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawEQCurve();
}

window.addEventListener("resize", resizeCanvas);
setTimeout(resizeCanvas, 100);

// The card grows and shrinks with the banners above it, so track the container
// rather than just the window.
if (window.ResizeObserver && canvasContainer) {
    new ResizeObserver(resizeCanvas).observe(canvasContainer);
}


// --- PLOT GEOMETRY ---

// Symmetric about 0 dB, because the scope shows TONAL SHAPE: what the filters
// do to the balance, with 0 dB meaning "unchanged".
//
// It previously ran +6 to -24, which was lopsided and hard to read. That
// happened because the plotted trace had the output preamp folded into it, and
// the preamp is always negative (it buys headroom for the boosts), so the whole
// curve sat in the lower third of an axis stretched to fit it.
//
// The preamp is a level control, not a tone control, so it belongs in the
// PREAMP readout rather than in the shape. Every EQ plugin draws it this way.
// Subtracting it back out re-centres the trace on 0 dB and lets the axis be
// even. Curve plus PREAMP readout still describes exactly what was written to
// Equalizer APO.
const DB_MAX = 12;
const DB_MIN = -12;
const DB_GRID = [12, 6, 0, -6, -12];
const PLOT_PAD = { left: 46, right: 12, top: 12, bottom: 22 };

const FREQ_MIN = 20;
const FREQ_MAX = 20000;
const LOG_MIN = Math.log10(FREQ_MIN);
const LOG_MAX = Math.log10(FREQ_MAX);

/**
 * Log-spaced x offset (from the left edge of the plot area) for a frequency.
 */
function freqToX(freq, plotWidth) {
    const f = Math.min(FREQ_MAX, Math.max(FREQ_MIN, freq));
    return ((Math.log10(f) - LOG_MIN) / (LOG_MAX - LOG_MIN)) * plotWidth;
}

function dbToY(db, top, plotHeight) {
    const clamped = Math.min(DB_MAX, Math.max(DB_MIN, db));
    return top + ((DB_MAX - clamped) / (DB_MAX - DB_MIN)) * plotHeight;
}

function formatDb(value, digits) {
    const d = typeof digits === "number" ? digits : 2;
    const n = Number(value) || 0;
    return (n >= 0 ? "+" : "") + n.toFixed(d) + " dB";
}


// --- MAIN LOG-SCALE PLOTTER LOOP ---
/**
 * Plot colours are read from the stylesheet rather than hardcoded, so the
 * canvas and the CSS can never disagree about what the theme is.
 */
const THEME = {};

function readTheme() {
    const cs = getComputedStyle(document.documentElement);
    const get = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
    THEME.accent = get("--plot-trace", "#e08a63");
    THEME.warn = get("--plot-bypass", "#e9a13b");
    THEME.text2 = get("--plot-label-hi", "rgba(233,161,59,0.9)");
    THEME.text3 = get("--plot-label", "rgba(236,232,219,0.46)");
    THEME.plotBg = get("--plot-bg", "#1c1914");
    THEME.grid = get("--plot-grid", "rgba(236,232,219,0.075)");
    THEME.zeroLine = get("--plot-zero", "rgba(217,119,87,0.55)");
    THEME.curveFill = get("--plot-fill", "rgba(217,119,87,0.13)");
}

readTheme();

function drawEQCurve() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;

    // The card resizes when a banner appears above it. If the backing store has
    // drifted from the CSS box, fix it first: resizeCanvas() draws again with a
    // matching buffer, so this recurses exactly once.
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        resizeCanvas();
        return;
    }

    const left = PLOT_PAD.left;
    const top = PLOT_PAD.top;
    const plotW = Math.max(1, width - PLOT_PAD.left - PLOT_PAD.right);
    const plotH = Math.max(1, height - PLOT_PAD.top - PLOT_PAD.bottom);

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = THEME.plotBg;
    ctx.fillRect(0, 0, width, height);
    ctx.font = '9px "IBM Plex Mono", "Cascadia Mono", Consolas, monospace';
    ctx.lineWidth = 1;
    ctx.setLineDash([]);

    // 1. LOGARITHMIC FREQUENCY GRID
    const freqsToGrid = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
    ctx.strokeStyle = THEME.grid;
    ctx.textBaseline = "alphabetic";
    freqsToGrid.forEach(f => {
        const x = left + freqToX(f, plotW);
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, top + plotH);
        ctx.stroke();

        ctx.fillStyle = THEME.text3;
        ctx.textAlign = f === FREQ_MAX ? "right" : "left";
        const label = f >= 1000 ? (f / 1000) + "k" : String(f);
        ctx.fillText(label, f === FREQ_MAX ? x - 2 : x + 3, height - 8);
    });

    ctx.textAlign = "left";
    ctx.fillStyle = THEME.text3;
    ctx.fillText("Hz", left + 3, top + 10);

    // 2. DECIBEL GRID, 0 dB REFERENCE, AND Y AXIS LABEL
    const dbLines = DB_GRID;
    ctx.textAlign = "right";
    dbLines.forEach(db => {
        const y = dbToY(db, top, plotH);
        const isZero = db === 0;

        ctx.setLineDash(isZero ? [] : [2, 4]);
        ctx.strokeStyle = isZero ? THEME.zeroLine : THEME.grid;
        ctx.lineWidth = isZero ? 1.5 : 1;
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(left + plotW, y);
        ctx.stroke();

        ctx.fillStyle = isZero ? THEME.accent : THEME.text3;
        ctx.fillText((db > 0 ? "+" : "") + db, left - 8, y + 3);
    });
    ctx.setLineDash([]);
    ctx.lineWidth = 1;

    ctx.save();
    ctx.translate(12, top + plotH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillStyle = THEME.text3;
    ctx.fillText("Gain (dB)", 0, 0);
    ctx.restore();
    ctx.textAlign = "left";

    // 3. THE EMITTED RESPONSE, EXACTLY AS THE BACKEND RENDERED IT
    if (responsePoints.length < 2) {
        // Nothing has been rendered yet; the 0 dB reference is the whole truth.
        return;
    }

    // The backend folds the preamp into every point so the payload is literally
    // what Equalizer APO received. Take it back out here so the trace is the
    // tonal shape centred on 0 dB; the PREAMP readout carries the level.
    const preampOffset = Number(outputState.preamp_db) || 0;

    ctx.beginPath();
    for (let i = 0; i < responsePoints.length; i++) {
        const point = responsePoints[i];
        const x = left + freqToX(point.f, plotW);
        const y = dbToY(point.db - preampOffset, top, plotH);
        if (i === 0) {
            ctx.moveTo(x, y);
        } else {
            ctx.lineTo(x, y);
        }
    }

    if (outputState.bypassed) {
        // Bypassed: flat, level-matched, and visibly inert.
        ctx.strokeStyle = THEME.warn;
        ctx.setLineDash([7, 5]);
        ctx.lineWidth = 3;
        ctx.shadowBlur = 0;
        ctx.stroke();
        ctx.setLineDash([]);
        return;
    }

    ctx.strokeStyle = THEME.accent;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();

    // Close the path back along the 0 dB line so the shaded region reads as
    // deviation from flat, which is the quantity that matters, rather than as
    // area under an arbitrary baseline.
    const zeroY = dbToY(0, top, plotH);
    ctx.lineTo(left + plotW, zeroY);
    ctx.lineTo(left, zeroY);
    ctx.closePath();
    ctx.fillStyle = THEME.curveFill;
    ctx.fill();
}


// --- OUTPUT STAGE (RESPONSE, PREAMP, HEADROOM, BYPASS) ---

/**
 * Adopt an `output` block from /api/status, /api/update_eq or /api/bypass.
 */
function applyOutput(output) {
    if (!output) return;

    responsePoints = Array.isArray(output.response) ? output.response : [];
    outputState = {
        preamp_db: Number(output.preamp_db) || 0.0,
        limited: Boolean(output.limited),
        limit_scale: typeof output.limit_scale === "number" ? output.limit_scale : 1.0,
        bypassed: Boolean(output.bypassed)
    };

    // Panel legends are engraved in caps on this console, and the LED windows
    // read like meters rather than like sentences.
    preampReadout.textContent = "PREAMP " + formatDb(outputState.preamp_db);

    if (outputState.limited) {
        limitReadout.textContent =
            "LIMITED " + Math.round(outputState.limit_scale * 100) + "%";
        limitReadout.title =
            "The requested curve exceeded the headroom budget, so every band was "
            + "scaled to " + Math.round(outputState.limit_scale * 100)
            + "% of the requested gain to keep the output below 0 dBFS.";
        limitReadout.classList.remove("hidden");
    } else {
        limitReadout.classList.add("hidden");
    }

    updateBypassIndicator();
    drawEQCurve();
}

function updateBypassIndicator() {
    // Prefer what the listener is asking for: while the button is held the chip
    // must be up even if the confirming response is still in flight.
    const on = bypassDesired || outputState.bypassed;
    bypassChip.classList.toggle("hidden", !on);
    compareBtn.classList.toggle("active", on);
    compareBtn.setAttribute("aria-pressed", on ? "true" : "false");
    if (canvasContainer) canvasContainer.classList.toggle("bypassed", on);
}


// --- HOLD-TO-COMPARE ---

function requestBypass(enabled) {
    if (bypassDesired === enabled && !bypassInFlight) {
        // Nothing to do, but keep the indicator honest after a failed request.
        updateBypassIndicator();
        return;
    }
    bypassDesired = enabled;
    bypassSettleUntil = Date.now() + 1200;
    updateBypassIndicator();
    if (!bypassInFlight) sendBypass();
}

function sendBypass() {
    const target = bypassDesired;
    bypassInFlight = true;

    fetch("/api/bypass", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled: target})
    })
    .then(res => res.json())
    .then(data => {
        bypassInFlight = false;
        if (data.success) {
            bypassSettleUntil = Date.now() + 400;
            applyOutput(data.output);
        }
        // The listener may already have let go while this was in flight.
        if (bypassDesired !== target) sendBypass();
    })
    .catch(err => {
        bypassInFlight = false;
        console.error("Bypass toggle failed:", err);
        if (bypassDesired !== target) sendBypass();
    });
}

function isTypingTarget(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    const tag = el.tagName;
    return tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
}

compareBtn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    compareBtn.focus();
    requestBypass(true);
});
["pointerup", "pointerleave", "pointercancel"].forEach(evt => {
    compareBtn.addEventListener(evt, () => requestBypass(false));
});

// Space anywhere on the page does the same, except while a control has focus.
document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" && e.key !== " ") return;
    if (e.repeat) return;                     // autorepeat is not a new press
    if (isTypingTarget(e.target)) return;
    e.preventDefault();                       // do not scroll the page
    requestBypass(true);
});

document.addEventListener("keyup", (e) => {
    if (e.code !== "Space" && e.key !== " ") return;
    if (isTypingTarget(e.target)) return;
    e.preventDefault();
    requestBypass(false);
});

// Never leave the system bypassed because focus went elsewhere mid-hold.
window.addEventListener("blur", () => requestBypass(false));
document.addEventListener("visibilitychange", () => {
    if (document.hidden) requestBypass(false);
});


// --- INTERACTION GUARD ---
// The 1.5 s status poll must not move a control the listener has hold of. Each
// control is marked busy on pointerdown/keydown and stays busy for a short
// settle after release, which is longer than the debounced POST takes to land.

const SETTLE_MS = 700;
const busyControls = new Map();   // element -> timestamp after which polls may write again

function beginInteraction(el) {
    busyControls.set(el, Infinity);
}

function endInteraction(el) {
    if (busyControls.has(el)) busyControls.set(el, Date.now() + SETTLE_MS);
}

function releaseHeldControls() {
    busyControls.forEach((until, el) => {
        if (until === Infinity) busyControls.set(el, Date.now() + SETTLE_MS);
    });
}

function isControlBusy(el) {
    const until = busyControls.get(el);
    if (until === undefined) return false;
    if (Date.now() < until) return true;
    busyControls.delete(el);
    return false;
}

/**
 * Push a polled value into a slider, unless the listener is working on it.
 */
function syncControl(el, labelEl, value, text) {
    if (isControlBusy(el)) return;
    el.value = value;
    if (labelEl) labelEl.textContent = text;
}

function guardControl(el) {
    el.addEventListener("pointerdown", () => beginInteraction(el));
    el.addEventListener("keydown", () => beginInteraction(el));
    el.addEventListener("keyup", () => endInteraction(el));
    el.addEventListener("blur", () => endInteraction(el));
}

// pointerup often lands outside the thumb, so release at the document level.
document.addEventListener("pointerup", releaseHeldControls);
document.addEventListener("pointercancel", releaseHeldControls);


// --- DYNAMIC SLIDER TRACK UPDATE HANDLERS ---
/**
 * Fill each fader track outward from the 0 dB centre detent.
 *
 * This used to fill from the bottom, so a flat band showed a half-full bar and
 * a cut showed a bar that was still mostly full. On an EQ that is backwards:
 * the fill should show the size and direction of the move, so a boost grows
 * upward from centre and a cut grows downward.
 */
function updateSliderGlowBars() {
    const keys = ["low_shelf", "mid_1", "mid_2", "mid_3", "high_shelf"];
    const RANGE_DB = 15;

    keys.forEach(k => {
        const bar = document.getElementById(`glow_${k}`);
        if (!bar) return;

        const val = parseFloat(sliders[k].value) || 0;
        const magnitude = Math.min(Math.abs(val), RANGE_DB) / RANGE_DB * 50;

        bar.style.height = `${magnitude}%`;
        bar.style.bottom = val >= 0 ? "50%" : `${50 - magnitude}%`;
        bar.classList.toggle("cut", val < 0);
    });
}


// --- REST COMMUNICATIONS API CONTROLLER ---

function showEqError(message) {
    if (!eqError) return;
    if (message) {
        eqError.textContent = message;
        eqError.classList.remove("hidden");
    } else {
        eqError.textContent = "";
        eqError.classList.add("hidden");
    }
}

function throttlePostUpdate() {
    if (updateTimer) clearTimeout(updateTimer);

    activeEq.low_shelf_gain = parseFloat(sliders.low_shelf.value);
    activeEq.first_band_gain = parseFloat(sliders.mid_1.value);
    activeEq.second_band_gain = parseFloat(sliders.mid_2.value);
    activeEq.third_band_gain = parseFloat(sliders.mid_3.value);
    activeEq.high_shelf_gain = parseFloat(sliders.high_shelf.value);

    activeMix.preamp_gain = parseFloat(mixSliders.preamp.value);
    activeMix.strength = parseFloat(mixSliders.strength.value);
    activeMix.bass_boost = parseFloat(mixSliders.bass.value);
    activeMix.vocal_clarity = parseFloat(mixSliders.vocal.value);
    activeMix.airiness = parseFloat(mixSliders.air.value);

    updateSliderGlowBars();

    updateTimer = setTimeout(() => {
        fetch("/api/update_eq", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                eq: activeEq,
                mix: activeMix
            })
        })
        .then(res => res.json().then(data => ({ok: res.ok, data: data || {}})))
        .then(result => {
            // The backend answers 400 with a message for non-numeric, non-finite
            // or out-of-range values. Say so rather than leaving the console
            // showing a filter that was never applied.
            if (!result.ok || !result.data.success) {
                showEqError(result.data.message || "The EQ update was rejected by the backend.");
                return;
            }

            showEqError("");
            updateWriteStatusAlert(result.data.state.apo_write_status);
            applyOutput(result.data.state.output);

            if (appMode === "auto" && result.data.state.mode === "manual") {
                setUiMode("manual");
            }
        })
        .catch(err => {
            showEqError("Could not reach the EQ backend. The audio is unchanged.");
            console.error("Failed to sync EQ settings:", err);
        });
    }, 80);
}


// --- OUTPUT-STAGE BANNERS ---

// Dismissal sticks for the rest of the page session. The status poll used to
// re-show this banner ~1.5 s after every dismissal, so the close button did
// nothing you could observe.
let adminWarningDismissed = false;

// apo_write_status is one of "ok" | "denied" | "path_missing" | "error".
// Elevation is not the fix for any of them: Equalizer APO's installer grants
// BUILTIN\Users FullControl on its config directory, so config.txt is writable
// without administrator rights.
const WRITE_STATUS_MESSAGES = {
    denied: {
        title: "Cannot write config.txt: ",
        text: "Windows refused write access to the Equalizer APO config folder. " +
              "This is not an elevation problem (APO grants Users full control there); " +
              "check the folder's permissions, or whether another program is holding the file open. " +
              "Your settings are still mirrored to data/config.txt.",
        variant: ""
    },
    path_missing: {
        title: "Equalizer APO config folder not found: ",
        text: "The configured Equalizer APO directory does not exist, so nothing can be written to it. " +
              "Check the apo path in config.yaml. Your settings are still mirrored to data/config.txt.",
        variant: "not-found"
    },
    error: {
        title: "Could not write config.txt: ",
        text: "The write to the Equalizer APO config failed. See the server log for the underlying error. " +
              "Your settings are still mirrored to data/config.txt.",
        variant: ""
    }
};

function updateWriteStatusAlert(status) {
    const info = WRITE_STATUS_MESSAGES[status];

    if (!info || adminWarningDismissed) {
        adminWarning.classList.add("hidden");
        return;
    }

    adminWarning.className = "admin-warning-banner" + (info.variant ? " " + info.variant : "");
    adminWarningTitle.textContent = info.title;
    adminWarningText.textContent = info.text;
}

/**
 * Show apo_status.detail verbatim whenever APO is not on the active output.
 * A config.txt write that succeeds into an endpoint nobody is listening
 * through is not success, so this banner cannot be dismissed.
 */
function updateApoStatusBanner(apoStatus) {
    if (!apoStatus || apoStatus.state === "apo_ready") {
        apoStatusBanner.classList.add("hidden");
        return;
    }
    apoStatusDetail.textContent = apoStatus.detail || "Equalizer APO status is unknown.";
    apoStatusBanner.classList.remove("hidden");
}

adminWarningClose.addEventListener("click", () => {
    adminWarningDismissed = true;
    adminWarning.classList.add("hidden");
});

apoRecheckBtn.addEventListener("click", () => {
    apoRecheckBtn.disabled = true;
    fetch("/api/apo/status")
    .then(res => res.json())
    .then(status => {
        apoRecheckBtn.disabled = false;
        updateApoStatusBanner(status);
    })
    .catch(err => {
        apoRecheckBtn.disabled = false;
        console.error("Equalizer APO re-probe failed:", err);
    });
});

// --- SPOTIFY STRIP AND SETUP PANEL -----------------------------------------
//
// Spotify is enrichment, never a gate: without it the search box, every fader,
// hold-to-compare, the presets and the headroom safety all still work. So the
// strip offers setup rather than demanding it, and the unconfigured form of it
// can be dismissed for the session.

let spotifyPromptDismissed = false;

function setSpotifySetupMessage(text, kind) {
    if (!spotifySetupMsg) return;
    spotifySetupMsg.textContent = text || "";
    spotifySetupMsg.classList.remove("ok", "err");
    if (kind) spotifySetupMsg.classList.add(kind);
}

function openSpotifySetup() {
    if (!spotifySetupPanel) return;
    spotifySetupPanel.classList.remove("hidden");
    if (spotifyClientId) spotifyClientId.focus();
}

function closeSpotifySetup() {
    if (spotifySetupPanel) spotifySetupPanel.classList.add("hidden");
}

function updateSpotifyStrip(data) {
    const configured = Boolean(data.spotify_configured);
    const linked = Boolean(data.spotify_authenticated);
    // When Windows is doing the detecting, Spotify adds nothing the listener
    // needs, so the strip must not read as a missing prerequisite. It used to
    // be the only thing on screen offering automatic now-playing.
    const windowsSource = data.now_playing_source === "windows";

    disconnectSpotifyBtn.classList.toggle("hidden", !linked);

    if (linked || windowsSource) {
        spotifyBanner.classList.add("hidden");
        return;
    }

    if (configured) {
        // Credentials exist, so the only thing left is the account login.
        spotifyBanner.classList.remove("hidden");
        spotifyBanner.classList.remove("muted");
        setBannerText("Spotify not connected",
            " — authorize your account to auto-detect what is playing. " +
            "Everything else already works.");
        connectSpotifyBtn.classList.remove("hidden");
        spotifyBannerClose.classList.add("hidden");
        return;
    }

    // No credentials. This is a legitimate way to run the app, so the strip is
    // an offer and it can be closed.
    spotifyBanner.classList.toggle("hidden", spotifyPromptDismissed);
    spotifyBanner.classList.add("muted");
    setBannerText("Spotify is off",
        " — optional. Add API keys to auto-detect now-playing, or just search " +
        "a track below.");
    connectSpotifyBtn.classList.add("hidden");
    spotifyBannerClose.classList.remove("hidden");
}

/** Built as nodes rather than markup, matching the rest of this file. */
function setBannerText(headline, rest) {
    clearNode(spotifyBannerText);
    spotifyBannerText.appendChild(makeEl("strong", null, headline));
    spotifyBannerText.appendChild(document.createTextNode(rest));
}

if (spotifyBannerClose) {
    spotifyBannerClose.addEventListener("click", () => {
        spotifyPromptDismissed = true;
        spotifyBanner.classList.add("hidden");
        closeSpotifySetup();
    });
}

if (spotifySetupBtn) {
    spotifySetupBtn.addEventListener("click", () => {
        if (spotifySetupPanel.classList.contains("hidden")) {
            setSpotifySetupMessage("");
            fetch("/api/spotify/config")
                .then(r => r.json())
                .then(cfg => {
                    if (cfg.redirect_uri) spotifyRedirectUri.value = cfg.redirect_uri;
                    if (cfg.configured) {
                        setSpotifySetupMessage(
                            "Keys are already stored (" + cfg.client_id +
                            "). Enter a new pair to replace them.");
                    }
                })
                .catch(() => {});
            openSpotifySetup();
        } else {
            closeSpotifySetup();
        }
    });
}

if (spotifySetupCancel) {
    spotifySetupCancel.addEventListener("click", closeSpotifySetup);
}

if (spotifySetupPanel) {
    spotifySetupPanel.addEventListener("submit", (e) => {
        e.preventDefault();
        const originalLabel = spotifySetupSave.textContent;
        spotifySetupSave.textContent = "VERIFYING...";
        spotifySetupSave.disabled = true;
        setSpotifySetupMessage("Checking the keys against Spotify…");

        fetch("/api/spotify/config", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                client_id: spotifyClientId.value.trim(),
                client_secret: spotifyClientSecret.value.trim(),
                redirect_uri: spotifyRedirectUri.value.trim()
            })
        })
        .then(res => res.json().then(data => ({ok: res.ok, data: data || {}})))
        .then(result => {
            spotifySetupSave.textContent = originalLabel;
            spotifySetupSave.disabled = false;

            if (!result.ok || !result.data.success) {
                setSpotifySetupMessage(
                    result.data.message || "Spotify rejected those keys.", "err");
                return;
            }
            // The secret does not linger in the DOM once it is stored.
            spotifyClientSecret.value = "";
            spotifyPromptDismissed = false;
            setSpotifySetupMessage(result.data.message, "ok");
            pollDashboardStatus();
        })
        .catch(err => {
            spotifySetupSave.textContent = originalLabel;
            spotifySetupSave.disabled = false;
            setSpotifySetupMessage("Could not reach the backend.", "err");
            console.error("Spotify config save failed:", err);
        });
    });
}


function setUiMode(mode) {
    appMode = mode;
    if (mode === "auto") {
        syncModeToggle.checked = true;
        consoleModeTag.innerText = "Auto-Sync Active";
        consoleModeTag.className = "manual-mode-indicator";
    } else {
        syncModeToggle.checked = false;
        consoleModeTag.innerText = "Manual Override Active";
        consoleModeTag.className = "manual-mode-indicator manual";
    }
}


// --- DOM BUILDERS (crowd-authored text is never parsed as HTML) ---

function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
}

function makeEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
}

/**
 * Last.fm tags are editable by anyone on the internet and land in an origin
 * that has unauthenticated control of every local API on this machine, so they
 * are only ever written through textContent.
 */
function renderTags(genres, tags) {
    clearNode(tagsList);
    let count = 0;

    (genres || []).forEach(g => {
        tagsList.appendChild(makeEl("span", "tag-badge genre", String(g)));
        count++;
    });
    (tags || []).forEach(t => {
        tagsList.appendChild(makeEl("span", "tag-badge", String(t)));
        count++;
    });

    if (count === 0) {
        tagsList.appendChild(makeEl("span", "no-tags-msg", "No tags returned. Creating default profile."));
    }
}

function renderTagsMessage(message) {
    clearNode(tagsList);
    tagsList.appendChild(makeEl("span", "no-tags-msg", message));
}

// Mirrors DEFECT_PROFILES in embed_song_predictor.py. These name what a
// listener wants removed, not a target to reproduce, and the backend excludes
// them from the additive blend, so they must not read as an achievement.
const DEFECT_PROFILES = ["muddy"];

function renderWeights(weights) {
    clearNode(weightsList);

    const entries = Object.entries(weights || {}).filter(entry => entry[1] > 0);
    if (entries.length === 0) {
        weightsList.appendChild(makeEl("div", "no-weights-msg", "No profile keywords matched."));
        return;
    }
    entries.sort((a, b) => b[1] - a[1]);

    entries.forEach(entry => {
        const profile = entry[0];
        const weight = entry[1];
        const isDefect = DEFECT_PROFILES.indexOf(profile) !== -1;

        const row = makeEl("div", isDefect ? "weight-row corrective" : "weight-row");
        const info = makeEl("div", "weight-info");
        info.appendChild(makeEl(
            "span", "weight-label",
            isDefect ? profile + " (defect: corrected, not matched)" : profile
        ));
        info.appendChild(makeEl(
            "span", "weight-percentage",
            isDefect ? (weight * 100).toFixed(0) + "% detected" : (weight * 100).toFixed(0) + "%"
        ));

        const track = makeEl("div", "progress-track");
        const bar = makeEl("div", "progress-glow-bar");
        bar.style.width = (weight * 100) + "%";
        track.appendChild(bar);

        row.appendChild(info);
        row.appendChild(track);
        weightsList.appendChild(row);
    });
}

function renderWeightsMessage(message) {
    clearNode(weightsList);
    weightsList.appendChild(makeEl("div", "no-weights-msg", message));
}

const PIPELINE_LABELS = {
    spotify: { label: "Now playing source" },
    playback: { label: "Playback" },
    dsp_engine: { label: "Prediction engine" },
    apo_writer: { label: "Output stage" }
};

/**
 * Lamp state for one signal-path row: "ok" (sage), "err" (red) or neutral.
 */
function pipelineLampState(value) {
    const lower = String(value).toLowerCase();
    if (lower.indexOf("disconnected") !== -1 || lower.indexOf("error") !== -1 ||
        lower.indexOf("failed") !== -1 || lower.indexOf("blocked") !== -1 ||
        lower.indexOf("cannot") !== -1 || lower.indexOf("not applied") !== -1 ||
        lower.indexOf("not installed") !== -1 ||
        lower.indexOf("not responding") !== -1) {
        return "err";
    }
    if (lower.indexOf("connected") !== -1 || lower.indexOf("active") !== -1 ||
        lower.indexOf("ready") !== -1 || lower.indexOf("eq active") !== -1) {
        return "ok";
    }
    return "";
}

function renderPipeline(pipeline) {
    clearNode(pipelineStatusList);

    Object.entries(pipeline).forEach(entry => {
        const key = entry[0];
        const value = entry[1];
        const config = PIPELINE_LABELS[key] || { label: key };

        const lamp = pipelineLampState(value);

        const li = makeEl("li", "pipe-row");
        li.appendChild(makeEl("span", "pipe-lamp" + (lamp ? " " + lamp : "")));
        li.appendChild(makeEl("span", "pipe-name", config.label));
        li.appendChild(makeEl("span", "pipe-state", String(value)));
        pipelineStatusList.appendChild(li);
    });
}


// --- ACTIVE STATUS POLLING ---

// Consecutive failed polls. Losing the backend used to be invisible: the page
// kept showing the last good snapshot and logged to a console nobody has open,
// so a dead server looked exactly like a working one that had stopped changing.
let pollFailures = 0;

function showBackendOffline() {
    setLive(false);
    if (mixingReason) {
        mixingReason.textContent =
            "The Sonic Vector server is not responding, so nothing on this page "
            + "is live. Your audio is unaffected: the last curve written is still "
            + "applied, and it resets to flat when the app exits cleanly.";
    }
    renderPipeline({
        spotify: "Unknown (server not responding)",
        playback: "Unknown (server not responding)",
        dsp_engine: "Unknown (server not responding)",
        apo_writer: "Unknown (server not responding)"
    });
}

function pollDashboardStatus() {
    fetch("/api/status")
    .then(res => res.json())
    .then(data => {
        pollFailures = 0;
        updateWriteStatusAlert(data.apo_write_status);
        updateApoStatusBanner(data.apo_status);

        // While the listener is holding Compare, the bypass responses are the
        // authoritative source; a status snapshot taken before the toggle would
        // only flap the curve.
        if (!bypassInFlight && Date.now() > bypassSettleUntil) {
            applyOutput(data.output);
        }

        updateSpotifyStrip(data);

        setUiMode(data.mode);

        // The AI engine is a bonus path that needs an external provider. This
        // app is meant to run on no API keys at all, so rather than offering a
        // choice that silently falls back on every track, the option is only
        // present when something is actually there to answer.
        // hidden keeps it out of the popup; disabled keeps it unselectable even
        // where a browser or an assistive tool ignores hidden on an <option>.
        if (aiEngineOption) {
            aiEngineOption.hidden = !data.llm_available;
            aiEngineOption.disabled = !data.llm_available;
        }

        if (!isControlBusy(aiEngineSelect)) aiEngineSelect.value = data.ai_engine;
        if (!isControlBusy(soundStyleSelect)) soundStyleSelect.value = data.sound_style;

        // Keyword profile matches only exist for the matching engine.
        if (data.ai_engine === "llm") {
            weightsCard.classList.add("hidden");
        } else {
            weightsCard.classList.remove("hidden");
        }

        if (data.mode === "auto") {
            activeEq = data.eq;
            activeMix = data.mix;

            syncControl(sliders.low_shelf, sliderVals.low_shelf,
                activeEq.low_shelf_gain, formatDb(activeEq.low_shelf_gain, 1));
            syncControl(sliders.mid_1, sliderVals.mid_1,
                activeEq.first_band_gain, formatDb(activeEq.first_band_gain, 1));
            syncControl(sliders.mid_2, sliderVals.mid_2,
                activeEq.second_band_gain, formatDb(activeEq.second_band_gain, 1));
            syncControl(sliders.mid_3, sliderVals.mid_3,
                activeEq.third_band_gain, formatDb(activeEq.third_band_gain, 1));
            syncControl(sliders.high_shelf, sliderVals.high_shelf,
                activeEq.high_shelf_gain, formatDb(activeEq.high_shelf_gain, 1));

            syncControl(mixSliders.preamp, mixVals.preamp,
                activeMix.preamp_gain, formatDb(activeMix.preamp_gain, 2));
            syncControl(mixSliders.strength, mixVals.strength,
                activeMix.strength, activeMix.strength.toFixed(2) + "x");
            syncControl(mixSliders.bass, mixVals.bass,
                activeMix.bass_boost, formatDb(activeMix.bass_boost, 1));
            syncControl(mixSliders.vocal, mixVals.vocal,
                activeMix.vocal_clarity, formatDb(activeMix.vocal_clarity, 1));
            syncControl(mixSliders.air, mixVals.air,
                activeMix.airiness, formatDb(activeMix.airiness, 1));

            updateSliderGlowBars();
        }

        const track = data.current_track;

        // Mastering Insights explanation (the card this was written into used
        // to be display:none, so none of this was ever readable).
        mixingReason.textContent = track.mixing_reason || "No dynamic mixing profile loaded.";

        // The backend flags placeholders explicitly. This used to be a hardcoded
        // list of status headlines that had already drifted out of sync with the
        // ones the server actually sends, so "Spotify Account Disconnected" was
        // rendered as though it were a song.
        const isSongActive = Boolean(track.track_name) && track.placeholder !== true;

        if (trackSourceTag) {
            const SOURCE_LABEL = {
                windows: "SRC · WINDOWS",
                spotify: "SRC · SPOTIFY",
                search: "SRC · SEARCH"
            };
            trackSourceTag.textContent = SOURCE_LABEL[track.source] || "SRC · IDLE";
        }
        // Remix needs a loaded track. Never re-enable it under an in-flight
        // request: the poll fires every 1.5 s and would undo the busy state.
        redoMixBtn.disabled = redoInFlight || !isSongActive;

        if (isSongActive) {
            trackTitle.textContent = track.track_name;
            trackArtist.textContent = track.artist_name;
            trackAlbum.textContent = track.album_name || "";

            if (track.album_art) {
                trackArt.src = track.album_art;
            } else {
                trackArt.src = "/static/placeholder_cover.png";
            }

            setLive(Boolean(track.is_playing));

            renderTags(track.genres, track.tags);

            if (data.ai_engine === "similarity") {
                renderWeights(track.weights);
            }
        } else {
            trackTitle.textContent = track.track_name || "No Track Loaded";
            trackArtist.textContent = track.artist_name || "Search a track below to build a mix.";
            trackAlbum.textContent = track.album_name || "";
            trackArt.src = "/static/placeholder_cover.png";
            setLive(false);
            renderTagsMessage("No track loaded. Search one to harvest its tags.");
            renderWeightsMessage("No profile keywords matched yet.");
        }

        if (data.pipeline_status && pipelineStatusList) {
            renderPipeline(data.pipeline_status);
        }

        // One vote per track: re-arm the buttons when the track changes, so a
        // latched thumb never carries over onto a different song.
        if (votedTrackKey !== null && currentTrackKey() !== votedTrackKey) {
            votedTrackKey = null;
            resetVoteButtons();
        }
    })
    .catch(err => {
        console.error("Status polling failed:", err);
        // One miss is a hiccup, not an outage; two in a row is worth saying.
        if (++pollFailures >= 2) showBackendOffline();
    });
}


// --- CONTROL BINDINGS ---

// Set vertical sliders drag bindings
const verticalSliders = ["low_shelf", "mid_1", "mid_2", "mid_3", "high_shelf"];
verticalSliders.forEach(k => {
    guardControl(sliders[k]);
    sliders[k].addEventListener("input", () => {
        const val = parseFloat(sliders[k].value);
        sliderVals[k].textContent = formatDb(val, 1);
        throttlePostUpdate();
    });
});

// Set mix sliders drag bindings
const mixKeys = ["preamp", "strength", "bass", "vocal", "air"];
mixKeys.forEach(k => {
    guardControl(mixSliders[k]);
    mixSliders[k].addEventListener("input", () => {
        const val = parseFloat(mixSliders[k].value);
        if (k === "strength") {
            mixVals[k].textContent = val.toFixed(2) + "x";
        } else if (k === "preamp") {
            mixVals[k].textContent = formatDb(val, 2);
        } else {
            mixVals[k].textContent = formatDb(val, 1);
        }
        throttlePostUpdate();
    });
});

// Keep the dropdowns from snapping back mid-choice as well.
guardControl(aiEngineSelect);
guardControl(soundStyleSelect);

// Bind Sync Mode Toggle check trigger
syncModeToggle.addEventListener("change", () => {
    const targetMode = syncModeToggle.checked ? "auto" : "manual";
    fetch("/api/mode", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({mode: targetMode})
    })
    .then(res => res.json())
    .then(data => {
        setUiMode(data.mode);
        pollDashboardStatus();
    })
    .catch(err => console.error("Mode switch error:", err));
});

// Bind AI Engine select dropdown trigger
aiEngineSelect.addEventListener("change", () => {
    const targetEngine = aiEngineSelect.value;
    endInteraction(aiEngineSelect);
    fetch("/api/engine", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({engine: targetEngine})
    })
    .then(res => res.json())
    .then(() => {
        pollDashboardStatus();
    })
    .catch(err => console.error("Mixing engine switcher error:", err));
});

// Bind Target Sound Style select dropdown trigger
soundStyleSelect.addEventListener("change", () => {
    const targetStyle = soundStyleSelect.value;
    endInteraction(soundStyleSelect);
    fetch("/api/style", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({style: targetStyle})
    })
    .then(res => res.json())
    .then(() => {
        pollDashboardStatus();
    })
    .catch(err => console.error("Sound Style Switcher error:", err));
});

// Power off. The taskbar launcher runs without a console, so this is the only
// clean way to stop the app; anything else is a hard kill, which by definition
// runs no exit handler and therefore leaves the last curve applied to every
// sound on the machine.
const quitBtn = document.getElementById("quitBtn");
if (quitBtn) {
    quitBtn.addEventListener("click", () => {
        if (!confirm("Stop Sonic Vector?\n\nYour system EQ will be reset to flat.")) {
            return;
        }
        quitBtn.textContent = "STOPPING...";
        quitBtn.disabled = true;

        fetch("/api/quit", {method: "POST"})
        .then(res => res.json())
        .then(data => showStoppedScreen(data.message))
        // The server may die before the response lands. That is a successful
        // quit, not an error, so it gets the same screen.
        .catch(() => showStoppedScreen(
            "Sonic Vector has stopped. Your EQ has been reset to flat."));
    });
}

function showStoppedScreen(message) {
    clearInterval(statusPoll);
    const veil = makeEl("div", "stopped-veil");
    const card = makeEl("div", "stopped-card");
    card.appendChild(makeEl("h2", null, "Sonic Vector has stopped"));
    card.appendChild(makeEl("p", null, message || "Your EQ has been reset to flat."));
    card.appendChild(makeEl("p", "stopped-hint",
        "You can close this window. Launch again from the taskbar or Start menu."));
    veil.appendChild(card);
    document.body.appendChild(veil);
}

// Reset button click
resetEqBtn.addEventListener("click", () => {
    fetch("/api/eq/reset", {method: "POST"})
    .then(res => res.json())
    .then(() => {
        showEqError("");
        setUiMode("manual");
        pollDashboardStatus();
    })
    .catch(err => console.error("Reset failed:", err));
});

/**
 * Where to write a button's transient label ("Remixing...").
 *
 * These two buttons hold their caption as a bare text node, so
 * querySelector("span") returned null and the very first line of both click
 * handlers threw "Cannot set properties of null". REDO MIX and DISCONNECT were
 * dead controls: the throw happened before the fetch, so nothing was ever sent
 * and nothing on screen changed. Falling back to the button itself keeps
 * working if either one later gains an icon and a span.
 */
function labelTarget(btn) {
    return btn.querySelector("span") || btn;
}

const redoMixLabel = labelTarget(redoMixBtn);
const redoMixText = redoMixLabel.textContent;
redoMixBtn.addEventListener("click", () => {
    const restoreRedoBtn = () => {
        redoInFlight = false;
        redoMixLabel.textContent = redoMixText;
        redoMixBtn.disabled = false;
    };

    redoInFlight = true;
    redoMixLabel.textContent = "REMIXING...";
    redoMixBtn.disabled = true;

    fetch("/api/eq/redo", {method: "POST"})
    .then(res => res.json().then(data => ({ok: res.ok, data: data || {}})))
    .then(result => {
        restoreRedoBtn();
        if (result.ok && result.data.success) {
            pollDashboardStatus();
        } else {
            showEqError("Remix failed: " + (result.data.message || "unknown error"));
        }
    })
    .catch(err => {
        restoreRedoBtn();
        console.error("Remix failed:", err);
    });
});

// Bind Connect Spotify button click trigger. The caption is captured rather
// than hardcoded, so restoring it cannot silently rename the control.
const connectSpotifyText = connectSpotifyBtn.textContent;
connectSpotifyBtn.addEventListener("click", () => {
    const restoreConnectBtn = () => {
        connectSpotifyBtn.textContent = connectSpotifyText;
        connectSpotifyBtn.disabled = false;
    };

    connectSpotifyBtn.textContent = "AUTHORIZING...";
    connectSpotifyBtn.disabled = true;

    fetch("/api/spotify/authenticate", {method: "POST"})
    .then(res => res.json().then(data => ({ok: res.ok, data: data || {}})))
    .then(result => {
        if (!result.ok || !result.data.success) {
            restoreConnectBtn();
            setSpotifySetupMessage(
                result.data.message || "Could not start the Spotify login.", "err");
            openSpotifySetup();
            return;
        }
        watchSpotifyAuth(restoreConnectBtn);
    })
    .catch(err => {
        restoreConnectBtn();
        console.error("Spotify Auth Trigger Error:", err);
    });
});

/**
 * Follow the login until it resolves, rather than re-enabling the button on a
 * blind eight-second timer and never saying what happened. Spotify reports
 * refusals and redirect-URI mismatches on the callback, which is server side,
 * so the browser can only learn the outcome by asking.
 */
function watchSpotifyAuth(restore) {
    let elapsed = 0;
    const timer = setInterval(() => {
        elapsed += 2;
        fetch("/api/spotify/status")
        .then(r => r.json())
        .then(st => {
            if (st.authenticated) {
                clearInterval(timer);
                restore();
                closeSpotifySetup();
                pollDashboardStatus();
                return;
            }
            if (!st.in_progress) {
                clearInterval(timer);
                restore();
                if (st.last_error) {
                    setSpotifySetupMessage(st.last_error, "err");
                    openSpotifySetup();
                }
            }
            // A login can legitimately take minutes (password manager, 2FA).
            // Stop watching well after the server's own timeout, not before.
            if (elapsed > 320) {
                clearInterval(timer);
                restore();
            }
        })
        .catch(() => { clearInterval(timer); restore(); });
    }, 2000);
}

// Bind Disconnect Spotify button click trigger
const disconnectSpotifyLabel = labelTarget(disconnectSpotifyBtn);
const disconnectSpotifyText = disconnectSpotifyLabel.textContent;
disconnectSpotifyBtn.addEventListener("click", () => {
    if (!confirm("Are you sure you want to disconnect your Spotify account? This will clear active credentials and require a fresh browser login.")) {
        return;
    }

    disconnectSpotifyLabel.textContent = "DISCONNECTING...";
    disconnectSpotifyBtn.disabled = true;

    fetch("/api/spotify/disconnect", {method: "POST"})
    .then(res => res.json())
    .then(() => {
        disconnectSpotifyLabel.textContent = disconnectSpotifyText;
        disconnectSpotifyBtn.disabled = false;
        pollDashboardStatus();
    })
    .catch(err => {
        disconnectSpotifyLabel.textContent = disconnectSpotifyText;
        disconnectSpotifyBtn.disabled = false;
        console.error("Spotify Disconnect Error:", err);
    });
});

// Explicit search override form submit (wrapped defensively in case element is removed)
if (searchOverrideForm) {
    searchOverrideForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const queryStr = searchQuery.value.trim();
        if (!queryStr) return;

        const btn = searchOverrideForm.querySelector("button");
        const originalText = btn.textContent;
        btn.textContent = "Analyzing...";
        btn.disabled = true;

        fetch("/api/search", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({query: queryStr})
        })
        .then(res => res.json().then(data => ({ok: res.ok, data: data || {}})))
        .then(result => {
            btn.textContent = originalText;
            btn.disabled = false;
            searchQuery.value = "";

            if (result.ok && result.data.success) {
                // The backend puts a searched track into auto mode so the
                // overlays follow it. Asserting "manual" here made the mode tag
                // flip for one poll interval and then flip back.
                showEqError("");
                pollDashboardStatus();
            } else {
                showEqError("Failed to find or analyze that track: " +
                    (result.data.message || "unknown error"));
            }
        })
        .catch(err => {
            btn.textContent = originalText;
            btn.disabled = false;
            console.error("Search request error:", err);
        });
    });
}


// --- INITIAL STARTUP ---
// Kept in a handle so Power Off can stop it; otherwise the poll keeps firing
// at a server that has deliberately gone away and paints the offline banner
// over the "stopped cleanly" message.
const statusPoll = setInterval(pollDashboardStatus, 1500);
pollDashboardStatus();
updateSliderGlowBars();


// --- LISTENER RATING -------------------------------------------------------
/**
 * Capture only. Nothing consumes these votes yet, but a vote is worth nothing
 * retroactively, so collection starts the moment the button exists.
 *
 * A bare thumb is the weakest feedback available: one bit about a
 * ~13-dimensional setting, with no direction, confounded with whether the
 * listener likes the song. The level-matched A/B next to the scope is the
 * stronger instrument. Both land in the same table.
 */
function setVoteNote(text, kind) {
    if (!voteNote) return;
    voteNote.textContent = text;
    voteNote.classList.remove("ok", "err");
    if (kind) voteNote.classList.add(kind);
}

function resetVoteButtons() {
    [voteUpBtn, voteDownBtn].forEach(b => {
        if (!b) return;
        b.classList.remove("cast");
        b.disabled = false;
    });
    setVoteNote("Votes are logged against the exact curve you are hearing.");
}

function renderVoteSummary(summary) {
    if (!voteCount || !summary) return;
    const total = summary.total || 0;
    voteCount.textContent = total === 1 ? "1 VOTE" : total + " VOTES";
    voteCount.title =
        total + " recorded (" + (summary.up || 0) + " up, " + (summary.down || 0)
        + "). A unary thumb needs roughly " + (summary.votes_for_signal || 40)
        + " votes before it says much; the A/B compare converges in about 9.";
}

function castVote(verdict) {
    const btn = verdict === "up" ? voteUpBtn : voteDownBtn;
    if (!btn || btn.disabled) return;

    [voteUpBtn, voteDownBtn].forEach(b => { if (b) b.disabled = true; });

    fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ verdict: verdict })
    })
        .then(r => r.json().then(body => ({ ok: r.ok, body: body })))
        .then(res => {
            if (!res.ok || !res.body.success) {
                resetVoteButtons();
                setVoteNote(res.body.message || "Could not record that vote.", "err");
                return;
            }
            btn.classList.add("cast");
            votedTrackKey = currentTrackKey();
            renderVoteSummary(res.body.summary);
            const c = res.body.coords || {};
            setVoteNote(
                "Recorded · tilt " + (c.tilt >= 0 ? "+" : "") + Number(c.tilt).toFixed(2)
                + " · presence " + (c.presence >= 0 ? "+" : "") + Number(c.presence).toFixed(2),
                "ok");
        })
        .catch(() => {
            resetVoteButtons();
            setVoteNote("Could not reach the server.", "err");
        });
}

function currentTrackKey() {
    return (trackTitle.textContent || "") + "|" + (trackArtist.textContent || "");
}

if (voteUpBtn) voteUpBtn.addEventListener("click", () => castVote("up"));
if (voteDownBtn) voteDownBtn.addEventListener("click", () => castVote("down"));

fetch("/api/feedback/summary")
    .then(r => r.json())
    .then(renderVoteSummary)
    .catch(() => {});


/* ── Turntable view ───────────────────────────────────────────────────────
   Opens the 3D deck view in its own window.

   That view is served by a separate small process on port 5177 rather than by
   Flask, so the button probes it before opening anything: a window that loads
   nothing is a worse outcome than a button that explains itself.

   `mode: "no-cors"` is deliberate. The probe only needs to know whether the
   port answers, and an opaque response is enough for that — asking for a
   readable one would require the view's server to send CORS headers purely so
   this check could pass.

   This block is appended at the end of the file and touches nothing above it. */
const turntableBtn = document.getElementById("turntableBtn");

if (turntableBtn) {
    const TURNTABLE_URL = "http://localhost:5177";

    turntableBtn.addEventListener("click", async () => {
        const label = turntableBtn.textContent;
        turntableBtn.disabled = true;
        turntableBtn.textContent = "OPENING…";

        try {
            await fetch(TURNTABLE_URL + "/", { mode: "no-cors", cache: "no-store" });
            window.open(
                TURNTABLE_URL,
                "sonicvector-turntable",
                "width=1500,height=940,menubar=no,toolbar=no,location=no,status=no",
            );
            turntableBtn.textContent = label;
        } catch {
            /* Not running. Say what to do rather than failing silently — the
               launcher starts it, so this only happens when the app was
               started on its own. */
            turntableBtn.textContent = "NOT RUNNING";
            console.warn(
                "Turntable view is not running. Start it with " +
                "launch_turntable.bat, or use the combined launcher.",
            );
            setTimeout(() => { turntableBtn.textContent = label; }, 2600);
        } finally {
            turntableBtn.disabled = false;
        }
    });
}
