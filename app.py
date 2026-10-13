#!/usr/bin/env python3
"""WikiPortraits Eval — web front-end for scoring Commons photos with LiftWing's
llm-clef-flash decision model.

    python3 app.py                 # local: http://127.0.0.1:8765
    PORT=8080 python3 app.py       # Toolforge sets PORT itself

Toolforge (Build Service): Procfile `web: python3 app.py`, no pip install needed.
Toolforge (traditional uWSGI backend): use wsgi.py, which exposes `app`.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import core

PORT = int(os.environ.get("PORT", "8765"))


class Handler(BaseHTTPRequestHandler):
    server_version = "WikiPortraitsEval/0.4"

    def log_message(self, fmt, *args):
        if os.environ.get("QUIET") != "1":
            print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def _respond(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        status, ctype, payload, extra = core.route(self.command, urlparse(self.path).path, body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_HEAD = _respond


if __name__ == "__main__":
    print(f"WikiPortraits Eval on http://0.0.0.0:{PORT} · model llm-clef-flash · "
          f"pacing {core.SPACING}s · thumb {core.THUMB_WIDTH}px · cache {core.CACHE_DIR}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
