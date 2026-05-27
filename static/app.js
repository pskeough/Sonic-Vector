/**
 * EqualizerAI — Frontend Interactive Application Engine & DSP Plotter.
 * 
 * Responsibility: Handles DOM binding, throttled REST communication, log-scale
 * frequency response Canvas rendering, dynamic vinyl disc rotation, state-based banner overlays,
 * popup Spotify OAuth integration, and dynamic AI Mixing Assistant engines.
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

let appMode = "auto";
let updateTimer = null;
let currentTrackId = "";

// Canvas elements
const canvas = document.getElementById("eqCanvas");
const ctx = canvas.getContext("2d");

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
const soundStyleSelect = document.getElementById("soundStyleSelect");

// Banners handles
const adminWarning = document.getElementById("adminWarning");
const spotifyBanner = document.getElementById("spotifyBanner");
const connectSpotifyBtn = document.getElementById("connectSpotifyBtn");
const disconnectSpotifyBtn = document.getElementById("disconnectSpotifyBtn");
const redoMixBtn = document.getElementById("redoMixBtn");

// Playback details DOM
const trackTitle = document.getElementById("trackTitle");
const trackArtist = document.getElementById("trackArtist");
const trackAlbum = document.getElementById("trackAlbum");
const trackArt = document.getElementById("trackArt");
const vinylSpindle = document.getElementById("vinylSpindle");
const tagsList = document.getElementById("tagsList");
const weightsList = document.getElementById("weightsList");
const weightsCard = document.getElementById("weightsCard");
const mixingReason = document.getElementById("mixingReason");
const pipelineStatusList = document.getElementById("pipelineStatusList");

// Search Form
const searchOverrideForm = document.getElementById("searchOverrideForm");
const searchQuery = document.getElementById("searchQuery");


// --- CANVAS INITIALIZATION & RESOLUTION FIX ---
function resizeCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    drawEQCurve();
}

window.addEventListener('resize', resizeCanvas);
setTimeout(resizeCanvas, 100);


// --- MATHEMATICAL FILTER RESPONSES (DSP LOGIC) ---

/**
 * Calculates log-spaced x coordinate from a physical frequency.
 * Spanned from 20 Hz to 20,000 Hz.
 */
function freqToX(freq, width) {
    const minF = Math.log10(20);
    const maxF = Math.log10(20000);
    const currF = Math.log10(freq);
    return ((currF - minF) / (maxF - minF)) * width;
}

/**
 * Calculates continuous gain contribution of a peaking (PK) bandpass-style filter.
 */
function peakingGain(f, f_c, gain, q) {
    if (gain === 0) return 0;
    const x = f / f_c;
    const denom = 1 + (q * q) * Math.pow(x - 1/x, 2);
    return gain / denom;
}

/**
 * Calculates low shelf filter magnitude contribution.
 */
function lowShelfGain(f, f_c, gain) {
    if (gain === 0) return 0;
    const x = f / f_c;
    return gain / (1 + Math.pow(x, 1.5));
}

/**
 * Calculates high shelf filter magnitude contribution.
 */
function highShelfGain(f, f_c, gain) {
    if (gain === 0) return 0;
    const x = f / f_c;
    return (gain * Math.pow(x, 1.5)) / (1 + Math.pow(x, 1.5));
}


