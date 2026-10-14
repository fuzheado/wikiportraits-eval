#!/usr/bin/env python3
"""Offline proof that the worker pipelines downloads against model calls.

Monkeypatches the two network calls with sleeps of known length, so the timing is
deterministic and no request leaves the machine:

  * every download sleeps DOWNLOAD_S
  * every model call sleeps MODEL_S

Serial execution would cost n*(DOWNLOAD_S + MODEL_S) plus the spacing between calls.
Pipelined execution should cost roughly n*MODEL_S + DOWNLOAD_S. The assertions below
check that overlap actually happened, not just that the total looks small.
"""
import os
import sys
import tempfile
import time

os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="wp-test-cache-")
os.environ["QUIET"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402
import wiclient  # noqa: E402

N = 5
DOWNLOAD_S = 0.30
MODEL_S = 0.50
SPACING = 0.05

core.SPACING = SPACING
wiclient.SPACING = SPACING

events = []
_t0 = time.time()


def fake_resolve(items):
    out = {}
    for n, it in enumerate(items, start=1):
        out[(it.get("wiki") or "commons.wikimedia.org", it["title"])] = {
            "ok": True, "wiki": "commons.wikimedia.org", "pageid": 9000 + n,
            "title": it["title"], "thumburl": f"https://example.invalid/{n}.jpg",
            "orig_w": 3000, "orig_h": 2000, "mime": "image/jpeg", "sha1": f"sha{n}",
            "license": "CC BY-SA 4.0", "artist": "test"}
    return out


def fake_fetch(url):
    time.sleep(DOWNLOAD_S)
    events.append(("download_end", round(time.time() - _t0, 3), url))
    return b"\xff\xd8\xff" + b"x" * 32          # jpeg-ish magic so image_size stays happy


def fake_size(data):
    return (640, 427)


snapshots = []


def fake_score(b64, budget):
    start = round(time.time() - _t0, 3)
    snapshots.append({                       # what a reader could see while this call is in flight
        "at": start,
        "done": sum(1 for i in JOB["items"] if i["status"] == "done"),
        "derived": sum(1 for i in JOB["items"] if i.get("composite") is not None),
        "spread_n": ((JOB.get("spread") or {}).get("composite") or {}).get("n", 0),
        "stats": bool((JOB.get("stats") or {}).get("composite_mean")),
    })
    time.sleep(MODEL_S)
    events.append(("model_end", round(time.time() - _t0, 3), start))
    n = len([e for e in events if e[0] == "model_end"])
    return {"http": 200, "wall_s": MODEL_S, "ttfb_s": MODEL_S,
            "usage": {"input_tokens": 900, "output_tokens": 0},
            "answers": {"face_visible": {"noul": 0.9}, "pose": {"choice": "head_shoulders",
                        "probabilities": {"head_shoulders": 0.8}},
                        "quality": {"score": 2.5, "confidence": 0.5, "probabilities": {"2": 0.5}},
                        "eyes_to_camera": {"noul": 0.9}, "infobox_ready": {"noul": 0.5},
                        "publish_ready": {"choice": "publish", "confidence": 0.8,
                                          "probabilities": {"publish": 0.8, "pass": 0.2}}}}


wiclient.resolve = fake_resolve
wiclient.fetch_bytes = fake_fetch
wiclient.image_size = fake_size
wiclient.score_image = fake_score
# pre-seed the endpoint pre-flight so run_job makes no network call in this offline test
import time as _time
core._PROBE.update(t=_time.time(), result={"ok": True, "http": 200, "wall_s": 0.05, "error": None})
core.wiclient = wiclient

job = core.new_job("\n".join(f"File:Test {n}.jpg" for n in range(1, N + 1)))
JOB = job                                    # so fake_score can snapshot mid-run state
core.run_job(job)

ok = [i for i in job["items"] if i["status"] == "done"]
elapsed = job["elapsed_s"]
serial = N * (DOWNLOAD_S + MODEL_S) + SPACING * (N - 1)
downloads = [e for e in events if e[0] == "download_end"]
models = [e for e in events if e[0] == "model_end"]

print(f"items scored      : {len(ok)}/{N}")
print(f"elapsed           : {elapsed}s")
print(f"downloads total   : {round(len(downloads) * DOWNLOAD_S, 2)}s ({len(downloads)} images)")
print(f"model calls total : {round(len(models) * MODEL_S, 2)}s ({len(models)} calls)")
print(f"serial would be   : {round(serial, 2)}s (+ resolve)")
print(f"pipeline summary  : {job.get('pipeline')}")
print("event order       :", [(k, t) for k, t, *_ in events])

# overlap test: a download must finish while a model call is in flight
overlaps = 0
for _, dt, _url in downloads:
    for _, mt, start in models:
        if start < dt < mt:
            overlaps += 1
failures = []
if len(ok) != N:
    failures.append(f"only {len(ok)} of {N} scored")
if overlaps == 0:
    failures.append("no download overlapped a model call")
if elapsed >= serial * 0.9:
    failures.append(f"elapsed {elapsed}s is not better than serial {round(serial, 2)}s")
if not job.get("pipeline", {}).get("serial_estimate_s"):
    failures.append("pipeline timing summary missing from the job payload")
# state machine: nothing may be left claiming to download bytes it already has
stuck = [i["title"] for i in job["items"] if i["status"] in ("downloading", "ready")]
if stuck:
    failures.append(f"items left mid-state: {stuck}")
if not all(i.get("scoring_s") is not None for i in ok):
    failures.append("scoring_s not recorded on every scored item")
if not all(i.get("fetch_s") is not None for i in ok):
    failures.append("fetch_s not recorded on every scored item")
lat = job.get("latency") or {}
if lat.get("verdict") not in ("normal", "elevated", "service-side"):
    failures.append(f"latency summary missing a verdict: {lat}")
if lat.get("median_s") is None:
    failures.append("latency summary missing the median call time")

# mid-run readability: photos that have settled must carry their numbers before the run ends.
# (Regression: the composite, P(publish), spread and stats used to be computed only after the last
# photo, so a 20-minute run showed nothing at all until it was over.)
# With INFLIGHT > 1 the first result now lands *after* a later call has started (the streams
# overlap), so the readable moment is "the first settled photo" — the first snapshot with
# done >= 1 — not necessarily snapshot[1].
mid = next((s for s in snapshots if s.get("done", 0) >= 1), None)
if mid is None:
    failures.append(f"no photo was readable while the run was still going: {snapshots}")
    mid = {}
else:
    if mid.get("derived", 0) < 1:
        failures.append("a settled photo had no composite while later photos were still being scored")
    if mid.get("spread_n", 0) < 1:
        failures.append("the spread was still empty mid-run although photos had settled")
    if not mid.get("stats"):
        failures.append("the stats grid was still empty mid-run")
if not any(s.get("done", 0) == 0 for s in snapshots):
    failures.append(f"no true mid-run state observed (every snapshot already had a done photo): "
                    f"{snapshots}")
missing_mid = [i["title"] for i in job["items"] if i["status"] == "done"
               and (i.get("composite") is None or i.get("publish_p") is None)]
if missing_mid:
    failures.append(f"scored photos without composite/P(publish): {missing_mid}")
# the thumbnail of every scored photo must have been saved for the reports (regression: a bare
# `except Exception` in the save helper hid a NameError and silently disabled report images)
missing_thumbs = [i["title"] for i in job["items"] if i["status"] == "done" and not i.get("thumb_file")]
if missing_thumbs:
    failures.append(f"scored photos without a saved thumbnail: {missing_thumbs}")
if not all(os.path.exists(i["thumb_file"]) for i in job["items"] if i.get("thumb_file")):
    failures.append("a recorded thumbnail path does not exist on disk")

# --- the cache: a repeat run is free, and `no_cache` re-scores on purpose --------------------
# "run fresh" is a real feature (people want to feel the true run speed) and a real hazard: it costs
# one model call per photo. Both halves are asserted here, offline.
def model_calls():
    return len([e for e in events if e[0] == "model_end"])


repeat = core.new_job("\n".join(f"File:Test {n}.jpg" for n in range(1, N + 1)))
JOB = repeat
core.run_job(repeat)
n_cached = len([i for i in repeat["items"] if i.get("from_cache")])
spent_repeat = model_calls() - len(models)
if n_cached != N or spent_repeat != 0:
    failures.append(f"a repeat run should be fully cached and cost nothing: "
                    f"cached={n_cached}/{N}, {spent_repeat} model calls")
if repeat.get("no_cache"):
    failures.append("a normal run must not set no_cache")

before = model_calls()
fresh = core.new_job("\n".join(f"File:Test {n}.jpg" for n in range(1, N + 1)), no_cache=True)
JOB = fresh
core.run_job(fresh)
spent_fresh = model_calls() - before
if spent_fresh != N:
    failures.append(f"no_cache must re-score every photo: {spent_fresh} calls for {N} photos")
if any(i.get("from_cache") for i in fresh["items"]):
    failures.append("a no_cache run still reported items as from_cache")
if not fresh.get("no_cache"):
    failures.append("the no_cache flag was not recorded on the job")
if not any("cache bypassed" in line for line in fresh["log"]):
    failures.append("a no_cache run does not say so in the run log")

# --- a cache-only replay must be INCAPABLE of spending a model call -------------------------
# This is the property that makes a shareable link safe to open, so it is asserted both ways:
# warm (the answers are here) and cold (they are not).
warm = core.new_job("\n".join(f"File:Test {n}.jpg" for n in range(1, N + 1)), cached_only=True)
JOB = warm
before = model_calls()
core.run_job(warm)
spent_warm = model_calls() - before
if spent_warm != 0:
    failures.append(f"a cached-only replay spent {spent_warm} calls (must be 0)")
if not all(i.get("from_cache") for i in warm["items"] if i["status"] == "done"):
    failures.append("a warm replay did not serve every photo from cache")
if any(i["status"] == "uncached" for i in warm["items"]):
    failures.append("a warm replay reported items as uncached")
if warm["status"] != "done":
    failures.append(f"a warm replay ended {warm['status']}")

# "cold" has to mean an empty cache, not different titles: fake_resolve numbers pageids by
# position, so a fresh list of titles would collide with the earlier run's keys and every lookup
# would hit. Point CACHE_DIR at a new directory instead — that is what "this server has never
# scored these photos" actually looks like.
real_cache = core.CACHE_DIR
core.CACHE_DIR = tempfile.mkdtemp(prefix="wp-replay-cold-")
try:
    cold = core.new_job("\n".join(f"File:Test {n}.jpg" for n in range(1, N + 1)), cached_only=True)
    JOB = cold
    before = model_calls()
    core.run_job(cold)
    spent_cold = model_calls() - before
finally:
    core.CACHE_DIR = real_cache
if spent_cold != 0:
    failures.append(f"a cold replay spent {spent_cold} calls (must be 0)")
if not all(i["status"] == "uncached" for i in cold["items"]):
    failures.append(f"a cold replay should mark every photo uncached: "
                    f"{[i['status'] for i in cold['items']]}")
if cold["status"] != "done":
    failures.append("a cold replay is information, not an error — status should be done")
if not any("not in cache" in line for line in cold["log"]):
    failures.append("a cold replay does not say why nothing was scored")

print(f"cache             : repeat run {n_cached}/{N} cached with {spent_repeat} calls; "
      f"no_cache re-scored all {N} ({spent_fresh} calls) in {fresh.get('elapsed_s')}s")
print(f"replay            : warm {spent_warm} calls, all from cache; cold {spent_cold} calls, all marked uncached")
print(f"mid-run snapshots : {snapshots}")
print(f"downloads overlapping a model call: {overlaps}")
print(f"latency summary  : {lat}")
print("FAILURES:", len(failures))
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
