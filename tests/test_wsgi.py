#!/usr/bin/env python3
"""Test the WSGI shim (the traditional Toolforge backend path) without a server."""
import io, json
import os
import sys

# this file lives in tests/; the app's modules and data files are at the repo root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wsgi


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

print("\nWSGI shim OK")
