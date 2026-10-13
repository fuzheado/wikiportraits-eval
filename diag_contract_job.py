#!/usr/bin/env python3
"""Diagnose the two failing contract checks: the exact job the contract test runs."""
import json
import time
import urllib.request

H = {"User-Agent": "diag", "Content-Type": "application/json"}


def post(p, d):
    r = urllib.request.Request("http://127.0.0.1:8765" + p, headers=H, data=json.dumps(d).encode())
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


def get(p):
    r = urllib.request.Request("http://127.0.0.1:8765" + p, headers=H)
    return json.loads(urllib.request.urlopen(r, timeout=600).read())


TEXT = "File:Baba Sissoko-63495.jpg\nFile:Lois Garrel.jpg\nHila Meir at Deauville American Film festival.jpg"
st = post("/api/run", {"text": TEXT})
jid = st["job_id"]
print("job", jid)
t0 = time.time()
while time.time() - t0 < 900:
    j = get("/api/job/" + jid)
    if j["status"] in ("done", "error"):
        break
    time.sleep(3)
print("status:", j["status"], "elapsed:", j.get("elapsed_s"))
print("pipeline:", j.get("pipeline"))
print("spread.composite:", json.dumps((j.get("spread") or {}).get("composite")))
for i in j["items"]:
    print(f"  {i['status']:8s} from_cache={i.get('from_cache')} composite={i.get('composite')} "
          f"scoring_s={i.get('scoring_s')} :: {(i.get('title') or i.get('raw'))[:44]}")
print("last log lines:", (j.get("log") or [])[-4:])
