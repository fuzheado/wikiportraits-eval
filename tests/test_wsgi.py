#!/usr/bin/env python3
"""Test the WSGI shim (the traditional Toolforge backend path) without a server."""
import io, json
import os
import sys

# this file lives in tests/; the app's modules and data files are at the repo root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wsgi
import core      # noqa: E402  (for MAX_INPUT_ITEMS and the CACHE_DIR the run cap touches)
import wiclient  # noqa: E402  (the resolver, which /api/parse reaches for)

# `/api/parse` resolves through wiclient, i.e. it calls Commons for real. This suite is listed as
# offline in CI, so stub the resolver: a Wikimedia outage or 429 must not be able to redden an
# unrelated commit, and CI should not be making unrequested API calls at all. The stub keeps the
# shape of a real reply (title, pageid, dimensions, licence) so the assertions below still mean
# something.
def _offline_resolve(titles):
    return {(t.get("wiki") or "commons.wikimedia.org", t["title"]):
            {"ok": True, "pageid": 1000 + n, "orig_w": 4000, "orig_h": 6000,
             "mime": "image/jpeg", "license": "CC BY-SA 4.0"}
            for n, t in enumerate(titles)}


wiclient.resolve = _offline_resolve


def call(method, path, body=b"", ctype="application/json"):
    captured = {}
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": ctype,
               "wsgi.input": io.BytesIO(body), "SERVER_NAME": "test", "SERVER_PORT": "80"}
    out = wsgi.app(environ, lambda status, headers: captured.update(status=status, headers=dict(headers)))
    return captured, b"".join(out)


c, b = call("GET", "/api/health")
print("GET /api/health ->", c["status"], b.decode())
assert c["status"].startswith("200") and json.loads(b)["ok"] is True

c, b = call("GET", "/")
print("GET / ->", c["status"], c["headers"]["Content-Type"], len(b), "bytes; has textarea:",
      b'id="input"' in b)
assert c["status"].startswith("200") and b'id="input"' in b

c, b = call("POST", "/api/parse", json.dumps({"text": "File:Example.svg\nnonsense"}).encode())
d = json.loads(b)
print("POST /api/parse ->", c["status"], [(i["title"], i["ok"]) for i in d["items"]],
      "warnings:", [w["raw"] for w in d["warnings"]])
assert d["items"] and d["warnings"]

c, b = call("GET", "/api/job/nope")
print("GET /api/job/nope ->", c["status"], b.decode())
assert c["status"].startswith("404")

c, b = call("POST", "/api/run", json.dumps({"text": ""}).encode())
print("POST /api/run (empty) ->", c["status"], b.decode())
assert c["status"].startswith("400")

# --- the run cap at its boundary, through the HTTP layer ---------------------------------------
# This is the boundary a user actually meets: *Validate files* may resolve up to PARSE_MAX (1000) while
# a run accepts MAX_INPUT_ITEMS, so the next click used to answer 413 after a green light. Patching the
# cap SMALL keeps the accepted side cheap (a real 200-photo run is ~3 minutes of SPACING) and proves
# the guard reads `core.MAX_INPUT_ITEMS` at request time rather than baking a number in.
#
# Everything the run touches is stubbed, including the pre-flight: without that this test would call
# LiftWing for real, which is exactly the sort of thing CI must never do.
import tempfile  # noqa: E402
import wiclient  # noqa: E402

saved = (core.MAX_INPUT_ITEMS, core.CACHE_DIR, wiclient.resolve, wiclient.score_image,
         wiclient.probe_endpoint, wiclient.SPACING)
core.MAX_INPUT_ITEMS = 3
core.CACHE_DIR = tempfile.mkdtemp(prefix="wp-wsgi-cap-")


def _fake_resolve(titles):
    return {(t.get("wiki") or "commons.wikimedia.org", t["title"]):
            {"ok": True, "pageid": 9000 + n, "orig_w": 800, "orig_h": 600,
             "mime": "image/jpeg", "license": "CC0"}
            for n, t in enumerate(titles)}


