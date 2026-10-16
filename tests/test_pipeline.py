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
import json
import os
import sys
import tempfile
import time

os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="wp-test-cache-")
os.environ["QUIET"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
sys.path.insert(0, ROOT)

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

# --- retries on a transient upstream failure -------------------------------------------------
# A 502/503/504 from the gateway means the model was never reached, so a retry costs no tokens. The
# policy is bounded (UPSTREAM_MAX_ATTEMPTS) and every attempt is paced through _next_slot(). A 400 is
# a client-side fault and must never be retried; neither case may loop; and a sustained outage must
# trip the breaker rather than multiply attempts across the whole queue.
import contextlib


def ok_payload():
    return {"http": 200, "wall_s": 0.02, "ttfb_s": 0.02,
            "usage": {"input_tokens": 900, "output_tokens": 0},
            "answers": {"face_visible": {"noul": 0.9},
                        "pose": {"choice": "head_shoulders", "probabilities": {"head_shoulders": 0.8}},
                        "quality": {"score": 2.5, "confidence": 0.5, "probabilities": {"2": 0.5}},
                        "eyes_to_camera": {"noul": 0.9}, "infobox_ready": {"noul": 0.5},
                        "publish_ready": {"choice": "publish", "confidence": 0.8,
                                          "probabilities": {"publish": 0.8, "pass": 0.2}}}}


def scripted(seq):
    """A score_image returning the given responses in order, counting calls."""
    state = {"n": 0}

    def fake(b64, budget):
        state["n"] += 1
        return seq[min(state["n"] - 1, len(seq) - 1)]
    return fake, state


@contextlib.contextmanager
def fresh_cache():
    """An empty cache directory.

    Each case needs its own: a successful case writes its answer, and every single-photo job here
    resolves to the *same* cache key (fake_resolve numbers pageids by position), so a shared cache
    would serve case 1's answer to case 2 and score_image would never be called.
    """
    real = core.CACHE_DIR
    core.CACHE_DIR = tempfile.mkdtemp(prefix="wp-retry-")
    try:
        yield
    finally:
        core.CACHE_DIR = real


def run_one():
    job = core.new_job("File:Retry 1.jpg")
    core.run_job(job)
    return job


gw_503 = {"http": 503, "wall_s": 0.1, "error": "request failed",
          "detail": '{"httpCode":503,"httpReason":"upstream connect error or disconnect/reset '
                    'before headers. reset reason: connection timeout"}'}
gw_400 = {"http": 400, "wall_s": 0.05, "error": "request failed",
          "detail": '{"error":"Image at index 0 is not valid base64"}'}

real_backoff = wiclient.UPSTREAM_RETRY_BACKOFF
real_backoff_max = wiclient.UPSTREAM_RETRY_BACKOFF_MAX
real_max_attempts = wiclient.UPSTREAM_MAX_ATTEMPTS
real_abort = core.UPSTREAM_ABORT_AFTER
wiclient.UPSTREAM_RETRY_BACKOFF = 0.01          # keep the suite fast
wiclient.UPSTREAM_RETRY_BACKOFF_MAX = 0.01
wiclient.UPSTREAM_MAX_ATTEMPTS = 3              # pin both: an env override must not change the test
core.UPSTREAM_ABORT_AFTER = 5
try:
    with fresh_cache():                          # 503 once, then 200
        fake1, st1 = scripted([gw_503, ok_payload()])
        wiclient.score_image = fake1
        j1 = run_one()
    if st1["n"] != 2:
        failures.append(f"a transient 503 should be retried (made {st1['n']} calls)")
    if j1["items"][0]["status"] != "done":
        failures.append(f"the retried photo should end up scored: {j1['items'][0].get('error')}")
    if not j1["items"][0].get("upstream_retry") or j1["items"][0].get("upstream_attempts") != 2:
        failures.append(f"the retry should be recorded with its attempt count: "
                        f"{j1['items'][0].get('upstream_attempts')!r}")
    if not any("retrying in" in line for line in j1["log"]):
        failures.append("the retry does not appear in the run log")
    if (j1.get("retries") or {}).get("recovered") != 1:
        failures.append(f"the run should report the retry as recovered: {j1.get('retries')}")
    if not any("retries:" in line for line in j1["log"]):
        failures.append("the run log should summarise the retries")

    with fresh_cache():                          # 503 every time
        fake2, st2 = scripted([gw_503])
        wiclient.score_image = fake2
        j2 = run_one()
    if st2["n"] != 3:
        failures.append(f"a persistent 503 must stop after UPSTREAM_MAX_ATTEMPTS=3 "
                        f"(made {st2['n']} calls)")
    if j2["items"][0]["status"] != "error":
        failures.append("a photo that fails every attempt should be an error")
    if "503" not in (j2["items"][0].get("error") or ""):
        failures.append(f"the item error should name the HTTP status: {j2['items'][0].get('error')!r}")
    if not any("connection timeout" in line for line in j2["log"]):
        failures.append("the full gateway body should reach the run log, not just the row")
    if (j2.get("retries") or {}).get("still_failed") != 1:
        failures.append(f"the run should report the retry as NOT recovered: {j2.get('retries')}")

    with fresh_cache():                          # a client-side fault: never retried
        fake3, st3 = scripted([gw_400])
        wiclient.score_image = fake3
        j3 = run_one()
    if st3["n"] != 1:
        failures.append(f"a 400 must NOT be retried (made {st3['n']} calls)")
    if j3["items"][0]["status"] != "error":
        failures.append("a 400 should still be reported as an error")

    # --- 429: the app's *stated* brake against overuse, and until 2026-10-16 the only branch of this
    # policy with no test at all. Retry, no-retry and the breaker were covered; the one that protects a
    # shared endpoint was not. The rule (core._score_item): ONE retry, and a second 429 is returned
    # as-is. It consumes its own budget — not UPSTREAM_MAX_ATTEMPTS — because the two are independent.
    gw_429 = {"http": 429, "wall_s": 0.05, "error": "rate limited", "retry_after": "30",
              "detail": '{"error":"rate limited, try again later"}'}

    with fresh_cache():                          # 429 once, then 200
        fake5, st5 = scripted([gw_429, ok_payload()])
        wiclient.score_image = fake5
        j5 = run_one()
    if st5["n"] != 2:
        failures.append(f"a 429 should be retried exactly once (made {st5['n']} calls)")
    if j5["items"][0]["status"] != "done":
        failures.append(f"the photo should be scored once the 429 clears: "
                        f"{j5['items'][0].get('error')!r}")
    if not j5["items"][0].get("rate_limited"):
        failures.append("the item must record that it was rate-limited — the UI and the reports "
                        "count it (reports.stats n_429), so losing the flag hides throttling")
    if j5["items"][0].get("upstream_attempts") != 2:
        failures.append(f"a 429 retry should be counted: "
                        f"{j5['items'][0].get('upstream_attempts')!r}")
    if not any("one retry" in line for line in j5["log"]):
        failures.append("the run log should say the 429 was retried")
    if not any("wait 30s" in line for line in j5["log"]):
        failures.append("the log should name the Retry-After the server asked for")
    if (j5.get("retries") or {}).get("recovered") != 1:
        failures.append(f"a recovered 429 should be reported like any other retry: {j5.get('retries')}")
    if j5["stats"].get("n_429") != 1:
        failures.append(f"the run stats should count 1 rate-limited photo: {j5['stats'].get('n_429')!r}")

    with fresh_cache():                          # 429 twice: stop, do not loop
        fake6, st6 = scripted([gw_429])
        wiclient.score_image = fake6
        j6 = run_one()
    if st6["n"] != 2:
        failures.append(f"a second 429 must stop, not retry again (made {st6['n']} calls — "
                        f"UPSTREAM_MAX_ATTEMPTS={wiclient.UPSTREAM_MAX_ATTEMPTS} must NOT apply to 429)")
    if j6["items"][0]["status"] != "error":
        failures.append("a photo that stays rate-limited should end as an error")
    if "429" not in (j6["items"][0].get("error") or ""):
        failures.append(f"the item error should name the 429: {j6['items'][0].get('error')!r}")
    if (j6.get("retries") or {}).get("still_failed") != 1:
        failures.append(f"an unrecovered 429 should be reported: {j6.get('retries')}")

    with fresh_cache():                          # 429, then a gateway 5xx, then 200
        fake7, st7 = scripted([gw_429, gw_503, ok_payload()])
        wiclient.score_image = fake7
        j7 = run_one()
    if st7["n"] != 3:
        failures.append(f"the 429 and 5xx retry budgets are independent, so this needs 3 calls "
                        f"(made {st7['n']})")
    if j7["items"][0]["status"] != "done":
        failures.append("the photo should still be scored after a 429 and then a 503")

    # --- the cooldown itself, which the fakes above bypass ----------------------------------------
    # They replace score_image, so `Budget.note_429` never runs and the shared-state contract is
    # untested. It is the part that makes a 429 discipline rather than a retry: EVERY worker waits, and
    # it expires. Asserted directly, plus the documented-but-nowhere-asserted fact that exceeding
    # HOURLY_BUDGET does not refuse a call — so nobody "fixes" the display into a brake.
    b = wiclient.Budget(limit=100)
    if b.wait_seconds() != 0:
        failures.append("a fresh Budget must not be blocked")
    b.note_429("0.3")
    if b.wait_seconds() <= 0:
        failures.append("note_429 must block the very next call, for every worker")
    if (b.status().get("last_429") or {}).get("retry_after") != "0.3":
        failures.append(f"the Retry-After should be recorded for the UI: {b.status().get('last_429')!r}")
    time.sleep(0.35)
    if b.wait_seconds() > 0:
        failures.append("the cooldown should expire with the Retry-After it was given (no sticky block)")
    b2 = wiclient.Budget(limit=100)
    b2.note_429(None)                            # the header can be absent entirely
    if not (55 <= b2.wait_seconds() <= 61):
        failures.append(f"a missing Retry-After should fall back to ~60s, not to zero "
                        f"(got {b2.wait_seconds():.1f}s)")
    b3 = wiclient.Budget(limit=0)                # limit deliberately exceeded
    for _ in range(3):
        b3.note_call()
    if b3.used() != 3:
        failures.append("Budget should count calls")
    if b3.wait_seconds() != 0:
        failures.append("exceeding HOURLY_BUDGET must NOT block a call — it is a display, not a brake "
                        "(score_image's docstring says so; this assertion is what keeps that true)")

    # the circuit breaker: a mid-run outage must not cost MAX_ATTEMPTS x every remaining photo
    NFAN = 12
    with fresh_cache():
        fake4, st4 = scripted([gw_503])
        wiclient.score_image = fake4
        j4 = core.new_job("\n".join(f"File:Outage {n}.jpg" for n in range(1, NFAN + 1)))
        core.run_job(j4)
    if not any("stopping after" in line for line in j4["log"]):
        failures.append("a sustained outage should stop the run, not grind through the queue")
    left = [i for i in j4["items"] if "stopping after" in (i.get("error") or "")]
    if not left:
        failures.append("the abort should leave the remaining photos marked, not silently dropped")
    floor = core.UPSTREAM_ABORT_AFTER * 3        # 5 consecutive photos x 3 attempts
    ceil = NFAN * 3                              # what it would cost without the breaker
    if not (floor <= st4["n"] < ceil):
        failures.append(f"the breaker did not bound the outage: {st4['n']} calls "
                        f"(expected {floor}..{ceil - 1} for {NFAN} photos)")
finally:
    wiclient.UPSTREAM_RETRY_BACKOFF = real_backoff
    wiclient.UPSTREAM_RETRY_BACKOFF_MAX = real_backoff_max
    wiclient.UPSTREAM_MAX_ATTEMPTS = real_max_attempts
    core.UPSTREAM_ABORT_AFTER = real_abort
    wiclient.score_image = fake_score

# --- the endpoint pre-flight, on the FAILING path ----------------------------------------------
# The harness pre-seeds _PROBE with ok=True at module level, so until 2026-10-16 the branch below had
# never executed in CI: the behaviour during an outage — stop before spending the queue, mark every
# pending photo honestly, keep the resolution already paid for — was only ever exercised by hand. That
# is the branch that runs when the service is down, which is the worst moment to meet it for the first
# time. score_image is replaced here too, so a regression makes this test *record* a call rather than
# make a real one.
real_probe = wiclient.probe_endpoint
probe_seed = (core._PROBE.get("t"), core._PROBE.get("result"))
try:
    with fresh_cache():
        spent = {"n": 0}

        def must_not_run(b64, budget):
            spent["n"] += 1
            return ok_payload()

        wiclient.score_image = must_not_run
        wiclient.probe_endpoint = lambda: {"ok": False, "http": 503, "wall_s": 0.05,
                                          "error": "gateway refused"}
        core._PROBE.update(t=0.0, result=None)          # defeat the 10-minute verdict reuse
        j8 = core.new_job("File:Down 1.jpg\nFile:Down 2.jpg")
        core.run_job(j8)
    if spent["n"] != 0:
        failures.append(f"a failed pre-flight must not spend a single model call (made {spent['n']})")
    if (j8.get("probe") or {}).get("ok") is not False:
        failures.append(f"the job should carry the failed verdict for the UI: {j8.get('probe')!r}")
    if not all(i["status"] == "error" for i in j8["items"]):
        failures.append(f"every pending photo should be settled, not left mid-state: "
                        f"{[i['status'] for i in j8['items']]}")
    if not any("pre-flight" in (i.get("error") or "") for i in j8["items"]):
        failures.append("the item error should blame the pre-flight, not the photo")
    if not any("pre-flight FAILED" in line for line in j8["log"]):
        failures.append("the run log should record the early stop")
    if not any("stopping before spending the queue" in line for line in j8["log"]):
        failures.append("the log should say the queue was NOT spent, so a reader is not left guessing")
    # "nothing was scored" is in the per-photo error (where a reader meets it), not in the log — an
    # assertion I got wrong the first time by assuming the two carried the same sentence.
    if not any("nothing was scored" in (i.get("error") or "") for i in j8["items"]):
        failures.append("the item error should say plainly that nothing was scored")
    if not all(i.get("title") for i in j8["items"]):
        failures.append("a failed pre-flight must keep the resolved titles — resolution is the part "
                        "already paid for, and keeping it is what makes the retry cheap")
finally:
    wiclient.probe_endpoint = real_probe
    core._PROBE.update(t=probe_seed[0], result=probe_seed[1])

# --- the cache-only GET: the API twin of a share link, and why it is safe to hand out --------
# A GET must be incapable of *spending*: it resolves the titles (one batched Commons call) and reads
# saved answers. So a warm photo comes back from cache, an unscored one comes back marked uncached,
# and neither can make a model call.
import urllib.parse as _up

before = model_calls()
status, _ct, payload, _hd = core.route("GET", "/api/eval", b"",
                                       "list=" + _up.quote("File:Test 1.jpg"))
body = json.loads(payload)
if status != 200:
    failures.append(f"GET /api/eval should be 200, got {status}")
if model_calls() != before:
    failures.append(f"a cache-only GET spent {model_calls() - before} model call(s)")
if not body.get("cached_only"):
    failures.append("the GET payload does not say it was cache-only")
if not (body.get("results") or [{}])[0].get("from_cache"):
    failures.append(f"a warm photo should come back from cache: {(body.get('results') or [{}])[0]}")

# a cold cache, not a differently-named file: fake_resolve numbers pageids by position, so another
# title would land on the same key and come back from cache
with fresh_cache():
    status2, _ct2, payload2, _hd2 = core.route("GET", "/api/eval", b"",
                                               "list=File:Never Scored.jpg")
    b2 = json.loads(payload2)
    if (b2.get("results") or [{}])[0].get("status") != "uncached":
        failures.append(f"an unscored photo should be reported as uncached: "
                        f"{(b2.get('results') or [{}])[0]}")
    if model_calls() != before:
        failures.append("the cold cache-only GET spent a call")
if core.route("GET", "/api/eval", b"", "")[0] != 400:
    failures.append("GET /api/eval with no list should be a 400")

print(f"cache-only GET    : warm -> from_cache, cold -> uncached, {model_calls() - before} model calls total")
print(f"retry             : 503 then 200 -> {st1['n']} calls (scored); 503 always -> {st2['n']} calls "
      f"(error); 400 -> {st3['n']} call (no retry); outage of {NFAN} photos -> {st4['n']} calls (breaker)")
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