// --- MAIN LOG-SCALE PLOTTER LOOP ---
function drawEQCurve() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    
    // Clear canvas
    ctx.fillStyle = "#06070a";
    ctx.fillRect(0, 0, width, height);
    
    // 1. DRAW BACKGROUND LOGARITHMIC GRID LINES
    ctx.strokeStyle = "rgba(255, 255, 255, 0.03)";
    ctx.lineWidth = 1;
    ctx.fillStyle = "rgba(156, 163, 175, 0.4)";
    ctx.font = "9px 'Plus Jakarta Sans'";
    
    const freqsToGrid = [
        20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000
    ];
    
    freqsToGrid.forEach(f => {
        const x = freqToX(f, width);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
        
        let label = f >= 1000 ? (f / 1000) + "kHz" : f + "Hz";
        ctx.fillText(label, x + 4, height - 8);
    });
    
    // 2. DRAW DECIBEL HORIZONTAL LABELS
    const dbLines = [12, 6, 0, -6, -12];
    ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
    dbLines.forEach(db => {
        const y = ((15 - db) / 30) * height;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
        
        ctx.fillStyle = db === 0 ? "rgba(0, 242, 254, 0.4)" : "rgba(156, 163, 175, 0.3)";
        ctx.fillText((db > 0 ? "+" : "") + db + "dB", 6, y - 4);
    });
    
    // 3. COMPUTE COMBINED MAGNITUDE VECTOR AND DRAW PLOT
    ctx.beginPath();
    
    const strength = activeMix.strength;
    const lowShelf = (activeEq.low_shelf_gain * strength) + activeMix.bass_boost;
    const pk1 = activeEq.first_band_gain * strength;
    const pk2 = (activeEq.second_band_gain * strength) + activeMix.vocal_clarity;
    const pk3 = (activeEq.third_band_gain * strength) + (activeMix.vocal_clarity * 0.5);
    const highShelf = (activeEq.high_shelf_gain * strength) + activeMix.airiness;
    
    const fc_low = activeEq.low_shelf_freq;
    const fc_1 = activeEq.first_band_freq;
    const fc_2 = activeEq.second_band_freq;
    const fc_3 = activeEq.third_band_freq;
    const fc_high = activeEq.high_shelf_freq;
    
    const q1 = activeEq.first_band_q;
    const q2 = activeEq.second_band_q;
    const q3 = activeEq.third_band_q;
    
    for (let xCoord = 0; xCoord < width; xCoord++) {
        const minLog = Math.log10(20);
        const maxLog = Math.log10(20000);
        const logFreq = minLog + (xCoord / width) * (maxLog - minLog);
        const f = Math.pow(10, logFreq);
        
        let totalGain = 0;
        totalGain += lowShelfGain(f, fc_low, lowShelf);
        totalGain += peakingGain(f, fc_1, pk1, q1);
        totalGain += peakingGain(f, fc_2, pk2, q2);
        totalGain += peakingGain(f, fc_3, pk3, q3);
        totalGain += highShelfGain(f, fc_high, highShelf);
        
        totalGain = Math.max(-15, Math.min(15, totalGain));
        const yCoord = ((15 - totalGain) / 30) * height;
        
        if (xCoord === 0) {
            ctx.moveTo(xCoord, yCoord);
        } else {
            ctx.lineTo(xCoord, yCoord);
        }
    }
    
    const gradient = ctx.createLinearGradient(0, 0, width, 0);
    gradient.addColorStop(0, '#7f00ff');
    gradient.addColorStop(0.5, '#00f2fe');
    gradient.addColorStop(1, '#ff007f');
    
    ctx.strokeStyle = gradient;
    ctx.lineWidth = 4;
    ctx.shadowBlur = 10;
    ctx.shadowColor = "rgba(0, 242, 254, 0.5)";
    ctx.stroke();
    
    ctx.shadowBlur = 0;
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    
    const fillGrad = ctx.createLinearGradient(0, 0, 0, height);
    fillGrad.addColorStop(0, 'rgba(0, 242, 254, 0.08)');
    fillGrad.addColorStop(1, 'rgba(127, 0, 255, 0.00)');
    ctx.fillStyle = fillGrad;
    ctx.fill();
}


// --- DYNAMIC SLIDER TRACK UPDATE HANDLERS ---
function updateSliderGlowBars() {
    const keys = ["low_shelf", "mid_1", "mid_2", "mid_3", "high_shelf"];
    keys.forEach(k => {
        const val = parseFloat(sliders[k].value);
        const percent = ((val + 15) / 30) * 100;
        document.getElementById(`glow_${k}`).style.height = `${percent}%`;
    });
}


