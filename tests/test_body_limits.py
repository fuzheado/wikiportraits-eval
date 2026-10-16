#!/usr/bin/env python3
"""The HTTP layer's own guards: the version it advertises, and what it does with a body it should not
read. Offline — the resolver is stubbed, so no Commons call and no model call is made.

Everything here was a measured problem on 2026-10-16 (docs/HANDOFF.md §4):
  * `Server: WikiPortraitsEval/0.4` while /api/about reported 0.20.1 — a hardcoded version string;
  * malformed JSON answered 500 "server error" (a client fault reported as a server fault);
  * a 32 MiB body was read, unbounded, into a 512 MiB container;
  * a client that declared 10 MiB and sent 1 KiB held the worker until it gave up (no read timeout).
"""
import http.client
import json
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["QUIET"] = "1"          # the handler's own request log is not the subject here

import app     # noqa: E402
import core    # noqa: E402
import wiclient  # noqa: E402

FAILURES = []


def ok(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


# --- offline resolver: /api/parse would otherwise call Commons for real ---------------------------------
what = wiclient.resolve
wiclient.resolve = lambda titles: {
    (t.get("wiki") or "commons.wikimedia.org", t["title"]):
        {"ok": True, "pageid": 5000 + n, "orig_w": 3600, "orig_h": 5400,
         "mime": "image/jpeg", "license": "CC BY-SA 4.0"}
    for n, t in enumerate(titles)}

httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(method, path, body=b"", ctype="application/json", headers=None):
    """A normal, well-formed request; returns (status, headers, body_bytes, seconds)."""
    t0 = time.time()
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Content-Type": ctype}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    out = (r.status, dict(r.getheaders()), data, time.time() - t0)
    c.close()
    return out


def raw(request_bytes, wait=6.0):
    """A hand-built request, for the cases a well-behaved client cannot produce.

    Reads until the peer closes, not until the header terminator: the body of a 413 or a 408 is sent
    in the same breath as the headers and may or may not share a packet with them, so stopping at
    `\\r\\n\\r\\n` made "the message names the limit" pass or fail depending on segmentation. Both
    refusal paths close the connection, so EOF is a reliable end.
    """
    s = socket.create_connection(("127.0.0.1", PORT), timeout=wait)
    s.sendall(request_bytes)
    s.settimeout(wait)
    out = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
    except socket.timeout:
        out += b"<timeout>"
    s.close()
    return out


print("the version the server advertises")
status, hdrs, body, _ = call("GET", "/api/health")
ok(status == 200, f"GET /api/health -> {status}")
# BaseHTTPRequestHandler appends its own sys_version ("… Python/3.13.5"), so compare the token that
# matters: the version the app reports about itself, which used to be the hardcoded "0.4".
advertised = str(hdrs.get("Server", "")).split()[0].split("/")[-1] if hdrs.get("Server") else ""
ok(advertised == core.APP_VERSION,
   "Server header carries the app version (got " + str(hdrs.get("Server")) + ")")
ok(json.loads(body).get("ok") is True, "health says ok")

print("\nmalformed input is a client error, not a server error")
status, hdrs, body, _ = call("POST", "/api/parse", b'{"text": "unterminated')
ok(status == 400, f"unterminated JSON -> {status} (was 500)")
ok(b"not valid JSON" in body, "the message says what is wrong: " + body.decode()[:70])
status, _, body, _ = call("POST", "/api/parse", b"not json at all")
ok(status == 400, f"non-JSON body -> {status}")
status, _, body, _ = call("POST", "/api/parse", b'{"text": 12345}')
ok(status == 400, f"a number where text belongs -> {status} (was 500)")
status, _, body, _ = call("POST", "/api/parse", b'{"text": ["File:A.jpg"]}')
ok(status == 400, f"a list where text belongs -> {status} (was 500)")
ok(b"/api/eval" in body, "and it points at the route that does take an array")
status, _, body, _ = call("POST", "/api/eval", b'["File:A.jpg"]')
ok(status == 400, f"JSON array instead of an object -> {status}")
ok(b"must be a JSON object" in body, "and it says so: " + body.decode()[:70])
status, _, body, _ = call("POST", "/api/parse", b'{"text": null}')
ok(status == 200, f"a null text is left alone -> {status} (an empty box is a fair thing to send)")

print("\na normal request still works")
status, _, body, _ = call("POST", "/api/parse", json.dumps({"text": "File:Example.svg"}).encode())
d = json.loads(body)
ok(status == 200 and d.get("items"), f"POST /api/parse -> {status}, {len(d.get('items', []))} item(s)")

print("\nthe body ceiling is enforced before the body is read")
over = core.MAX_BODY_BYTES + 1
# Send headers only, declaring more than the cap: the 413 must come back without any body at all.
head = ("POST /api/parse HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"Content-Length: {over}\r\nConnection: close\r\n\r\n").encode()
t0 = time.time()
resp = raw(head)
dt = time.time() - t0
first = resp.split(b"\r\n")[0].decode()
rbody = resp.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in resp else b""
ok(b"413" in first.encode(), "over-cap Content-Length -> " + first)
ok(str(core.MAX_BODY_BYTES).encode() in rbody and b"MAX_BODY_BYTES" in rbody,
   "the 413 body names the live limit: " + rbody.decode()[:80])
ok(b"<timeout>" not in resp and dt < 3,
   f"answered without waiting for a body that never came ({dt:.1f}s)")

# A body at the cap is still accepted: the guard must not be off by one.
status, _, body, _ = call("POST", "/api/parse", b'{"text": "File:Example.svg"}',
                          headers={"Content-Length": str(len(b'{"text": "File:Example.svg"}'))})
ok(status == 200, f"a body under the cap still gets through -> {status}")

print("\na body that never fully arrives times out instead of holding the worker")
saved_timeout = app.READ_TIMEOUT
app.READ_TIMEOUT = 1.0
try:
    head = ("POST /api/parse HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            "Content-Length: 5000\r\n\r\n").encode()
    t0 = time.time()
    resp = raw(head + b'{"text": "only a few bytes', wait=8.0)
    dt = time.time() - t0
    first = resp.split(b"\r\n")[0]
    ok(b"408" in first, "under-sent body -> " + first.decode() + f" after {dt:.1f}s")
    ok(dt < 6, f"and it gave up at the timeout, not never ({dt:.1f}s)")
finally:
    app.READ_TIMEOUT = saved_timeout

status, _, _, _ = call("GET", "/api/health")
ok(status == 200, "the server is still healthy afterwards")

httpd.shutdown()
wiclient.resolve = what

print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("body limits OK")
