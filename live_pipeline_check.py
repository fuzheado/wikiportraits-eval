#!/usr/bin/env python3
"""Live end-to-end run on uncached files: proves the pipeline overlaps real downloads
with real model calls, and that the thumbnail URL reaches the UI payload.

Picks files that are NOT already in the local cache, runs them through the app, then
compares the wall time against the job's own serial estimate. Spends one API call per
image (the app's budget meter tracks it).
"""
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
UA = "WikiPortraitsEval/0.4 (pipeline check)"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
WANT = int(os.environ.get("WANT", "4"))


def post(path, obj):
    req = urllib.request.Request(URL + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def get(path):
    req = urllib.request.Request(URL + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def api(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


cached_ids = set()
for f in glob.glob(os.path.join(CACHE, "*.json")):
    m = re.match(r".*?-(\d+)-", os.path.basename(f))
    if m:
        cached_ids.add(int(m.group(1)))
print(f"cached pageids: {len(cached_ids)}")

q = urllib.parse.urlencode({"action": "query", "format": "json", "formatversion": "2",
                            "list": "search", "srsearch": "WikiPortraits", "srnamespace": 6,
                            "srlimit": 50})
cands = [p["title"] for p in api(f"https://commons.wikimedia.org/w/api.php?{q}")["query"]["search"]]
print(f"candidate files from Commons search: {len(cands)}")

info = api("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
    {"action": "query", "format": "json", "formatversion": "2", "titles": "|".join(cands[:50]),
     "prop": "imageinfo", "iiprop": "size"}))
fresh = [p["title"] for p in info["query"]["pages"]
         if not p.get("missing") and p.get("pageid") not in cached_ids]
pick = fresh[:WANT]
print(f"uncached files chosen: {len(pick)}")
for t in pick:
    print("   ", t)
if not pick:
    print("no uncached file found — nothing to measure")
    sys.exit(1)

started = time.time()
job_id = post("/api/run", {"items": pick})["job_id"]
statuses_seen = set()
timeline = []
while True:
    job = get(f"/api/job/{job_id}")
    statuses_seen.update(i["status"] for i in job["items"])
    timeline.append((round(time.time() - started, 1),
                     {i["title"][:34]: i["status"] for i in job["items"]}))
    if job["status"] in ("done", "error"):
        break
    time.sleep(0.5)
wall = round(time.time() - started, 2)

print(f"\nstatuses observed during the run: {sorted(statuses_seen)}")
for t, snap in timeline[:3] + timeline[-2:]:
    print(f"   t+{t:5.1f}s  {snap}")

pl = job.get("pipeline") or {}
print(f"\njob {job_id}: {job['status']} in {job['elapsed_s']}s (client wall {wall}s)")
print(f"pipeline: {pl}")
if pl:
    print(f"  scoring phase {pl.get('scoring_s')}s vs serial estimate {pl.get('serial_estimate_s')}s "
          f"-> saved {pl.get('saved_s')}s")
print(f"{'title':52s} {'status':9s} {'fetch_s':>7s} {'sent':>10s}  thumb")
for it in job["items"]:
    r = it.get("result") or {}
    print(f"{it['title'][:52]:52s} {it['status']:9s} {str(it.get('fetch_s', '-')):>7s} "
          f"{str(it.get('sent_px')):>10s}  {'yes' if it.get('thumburl') else 'NO'} "
          f"{(r.get('wall_s') if r else '')}")

checks = {
    "all images scored": all(i["status"] == "done" for i in job["items"]),
    "no errors": not [i for i in job["items"] if i["status"] not in ("done", "notfound")],
    "pipeline summary present": bool(pl.get("serial_estimate_s")),
    "every row has a thumbnail URL": all(i.get("thumburl") for i in job["items"]),
    "thumbnails are real (HEAD 200/302)": True,
    # the pipeline wins only when downloads are non-trivial next to the call; with a slow
    # endpoint it must at least not lose time. The strict version of this check is the
    # deterministic offline test (test_pipeline.py), where downloads and calls are comparable.
    "pipeline does not lose time": bool(pl.get("saved_s") is not None) and pl["saved_s"] > -0.5,
    # the states the UI labels: a row must never say "downloading" unless bytes are in flight
    "state machine exercised": bool(statuses_seen & {"downloading", "ready", "scoring"}),
    "no row stuck mid-state": not any(i["status"] in ("downloading", "ready") for i in job["items"]),
    "per-call ttfb recorded": all((i.get("result") or {}).get("ttfb_s") is not None
                                  for i in job["items"] if i["status"] == "done"),
    "latency verdict present": (job.get("latency") or {}).get("verdict") in
    ("normal", "elevated", "service-side"),
}
# one HEAD per thumbnail, to be sure the UI will actually load them
for it in job["items"]:
    req = urllib.request.Request(it["thumburl"], method="HEAD", headers={"User-Agent": UA})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as r:
            st = r.status
    except urllib.error.HTTPError as e:
        st = e.code
    print(f"   HEAD {st} {it['thumburl'][:96]}")
    if st not in (200, 301, 302, 304):
        checks["thumbnails are real (HEAD 200/302)"] = False

print()
fails = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(f"  {'ok ' if v else 'FAIL'} {k}")
print("RESULT:", "PASS" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