// --- REST COMMUNICATIONS API CONTROLLER ---

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
    
    drawEQCurve();
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
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                updateApoAlert(data.state.apo_write_status);
                
                if (appMode === "auto" && data.state.mode === "manual") {
                    setUiMode("manual");
                }
            }
        })
        .catch(err => console.error("Failed to sync EQ settings:", err));
    }, 80);
}

function updateApoAlert(status) {
    const content = adminWarning.querySelector(".banner-content");
    
    if (status === "permission_denied") {
        adminWarning.classList.remove("hidden");
        adminWarning.className = "admin-warning-banner";
        content.innerHTML = `
            <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            <span><strong>Write Permission Error</strong>: Failed to write to Equalizer APO config.txt. Please right-click <strong>launch_isolated_gui.bat</strong> and select <strong>"Run as Administrator"</strong>. Local backup updated in <code>data/config.txt</code>.</span>
        `;
    } else if (status === "not_found") {
        adminWarning.classList.remove("hidden");
        adminWarning.className = "admin-warning-banner not-found";
        content.innerHTML = `
            <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
            <span><strong>Equalizer APO not detected</strong>: The default system directory <code>C:\\Program Files\\EqualizerAPO</code> was not found. System-wide EQ is bypassed, but you can view your live EQ values here and check local presets saved in <code>data/config.txt</code>.</span>
        `;
    } else {
        adminWarning.classList.add("hidden");
    }
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


// --- ACTIVE STATUS POLLING ---
function pollDashboardStatus() {
    fetch("/api/status")
    .then(res => res.json())
    .then(data => {
        updateApoAlert(data.apo_write_status);

        if (data.spotify_authenticated) {
            spotifyBanner.classList.add("hidden");
            disconnectSpotifyBtn.classList.remove("hidden");
        } else {
            spotifyBanner.classList.remove("hidden");
            disconnectSpotifyBtn.classList.add("hidden");
        }
        
        setUiMode(data.mode);
        aiEngineSelect.value = data.ai_engine;
        soundStyleSelect.value = data.sound_style;
        
        // Hide or show the preprocessed tag similarity centroids card based on active engine
        if (data.ai_engine === "llm") {
            weightsCard.classList.add("hidden");
        } else {
            weightsCard.classList.remove("hidden");
        }
        
        if (data.mode === "auto") {
            activeEq = data.eq;
            activeMix = data.mix;
            
            sliders.low_shelf.value = activeEq.low_shelf_gain;
            sliders.mid_1.value = activeEq.first_band_gain;
            sliders.mid_2.value = activeEq.second_band_gain;
            sliders.mid_3.value = activeEq.third_band_gain;
            sliders.high_shelf.value = activeEq.high_shelf_gain;
            
            sliderVals.low_shelf.innerText = (activeEq.low_shelf_gain >= 0 ? "+" : "") + activeEq.low_shelf_gain.toFixed(1) + " dB";
            sliderVals.mid_1.innerText = (activeEq.first_band_gain >= 0 ? "+" : "") + activeEq.first_band_gain.toFixed(1) + " dB";
            sliderVals.mid_2.innerText = (activeEq.second_band_gain >= 0 ? "+" : "") + activeEq.second_band_gain.toFixed(1) + " dB";
            sliderVals.mid_3.innerText = (activeEq.third_band_gain >= 0 ? "+" : "") + activeEq.third_band_gain.toFixed(1) + " dB";
            sliderVals.high_shelf.innerText = (activeEq.high_shelf_gain >= 0 ? "+" : "") + activeEq.high_shelf_gain.toFixed(1) + " dB";
            
            mixSliders.preamp.value = activeMix.preamp_gain;
            mixSliders.strength.value = activeMix.strength;
            mixSliders.bass.value = activeMix.bass_boost;
            mixSliders.vocal.value = activeMix.vocal_clarity;
            mixSliders.air.value = activeMix.airiness;
            
            mixVals.preamp.innerText = (activeMix.preamp_gain >= 0 ? "+" : "") + activeMix.preamp_gain.toFixed(2) + " dB";
            mixVals.strength.innerText = activeMix.strength.toFixed(2) + "x";
            mixVals.bass.innerText = "+" + activeMix.bass_boost.toFixed(1) + " dB";
            mixVals.vocal.innerText = "+" + activeMix.vocal_clarity.toFixed(1) + " dB";
            mixVals.air.innerText = "+" + activeMix.airiness.toFixed(1) + " dB";
            
            updateSliderGlowBars();
            drawEQCurve();
        }
        
        const track = data.current_track;
        
        // Update Mastering Insights explanation text directly
        mixingReason.innerText = track.mixing_reason || "No dynamic mixing profile loaded.";
        
        const isSongActive = track.track_name && 
            track.track_name !== "No Track Playing" && 
            track.track_name !== "Spotify Player Paused" && 
            track.track_name !== "Spotify Account Not Connected" &&
            track.track_name !== "🔒 Spotify Private Session Active" &&
            track.track_name !== "No Active Spotify Playback" &&
            track.track_name !== "Playback Not Supported" &&
            track.track_name !== "Spotify Active Playback Blocked";
            
        if (isSongActive) {
            trackTitle.innerText = track.track_name;
            trackArtist.innerText = track.artist_name;
            trackAlbum.innerText = track.album_name || "";
            
            if (track.album_art) {
                trackArt.src = track.album_art;
            } else {
                trackArt.src = "/static/placeholder_cover.png";
            }
            
            // Spin vinyl if song is actively playing, pause it if track is paused
            if (track.is_playing) {
                vinylSpindle.style.animationPlayState = "running";
            } else {
                vinylSpindle.style.animationPlayState = "paused";
            }
            
            let combinedTagsHtml = "";
            if (track.genres && track.genres.length > 0) {
                track.genres.forEach(g => {
                    combinedTagsHtml += `<span class="tag-badge genre">${g}</span>`;
                });
            }
            if (track.tags && track.tags.length > 0) {
                track.tags.forEach(t => {
                    combinedTagsHtml += `<span class="tag-badge">${t}</span>`;
                });
            }
            
            if (combinedTagsHtml === "") {
                tagsList.innerHTML = `<span class="no-tags-msg">No tags returned. Creating default profile.</span>`;
            } else {
                tagsList.innerHTML = combinedTagsHtml;
            }
            
            // Only render centroid weights progress bars if similarity engine is active
            if (data.ai_engine === "similarity") {
                let weightsHtml = "";
                const activeWeights = Object.entries(track.weights);
                
                if (activeWeights.length > 0) {
                    activeWeights.sort((a, b) => b[1] - a[1]);
                    activeWeights.forEach(([profile, w]) => {
                        if (w > 0) {
                            weightsHtml += `
                                <div class="weight-row">
                                    <div class="weight-info">
                                        <span class="weight-label">${profile}</span>
                                        <span class="weight-percentage">${(w * 100).toFixed(0)}%</span>
                                    </div>
                                    <div class="progress-track">
                                        <div class="progress-glow-bar" style="width: ${w * 100}%"></div>
                                    </div>
                                </div>
                            `;
                        }
                    });
                }
                
                if (weightsHtml === "") {
                    weightsList.innerHTML = `<div class="no-weights-msg">Flat Response profile (0% matches).</div>`;
                } else {
                    weightsList.innerHTML = weightsHtml;
                }
            }
            
        } else {
            trackTitle.innerText = track.track_name || "No Track Playing";
            trackArtist.innerText = track.artist_name || "Please stream Spotify or run search";
            trackAlbum.innerText = track.album_name || "";
            trackArt.src = "/static/placeholder_cover.png";
            vinylSpindle.style.animationPlayState = "paused";
            tagsList.innerHTML = `<span class="no-tags-msg">No active streaming metadata.</span>`;
            weightsList.innerHTML = `<div class="no-weights-msg">0% Active similarities.</div>`;
        }
        
        // Update Dynamic System Status & Pipeline Monitor List
        if (data.pipeline_status && pipelineStatusList) {
            let pipelineHtml = "";
            const statusConfig = {
                spotify: { label: "Spotify API Connection", icon: "🔌" },
                playback: { label: "Live Player Stream", icon: "🎵" },
                dsp_engine: { label: "Active EQ Engine", icon: "🧠" },
                apo_writer: { label: "Hardware EQ Config", icon: "🎛️" }
            };
            
            Object.entries(data.pipeline_status).forEach(([key, val]) => {
                const config = statusConfig[key] || { label: key, icon: "⚙️" };
                let bgStyle = "";
                let borderStyle = "";
                let textColor = "";

                const lowerVal = val.toLowerCase();
                if (lowerVal.includes("disconnected") || lowerVal.includes("error") || lowerVal.includes("failed") || lowerVal.includes("blocked")) {
                    bgStyle = "rgba(239, 68, 68, 0.08)";
                    borderStyle = "1px solid rgba(239, 68, 68, 0.25)";
                    textColor = "#fca5a5";
                } else if (lowerVal.includes("connected") || lowerVal.includes("active") || lowerVal.includes("success") || lowerVal.includes("vector") || lowerVal.includes("ai mixing")) {
                    bgStyle = "rgba(0, 242, 254, 0.08)";
                    borderStyle = "1px solid rgba(0, 242, 254, 0.25)";
                    textColor = "#67e8f9";
                } else {
                    bgStyle = "rgba(255, 255, 255, 0.03)";
                    borderStyle = "1px solid rgba(255, 255, 255, 0.08)";
                    textColor = "#cbd5e1";
                }
                
                pipelineHtml += `
                    <li style="display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.04); border-radius: 6px; padding: 6px 10px; margin-bottom: 2px;">
                        <span style="color: rgba(255,255,255,0.7); display: flex; align-items: center; gap: 6px; font-weight: 500;">
                            <span style="font-size: 14px;">${config.icon}</span>
                            <span>${config.label}</span>
                        </span>
                        <span style="margin: 0; font-size: 10px; padding: 3px 8px; border-radius: 4px; font-weight: 700; background: ${bgStyle}; border: ${borderStyle}; color: ${textColor}; letter-spacing: 0.02em;">
                            ${val}
                        </span>
                    </li>
                `;
            });
            pipelineStatusList.innerHTML = pipelineHtml;
        }
    })
    .catch(err => console.error("Status polling failed:", err));
}


// --- CONTROL BINDINGS ---

// Set vertical sliders drag bindings
const verticalSliders = ["low_shelf", "mid_1", "mid_2", "mid_3", "high_shelf"];
verticalSliders.forEach(k => {
    sliders[k].addEventListener("input", () => {
        const val = parseFloat(sliders[k].value);
        sliderVals[k].innerText = (val >= 0 ? "+" : "") + val.toFixed(1) + " dB";
        throttlePostUpdate();
    });
});

// Set mix sliders drag bindings
const mixKeys = ["preamp", "strength", "bass", "vocal", "air"];
mixKeys.forEach(k => {
    mixSliders[k].addEventListener("input", () => {
        const val = parseFloat(mixSliders[k].value);
        if (k === "preamp") {
            mixVals[k].innerText = (val >= 0 ? "+" : "") + val.toFixed(2) + " dB";
        } else if (k === "strength") {
            mixVals[k].innerText = val.toFixed(2) + "x";
        } else {
            mixVals[k].innerText = "+" + val.toFixed(1) + " dB";
        }
        throttlePostUpdate();
    });
});

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
    });
});

