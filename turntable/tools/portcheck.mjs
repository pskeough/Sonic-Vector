/* ════════════════════════════════════════════════════════════════════════
   portcheck.mjs — exit 0 if something accepts a TCP connection on a port.

       node tools/portcheck.mjs 5177

   PROTOTYPE TOOLING ONLY.

   This is a separate file rather than a `node -e "…"` one-liner in the batch
   script, and that is the entire point of it. The one-liner version contained
   arrow functions, and cmd.exe parses the `)` in `() => {}` as the end of an
   enclosing block — so the moment the probe was used inside a `for /l` retry
   loop, the loop's own parenthesis was closed early and the script silently
   reported that the server had failed to start. Batch has no way to escape
   that cleanly. A file has no quoting problem at all.

   An actual connect is also a stronger check than parsing netstat: netstat's
   output is localised and its columns shift between Windows builds, and a
   socket in LISTENING state is not proof that anything will answer.
   ════════════════════════════════════════════════════════════════════════ */

import net from "node:net";

const port = Number(process.argv[2]);
const host = process.argv[3] || "127.0.0.1";
const timeoutMs = Number(process.argv[4] || 800);

if (!Number.isFinite(port) || port <= 0) {
  console.error("usage: node portcheck.mjs <port> [host] [timeoutMs]");
  process.exit(2);
}

const socket = net.connect(port, host);

const done = code => {
  socket.destroy();
  process.exit(code);
};

socket.once("connect", () => done(0));
socket.once("error", () => done(1));
setTimeout(() => done(1), timeoutMs).unref();
