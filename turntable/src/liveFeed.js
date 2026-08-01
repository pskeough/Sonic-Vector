/* ════════════════════════════════════════════════════════════════════════
   liveFeed.js — real playback data from the running SonicVectorEQ app.

   Drop-in replacement for MockFeed: same subscribe/emit contract, same payload
   shape, so the choreographer cannot tell them apart. It polls GET /api/status
   through the dev server's proxy on the same 1.5 s cadence static/app.js uses,
   because matching that cadence is the whole reason the changeover prediction
   exists.

   WHAT THE APP ACTUALLY SENDS
   ---------------------------
   web_gui_app.py returns the whole app_state; current_track carries:

     track_id, track_name, artist_name, album_name, album_art,
     genres, tags, weights, source, placeholder, mixing_reason,
     is_playing, is_private_session

   Two consequences drive most of this file:

   1. THERE IS NO progress_ms OR duration_ms. Both exist in
      src/spotify/service.py and are simply not forwarded. The tonearm needs a
      position, so one is kept locally — see the clock below.

   2. album_art IS NOT ONE KIND OF THING. Spotify gives an absolute
      https://i.scdn.co/... URL. Local playback via the Windows media session
      gives a RELATIVE path, "/api/art/<16 hex>", served by the app itself — a
      page on :5177 cannot resolve that at all. Both go through the proxy, and
      the empty case falls through to a generated house label.
   ════════════════════════════════════════════════════════════════════════ */

const POLL_INTERVAL_MS = 1500;

/* Assumed track length when the app does not tell us one. Only ever used to
   move the tonearm; never to decide anything. See the note on prediction. */
const ASSUMED_DURATION_MS = 240000;

/* How far into an ASSUMED duration the arm is allowed to travel. Parking at
   the run-out groove is a meaningful position — it is where a side ends — so
   an estimated clock must never reach it. */
const ESTIMATE_CEILING = 0.9;

export class LiveFeed {
  constructor({ onConnectionChange } = {}) {
    this.subscribers = new Set();
    this.timer = null;
    this.connected = false;
    this.onConnectionChange = onConnectionChange || (() => {});
    this.failures = 0;

    /* Local playback clock. The app reports what is playing but not where in
       the track it is, so elapsed time is accumulated here: reset when
       track_id changes, advanced only while is_playing. It is an estimate and
       is labelled as one in the payload. */
    this.clockTrackId = null;
    this.clockElapsed = 0;
    this.clockStamp = 0;
  }

  start() {
    if (this.timer !== null) return;
    this.poll();
    this.timer = setInterval(() => this.poll(), POLL_INTERVAL_MS);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }

  subscribe(fn) {
    this.subscribers.add(fn);
    return () => this.subscribers.delete(fn);
  }

  setConnected(connected, detail) {
    if (connected === this.connected) return;
    this.connected = connected;
    this.onConnectionChange(connected, detail);
  }

  async poll() {
    try {
      const res = await fetch("/api/status", { cache: "no-store" });

      if (res.status === 503) {
        /* The proxy's signal that the app is not running. Distinct from a
           network error, and not worth retrying differently. */
        this.failures++;
        this.setConnected(false, "SonicVectorEQ is not running");
        return;
      }
      if (!res.ok) {
        this.failures++;
        this.setConnected(false, `app returned ${res.status}`);
        return;
      }

      const state = await res.json();
      this.failures = 0;
      this.setConnected(true, state.ai_engine ? `engine: ${state.ai_engine}` : "");
      this.emit(this.normalise(state));
    } catch (err) {
      this.failures++;
      this.setConnected(false, String(err.message || err));
    }
  }

  /* Map the app's payload onto the shape the view consumes. */
  normalise(state) {
    const t = (state && state.current_track) || {};
    const isLive = Boolean(t.track_name) && t.placeholder !== true;
    const trackId = t.track_id || (isLive ? `name:${t.artist_name}:${t.track_name}` : "");

    const { progress_ms, duration_ms, estimated } = this.advanceClock(
      trackId, Boolean(t.is_playing), t,
    );

    return {
      current_track: {
        track_id: trackId,
        track_name: t.track_name || "",
        artist_name: t.artist_name || "",
        album_name: t.album_name || "",
        album_art: proxiedArt(t.album_art),
        is_playing: Boolean(t.is_playing),
        source: t.source || "idle",
        placeholder: t.placeholder === true || !isLive,
        progress_ms,
        duration_ms,
        /* The choreographer checks this before predicting a changeover — see
           advanceClock. */
        duration_estimated: estimated,
      },
    };
  }

  advanceClock(trackId, isPlaying, raw) {
    const now = performance.now();

    if (trackId !== this.clockTrackId) {
      this.clockTrackId = trackId;
      this.clockElapsed = 0;
      this.clockStamp = now;
    } else if (isPlaying) {
      this.clockElapsed += now - this.clockStamp;
    }
    this.clockStamp = now;

    /* If the app ever starts forwarding the real figures — they already exist
       in src/spotify/service.py — use them and stop guessing. */
    const realDuration = Number(raw.duration_ms) || 0;
    const realProgress = Number(raw.progress_ms) || 0;
    if (realDuration > 0) {
      return {
        progress_ms: realProgress || Math.min(this.clockElapsed, realDuration),
        duration_ms: realDuration,
        estimated: false,
      };
    }

    /* Otherwise: an estimated position, capped short of the run-out groove.
       Letting an estimate reach the run-out would trigger a speculative
       changeover on a track that is still playing — the animation would lift
       the record off mid-song and put it straight back. */
    return {
      progress_ms: Math.min(this.clockElapsed, ASSUMED_DURATION_MS * ESTIMATE_CEILING),
      duration_ms: ASSUMED_DURATION_MS,
      estimated: true,
    };
  }

  emit(payload) {
    this.subscribers.forEach(fn => fn(payload));
  }
}

/* Route art through the proxy so it arrives same-origin and can be uploaded as
   a WebGL texture. Handles both shapes the app produces, and passes data URIs
   straight through — they are already same-origin by definition. */
export function proxiedArt(url) {
  if (!url) return "";
  if (url.startsWith("data:")) return url;
  return `/art?u=${encodeURIComponent(url)}`;
}

/* One-shot probe, used at boot to decide between live and demo data without
   waiting out a poll interval. */
export async function appReachable() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}