// Bind AI Engine select dropdown trigger
aiEngineSelect.addEventListener("change", () => {
    const targetEngine = aiEngineSelect.value;
    fetch("/api/engine", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({engine: targetEngine})
    })
    .then(res => res.json())
    .then(data => {
        pollDashboardStatus();
    })
    .catch(err => console.error("AI Engine Switcher error:", err));
});

// Bind Target Sound Style select dropdown trigger
soundStyleSelect.addEventListener("change", () => {
    const targetStyle = soundStyleSelect.value;
    fetch("/api/style", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({style: targetStyle})
    })
    .then(res => res.json())
    .then(data => {
        pollDashboardStatus();
    })
    .catch(err => console.error("Sound Style Switcher error:", err));
});

// Reset button click
resetEqBtn.addEventListener("click", () => {
    fetch("/api/eq/reset", {method: "POST"})
    .then(res => res.json())
    .then(data => {
        setUiMode("manual");
        pollDashboardStatus();
    });
});

// Redo Mix button click
redoMixBtn.addEventListener("click", () => {
    redoMixBtn.innerText = "Remixing...";
    redoMixBtn.disabled = true;
    
    fetch("/api/eq/redo", {method: "POST"})
    .then(res => res.json())
    .then(data => {
        redoMixBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2.5" fill="none" style="vertical-align: middle; margin-right: 4px;"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            <span>Redo Mix (Remix)</span>
        `;
        redoMixBtn.disabled = false;
        if (data.success) {
            pollDashboardStatus();
        } else {
            alert("Remix failed: " + data.message);
        }
    })
    .catch(err => {
        redoMixBtn.innerHTML = `
            <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2.5" fill="none" style="vertical-align: middle; margin-right: 4px;"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            <span>Redo Mix (Remix)</span>
        `;
        redoMixBtn.disabled = false;
        console.error("Remix failed:", err);
    });
});

// Bind Connect Spotify button click trigger
connectSpotifyBtn.addEventListener("click", () => {
    connectSpotifyBtn.innerText = "Authenticating...";
    connectSpotifyBtn.disabled = true;
    
    fetch("/api/spotify/authenticate", {method: "POST"})
    .then(res => res.json())
    .then(data => {
        setTimeout(() => {
            connectSpotifyBtn.innerText = "Connect Spotify Account";
            connectSpotifyBtn.disabled = false;
        }, 8000);
    })
    .catch(err => {
        connectSpotifyBtn.innerText = "Connect Spotify Account";
        connectSpotifyBtn.disabled = false;
        console.error("Spotify Auth Trigger Error:", err);
    });
});

// Bind Disconnect Spotify button click trigger
disconnectSpotifyBtn.addEventListener("click", () => {
    if (!confirm("Are you sure you want to disconnect your Spotify account? This will clear active credentials and require a fresh browser login.")) {
        return;
    }
    
    disconnectSpotifyBtn.innerText = "Disconnecting...";
    disconnectSpotifyBtn.disabled = true;
    
    fetch("/api/spotify/disconnect", {method: "POST"})
    .then(res => res.json())
    .then(data => {
        disconnectSpotifyBtn.innerText = "Disconnect Spotify";
        disconnectSpotifyBtn.disabled = false;
        pollDashboardStatus();
    })
    .catch(err => {
        disconnectSpotifyBtn.innerText = "Disconnect Spotify";
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
        const originalText = btn.innerText;
        btn.innerText = "Analyzing...";
        btn.disabled = true;
        
        fetch("/api/search", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({query: queryStr})
        })
        .then(res => res.json())
        .then(data => {
            btn.innerText = originalText;
            btn.disabled = false;
            searchQuery.value = "";
            
            if (data.success) {
                setUiMode("manual");
                pollDashboardStatus();
            } else {
                alert("Failed to find or analyze that track: " + data.message);
            }
        })
        .catch(err => {
            btn.innerText = originalText;
            btn.disabled = false;
            console.error("Search request error:", err);
        });
    });
}


// --- INITIAL STARTUP ---
setInterval(pollDashboardStatus, 1500);
pollDashboardStatus();
updateSliderGlowBars();
