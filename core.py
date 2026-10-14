"""Routing core shared by the http.server handler (app.py) and the WSGI shim (wsgi.py).

Keeping it in one place means the Toolforge build path (`Procfile: python3 app.py`) and the
traditional uWSGI path (`src/app.py` exposing `app`) run identical code.
"""
import base64, json, os, queue, re, threading, time, traceback, uuid
from urllib.parse import parse_qs, urlparse

import reports, wiclient
from wiclient import (APP_VERSION, Budget, CANDIDATE_FLOOR, QUESTIONS, SCHEMA_HASH, STATE,
                      THUMB_WIDTH, SPACING)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
# The starter list the UI shows in the textarea. A plain text file at the repo root so it is easy to
# find and replace. No comment lines in it: the file is fed to the parser verbatim, and `#` is a
# character in a Commons filename, not a comment.
DEFAULT_LIST = os.path.join(HERE, "default_list.txt")
CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(HERE, "cache"))
MAX_PARALLEL_JOBS = int(os.environ.get("MAX_PARALLEL_JOBS", "2"))
# Concurrent model-call workers. **Measured 2026-10-14, so the comment is long on purpose:**
# `_next_slot()` paces call *starts* SPACING apart globally, so a second worker can only ever
# overlap a call that *outlasts* SPACING. At production speed (0.33 s/call vs SPACING 0.9 s)
# INFLIGHT=2 is therefore **inert** — proven with this pipeline to the second, max overlap 1
# (`probe_inflight_pacing.py`); once the endpoint degrades past 0.9 s/call the same setting fills
# the gap for ~1.8x. It is self-gating, which is why the default is 2 rather than 1.
# **Do not raise it further:** 4 streams cost +61% per-call latency for only 2.24x
# (`research/concurrency-2026-10-14.md`). `INFLIGHT=1` restores the old serial behaviour.
INFLIGHT = max(1, int(os.environ.get("INFLIGHT", "2")))
# Stop the run when this many *consecutive* photos fail on a transient gateway 5xx (the model was
# never reached). This is what makes more than one retry per photo safe: without it, a mid-run outage
# costs UPSTREAM_MAX_ATTEMPTS requests for *every* remaining photo — the load amplification §13 warns
# about. With it, a run notices the outage after a handful of photos and stops honestly, the same
# shape as the 429 stop rule and the pre-flight check. 0 disables the breaker.
UPSTREAM_ABORT_AFTER = max(0, int(os.environ.get("UPSTREAM_ABORT_AFTER", "5")))
MAX_INPUT_ITEMS = int(os.environ.get("MAX_INPUT_ITEMS", "200"))  # cap per run (DoS guard)
EVAL_MAX = int(os.environ.get("EVAL_MAX", "25"))       # per-call cap for POST /api/eval
PROBE_TTL = float(os.environ.get("PROBE_TTL", "600"))  # reuse a pre-flight answer for 10 min
PREFETCH = max(1, int(os.environ.get("PREFETCH", "2")))   # images buffered ahead of the model call
START = time.time()

os.makedirs(CACHE_DIR, exist_ok=True)
BUDGET = Budget(limit=int(os.environ.get("HOURLY_BUDGET", "100")))
JOBS: dict = {}
JOBS_LOCK = threading.Lock()
MAX_JOBS_KEPT = int(os.environ.get("MAX_JOBS_KEPT", "100"))   # bounded job history (memory guard)
PARSE_MAX = int(os.environ.get("PARSE_MAX", "1000"))         # cap /api/parse resolve batches


# ------------------------------------------------------------------ job store
def new_job(payload, no_cache=False, cached_only=False):
    if isinstance(payload, str):
        parsed = wiclient.parse_entries(payload)
        entries, warnings = parsed["items"], parsed["warnings"]
    else:
        entries = [{"raw": t, "title": t} for t in payload]
        warnings = []
    job = {"id": uuid.uuid4().hex[:12], "created": time.time(), "status": "queued",
           "no_cache": bool(no_cache), "cached_only": bool(cached_only),
           "progress": {"done": 0, "total": len(entries)}, "warnings": warnings,
           "items": [{"raw": e["raw"], "title": e["title"], "status": "pending",
                      "wiki": e.get("wiki", "commons.wikimedia.org")} for e in entries],
           "log": [], "stats": {}, "elapsed_s": None}
    with JOBS_LOCK:
        # keep the job store bounded: finished/errored jobs are evicted oldest-first so a client
        # that spams runs cannot grow server memory without limit (live jobs are never evicted).
        JOBS[job["id"]] = job
        finished = [j for j in JOBS.values() if j["status"] in ("done", "error")]
        for old in sorted(finished, key=lambda j: j["created"])[:-MAX_JOBS_KEPT]:
            JOBS.pop(old["id"], None)
    return job


def cache_path(pageid, sha1, wiki="commons.wikimedia.org", served_w=None):
    # pageids are per-wiki and do collide, so the host is part of the key.
    # The width recorded is the SERVED one, not the requested one: Commons quantises `iiurlwidth`
    # upward to a ladder step, so 640 and 960 fetch byte-identical files and must not produce two
    # entries for the same image (T388792). `served_w` comes from the thumburl; it is None when the
    # original is smaller than the request (Commons serves the original rather than upscaling), so
    # fall back to the width we asked for.
    safe = wiki.replace(":", "_")
    width = served_w or THUMB_WIDTH
    return os.path.join(CACHE_DIR, f"{safe}-{pageid}-{sha1}-{SCHEMA_HASH}-{width}.json")


def cache_get(pageid, sha1, wiki="commons.wikimedia.org", served_w=None):
    p = cache_path(pageid, sha1, wiki, served_w)
    if os.path.exists(p):
        try:
            with open(p) as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def cache_put(pageid, sha1, payload, wiki="commons.wikimedia.org", served_w=None):
    try:
        with open(cache_path(pageid, sha1, wiki, served_w), "w") as fh:
            json.dump(payload, fh)
    except Exception:
        pass


