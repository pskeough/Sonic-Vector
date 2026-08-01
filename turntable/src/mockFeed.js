/* ════════════════════════════════════════════════════════════════════════
   mockFeed.js — stands in for the real app's GET /api/status poll.

   The prototype must never talk to the running SonicVectorEQ server, so this
   module fabricates the exact payload shape web_gui_app.py returns:

     current_track: {
       track_id, track_name, artist_name, album_name,
       album_art, is_playing, source, placeholder,
       progress_ms, duration_ms          <- not yet exposed by the real /api/status,
     }                                      but present in src/spotify/service.py

   Two things are deliberately faithful to production and are the whole reason
   this file exists:

     1. The poll is on a 1500 ms interval. The view therefore learns about a
        track change up to 1.5 s AFTER it happened, with zero advance warning.
        Any choreography that assumes it can pre-roll is lying to itself.
     2. album_art is a cross-origin URL in production. Here it is a data URI
        from the procedural cover generator, which is also the real fallback
        path when Spotify hands us a track with no artwork.
   ════════════════════════════════════════════════════════════════════════ */

import { makeProceduralCover } from "./proceduralCover.js";

const POLL_INTERVAL_MS = 1500;

/* A small fake library. Durations are real-ish so the tonearm sweep is honest:
   the arm's radial position is progress/duration, so a 2-minute song and a
   7-minute song must visibly differ in how fast the needle crawls inward. */
const LIBRARY = [
  { track_name: "Blue in Green",        artist_name: "Miles Davis",       album_name: "Kind of Blue",            duration_ms: 337000, hue: 205 },
  { track_name: "A Love Supreme, Pt. I", artist_name: "John Coltrane",     album_name: "A Love Supreme",          duration_ms: 452000, hue: 28  },
  { track_name: "Teardrop",             artist_name: "Massive Attack",     album_name: "Mezzanine",               duration_ms: 330000, hue: 168 },
  { track_name: "Windowlicker",         artist_name: "Aphex Twin",         album_name: "Windowlicker",            duration_ms: 366000, hue: 96  },
  { track_name: "Nights",               artist_name: "Frank Ocean",        album_name: "Blonde",                  duration_ms: 307000, hue: 12  },
  { track_name: "Svefn-g-englar",       artist_name: "Sigur Ros",          album_name: "Agaetis byrjun",          duration_ms: 602000, hue: 190 },
  { track_name: "Xtal",                 artist_name: "Aphex Twin",         album_name: "Selected Ambient Works",  duration_ms: 293000, hue: 268 },
  { track_name: "Avril 14th",           artist_name: "Aphex Twin",         album_name: "Drukqs",                  duration_ms: 125000, hue: 340 },
];

export class MockFeed {
  constructor() {
    this.index = 0;
    this.playing = true;
    this.startedAt = performance.now();
    /* Covers are generated once and cached by album, exactly as a real texture
       cache would be — regenerating a 1024px canvas on every poll would be a
       self-inflicted stutter. */
    this.coverCache = new Map();
    this.subscribers = new Set();
    this.timer = null;
    /* Playback speed multiplier. The tonearm sweep across a 5-minute song is
       far too slow to evaluate by eye, so the harness can run the clock hot. */
    this.timeScale = 1;
  }

  start() {
    if (this.timer !== null) return;
    this.emit();
    this.timer = setInterval(() => this.emit(), POLL_INTERVAL_MS);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }

  subscribe(fn) {
    this.subscribers.add(fn);
    return () => this.subscribers.delete(fn);
  }

  cover(entry) {
    if (!this.coverCache.has(entry.album_name)) {
      this.coverCache.set(entry.album_name, makeProceduralCover(entry));
    }
    return this.coverCache.get(entry.album_name);
  }

  currentPayload() {
    const entry = LIBRARY[this.index];
    const elapsed = (performance.now() - this.startedAt) * this.timeScale;
    /* Clamp rather than wrap. A song that has run past its end should sit at
       the run-out groove, which is a state the view has to render correctly. */
    const progress_ms = Math.min(elapsed, entry.duration_ms);

    return {
      current_track: {
        track_id: `mock:${this.index}`,
        track_name: entry.track_name,
        artist_name: entry.artist_name,
        album_name: entry.album_name,
        album_art: this.cover(entry),
        is_playing: this.playing,
        source: "spotify",
        placeholder: false,
        progress_ms,
        duration_ms: entry.duration_ms,
      },
    };
  }

  emit() {
    const payload = this.currentPayload();
    this.subscribers.forEach(fn => fn(payload));
  }

  /* ── Harness controls ────────────────────────────────────────────────── */

  next()     { this.jumpTo((this.index + 1) % LIBRARY.length); }
  previous() { this.jumpTo((this.index - 1 + LIBRARY.length) % LIBRARY.length); }

  jumpTo(index) {
    this.index = index;
    this.startedAt = performance.now();
    /* Deliberately does NOT emit. In production the change happens on
       Spotify's side and the view stays ignorant until the next poll fires.
       Emitting here would hide the exact latency the choreography must absorb. */
  }

  setPlaying(playing) {
    if (playing === this.playing) return;
    if (playing) {
      /* Resume where we paused rather than snapping the needle back. */
      this.startedAt = performance.now() - this.pausedElapsed / this.timeScale;
    } else {
      this.pausedElapsed = (performance.now() - this.startedAt) * this.timeScale;
    }
    this.playing = playing;
  }

  /* Drop the needle at an arbitrary point, for inspecting the arm at the
     lead-in, mid-record and run-out without waiting out a whole song. */
  seekFraction(f) {
    const entry = LIBRARY[this.index];
    this.startedAt = performance.now() - (entry.duration_ms * f) / this.timeScale;
  }

  setIdle() {
    /* The "no track loaded" state the real backend sends on startup and after
       a Spotify disconnect. The view must have a resting pose for this. */
    this.subscribers.forEach(fn => fn({
      current_track: {
        track_id: "", track_name: "", artist_name: "", album_name: "",
        album_art: "", is_playing: false, source: "idle", placeholder: true,
        progress_ms: 0, duration_ms: 0,
      },
    }));
  }

  get library() { return LIBRARY; }
}
