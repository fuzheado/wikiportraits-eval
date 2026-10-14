#!/usr/bin/env python3
"""WikiPortraits Eval — web front-end for scoring Commons photos with LiftWing's
llm-clef-flash decision model.

    python3 app.py                 # local: http://127.0.0.1:8765
    PORT=8080 python3 app.py       # Toolforge sets PORT itself

Toolforge (Build Service): Procfile `web: python3 app.py`, no pip install needed.
Toolforge (traditional uWSGI backend): use wsgi.py, which exposes `app`.
"""
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import core

PORT = int(os.environ.get("PORT", "8765"))


def redact_query(msg: str) -> str:
    """Remove query strings from a log line before it is written.

    Data minimisation. A `?list=` deep link puts as much as 4 KB of user-supplied file names into the
    request line, and every parameter added later lands in the same slot — so nothing after the "?"
    belongs in a log. The path alone is what is useful for debugging; *how often* deep links are used
    is a question for a counter, not for a copy of the contents.

    Note what this does NOT try to do: the Toolforge platform logs every request itself, with the
    client IP and User-Agent, in /data/project/<tool>/logs/. That is the platform's log, not ours to
    change, publish, or export — see the privacy note in HANDOFF §13.
    """
    return re.sub(r"\?[^ \"']*", "?<redacted>", msg)


class Handler(BaseHTTPRequestHandler):
    server_version = "WikiPortraitsEval/0.4"

    def log_message(self, fmt, *args):
        # `BaseHTTPRequestHandler.log_request` formats the request line, status and size — it does
        # not include the client address (the default `log_message` would). So this log has no IP and
        # no User-Agent by construction; the redaction below removes the query as well.
        if os.environ.get("QUIET") != "1":
            print(f"[{self.log_date_time_string()}] {redact_query(fmt % args)}", flush=True)

    def _respond(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        status, ctype, payload, extra = core.route(self.command, urlparse(self.path).path, body,
                                                    urlparse(self.path).query)
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
