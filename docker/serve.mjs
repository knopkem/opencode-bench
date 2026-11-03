#!/usr/bin/env node
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(process.argv[2] || "/app");
const port = Number(process.argv[3] || 4173);
const host = process.argv[4] || "127.0.0.1";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".map": "application/json; charset=utf-8",
};

function send(res, status, body, type = "text/plain; charset=utf-8") {
  res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(body);
}

function safePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const joined = path.resolve(root, "." + decoded);
  return joined === root || joined.startsWith(root + path.sep) ? joined : null;
}

const server = http.createServer((req, res) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    send(res, 405, "method not allowed");
    return;
  }

  let file = safePath(new URL(req.url || "/", "http://localhost").pathname);
  if (!file) {
    send(res, 403, "forbidden");
    return;
  }

  try {
    if (fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
  } catch {
    // fall through to the SPA fallback
  }

  fs.readFile(file, (err, data) => {
    if (err) {
      // Single-page app fallback: unknown routes get index.html.
      if (!path.extname(file)) {
        fs.readFile(path.join(root, "index.html"), (indexErr, index) => {
          if (indexErr) send(res, 404, "not found");
          else send(res, 200, index, MIME[".html"]);
        });
        return;
      }
      send(res, 404, "not found");
      return;
    }
    send(res, 200, data, MIME[path.extname(file).toLowerCase()] || "application/octet-stream");
  });
});

server.listen(port, host, () => {
  console.log(`opencode-bench static server: http://${host}:${port} -> ${root}`);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