def log(job, msg):
    job["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    job["log"] = job["log"][-200:]


def default_list_text():
    """The example list shown in the textarea, read from `default_list.txt` at the repo root.

    Kept in a file of its own so it can be swapped for a real event's file list without touching
    code. Missing/unreadable file is not fatal: the UI simply starts empty.
    """
    try:
        with open(DEFAULT_LIST, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _progress(job):
    job["progress"] = {"done": sum(1 for i in job["items"]
                                   if i["status"] in ("done", "error", "notfound")),
                       "total": len(job["items"])}
    return job["progress"]


THUMBS_DIR = os.path.join(CACHE_DIR, "thumbs")


def _derive(item):
    """The two numbers the UI shows per photo, set as soon as the photo is done.

    Deliberately called at scoring time, not only when the run ends: a 24-photo run takes 20+
    minutes at current endpoint speeds, and the first photos must be readable, ranked and
    reportable long before the last one settles.
    """
    a = ((item.get("result") or {}).get("answers")) or {}
    if not a:
        return item
    item["publish_p"] = wiclient.publish_p(a)
    item["composite"] = wiclient.composite(a)
    return item


def thumb_path(pageid, sha1, wiki="commons.wikimedia.org"):
    """Where the exact bytes sent to the model are kept, so reports can embed them offline."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (wiki or "commons.wikimedia.org").split(".")[0])
    return os.path.join(THUMBS_DIR, f"{safe}-{pageid}-{sha1}.img")


def _save_thumb(item, data: bytes):
    """Save the bytes sent to the model. Only filesystem problems are tolerated here.

    Deliberately narrow: a bare `except Exception` in this spot once hid a NameError for hours
    (the cache-hit path called `thumb_path()` outside this helper and died), so a coding error must
    surface as a failure rather than as a silent "no thumbnail".
    """
    try:
        os.makedirs(THUMBS_DIR, exist_ok=True)
        p = thumb_path(item.get("pageid"), item.get("sha1"), item.get("wiki"))
        with open(p, "wb") as fh:
            fh.write(data)
        item["thumb_file"] = p
        return p
    except OSError:
        return None


def saved_thumb(item):
    """The path of a saved thumbnail for an already-answered photo, if it is still on disk."""
    try:
        p = thumb_path(item.get("pageid"), item.get("sha1"), item.get("wiki"))
    except (NameError, TypeError):
        raise
    return p if os.path.exists(p) else None


def thumb_bytes(item):
    """Bytes for a report: from the saved file if present, else fetched from Commons."""
    p = item.get("thumb_file")
    if p and os.path.exists(p):
        try:
            with open(p, "rb") as fh:
                return fh.read()
        except Exception:                                        # noqa: BLE001
            pass
    if item.get("thumburl"):
        try:
            data = wiclient.fetch_bytes(item["thumburl"])
            _save_thumb(item, data)
            return data
        except Exception:                                        # noqa: BLE001
            return None
    return None


def _download(item):
    """Fetch one thumbnail, keep the bytes for the reports, and base64-encode them.

    Returns (b64, sent_px, seconds). Raises ValueError on over-cap media (bytes via
    wiclient.fetch_bytes, pixels here) — the prefetch thread turns that into an 'error' row,
    so a pathological file cannot clog the pipeline.
    """
    t = time.time()
    data = wiclient.fetch_bytes(item["thumburl"])
    size = wiclient.image_size(data)
    if size and size[0] * size[1] > wiclient.MAX_SEND_PIXELS:
        raise ValueError(f"rendered thumbnail is {size[0]}x{size[1]} px — above the "
                         f"{wiclient.MAX_SEND_PIXELS}-px cap")
    # Cross-check the two independent signals for the width the model will have seen: the `/Npx-`
    # segment of the URL we fetched, and the bytes themselves. If a CDN or proxy ever serves a
    # rendition other than the one the URL advertises, record it on the item rather than silently
    # mislabelling the photo — the same failure class as trusting `imageinfo.thumbwidth`.
    url_w = item.get("served_w")
    if size and url_w and url_w != size[0]:
        item["width_mismatch"] = (f"thumburl advertises {url_w}px, bytes decode as {size[0]}px")
    _save_thumb(item, data)
    return (base64.b64encode(data).decode(), f"{size[0]}x{size[1]}" if size else "unknown",
            time.time() - t)


def _prefetch(items, out, stop, job=None):
    """Producer thread: download images ahead of the (serial) model caller.

    The download half of the work is I/O-bound and the model call is a separate
    round-trip, so one prefetch thread keeps the network busy while the model
    answers for the previous image.

    The item is marked `downloading` only while its bytes are actually in flight and
    `ready` as soon as they are in hand — a row must never claim to be downloading
    something it already holds (it may then wait in the queue for minutes).
    """
    for item in items:
        if stop.is_set():
            return
        item["status"] = "downloading"
        try:
            b64, sent_px, dt = _download(item)
        except Exception as e:                                  # noqa: BLE001 - report, don't die
            item.update({"status": "error", "error": f"thumbnail fetch failed: {e}"})
            out.put(("err", item, str(e), None, 0.0))
            if job is not None:
                _progress(job)
            continue
        item["status"] = "ready"
        item["fetch_s"] = round(dt, 2)
        if item.get("width_mismatch") and job is not None:
            # the cross-check fired: the URL and the bytes disagree about the served width. Report it
            # rather than letting a mislabelled photo through (see _download).
            log(job, f"{item['title']} — served-width mismatch: {item['width_mismatch']}")
        out.put(("ok", item, b64, sent_px, dt))
    out.put(("end", None, None, None, 0.0))


# Measured on this install, not documented anywhere: median per-call latency of
# llm-clef-flash over 62 recorded calls (2026-10-02: 23 calls, median 0.20-0.50s;
# 2026-10-12: 39 calls, median 0.54s, worst 6.95s). Used only to label a run as
# normal / elevated / service-side — see latency_summary().
BASELINE_WARM_S = float(os.environ.get("BASELINE_WARM_S", "0.54"))
SLOW_FACTOR = 4.0        # >4x baseline on every call = the endpoint, not the input


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return None
    return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 3)


def latency_summary(job: dict) -> dict:
    """Compare this job's model-call times against the measured warm baseline."""
    calls = [i["result"]["wall_s"] for i in job["items"]
             if (i.get("result") or {}).get("wall_s") is not None]
    ttfb = [i["result"]["ttfb_s"] for i in job["items"]
            if (i.get("result") or {}).get("ttfb_s") is not None]
    upstream = [int(i["result"]["upstream_ms"]) for i in job["items"]
                if str((i.get("result") or {}).get("upstream_ms") or "").isdigit()]
    out = {"calls": len(calls), "baseline_s": BASELINE_WARM_S,
           "median_s": _median(calls), "min_s": min(calls) if calls else None,
           "max_s": max(calls) if calls else None,
           "median_ttfb_s": _median(ttfb), "median_upstream_ms": _median(upstream)}
    if calls:
        out["factor"] = round(out["median_s"] / BASELINE_WARM_S, 1)
        out["verdict"] = ("normal" if out["factor"] < 1.5 else
                          "elevated" if out["factor"] < SLOW_FACTOR else "service-side")
        out["note"] = {
            "normal": f"model calls match the measured warm baseline "
                      f"({BASELINE_WARM_S}s median over 62 calls on 2026-10-02/12)",
            "elevated": f"model calls are {out['factor']}x the measured warm baseline — "
                        f"usually a cold pod or a queued request",
            "service-side": f"model calls are {out['factor']}x the measured warm baseline "
                            f"({out['median_s']}s vs {BASELINE_WARM_S}s) with no 429s — the "
                            f"slowdown is server-side, not the input, the image size, or this app",
        }[out["verdict"]]
    return out


# ------------------------------------------------------------------- worker
_slot_lock = threading.Lock()
_slot_next = [0.0]           # earliest time the next model call may start (global pacing)


def _next_slot() -> float:
    """Reserve the next model-call start, SPACING apart globally. Returns the wait in seconds.

    With INFLIGHT > 1 this is what keeps several streams looking like one well-paced client:
    starts never bunch up, and the inter-call gap stays exactly what it was serially.
    """
    now = time.time()
    with _slot_lock:
        t = max(now, _slot_next[0] + SPACING)
        _slot_next[0] = t
    return max(0.0, t - now)


def retry_summary(items) -> dict:
    """How the bounded retry policy actually behaved on this run.

    `recovered` is the number that matters and the reason this exists: it is the only in-repo
    measurement of whether retrying a transient gateway 5xx was worth it. It is reported on every
    job (and logged) so the answer accumulates from ordinary runs rather than needing a dedicated
    load test against a young service. Added 2026-10-14 after the 503 rate was measured (3.5%) but
    the *recovery* rate could not be — no run had ever exercised the retry path.
    """
    attempts = [int(i.get("upstream_attempts") or 1) for i in items]
    retried = [i for i, n in zip(items, attempts) if n > 1]
    return {"photos_retried": len(retried),
            "extra_requests": sum(n - 1 for n in attempts if n > 1),
            "recovered": sum(1 for i in retried if i.get("status") == "done"),
            "still_failed": sum(1 for i in retried if i.get("status") != "done")}


def _score_item(item, payload, job) -> dict:
    """One photo's model call, with the whole retry policy. Returns the final response.

    Every attempt — first or retry — is reserved through `_next_slot()`, so a retry is paced like any
    other call start and the endpoint never sees a retry burst. There is never a loop:

      * `429` — the server said when to come back, so one retry; `Budget` holds the cooldown for
        every worker, and a second 429 is returned as-is.
      * `502/503/504` — the gateway could not reach the model ("upstream connect error or
        disconnect/reset before headers … connection timeout"), so the photo was never processed and
        nothing was billed. Retried up to `UPSTREAM_MAX_ATTEMPTS` with exponential backoff.
      * anything else — returned as-is. A 4xx is a client-side fault that retrying cannot fix.
    """
    item["status"] = "scoring"
    item["scoring_started"] = time.time()   # the UI ticks its "waiting for the model" clock
    attempt = 0
    rate_limited_retry_used = False
    while True:
        delay = _next_slot()
        if delay > 0:
            time.sleep(delay)
        attempt += 1
        res = wiclient.score_image(payload, BUDGET)
        code = res.get("http")

        if code == 429 and not rate_limited_retry_used:
            rate_limited_retry_used = True
            item["rate_limited"] = True
            log(job, f"429 — the server said wait {res.get('retry_after')}s; one retry")
            continue

        if code in wiclient.UPSTREAM_RETRY_CODES and attempt < wiclient.UPSTREAM_MAX_ATTEMPTS:
            wait = min(wiclient.UPSTREAM_RETRY_BACKOFF * (2 ** (attempt - 1)),
                       wiclient.UPSTREAM_RETRY_BACKOFF_MAX)
            log(job, f"HTTP {code} from the gateway — the model was not reached, so nothing was "
                     f"processed; retrying in {wait:g}s (attempt {attempt + 1} of "
                     f"{wiclient.UPSTREAM_MAX_ATTEMPTS})"
                     + (f" — {str(res.get('detail', ''))[:200]}" if res.get("detail") else ""))
            time.sleep(wait)
            continue
        break

    item["upstream_attempts"] = attempt
    if attempt > 1:
        item["upstream_retry"] = True
    item["scoring_s"] = round(time.time() - item["scoring_started"], 2)
    return res


def _endpoint_probe() -> dict:
    """Pre-flight the endpoint once per PROBE_TTL and reuse the verdict for later runs."""
    if time.time() - _PROBE.get("t", 0) < PROBE_TTL and _PROBE.get("result"):
        return dict(_PROBE["result"]) | {"cached": True}
    res = wiclient.probe_endpoint()
    BUDGET.note_call()
    _PROBE.update(t=time.time(), result=res)
    return dict(res)


_PROBE: dict = {"t": 0.0, "result": None}


def _refresh_derived(job):
    """Recompute the per-photo numbers and the run-level spread from whatever has settled.

    Called after every photo settles, not just at the end of the run: with the endpoint at ~1 call
    per minute, a 24-photo run is 20+ minutes long, and a reader must be able to see the composite,
    the ranking and the spread for the photos that are already done.
    """
    for item in job["items"]:
        _derive(item)
    scored = [i for i in job["items"] if i.get("composite") is not None or
              ((i.get("result") or {}).get("answers") or {}).get("infobox_ready")]
    job["spread"] = {
        "by": "composite",
        "composite": wiclient.candidate_spread(
            [(i.get("title") or i.get("raw"), i.get("composite")) for i in scored]),
        "infobox_ready": wiclient.candidate_spread(
            [(i.get("title") or i.get("raw"),
              (i["result"]["answers"].get("infobox_ready") or {}).get("noul")) for i in scored]),
        "publish_ready": wiclient.candidate_spread(
            [(i.get("title") or i.get("raw"), i.get("publish_p")) for i in scored]),
    }
    sp = job["spread"]["composite"]
    if sp.get("n") and sp.get("n") != job.get("_spread_logged_n"):
        job["_spread_logged_n"] = sp["n"]
        log(job, f"candidate spread (composite, {sp['n']} scored): {sp['n_candidates']} above "
                 f"{sp['floor']} · range {sp['candidate_range']} · median gap "
                 f"{sp['candidate_median_gap']} · {sp['dead_zone']} in the 0.35–0.65 band")
    job["stats"] = reports.stats(job["items"])


def run_job(job):
    job["status"] = "running"
    t0 = time.time()
    try:
        log(job, "resolving %d titles on %s" % (len(job["items"]),
            ", ".join(sorted({i.get("wiki", "commons.wikimedia.org") for i in job["items"]}))))
        meta = wiclient.resolve([{"title": i["title"], "wiki": i.get("wiki")} for i in job["items"]])
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"Commons lookup failed: {e}"
        return
    for item in job["items"]:
        m = meta.get((item.get("wiki") or "commons.wikimedia.org", item["title"])) or {}
        item.update({k: v for k, v in m.items() if k != "ok"})
        if not m.get("ok"):
            item["status"] = "notfound"
            item["error"] = m.get("error", "not found")
    log(job, f"resolved {sum(1 for i in job['items'] if i.get('pageid'))} of {len(job['items'])}")

    # cache pass first, so the download thread only handles images that need scoring
    bypass = bool(job.get("no_cache"))
    replay = bool(job.get("cached_only")) and not bypass
    if bypass:
        log(job, "cache bypassed for this run (no_cache) — every photo is scored fresh, "
                 "one model call each")
    if replay:
        log(job, "replay (cached_only): scoring from saved answers only, so opening this link "
                 "cannot spend a model call — anything not already scored is left for you")
    pending = []
    for item in job["items"]:
        if item["status"] == "notfound":
            continue
        hit = None if bypass else cache_get(item["pageid"], item.get("sha1"),
                                            item.get("wiki", "commons.wikimedia.org"),
                                            item.get("served_w"))
        if hit:
            item.update({"status": "done", "resolved": True, "result": hit["result"],
                         "sent_px": hit.get("sent_px"), "from_cache": True})
            item["thumb_file"] = saved_thumb(item)
            if not item["thumb_file"] and item.get("thumburl"):
                # the answer predates the thumbnail store (or it was cleaned out): fetch the bytes
                # once from Commons so the reports stay self-contained. Commons is not the
                # rate-limited service here, so this costs nothing against the model budget.
                try:
                    _save_thumb(item, wiclient.fetch_bytes(item["thumburl"]))
                except Exception as exc:                          # noqa: BLE001 — never fatal
                    log(job, f"{item['title']} — thumbnail not stored: {exc}")
            _derive(item)
            log(job, f"{item['title']} — cached")
        elif replay:
            # A shared link must be incapable of spending: no answer on this server means no answer,
            # not "go and fetch one". The row says so, and Score them is left armed.
            item["status"] = "uncached"
            item["error"] = "not scored on this server yet — press Score them to evaluate it"
            log(job, f"{item['title']} — not in cache (replay)")
        else:
            pending.append(item)
    _progress(job)
    if job["items"] and len(pending) < len(job["items"]):
        log(job, f"{len(job['items']) - len(pending)} of {len(job['items'])} answered from cache "
                 f"(no API call)")
    _refresh_derived(job)          # cached photos are already readable before the slow ones start

    fetch_s = score_s = 0.0
    if pending:
        # pre-flight: verify the endpoint answers a tiny text-only question before spending the
        # queue. On failure the run stops here — n cheap-looking "error" rows after an outage are
        # worse than one honest early stop (and 429s/EngineCore 500s cost nothing further).
        probe = _endpoint_probe()
        job["probe"] = probe
        if not probe.get("ok"):
            why = probe.get("error") or (f"HTTP {probe.get('http')}" if probe.get("http") else "no answer")
            log(job, f"endpoint pre-flight FAILED ({why}, {probe.get('wall_s')}s) — stopping before "
                     f"spending the queue; retry when the endpoint answers again")
            for item in pending:
                item.update({"status": "error",
                             "error": "endpoint pre-flight failed — the model endpoint did not "
                                      "answer, so nothing was scored; thumbnails and resolution "
                                      "are kept"})
            _refresh_derived(job)
            job["stats"] = reports.stats(job["items"])
            job["latency"] = latency_summary(job)
            job["elapsed_s"] = round(time.time() - t0, 2)
            job["status"] = "error"
            log(job, f"finished in {job['elapsed_s']}s")
            return
        log(job, f"endpoint check ok — answered a text-only pre-flight in {probe.get('wall_s')}s")
        log(job, f"pipeline: downloading ahead ({PREFETCH} buffered) while the model answers, "
                 f"{INFLIGHT} call(s) in flight, spaced {SPACING}s")
        q: queue.Queue = queue.Queue(maxsize=PREFETCH)
        out: queue.Queue = queue.Queue()
        stop = threading.Event()
        t_pipe = time.time()
        threading.Thread(target=_prefetch, args=(pending, q, stop, job), daemon=True).start()

        def scorer():
            """One of up to INFLIGHT workers: turn a downloaded image into a model answer.

            Call starts are paced globally (SPACING apart, whichever worker they come from), so
            two in-flight calls still look like one polite client to the endpoint — budget and
            the one-retry-then-stop 429 rule are unchanged. The win: a slow call is paid by one stream
            while the other keeps working, instead of the whole queue waiting on it.

            NOTE — an earlier version of this comment claimed the slow calls were a "cold-start
            dimension load (~6 s for a never-seen width×height)". Measured 2026-10-14 and **false**:
            33 of 34 never-seen shapes in a 50-file run took ~0.3 s, and repeating one shape gave
            0.52 s, then 4.60 s, then twelve ~0.45 s calls. See HANDOFF §4 before "optimising" the
            image size or cropping to a known dimension — it buys nothing and changes the answers.
            """
            while not stop.is_set():
                try:
                    kind, item, payload, sent_px, dt = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if kind == "end":
                    out.put(("end", None, None, None, 0.0))
                    return
                if kind == "err":
                    out.put(("err", item, payload, None, 0.0))
                    continue
                out.put(("start", item, payload, sent_px, dt))
                item["sent_px"] = sent_px
                # Pacing AND the whole retry policy live in _score_item (see its docstring). Every
                # attempt goes through _next_slot(), so a retry is paced like any other call start
                # and can never arrive as a burst — that was the one path that used to bypass the gate.
                res = _score_item(item, payload, job)
                out.put(("res", item, res, None, dt))

        threads = [threading.Thread(target=scorer, daemon=True) for _ in range(INFLIGHT)]
        for t in threads:
            t.start()
        # the loop counts *terminal* events only: one "res" (or "err") per pending item.
        # "start" events make the scoring state visible but do not count; "end" (prefetch
        # exhausted) is noted and waited through, never a break — results may still be draining.
        finished = 0
        consecutive_upstream_failures = 0     # circuit breaker — see UPSTREAM_ABORT_AFTER
        abort_reason = None
        while finished < len(pending):
            kind, item, payload, sent_px, dt = out.get()
            if kind == "end":
                continue
            if kind == "err":
                finished += 1
                log(job, f"{item['title']} — fetch failed: {payload}")
                _progress(job)
                continue
            if kind == "start":
                fetch_s += dt
                continue
            # kind == "res": a model answer (or error) for one item
            finished += 1
            res = payload
            if res.get("http") == 200:
                consecutive_upstream_failures = 0
                item.update({"status": "done", "resolved": True, "result": res, "from_cache": False})
                cache_put(item["pageid"], item.get("sha1"),
                          {"result": res, "sent_px": item["sent_px"], "title": item["title"]},
                          item.get("wiki", "commons.wikimedia.org"), item.get("served_w"))
                score_s += res.get("wall_s") or 0
                a = res["answers"]
                pub = wiclient.publish_p(a)
                comp = wiclient.composite(a)
                log(job, f"{item['title']} — P(infobox) {a['infobox_ready']['noul']:.3f}, "
                         f"P(publish) {'n/a' if pub is None else format(pub, '.3f')}, "
                         f"composite {'n/a' if comp is None else format(comp, '.3f')}, "
                         f"quality {a['quality']['score']:.2f}, pose {a['pose']['choice']}, "
                         f"{res['wall_s']}s")
            else:
                code = res.get("http")
                detail = str(res.get("detail") or "")
                item.update({"status": "error",
                             "error": f"HTTP {code} {res.get('error')}"
                                      + (f": {detail[:160]}" if detail else "")})
                # the *run log* keeps the whole gateway body (300 chars from score_image): a
                # truncated cell once hid the one word that identified a 503 as a connect error
                log(job, f"{item['title']} — failed: HTTP {code} {res.get('error')}"
                         + (f" — {detail}" if detail else ""))
                # only the *connection-level* failures count toward the breaker: the model was never
                # reached, so these are the ones an outage produces en masse.
                consecutive_upstream_failures = (consecutive_upstream_failures + 1
                                                 if code in wiclient.UPSTREAM_RETRY_CODES else 0)
            _refresh_derived(job)          # this photo is readable now, not at the end of the run
            _progress(job)
            if UPSTREAM_ABORT_AFTER and consecutive_upstream_failures >= UPSTREAM_ABORT_AFTER:
                abort_reason = (f"stopping after {consecutive_upstream_failures} consecutive photos "
                                f"failed on a transient gateway error — the model was not being "
                                f"reached, so the rest of the queue is left unscored rather than "
                                f"retried at the endpoint's expense (UPSTREAM_ABORT_AFTER="
                                f"{UPSTREAM_ABORT_AFTER})")
                break
        if abort_reason:
            stop.set()                     # stop the prefetch thread and the workers
            log(job, abort_reason)
            for it in pending:
                if it.get("status") not in ("done", "error"):
                    it.update({"status": "error", "error": abort_reason})
            finished = len(pending)
            _refresh_derived(job)
        stop.set()
        scoring_s = round(time.time() - t_pipe, 2)
        job["pipeline"] = {"images": len(pending), "prefetch_depth": PREFETCH,
                           "download_s": round(fetch_s, 2), "model_s": round(score_s, 2),
                           "scoring_s": scoring_s,
                           # what this phase would have cost doing it one image at a time
                           "serial_estimate_s": round(fetch_s + score_s
                                                      + SPACING * max(0, len(pending) - 1), 1),
                           "saved_s": round(fetch_s + score_s + SPACING * max(0, len(pending) - 1)
                                            - scoring_s, 2)}
        job["retries"] = retry_summary(job["items"])
        if job["retries"]["photos_retried"]:
            r = job["retries"]
            log(job, f"retries: {r['photos_retried']} photo(s) retried for {r['extra_requests']} "
                     f"extra request(s) — {r['recovered']} recovered, {r['still_failed']} still failed")

    job["retries"] = retry_summary(job["items"])      # zeros for a fully cached run

    # the per-photo numbers and the spread are recomputed every time a photo settles, so the list,
    # the ranking and the spread panel are readable long before the last photo finishes.
    _refresh_derived(job)

    job["stats"] = reports.stats(job["items"])
    job["latency"] = latency_summary(job)
    if job["latency"].get("note"):
        log(job, f"latency: {job['latency']['note']}")
    job["elapsed_s"] = round(time.time() - t0, 2)
    # A replay that found nothing cached is not a failure — it is information (nobody has scored
    # these here yet), and the UI should invite a real run rather than show an error.
    job["status"] = ("done" if (replay or any(i["status"] == "done" for i in job["items"]))
                     else "error")
    log(job, f"finished in {job['elapsed_s']}s")


# ------------------------------------------------------------------- about
# `APP_VERSION` now lives in wiclient.py (it also builds the default User-Agent) and is re-exported
# by the import above, so `core.APP_VERSION` keeps working (package.sh reads it).
MODEL_CARD = "https://huggingface.co/Cloudflare/clef-flash"
LIFTWING_TICKET = "https://phabricator.wikimedia.org/T440223"
RATE_LIMIT_TICKET = "https://phabricator.wikimedia.org/T426749"
WIKITECH_PAGE = "https://wikitech.wikimedia.org/wiki/Machine_Learning/LiftWing/Large_Language_Models"


def about_payload() -> dict:
    """Everything needed to audit one decision, assembled from the live constants.

    The prompt, the legends and the limits are interpolated from `wiclient` rather than
    retyped, so this panel cannot drift from what the app actually sends.
    """
    import html as _html

    esc = _html.escape
    q_items = []
    for qid, spec in QUESTIONS.items():
        lines = [f"<b>{esc(qid)}</b> — <code>{esc(spec['type'])}</code>"]
        if spec.get("instructions"):
            lines.append(f"<i>{esc(spec['instructions'])}</i>")
        crit = spec.get("criteria")
        if isinstance(crit, dict):
            lines.append("<br>".join(f"<code>{esc(k)}</code> — {esc(v)}"
                                     for k, v in crit.items()))
        elif isinstance(crit, list):
            lines.append("<br>".join(f"<code>{i}</code> — {esc(v)}"
                                     for i, v in enumerate(crit)))
        q_items.append({"id": qid, "type": spec["type"],
                        "instructions": spec.get("instructions", ""),
                        "criteria": crit, "html": "<br>".join(lines)})

    template = {"model": "llm-clef-flash", "state": STATE, "questions": QUESTIONS,
                "images": ["<base64 of the thumbnail, one image per request>"]}

    sections = [
        {"title": "What this app does",
         "html": (
           "<p><b>WikiPortraits Eval is a tool to help evaluate the quality and usefulness of "
           "Wikimedia Commons images in illustrating a human-subject portrait</b> — for example when "
           "choosing which photograph to add to a Wikipedia article. It puts each photo in front of "
           "a Wikimedia-hosted model, which returns probabilities rather than prose, and ranks them."
           "</p>"
           "<p>Paste Wikimedia Commons file references (bare names, <code>File:</code> titles, "
           "page URLs, upload/thumbnail URLs, wikitext), and every photo is sent once to "
           "<b>llm-clef-flash</b> — Cloudflare's decision model, served by the Wikimedia "
           "Foundation on LiftWing — with one fixed six-question schema. The app then ranks the "
           "photos by a <b>composite</b> it computes itself from three of the answers (face "
           "visible, eyes to camera, pose), and shows the model's own P(publish) and P(infobox) "
           "beside it.</p>"
           "<p><b>Clicking the title</b> starts a new session — it asks first, then clears the list and the "
                  "run on screen; the chart view, thumbnail size and row order you chose are kept.</p>"
                  "<p><b>How you drive it:</b> the box starts empty — the accepted formats below are shown "
           "only as a grey hint — so nothing can be scored by accident. Press <b>Load sample list</b> "
           "to fill it with 12 real Commons photos, or paste your own list. <b>Validate files</b> "
           "resolves every line against Commons first; <b>Score them</b> then spends one model call "
           "per photo. Starting a new run clears the previous results — after asking, and only when "
           "there is something to lose.</p>"
           "<p>Every answer comes from one request per photo. There is no text generation, no "
           "second pass, no human review, and nothing is written to any wiki.</p>")},
        {"title": "Accepted input formats",
         "html": ("<p>One image per line; these forms all work and can be mixed:</p>"
                  "<p><code>Foo.jpg</code> · <code>File:Foo.jpg</code> · "
                  "<code>commons.wikimedia.org/wiki/File:Foo.jpg</code> · "
                  "<code>https://upload.wikimedia.org/wikipedia/commons/0/06/Foo.jpg</code> · "
                  "<code>…/thumb/0/06/Foo.jpg/960px-Foo.jpg</code> · "
                  "<code>Special:FilePath/Foo.jpg</code> · "
                  "<code>…/w/index.php?title=File:Foo.jpg</code> · "
                  "<code>[[File:Foo.jpg|thumb|caption]]</code></p>"
                  "<p>Case, underscores, <code>%20</code> encoding, bullets/numbering and quotes are "
                  "all tolerated; duplicates are merged. A comma or semicolon only separates two "
                  "entries when another entry clearly follows it (a <code>File:</code> prefix, a URL, "
                  "<code>[[</code>), so file names that contain a comma — "
                  "<code>…(In die Sonne schauen, Cannes 2025).jpg</code> — stay intact, while "
                  "<code>Foo.jpg, Bar.jpg</code> still splits. Files hosted on other Wikimedia wikis "
                  "work too and are labelled with their wiki. Non-photo media (SVG, PDF, video) "
                  "resolve as well, because Commons renders a raster preview. Category listings are "
                  "<em>not</em> supported yet — paste the file names instead.</p>"
                  "<p><b>Linking to a list:</b> <code>?list=</code> fills the box from the URL, and "
                  "adding <code>&amp;check=1</code> also presses <i>Validate files</i>. Entries are "
                  "separated by <code>%0A</code> — deliberately the only separator, because "
                  "<code>|</code> occurs in the wikitext form above and a comma occurs in real file "
                  "names. Build links with <code>encodeURIComponent</code> (a literal "
                  "<code>+</code> must be <code>%2B</code>, or it arrives as a space). Links are "
                  "capped: refused past ~4,000 characters, and the input check is never run "
                  "automatically for more than 50 entries. <b>A link never starts a run</b> — "
                  "scoring always takes a click, because it spends a model call per photo.</p>")},
        {"title": "How a run works, step by step",
         "html": ("<ol>"
           "<li><b>Parse and normalise</b> each pasted line to a <code>File:</code> title. A line "
           "that already reads as one file name is never split, so names containing commas survive "
           "(<code>…(In die Sonne schauen, Cannes 2025).jpg</code>). A <code>#</code> truncates, "
           "duplicates merge.</li>"
           "<li><b>Work out which wiki owns it.</b> <code>upload.wikimedia.org/wikipedia/&lt;project&gt;/…"
           "</code> and <code>&lt;lang&gt;.wikipedia.org/wiki/File:…</code> are resolved on that "
           "wiki's API, not on Commons. Files on another wiki are labelled with their host.</li>"
           "<li><b>Resolve the title</b> with the wiki's Action API "
           "(<code>action=query&amp;prop=imageinfo&amp;iiprop=url|size|mime|sha1|extmetadata</code>): "
           "page ID, dimensions, MIME type, SHA-1, licence, revision timestamp and a pre-rendered "
           "thumbnail URL. Titles are batched <code>|</code>-separated; API normalisation and "
           "redirects are mapped back to the string you pasted.</li>"
           "<li><b>Cache check</b> on <code>pageid + SHA-1 + schema + served width</code> — the "
           "width actually <em>served</em>, read from the thumbnail URL, so two different requests "
           "that resolve to the same rendition share one entry. A cache hit "
           "costs no API call; overwriting a file on Commons changes its SHA-1 and invalidates the "
           "cached answer.</li>"
           "<li><b>Download the thumbnail</b> server-side, asked for at the " + str(THUMB_WIDTH) +
           "&nbsp;px step of Commons' <code>$wgThumbnailSteps</code> ladder. The original file is "
           "never fetched; Commons quantises a request up to the next step it actually renders — "
           "640 is not a step, which is why the app asks for 960 and sends whatever bytes come back "
           "— and the model server then downscales to 640&nbsp;px before the vision encoder. Every "
           "result keeps the served width (<code>served_w</code>) beside the decoded size "
           "(<code>sent_px</code>).</li>"
           "<li><b>One decision call per photo</b>: the image inlined as base64 plus the schema "
           "below. Downloads for the next photos already run in a background thread while the model "
           "answers, and calls are spaced ~0.9&nbsp;s apart with a stop-on-429 policy (~100 "
           "anonymous calls per hour; more from Toolforge/WMCS).</li>"
           "<li><b>Record, cache, report.</b> Answers, token counts and timings (wall time, "
           "time-to-first-byte) are stored per photo, then rendered as Markdown, JSON, CSV or PDF.</li>"
           "</ol>")},
        {"title": "The exact request sent to Clef",
         "html": (f"<p><code>POST {esc(wiclient.LIFTWING)}</code><br>"
                  f"User-Agent: <code>{esc(wiclient.UA)}</code></p>"
                  "<p>The body is literally this, with the image base64 substituted:</p>"
                  f"<pre>{esc(json.dumps(template, indent=2))}</pre>"
                  "<p><code>state</code> is the standing context; <code>instructions</code> is the "
                  "question; <code>criteria</code> defines the allowed answers. Nothing else is "
                  "sent — no filename, no Commons metadata, no prompt engineering beyond these "
                  "strings.</p>")},
        {"title": "The six questions, verbatim",
         "html": "<table><tr><th>question</th><th>type</th><th>instructions and allowed answers</th></tr>"
                 + "".join(f"<tr><td><code>{esc(q['id'])}</code></td>"
                           f"<td><code>{esc(q['type'])}</code></td><td>{q['html']}</td></tr>"
                           for q in q_items)
                 + "</table>"
                 + "<p><b>The three headline numbers, and what each one is:</b></p><ul>"
                 + ("<li><b>composite</b> — <i>not a model probability</i>. This is our own "
                    "arithmetic: <code>min(face_visible, eyes_to_camera) × pose weight × "
                    "unobstructed</code>, with "
                    "head-and-shoulders 1.0, three-quarter 0.9, full body 0.6, candid 0.5, group "
                    "0.25. <b>unobstructed</b> is the model's answer to whether anything is held in "
                    "front of the face (a microphone, a hand): it scales the score by 0.5–1.0, so a "
                    "covered face is demoted rather than disqualified — the photo may still be the "
                    "best one that exists of that person. It is the ranking key because the questions "
                    "that ask about something "
                    "<i>visible in the frame</i> separate photos well, while the editorial ones "
                    "collapse toward the middle — measured on 57 photos: face_visible spread 0.372, "
                    "eyes_to_camera 0.350, infobox_ready 0.229, quality 0.145 (sd). Every "
                    "ingredient is shown on the card, so you can disagree with the weighting.</li>"
                    "<li><b>publish_ready</b> — the model's own two-option answer: it picks "
                    "<code>publish</code> (\u201cthis photograph is the one to use\u201d) or "
                    "<code>pass</code>. We show P(publish). It was chosen because it spreads without "
                    "saturating at the ceiling, unlike the rewrites tested on 2026-10-13.</li>"
                    "<li><b>infobox_ready</b> — the model's own yes/no on the original wording "
                    "(\u201cgood enough to be the lead image (infobox portrait) of a Wikipedia "
                    "biography\u201d). Kept for continuity, shown but no longer the ranking key.</li>"
                    "</ul>")},
        {"title": "What each answer means",
         "html": (
           "<p>The model returns a probability for every allowed option, in one forward pass "
           "(<code>usage.output_tokens</code> is always 0 — it generates no text):</p><ul>"
           "<li><b>noul</b> (true/false) — a single number, the model's <i>probability that the "
           "answer is true</i>. So <code>infobox_ready: 0.65</code> means \"65% likely to be good "
           "enough as a biography lead image\" <i>on the wording above</i>. The app reads it that "
           "way because the model card defines it that way.</li>"
           "<li><b>choice</b> — the most likely option plus the full distribution; the app shows "
           "the winner and its probability, and lists the rest.</li>"
           "<li><b>score</b> — an <i>expected value</i> across the ordered legend plus the "
           "distribution. <b>quality 2.23/4</b> is the mean of that distribution: 0 unusable, "
           "1 poor, 2 acceptable, 3 good, 4 excellent.</li></ul>"
           "<p><b>What \"quality\" means here, exactly:</b> only the five labels above. There is no "
           "sharpness, resolution, lighting or composition rubric in the request, and the model "
           "sees a 640&nbsp;px-wide thumbnail — so it is judging \"usable in an encyclopedia "
           "article\" as described, not performing a technical image-quality measurement. Treat it "
           "as a comparative signal between photos, not an absolute grade.</p>")},
        {"title": "Candidate spread — the metric the run reports",
         "html": (
           "<p>\"Do the numbers separate anything?\" is answered by <b>candidate spread</b>, not by "
           "standard deviation. sd rises whenever answers pile up at either end — including at the "
           "ceiling, which is exactly what happened when we tried rewording the infobox question: "
           "on the same four photos every rewrite emptied the 0.35–0.65 band by pushing all the "
           "real portraits to 0.81–0.96, cutting the range between them from 0.486 to as little as "
           "0.021.</p>"
           "<p>The collapsed line above the numbers carries just the candidate counts; expanding it "
           "gives the range, the median gap, the dead-zone share and the leading file names. The "
           "chart below the run plots the composite for every photo, and its <b>by pose</b> view puts "
           "each pose in its own labelled stratum on the same axis, so you can compare photos within "
           "a pose. Rank numbers stay global in both views. The list itself can be ordered by the "
           "<b>composite</b> (best first, the default) or <b>as pasted</b> — the order the lines "
           "arrived in, which is also the order they were evaluated; that view shows each row's "
           "list position with its composite rank named beside it. The <b>PDF report exports "
           "whichever view you are looking at</b> — its chart switches with the same control. "
           "<b>Clicking a thumbnail</b> — in the chart or on a result card — opens that photo's "
           "full determinations in place, with links out to its Commons page and the original "
           "file; Escape closes it. The image shown is the exact thumbnail sent to the model.</p>"
           "<p>A finished run can be shared as <b>one link</b> (the <i>Copy share link</i> "
           "button under the results). The link carries the file list and the chart view, and "
           "<b>replays from saved answers only</b> — so opening it shows the same graph at once "
           "and can never spend a model call. Anything this server has not scored stays in the "
           "list, marked as not scored, with <i>Score them</i> armed for the person reading it."
           "</p>"
           "<p>So each run reports, per headline number:</p><ul>"
           "<li><b>candidates</b> — how many photos score at or above the candidate floor "
           f"(<code>{CANDIDATE_FLOOR}</code>, set by <code>CANDIDATE_FLOOR</code>);</li>"
           "<li><b>candidate range</b> — the gap between the best and worst <i>candidate</i>, which "
           "is the separation triage actually uses;</li>"
           "<li><b>median gap</b> — the typical distance between neighbouring candidates (small "
           "gaps mean near-ties);</li>"
           "<li><b>dead zone</b> — how many answers sit in 0.35–0.65, where a number carries little "
           "information either way.</li></ul>"
           "<p>These are reported for the composite, for P(publish) and for P(infobox) side by "
           "side, so you can see which number is doing the separating in your own run.</p>")},
        {"title": "What it does not do",
         "html": ("<ul>"
           "<li>No identification, recognition or depiction of people — it never names anyone and "
           "the app sends no names.</li>"
           "<li>No notability, licence, copyright or consent check. Licence strings are shown from "
           "Commons metadata for context only.</li>"
           "<li>No threshold magic: \"≥ 0.5\" is a reporting convenience, not a tuned decision "
           "boundary. In our samples nothing reached above ~0.71, so use the ranking, and calibrate "
           "against labelled data before trusting any cut-off.</li>"
           "<li>No on-wiki action of any kind. Nothing is uploaded, tagged, categorised or edited.</li>"
           "<li>Answers are model estimates, not ground truth, and the platform is officially "
           "\"experimental\" with no availability SLA.</li></ul>")},
        {"title": "Where the bytes go",
         "html": ("<ul><li>The browser only talks to this app. Commons API lookups, thumbnail "
                  "downloads and the Clef call are all made by the server process, so the client IP "
                  "the endpoint sees is this host's.</li>"
                  "<li>The one exception: the thumbnails shown in the results list are hotlinked by "
                  "your browser from <code>thumb.wikimedia.org</code>/<code>upload.wikimedia.org</code>."
                  "</li>"
                  "<li>The image bytes and the <code>state</code> string are sent to Wikimedia's "
                  "LiftWing (<code>api.wikimedia.org</code>). Results are cached locally under "
                  "<code>cache/</code>; job state lives in memory and disappears when the server "
                  "restarts. Ticking <b>run fresh</b> ignores those saved answers and re-scores "
                  "every photo, so the speed you see is a real run — it costs one call per photo, "
                  "which is why it is off by default. The app also counts its own model calls and shows "
                  "the count against <code>HOURLY_BUDGET</code> per hour — a <b>display, not a brake</b>: "
                  "no call is refused when the counter passes it. That ceiling is the app's "
                  "own choice, not the platform's: on Toolforge the endpoint's "
                  "<code>LiftWingLLM</code> policy rates WMCS traffic as effectively unlimited "
                  "(T426749). No accounts, no analytics, no third parties. <b>What is logged:</b> one line per request with the method, path and "
                  "status — no query string, no IP address and no User-Agent — plus a per-run log "
                  "held in memory and a per-photo cache entry. Toolforge logs requests at the "
                  "platform layer; this app neither reads nor republishes that. If usage is ever "
                  "measured it will be as counters, not as logs.</li></ul>")},
        {"title": "HTTP API",
         "html": ("<p>Everything the page does is also available programmatically. Client "
                  "timeout should be generous — a call blocks until the whole run finishes "
                  "(~1 call per photo, spaced " + str(SPACING) + "&nbsp;s apart).</p>"
                  "<pre>GET  /api/health               → liveness\n"
                  "GET  /api/about                → this panel (live prompt, legends, limits)\n"
                  "GET  /api/config               → budget, cache size, schema hash\n"
                  "GET  /api/default-list         → the example list (default_list.txt)\n"
                  "POST /api/parse               {\"text\": \"Foo.jpg\\nBar.jpg\"} → resolved metadata\n"
                  "POST /api/run                 {\"text\": …} → job_id; poll GET /api/job/&lt;id&gt;\n"
                  "                              add \"no_cache\": true to re-score every photo\n"
                  "POST /api/eval                {\"files\": [\"File:A.jpg\", …]} → blocked, final\n"
                  "                                results (≤ " + str(EVAL_MAX) + " files per call)\n"
                  "GET  /api/job/&lt;id&gt;/report.{md,json,csv,pdf}</pre>"
                  "<p><code>GET /api/eval?list=…</code> is the readable twin of a share link: the same "
                  "ranked JSON with no model calls at all — anything unscored comes back as "
                  "<code>uncached</code>. <code>POST /api/eval</code> is the one that scores; the "
                  "GET form exists only because it cannot spend.</p>"
                  "<p>The <b>Copy API link</b> button under a finished run writes a ready-to-paste <code>curl</code> "
                  "command for that exact list (split per <code>EVAL_MAX</code> if it is long). It is a "
                  "command to run rather than a URL: a GET that scores would fire from link previews and "
                  "crawlers, which is why the share link replays from cache only.</p>"
                  "<p><code>/api/eval</code> returns ranked <code>results[]</code> (composite, "
                  "P(publish), pose, quality, per-question probabilities, licence, cache/wall "
                  "info), the run's <code>spread</code> and <code>stats</code>, and links to the "
                  "four reports. It honours the same cache, budget and 429 rules as the UI.</p>")},
        {"title": "Provenance",
         "html": (f"<ul><li>App version {esc(APP_VERSION)} · model "
                  f"<code>llm-clef-flash</code> (clef-flash 9B, Apache-2.0) — BF16, 16,384-token "
                  f"context, 1&nbsp;GPU, on the HuggingFace backend rather than vLLM, which is why "
                  f"this app calls the KServe <code>:predict</code> endpoint and not an "
                  f"OpenAI-compatible one · schema hash "
                  f"<code>{esc(SCHEMA_HASH)}</code> · thumbnail step {THUMB_WIDTH}&nbsp;px · "
                  f"call spacing {SPACING}&nbsp;s (the rate control, independent of workers), "
                  f"up to {INFLIGHT} concurrent model call(s).</li>"
                  f"<li>Latency baseline {BASELINE_WARM_S}&nbsp;s per call, measured on this "
                  f"install (62 recorded calls, 2026-10-02 and 2026-10-12) — not a documented "
                  f"figure. Measured 0.327&nbsp;s inside the deployed pod (2026-10-14), so it "
                  f"reads ~1.6&times; pessimistic there.</li>"
                  f"<li>Semantics quoted from the model card: <a href=\"{MODEL_CARD}\">"
                  f"huggingface.co/Cloudflare/clef-flash</a>; the model list and platform caveats "
                  f"from <a href=\"{WIKITECH_PAGE}\">Wikitech: LiftWing Large Language Models</a>."
                  f"</li>"
                  f"<li>Parsing and thumbnail rules follow the Wikipedia-AI-Skills "
                  f"<code>commons-file-resolution</code> and <code>wikimedia-commons-thumbnails</code> "
                  f"skills.</li>"
                  f"<li>The model was deployed under <a href=\"{LIFTWING_TICKET}\">Phabricator"
                  f" T440223</a>, and this app is probably its heaviest consumer; the endpoint's "
                  f"rate-limit policy is <code>LiftWingLLM</code> "
                  f"(<a href=\"{RATE_LIMIT_TICKET}\">T426749</a>), which rates WMCS traffic as "
                  f"effectively unlimited but keeps anonymous use at a shared 100 requests/hour."
                  f"</li>"
                  f"<li>Thumbnails are requested at the {THUMB_WIDTH}&nbsp;px step of Commons' "
                  f"<code>$wgThumbnailSteps</code> ladder "
                  f"({', '.join(str(x) for x in wiclient.THUMB_LADDER)}). Commons quantises a "
                  f"request upward to the next step, so asking for anything between steps fetches "
                  f"the step above it — and <code>imageinfo.thumbwidth</code> reports the request "
                  f"rather than the file. Every result therefore carries <code>served_w</code> "
                  f"(read from the thumbnail URL) beside <code>sent_px</code> (decoded from the "
                  f"bytes), so the width is never taken on trust. The vision encoder then "
                  f"normalises to {wiclient.MODEL_MAX_WIDTH}&nbsp;px wide whatever is sent."
                  f"</li></ul>")},
    ]
    return {"app_version": APP_VERSION, "title": "About WikiPortraits Eval",
            "sections": sections,
            "questions": q_items,
            "request": {"endpoint": wiclient.LIFTWING, "model": "llm-clef-flash",
                        "state": STATE, "user_agent": wiclient.UA,
                        "body_template": template, "thumb_width": THUMB_WIDTH,
                        "thumb_width_is_ladder_step": THUMB_WIDTH in wiclient.THUMB_LADDER,
                        "thumb_ladder": list(wiclient.THUMB_LADDER),
                        "model_max_width": wiclient.MODEL_MAX_WIDTH,
                        "spacing_s": SPACING, "inflight": INFLIGHT,
                        "schema_hash": SCHEMA_HASH},
            "sources": [MODEL_CARD, WIKITECH_PAGE]}


# ------------------------------------------------------------------- routing
def _read_static(fname):
    full = os.path.join(STATIC, os.path.basename(fname))
    if not os.path.exists(full):
        return None
    ctype = ("text/css" if fname.endswith(".css") else
             "application/javascript" if fname.endswith(".js") else
             "text/html; charset=utf-8" if fname.endswith(".html") else "application/octet-stream")
    with open(full, "rb") as fh:
        return ctype, fh.read()


def route(method: str, path: str, body: bytes = b"", query: str = "") -> tuple:
    """Dispatch one request. `path` is already stripped of its query string by the caller; `query`
    is passed separately so the few routes that care can read it (`report.pdf?view=pose`)."""
    """Return (status, content_type, payload_bytes, extra_headers)."""
    def js(obj, code=200):
        return code, "application/json", json.dumps(obj, ensure_ascii=False).encode(), {}

    try:
        if method == "GET":
            if path in ("/", "/index.html"):
                got = _read_static("index.html")
                return 200, got[0], got[1], {} if got else (500, "text/plain", b"no ui", {})
            if path.startswith("/static/"):
                got = _read_static(path[len("/static/"):])
                return (200, got[0], got[1], {}) if got else js({"error": "not found"}, 404)
            if path == "/api/health":
                return js({"ok": True, "uptime_s": round(time.time() - START, 1)})
            if path == "/api/about":
                return js(about_payload())
            if path == "/api/default-list":
                text = default_list_text()
                return js({"text": text, "source": "default_list.txt",
                           "count": len([l for l in text.splitlines() if l.strip()]),
                           "is_example_list": True})
            if path == "/api/eval":
                # The API twin of the share link, and the reason it is safe to hand out: a GET must be
                # incapable of spending, so cached_only is *forced* here rather than read from the
                # query (a `no_cache` parameter is ignored outright). Resolving the titles is one
                # batched Commons call per wiki; no model call is possible.
                text = parse_qs(query or "").get("list", [""])[0]
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                if not lines:
                    return js({"error": "GET /api/eval?list=<file names, one per line> — reads saved "
                                        "answers only; POST /api/eval to actually score"}, 400)
                if len(lines) > MAX_INPUT_ITEMS:
                    return js({"error": f"at most {MAX_INPUT_ITEMS} files (MAX_INPUT_ITEMS; got "
                                        f"{len(lines)})"}, 413)
                job = new_job("\n".join(lines), cached_only=True)
                if not job["items"]:
                    return js({"error": "nothing to read", "warnings": job["warnings"]}, 400)
                run_job(job)
                return js(_eval_payload(job))
            if path == "/api/config":
                return js({"model": "llm-clef-flash", "endpoint": wiclient.LIFTWING,
                           "thumb_width_requested": THUMB_WIDTH, "spacing_s": SPACING,
                           "inflight": INFLIGHT,
                           "eval_max": EVAL_MAX,
                           "thumbnail": {"requested_w": THUMB_WIDTH,
                                         "is_ladder_step": THUMB_WIDTH in wiclient.THUMB_LADDER,
                                         "ladder": list(wiclient.THUMB_LADDER),
                                         "model_max_width": wiclient.MODEL_MAX_WIDTH,
                                         "served_w": "per photo — read served_w (from the "
                                                     "thumbnail URL) and sent_px (decoded from "
                                                     "the bytes) on each result item"},
                           "schema_hash": SCHEMA_HASH, "questions": QUESTIONS,
                           "budget": BUDGET.status(), "cache_dir": CACHE_DIR,
                           "cache_entries": len([f for f in os.listdir(CACHE_DIR) if f.endswith(".json")])})
            if path.startswith("/api/job/"):
                parts = path.strip("/").split("/")
                job = JOBS.get(parts[2]) if len(parts) > 2 else None
                if not job:
                    return js({"error": "no such job"}, 404)
                if len(parts) == 4 and parts[3].startswith("report."):
                    return _report(job, parts[3].split(".")[1], query)
                return js(dict(job) | {"budget": BUDGET.status(), "latency": latency_summary(job)})
            return js({"error": "not found"}, 404)

        if method == "POST":
            data = json.loads(body.decode() or "{}") if body else {}
            if path == "/api/eval":
                files = data.get("files")
                if isinstance(files, str):
                    files = [files]
                files = [str(f).strip() for f in (files or []) if str(f).strip()]
                if not files:
                    return js({"error": 'POST JSON {"files": ["File:A.jpg", ...]} — up to '
                                        f"{EVAL_MAX} per call (raise the EVAL_MAX env var if you "
                                        "need more)"}, 400)
                if len(files) > EVAL_MAX:
                    return js({"error": f"at most {EVAL_MAX} files per /api/eval call "
                                        f"(got {len(files)})"}, 413)
                if len(files) > MAX_INPUT_ITEMS:
                    return js({"error": f"at most {MAX_INPUT_ITEMS} images per run "
                                        f"(MAX_INPUT_ITEMS; got {len(files)})"}, 413)
                active = [j for j in JOBS.values() if j["status"] in ("queued", "running")]
                if len(active) >= MAX_PARALLEL_JOBS:
                    return js({"error": "another run is already in progress"}, 409)
                job = new_job("\n".join(files), no_cache=data.get("no_cache"))
                if not job["items"]:
                    return js({"error": "nothing to score", "warnings": job["warnings"]}, 400)
                run_job(job)          # synchronous: /api/eval blocks until the whole run finishes
                return js(_eval_payload(job))
            if path == "/api/parse":
                parsed = wiclient.parse_entries(data.get("text", ""))
                if len(parsed["items"]) > PARSE_MAX:
                    return js({"error": f"at most {PARSE_MAX} images per parse request "
                                        f"(got {len(parsed['items'])})"}, 413)
                titles = [{"title": i["title"], "wiki": i.get("wiki")} for i in parsed["items"]]
                meta = wiclient.resolve(titles) if titles else {}
                def _m(e):
                    return meta.get((e.get("wiki") or "commons.wikimedia.org", e["title"])) or {}
                items = [{"raw": e["raw"], "title": e["title"], "wiki": e.get("wiki"),
                          "ok": bool(_m(e).get("ok")),
                          "pageid": _m(e).get("pageid"),
                          "orig_w": _m(e).get("orig_w"),
                          "orig_h": _m(e).get("orig_h"),
                          "mime": _m(e).get("mime"),
                          "license": _m(e).get("license"),
                          "error": _m(e).get("error")}
                         for e in parsed["items"]]
                return js({"items": items, "warnings": parsed["warnings"], "n": len(items)})
            if path == "/api/run":
                active = [j for j in JOBS.values() if j["status"] in ("queued", "running")]
                if len(active) >= MAX_PARALLEL_JOBS:
                    return js({"error": "another run is already in progress"}, 409)
                job = new_job(data.get("items") or data.get("text", ""),
                              no_cache=data.get("no_cache"),
                              cached_only=data.get("cached_only"))
                if not job["items"]:
                    return js({"error": "nothing to score", "warnings": job["warnings"]}, 400)
                if len(job["items"]) > MAX_INPUT_ITEMS:
                    del JOBS[job["id"]]
                    return js({"error": f"at most {MAX_INPUT_ITEMS} images per run "
                                        f"(MAX_INPUT_ITEMS; got {len(job['items'])}) — split the "
                                        "list across runs; the hourly budget is the other guardrail"}, 413)
                threading.Thread(target=run_job, args=(job,), daemon=True).start()
                return js({"job_id": job["id"], "total": job["progress"]["total"]})
            return js({"error": "not found"}, 404)
        return js({"error": "method not allowed"}, 405)
    except Exception:
        traceback.print_exc()
        return 500, "application/json", json.dumps({"error": "server error"}).encode(), {}


def _eval_payload(job) -> dict:
    """The /api/eval response, shared by the POST (which scores) and the GET (which only reads)."""
    return {"job_id": job["id"], "status": job["status"],
            "elapsed_s": job.get("elapsed_s"),
            "cached_only": bool(job.get("cached_only")),
            "probe": job.get("probe"), "spread": job.get("spread"),
            "stats": job.get("stats"), "latency": latency_summary(job),
            "results": [_eval_row(i) for i in job["items"]],
            "reports": {f"report.{f}": f"/api/job/{job['id']}/report.{f}"
                        for f in ("md", "json", "csv", "pdf")}}


def _eval_row(item: dict) -> dict:
    """One compact, API-friendly row for POST /api/eval (the full job payload stays available)."""
    a = (item.get("result") or {}).get("answers") or {}
    pose = a.get("pose") or {}
    title = item.get("title")
    return {"title": title, "pageid": item.get("pageid"), "wiki": item.get("wiki"),
            "status": item.get("status"), "error": item.get("error"),
            "from_cache": item.get("from_cache"),
            "commons_url": ("https://commons.wikimedia.org/wiki/"
                            + title.replace(" ", "_")) if title else None,
            "license": item.get("license"),
            "composite": item.get("composite"), "publish_p": item.get("publish_p"),
            "infobox_ready": (a.get("infobox_ready") or {}).get("noul"),
            "face_visible": (a.get("face_visible") or {}).get("noul"),
            "eyes_to_camera": (a.get("eyes_to_camera") or {}).get("noul"),
            "pose": {"choice": pose.get("choice"),
                     "probability": (pose.get("probabilities") or {}).get(pose.get("choice"))},
            "quality_0_4": (a.get("quality") or {}).get("score"),
            "input_tokens": ((item.get("result") or {}).get("usage") or {}).get("input_tokens"),
            "wall_s": (item.get("result") or {}).get("wall_s")}


def _report(job, fmt, query=""):
    # The PDF's chart follows the view and the axis the reader had on screen; only the PDF takes a
    # parameter, so nothing else about the report changes. Parsed by hand rather than with a form
    # decoder. `axis=candidates` must mean the same thing here as in the page — see
    # reports.CANDIDATE_WINDOW, which the two have to agree on.
    chart_view = "pose" if "view=pose" in (query or "") else "combined"
    chart_axis = "candidates" if "axis=candidates" in (query or "") else "full"
    chart_scale = "top" if "scale=top" in (query or "") else "linear"
    meta = {"state": STATE, "questions": QUESTIONS, "elapsed_s": job.get("elapsed_s"),
            "latency": latency_summary(job), "spread": job.get("spread"),
            "chart_view": chart_view, "chart_axis": chart_axis, "chart_scale": chart_scale,
            "no_cache": bool(job.get("no_cache")),
            "cached_only": bool(job.get("cached_only")),
            "pdf_path": os.path.join(CACHE_DIR, f"report-{job['id']}.pdf")}
    name = f'filename="wikiportraits-{job["id"]}.{fmt}"'
    if fmt == "md":
        return (200, "text/markdown; charset=utf-8", reports.build_md(job["items"], meta).encode(),
                {"Content-Disposition": "attachment; " + name})
    if fmt == "csv":
        return (200, "text/csv; charset=utf-8", reports.build_csv(job["items"]).encode(),
                {"Content-Disposition": "attachment; " + name})
    if fmt == "json":
        return (200, "application/json; charset=utf-8",
                reports.build_json(job["items"], meta).encode(),
                {"Content-Disposition": "attachment; " + name})
    if fmt == "pdf":
        return (200, "application/pdf",
                reports.build_pdf(job["items"], meta, chart_view=chart_view,
                                  chart_axis=chart_axis, chart_scale=chart_scale),
                {"Content-Disposition": "attachment; " + name})
    return 400, "application/json", json.dumps({"error": "unknown format"}).encode(), {}
