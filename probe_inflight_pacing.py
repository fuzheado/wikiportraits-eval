#!/usr/bin/env python3
"""Does INFLIGHT>1 do anything at production speeds? Offline, no API calls.

`core._next_slot()` paces every model-call **start** SPACING apart, globally across workers:

    delay = _next_slot()      # reserves the next start, SPACING after the previous one
    time.sleep(delay)
    res = wiclient.score_image(...)

So a second worker does not start a second call until SPACING has elapsed. Which means the only
thing that can ever overlap is a call whose duration **exceeds** SPACING:

    per-call 0.33 s (measured in the pod, 2026-10-14) with SPACING 0.90 s
        → at most one call in flight; a second worker sleeps through the gap and INFLIGHT is inert
    per-call 3.00 s with SPACING 0.90 s
        → the pacing gate is no longer binding; a second worker keeps the pipeline full

This runs the real `core.run_job` against a stubbed model, so it is a statement about this
pipeline's scheduling and not a re-implementation of it. Zero network calls.

    python3 probe_inflight_pacing.py
"""
import os
import sys
import tempfile
import threading
import time

os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="wp-inflight-cache-")
os.environ["QUIET"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core        # noqa: E402
import wiclient    # noqa: E402

N = 6
MODEL_RETURN = {
    "http": 200, "wall_s": 0.0, "ttfb_s": 0.0,
    "usage": {"input_tokens": 1000, "output_tokens": 0},
    "answers": {"face_visible": {"noul": 0.9},
                "pose": {"choice": "head_shoulders", "probabilities": {"head_shoulders": 0.8}},
                "quality": {"score": 2.5, "confidence": 0.5, "probabilities": {"2": 0.5}},
                "eyes_to_camera": {"noul": 0.9}, "infobox_ready": {"noul": 0.5},
                "publish_ready": {"choice": "publish", "confidence": 0.8,
                                  "probabilities": {"publish": 0.8, "pass": 0.2}}}}


def instrument(model_s, run_id=0):
    """Fresh stubs per run; returns the list of (start, end) intervals actually observed.

    `run_id` offsets the pageids so each run gets its own cache entries — otherwise runs 2+ hit
    the answers written by run 1, make no calls at all, and silently measure nothing.
    """
    intervals = []
    lock = threading.Lock()

    def fake_resolve(items):
        return {(it.get("wiki") or "commons.wikimedia.org", it["title"]): {
            "ok": True, "wiki": "commons.wikimedia.org", "pageid": run_id * 1000 + n,
            "title": it["title"], "thumburl": f"https://example.invalid/{run_id}-{n}.jpg",
            "orig_w": 3000, "orig_h": 2000, "mime": "image/jpeg",
            "sha1": f"sha{run_id}-{n}",
            "license": "CC BY-SA 4.0", "artist": "test"}
            for n, it in enumerate(items, start=1)}

    def fake_fetch(url):
        time.sleep(0.02)
        return b"\xff\xd8\xff" + b"x" * 32

    def fake_score(b64, budget):
        t0 = time.time()
        time.sleep(model_s)
        with lock:
            intervals.append((t0, time.time()))
        return dict(MODEL_RETURN, wall_s=model_s, ttfb_s=model_s)

    wiclient.resolve = fake_resolve
    wiclient.fetch_bytes = fake_fetch
    wiclient.image_size = lambda d: (640, 427)
    wiclient.score_image = fake_score
    core.wiclient = wiclient
    core._PROBE.update(t=time.time(), result={"ok": True, "http": 200, "wall_s": 0.05, "error": None})
    return intervals


def max_overlap(intervals):
    """Largest number of model calls genuinely in flight at the same instant."""
    events = []
    for s, e in intervals:
        events += [(s, 1), (e, -1)]
    events.sort()
    cur = best = 0
    for _, delta in events:
        cur += delta
        best = max(best, cur)
    return best


def run(model_s, spacing, inflight, run_id):
    core.SPACING = wiclient.SPACING = spacing
    core.INFLIGHT = inflight
    core._slot_next[0] = 0.0                       # reset the global pacing gate
    intervals = instrument(model_s, run_id)
    job = core.new_job("\n".join(f"File:Pacing {run_id}-{n}.jpg" for n in range(1, N + 1)))
    t0 = time.time()
    core.run_job(job)
    elapsed = time.time() - t0
    done = [i for i in job["items"] if i["status"] == "done"]
    return {"elapsed": elapsed, "overlap": max_overlap(intervals), "calls": len(intervals),
            "done": len(done), "intervals": intervals}


print(f"real core.run_job, stubbed model, N={N} photos, zero API calls\n")
print(f"{'per-call':>9} {'SPACING':>8} {'INFLIGHT':>9} {'wall':>8} {'max overlap':>12} {'calls':>6}")
print("-" * 60)

rows = {}
run_id = 0
for model_s in (0.33, 3.00):
    for inflight in (1, 2):
        run_id += 1
        r = run(model_s, 0.90, inflight, run_id)
        rows[(model_s, inflight)] = r
        print(f"{model_s:8.2f}s {0.90:7.2f}s {inflight:9d} {r['elapsed']:7.2f}s "
              f"{r['overlap']:12d} {r['calls']:6d}")
        if r["calls"] != N:
            print(f"      !! only {r['calls']} of {N} calls were made — the measurement is void")

print("\n--- verdict ---")
for model_s in (0.33, 3.00):
    a, b = rows[(model_s, 1)], rows[(model_s, 2)]
    speedup = a["elapsed"] / b["elapsed"]
    regime = ("per-call < SPACING — the pacing gate binds, so the extra worker never overlaps"
              if model_s < 0.90 else
              "per-call > SPACING — the pacing gate is slack, so the extra worker fills the gap")
    print(f"  per-call {model_s:.2f}s: INFLIGHT 1→2 gives {speedup:.2f}x, "
          f"max overlap {a['overlap']} → {b['overlap']}")
    print(f"      {regime}")
