#!/usr/bin/env python3
"""End-to-end test of the running app: parse+resolve over HTTP, then a 3-image scoring run
with report downloads. Uses three files already scored earlier in the session so the run
proves the cache path as well as the live path.
"""
import json, time, urllib.request

API = "http://127.0.0.1:8765"
MIXED = """Hila Meir at Deauville American Film festival.jpg
File:Baba Sissoko-63495.jpg
https://commons.wikimedia.org/wiki/File:Louis_Garrel_at_82nd_Venice_International_Film_Festival-2.jpg
- https://upload.wikimedia.org/wikipedia/commons/thumb/0/08/Willy_Hernang%C3%B3mez_14_FC_Barcelona_%28basketball%29_Euroleague_20251120_%287%29.jpg/960px-Willy_Hernang%C3%B3mez_14_FC_Barcelona_%28basketball%29_Euroleague_20251120_%287%29.jpg
Category:WikiPortraits
not a file
"""


def post(path, obj):
    req = urllib.request.Request(API + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(API + path, timeout=180) as r:
        return r.read(), dict(r.headers)


print("=== parse+resolve over HTTP ===")
d = post("/api/parse", {"text": MIXED})
for it in d["items"]:
    print(f"  ok={it['ok']} {it['title'][:70]:70s} {it['orig_w']}x{it['orig_h']} {it['license'][:18]}")
for w in d["warnings"]:
    print(f"  skipped {w['raw'][:50]!r}: {w['reason']}")

print("\n=== scoring run ===")
start = post("/api/run", {"text": MIXED})
print("job:", start)
jid = start["job_id"]
t0 = time.time()
while True:
    body, _ = get(f"/api/job/{jid}")
    job = json.loads(body)
    done = job["progress"]["done"]
    print(f"  t+{time.time()-t0:5.1f}s  {done}/{job['progress']['total']}  status={job['status']}  "
          f"budget={job['budget']['used_last_hour']}/{job['budget']['limit_per_hour']}")
    if job["status"] in ("done", "error"):
        break
    time.sleep(2)

print("\nstats:", json.dumps(job["stats"], indent=1))
for it in job["items"]:
    a = ((it.get("result") or {}).get("answers") or {})
    if a:
        print(f"  {it['title'][:52]:52s} P(infobox)={a['infobox_ready']['noul']:.3f} "
              f"q={a['quality']['score']:.2f} pose={a['pose']['choice']} eyes={a['eyes_to_camera']['noul']:.3f} "
              f"face={a['face_visible']['noul']:.3f} cached={it.get('from_cache')} sent={it.get('sent_px')}")
    else:
        print(f"  {it['title'][:52]:52s} {it['status']}: {it.get('error')}")

print("\n=== reports ===")
for fmt in ("md", "json", "csv", "pdf"):
    data, hdr = get(f"/api/job/{jid}/report.{fmt}")
    open(f"/tmp/report.{fmt}", "wb").write(data)
    print(f"  {fmt}: {len(data)} bytes  {hdr.get('Content-Type')}  {hdr.get('Content-Disposition','')[:60]}")
print("\nmd preview:")
print(open("/tmp/report.md").read()[:700])
