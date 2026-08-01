/* ════════════════════════════════════════════════════════════════════════
   devserver.mjs — static file server, live-data proxy, frame capture sink.

   PROTOTYPE TOOLING ONLY. Nothing here ships.

   Jobs:
     GET  /*            serve the prototype folder, with correct MIME types for
                        ES modules (a wrong Content-Type on a .js file makes
                        the browser refuse the module outright).
     GET  /api/status   proxy to the running SonicVectorEQ app.
     GET  /art?u=…      proxy album art bytes.
     POST /shot         accept a base64 image and write it to shots/.

   WHY PROXY AT ALL
   ----------------
   The view is served from :5177 and the app from :5001, so every direct call
   would be cross-origin. Proxying keeps it same-origin, which buys two things
   that matter and are not interchangeable:

     · /api/status works without the app sending any CORS headers, so the app
       needs no modification whatsoever.
     · Album art arrives same-origin, which is a hard requirement rather than a
       convenience: the art becomes a WebGL texture, and uploading a
       cross-origin image the browser considers tainted is refused outright.
       Spotify's CDN may or may not send permissive CORS headers on any given
       day, and SMTC art is a relative path on the app that a browser pointed
       at :5177 could not resolve at all.

   The capture sink is separate, and exists because the render has to be LOOKED
   at. Automated checks can prove the scene compiled and the GL context is
   error-free, and both of those can be true of a completely black frame.
   ════════════════════════════════════════════════════════════════════════ */

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SHOTS = path.join(ROOT, "shots");
const PORT = Number(process.argv[2] || 5177);

/* Where SonicVectorEQ listens. web_gui_app.py reads SONICVECTOR_PORT and
   defaults to 5001; match that so the two cannot drift apart. */
const APP_PORT = Number(process.argv[3] || process.env.SONICVECTOR_PORT || 5001);
const APP_ORIGIN = `http://127.0.0.1:${APP_PORT}`;

fs.mkdirSync(SHOTS, { recursive: true });

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js":   "text/javascript; charset=utf-8",
  ".mjs":  "text/javascript; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png":  "image/png",
  ".jpg":  "image/jpeg",
  ".ico":  "image/x-icon",
  ".webmanifest": "application/manifest+json",
};

const json = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
};

/* ── Live data proxy ────────────────────────────────────────────────────
   A short timeout on purpose. The view polls every 1.5 s, so a request that
   has not answered in a second is already late; failing fast lets the client
   drop to its demo feed rather than queueing requests behind a hung app. */
async function proxyStatus(res) {
  try {
    const upstream = await fetch(`${APP_ORIGIN}/api/status`, {
      signal: AbortSignal.timeout(1200),
      headers: { Accept: "application/json" },
    });
    if (!upstream.ok) return json(res, 502, { error: `app returned ${upstream.status}` });
    const body = await upstream.text();
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(body);
  } catch (err) {
    /* 503 is the signal the client watches for to fall back to demo data.
       Not an error condition — the app simply is not running. */
    json(res, 503, { error: "SonicVectorEQ is not reachable", origin: APP_ORIGIN });
  }
}

async function proxyArt(res, raw) {
  let target;
  if (!raw) return json(res, 400, { error: "missing u" });

  if (raw.startsWith("/")) {
    /* SMTC art: a relative path on the app, e.g. /api/art/<16 hex>. */
    target = APP_ORIGIN + raw;
  } else if (/^https:\/\//i.test(raw)) {
    /* Remote art, e.g. Spotify's i.scdn.co. https only — this proxy runs on
       the user's machine, and an open relay that accepted arbitrary schemes or
       plain http would let any page reach services behind their firewall. */
    target = raw;
  } else {
    return json(res, 400, { error: "only https URLs or app-relative paths" });
  }

  try {
    const upstream = await fetch(target, { signal: AbortSignal.timeout(6000) });
    if (!upstream.ok) return json(res, upstream.status, { error: "art fetch failed" });
    const buf = Buffer.from(await upstream.arrayBuffer());
    res.writeHead(200, {
      "Content-Type": upstream.headers.get("content-type") || "image/jpeg",
      "Content-Length": buf.length,
      /* Cover art for a given track never changes, and the view re-requests it
         on every track change. */
      "Cache-Control": "public, max-age=86400",
    });
    res.end(buf);
  } catch {
    json(res, 504, { error: "art fetch timed out" });
  }
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (req.method === "GET" && url.pathname === "/api/status") {
    proxyStatus(res);
    return;
  }

  if (req.method === "GET" && url.pathname === "/art") {
    proxyArt(res, url.searchParams.get("u"));
    return;
  }

  if (req.method === "POST" && req.url.startsWith("/shot")) {
    let body = "";
    req.on("data", c => { body += c; });
    req.on("end", () => {
      try {
        const { name = "frame", data } = JSON.parse(body);
        const b64 = data.replace(/^data:image\/\w+;base64,/, "");
        const ext = data.startsWith("data:image/png") ? "png" : "jpg";
        /* Basename only — a name from the page must never be able to write
           outside the shots directory. */
        const safe = path.basename(String(name)).replace(/[^\w.-]/g, "_");
        const file = path.join(SHOTS, `${safe}.${ext}`);
        fs.writeFileSync(file, Buffer.from(b64, "base64"));
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, file, bytes: Buffer.byteLength(b64, "base64") }));
        console.log(`[shot] ${file}`);
      } catch (err) {
        res.writeHead(400).end(String(err));
      }
    });
    return;
  }

  let rel = decodeURIComponent(url.pathname);
  if (rel === "/") rel = "/index.html";
  const file = path.join(ROOT, rel);

  if (!file.startsWith(ROOT)) { res.writeHead(403).end("forbidden"); return; }

  fs.readFile(file, (err, buf) => {
    if (err) { res.writeHead(404).end("not found"); return; }
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    res.end(buf);
  });
});

server.listen(PORT, () => {
  console.log(`turntable view   http://localhost:${PORT}`);
  console.log(`proxying app at  ${APP_ORIGIN}`);
});
