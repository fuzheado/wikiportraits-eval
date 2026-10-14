#!/usr/bin/env python3
"""Offline checks that the app's own logging stays inside the data-minimisation rules.

The app prints one line per request to stdout, which on Toolforge lands in the container log. Two
properties must hold, and both are easy to undo by accident:

  * **no query string** — a `?list=` deep link carries up to 4 KB of user-supplied file names into the
    request line, and every parameter added later lands in the same slot;
  * **no client address and no User-Agent** — `BaseHTTPRequestHandler.log_request` formats only the
    request line, status and size, so this holds by construction; the assertions below keep it that
    way.

Out of scope, and deliberately so: the **platform's** request log (Toolforge writes IP and User-Agent
to `/data/project/<tool>/logs/`) is not this app's to change, publish or export. See HANDOFF §13.

Costs nothing: no network.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import app  # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got!r}" + ("" if ok else f"  want {want!r}"))
    if not ok:
        FAIL.append(name)


# --- a ?list= deep link must not put the user's file names in the log -------------------------
raw = '"GET /?list=File%3ACalli+McMurray+at+2026+AAAS.jpg%0AFile%3AOther.jpg HTTP/1.1" 200 -'
# positive control first: if this fails, the assertions below prove nothing
check("control: the raw line does contain the file name", "Calli" in raw, True)
red = app.redact_query(raw)
check("the file name does not survive redaction", "Calli" in red, False)
check("the path survives", red.startswith('"GET /?'), True)
check("the status survives", red.endswith("200 -"), True)
check("it says it redacted rather than dropping it silently", "<redacted>" in red, True)

# --- requests without a query are left exactly as they were -----------------------------------
check("a plain request is unchanged",
      app.redact_query('"POST /api/run HTTP/1.1" 200 -'), '"POST /api/run HTTP/1.1" 200 -')
check("a report link keeps its path",
      app.redact_query('"GET /api/job/abc123/report.pdf?view=pose HTTP/1.1" 200 -'),
      '"GET /api/job/abc123/report.pdf?<redacted> HTTP/1.1" 200 -')

# --- nothing in the logging path asks for a client identity -----------------------------------
src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
check("the log never asks for the remote address", "address_string" in src, False)
check("the log never asks for the peer address", "client_address" in src, False)
# Statement-level, not a bare substring: the comments in app.py legitimately *mention*
# User-Agent, and a check on the word alone would be satisfied by prose rather than by code.
check("the log never reads a User-Agent header",
      re.search(r"headers(?:\.get\(|\[)\s*['\"]User-Agent", src) is not None, False)

print()
print(f"FAILURES: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