wiclient.resolve = _fake_resolve
wiclient.score_image = lambda b64, budget: {"http": 200, "wall_s": 0.0, "ttfb_s": 0.0,
                                            "usage": {"input_tokens": 900, "output_tokens": 0},
                                            "answers": {}}
wiclient.probe_endpoint = lambda: {"ok": True, "http": 200, "wall_s": 0.01, "error": None}
wiclient.SPACING = 0.0
try:
    def names(n):
        return json.dumps({"text": "\n".join(f"File:Cap {i}.jpg" for i in range(n))}).encode()

    # one over the cap: refused, and the message must state the limit and the count.
    # Do this BEFORE starting a run — /api/run checks MAX_PARALLEL_JOBS first, so an in-flight job
    # answers 409 "another run is already in progress" and the cap is never reached (which is also
    # worth knowing: a user with a run going gets 409, not 413).
    c, b = call("POST", "/api/run", names(4))
    d = json.loads(b)
    print("POST /api/run (4 items, cap 3) ->", c["status"], d.get("error"))
    assert c["status"].startswith("413"), (c["status"], d)
    assert "3" in d["error"] and "4" in d["error"], d["error"]

    # at the cap: accepted (asserted from the response, not by waiting — the worker is a daemon thread)
    c, b = call("POST", "/api/run", names(3))
    d = json.loads(b)
    print("POST /api/run (3 items, cap 3) ->", c["status"], {k: d.get(k) for k in ("job_id", "total")})
    assert c["status"].startswith("200") and d.get("total") == 3, (c["status"], d)

    # and the cap the page is told about is the one enforced here
    c, b = call("GET", "/api/config")
    cfg = json.loads(b)
    print("GET /api/config ->", {k: cfg.get(k) for k in ("max_input_items", "eval_max", "parse_max",
                                                         "max_body_bytes")})
    assert cfg["max_input_items"] == core.MAX_INPUT_ITEMS, cfg.get("max_input_items")
    assert cfg["max_body_bytes"] == core.MAX_BODY_BYTES, cfg.get("max_body_bytes")
finally:
    (core.MAX_INPUT_ITEMS, core.CACHE_DIR, wiclient.resolve, wiclient.score_image,
     wiclient.probe_endpoint, wiclient.SPACING) = saved

# --- the request-body ceiling, on the WSGI path -------------------------------------------------
# The same guard as the http.server backend, from the same helper: a limit that exists in one code
# path and not the other is not a limit (docs/HANDOFF.md §4).
saved_cap = core.MAX_BODY_BYTES
core.MAX_BODY_BYTES = 200
try:
    c, b = call("POST", "/api/parse", b"x" * 500)
    d = json.loads(b)
    print("POST /api/parse (500 bytes, cap 200) ->", c["status"], d.get("error", "")[:70])
    assert c["status"].startswith("413"), (c["status"], d)
    assert "200" in d["error"], d["error"]          # the message names the limit

    c, b = call("POST", "/api/parse", json.dumps({"text": "File:A.jpg"}).encode())
    print("POST /api/parse (under the cap) ->", c["status"])
    assert c["status"].startswith("200"), (c["status"], b[:120])
finally:
    core.MAX_BODY_BYTES = saved_cap

# --- malformed input is the caller's fault, wherever it arrives from ------------------------------
c, b = call("POST", "/api/parse", b'{"text": "unterminated')
d = json.loads(b)
print("POST /api/parse (malformed JSON) ->", c["status"], d.get("error", "")[:60])
assert c["status"].startswith("400"), (c["status"], d)      # was 500 through the blanket handler

c, b = call("POST", "/api/parse", json.dumps({"text": ["File:A.jpg"]}).encode())
d = json.loads(b)
print("POST /api/parse (text is a list) ->", c["status"], d.get("error", "")[:60])
assert c["status"].startswith("400"), (c["status"], d)      # raised inside the parser before

print("\nWSGI shim OK")
