#!/usr/bin/env python3
"""
Lightweight proxy + static UI for the Document Verification service.

The browser talks only to this server (same origin, so no CORS needed).
This server forwards uploads to the upstream Cloud Run API and returns its JSON.

Standard library only - run with:  python server.py
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = os.environ.get(
    "UPSTREAM_URL", "https://fraud-detection-188993707104.europe-west1.run.app"
).rstrip("/")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "15"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "4"))      # upstream calls in flight
UPSTREAM_TIMEOUT = int(os.environ.get("UPSTREAM_TIMEOUT", "180"))  # seconds per analysis

STATIC_DIR = (Path(__file__).parent / "static").resolve()
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
CSP = (
    "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class Handler(BaseHTTPRequestHandler):
    server_version = "DocVerifyProxy/0.1"

    # ---- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # never logs request bodies
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send(status, json.dumps({"status": "error", "error": message}))

    def _path(self):
        return urllib.parse.unquote(self.path.split("?", 1)[0])

    # ---- routing --------------------------------------------------------
    def do_GET(self):
        path = self._path()
        if path == "/api/health":
            return self._health()
        if path.startswith("/api/"):
            return self._error(404, "Not found")
        return self._static(path)

    def do_POST(self):
        if self._path() == "/api/analyze":
            return self._analyze()
        return self._error(404, "Not found")

    # ---- handlers -------------------------------------------------------
    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in target.parents or not target.is_file():
            return self._send(404, "Not found", "text/plain; charset=utf-8")
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype, {"Content-Security-Policy": CSP})

    def _health(self):
        try:
            with urllib.request.urlopen(UPSTREAM + "/health", timeout=15) as r:
                up = r.status == 200
        except Exception:
            up = False
        self._send(200, json.dumps({"status": "ok" if up else "down", "upstream": up}))

    def _analyze(self):
        ctype = self.headers.get("Content-Type", "")
        if not ctype.lower().startswith("multipart/form-data"):
            return self._error(415, "Expected multipart/form-data with a 'file' field")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "Content-Length required")
        if length <= 0:
            return self._error(400, "Empty upload")
        if length > MAX_UPLOAD_MB * 1024 * 1024:
            return self._error(413, f"File too large (max {MAX_UPLOAD_MB:g} MB)")

        body = self.rfile.read(length)  # multipart body is forwarded untouched

        if not _slots.acquire(timeout=30):
            return self._error(429, "Server busy - please retry shortly")
        started = time.monotonic()
        try:
            req = urllib.request.Request(
                UPSTREAM + "/api/analyze",
                data=body,
                method="POST",
                headers={
                    "Content-Type": ctype,
                    "Accept": "application/json",
                    "User-Agent": "DocVerifyProxy/0.1",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:
                    status, data = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, data = e.code, e.read()
        except TimeoutError:
            return self._error(504, "Analysis timed out at the verification service")
        except (urllib.error.URLError, OSError) as e:
            return self._error(502, f"Could not reach the verification service ({e.__class__.__name__})")
        finally:
            _slots.release()

        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            json.loads(data)
        except ValueError:
            return self._error(502, f"Verification service returned a non-JSON response (HTTP {status})")
        self._send(status, data, extra={"X-Upstream-Ms": str(elapsed_ms)})


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Document Check UI:  http://{HOST}:{PORT}   (demo data: http://{HOST}:{PORT}/?demo)")
    print(f"Forwarding to:      {UPSTREAM}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
