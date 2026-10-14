#!/usr/bin/env python3
"""UI contract check: no browser here (no Chromium on this host), so verify the wiring instead.

1. every element id the JS looks up exists in the HTML
2. every fetch() path the JS calls exists as a route, and the job payload carries every
   field the renderer reads
3. the public URL serves the page, the API and all four report formats
"""
import json, re, sys, time, urllib.parse, urllib.request

import wiclient as _wc      # the same constants the server runs, so fields can be diffed
import core as _pg          # the live prompt/config constants (and the default-list cap)

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
UA = "WikiPortraitsEval/0.4 (contract check)"


def get(path, raw=False):
    req = urllib.request.Request(URL + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
        return (data, dict(r.headers)) if raw else (json.loads(data), dict(r.headers))


def post(path, obj):
    req = urllib.request.Request(URL + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


html = get("/", raw=True)[0].decode()
js = html.split("<script>")[-1]

ids_in_html = set(re.findall(r'id="([^"]+)"', html))
ids_in_js = set(re.findall(r'\$\("([^"]+)"\)', js)) | set(re.findall(r'getElementById\("([^"]+)"\)', js))
dynamic = {i for i in ids_in_js if i.endswith(("-md", "-json", "-csv", "-pdf"))}
missing = {i for i in ids_in_js - dynamic if i not in ids_in_html}
print(f"HTML ids: {len(ids_in_html)} · ids used by JS: {len(ids_in_js)} · missing: {sorted(missing) or 'none'}")

paths = sorted(set(re.findall(r'fetch\(`?"?([^"`$\s]*)', js)) | set(re.findall(r'fetch\(`([^`]+)`', js)))
paths = sorted({p.replace("${jobId}", "JOB").replace("${f}", "pdf") for p in paths if p.startswith("/")})
print("fetch paths in JS:", paths)

# 2. run a real job (the files are cached from earlier runs, so this normally spends no budget).
# Wait for the job to *finish*: with the endpoint at ~57 s/call, a 60-second poll returns a mid-run
# payload and the assertions below would be reading fields that legitimately do not exist yet
# (the pipeline summary is written when the scoring phase ends). Mid-run readability is asserted
# separately, in test_pipeline.py.
jobs = post("/api/run", {"text": "File:Baba Sissoko-63495.jpg\nFile:Lois Garrel.jpg\nHila Meir at Deauville American Film festival.jpg"})
jid = jobs["job_id"]
deadline = time.time() + 900
while time.time() < deadline:
    job, _h = get(f"/api/job/{jid}")
    if job["status"] in ("done", "error"):
        break
    time.sleep(5)
else:
    print(f"WARNING: job {jid} still {job.get('status')} after 900s — assertions below read a "
          f"mid-run payload")
print(f"job {jid}: status={job['status']} elapsed={job.get('elapsed_s')}s")

required = {
    "progress.done": job.get("progress", {}).get("done") is not None,
    "progress.total": job.get("progress", {}).get("total") is not None,
    "status": "status" in job,
    "stats.n_scored": (job.get("stats") or {}).get("n_scored") is not None,
    "stats.p_infobox_mean": "p_infobox_mean" in (job.get("stats") or {}),
    "stats.quality_mean": "quality_mean" in (job.get("stats") or {}),
    "stats.n_above_half": "n_above_half" in (job.get("stats") or {}),
    "stats.tokens_total": "tokens_total" in (job.get("stats") or {}),
    "stats.wall_median_s": "wall_median_s" in (job.get("stats") or {}),
    "budget.used_last_hour": job.get("budget", {}).get("used_last_hour") is not None,
    "budget.limit_per_hour": job.get("budget", {}).get("limit_per_hour") is not None,
    "items[].title": all("title" in i for i in job["items"]),
    "items[].status": all("status" in i for i in job["items"]),
    "items[].sent_px": any(i.get("sent_px") for i in job["items"]),
    # each row shows the file's own thumbnail, built once and patched in place
    "items[].thumburl": any(i.get("thumburl") for i in job["items"]),
    "ui renders the thumbnail": 'img.thumb' in js and '"src"' in js,
    "thumb CSS rule": ".item .thumb{" in html,
    "list is NOT re-rendered wholesale": '$("items").innerHTML' not in js,
    # deep links: the URL layer must never grow a second splitter (invariant 1) and must stay inert
    # unless a ?list= was supplied; and a link may press Validate files but never Score them
    "deep links add no comma splitting": 'split(",")' not in js,
    "deep links are gated on ?list=": 'qs.get("list")' in js,
    "deep links press Validate files, never Score them":
        '$("check").click()' in js and '$("run").click()' not in js,
    "deep links have a hard length cap": 'URL_LIST_HARD_MAX' in js,
    # a share link may replay a run, but only from cache — it must be incapable of spending
    "the share link replays from cache only": 'cached_only: replay' in js,
    "the share link carries the chart view": 'q.set("view", "pose")' in js,
    # the exported PDF chart must follow the on-screen view, so the download URL carries it
    "the PDF export carries the chart view": 'view=pose' in js and 'chartMode' in js,
    "rows are created once (keyed map)": "const rows = new Map()" in js and "rowFor(" in js,
    "statuses: downloading + ready": '"downloading thumbnail"' in js and "ready · queued" in js,
    "per-row scoring clock": 'class="clock"' in js and "data-start" in js and "setInterval(" in js,
    "latency panel": 'id="latency"' in html and "endpoint ${L.verdict}" in js,
    "pipeline summary (when downloads happened)": (isinstance(job.get("pipeline"), dict)
                                                   and "serial_estimate_s" in job["pipeline"])
    if any(i.get("status") == "done" and not i.get("from_cache") for i in job["items"]) else True,
    "pipeline shown in UI": "pl.serial_estimate_s" in js,
    "latency summary in payload": isinstance(job.get("latency"), dict) and job["latency"].get("verdict")
    in ("normal", "elevated", "service-side"),
    "latency baseline recorded": (job.get("latency") or {}).get("baseline_s") is not None,
    # the two checks below only apply when this job actually made fresh calls; a fully cached
    # run has no download phase and its answers predate the timing fields (live_pipeline_check.py
    # exercises the fresh path with fresh files)
    "shots carry ttfb (fresh calls only)": (all((i.get("result") or {}).get("ttfb_s") is not None
                                                for i in job["items"]
                                                if i["status"] == "done" and not i.get("from_cache"))
                                            if any(i.get("status") == "done" and not i.get("from_cache")
                                                   for i in job["items"]) else True),
    "result.answers.infobox_ready.noul": any(
        ((i.get("result") or {}).get("answers") or {}).get("infobox_ready", {}).get("noul") is not None
        for i in job["items"]),
    "result.answers.pose.probabilities": any(
        bool(((i.get("result") or {}).get("answers") or {}).get("pose", {}).get("probabilities"))
        for i in job["items"]),
    "result.answers.quality.probabilities": any(
        bool(((i.get("result") or {}).get("answers") or {}).get("quality", {}).get("probabilities"))
        for i in job["items"]),
    "result.usage.input_tokens": any(
        ((i.get("result") or {}).get("usage") or {}).get("input_tokens") for i in job["items"]),
    # --- the new numbers: composite, P(publish), rank order and candidate spread ---
    "items[].composite": any(i.get("composite") is not None for i in job["items"]),
    "items[].publish_p": any(i.get("publish_p") is not None for i in job["items"]),
    "result.answers.publish_ready.probabilities": any(
        bool(((i.get("result") or {}).get("answers") or {}).get("publish_ready", {}).get("probabilities"))
        for i in job["items"]),
    "job.spread covers the three headline numbers": all(
        k in (job.get("spread") or {}) for k in ("composite", "publish_ready", "infobox_ready")),
    "spread reports candidates": (job.get("spread") or {}).get("composite", {}).get("n_candidates") is not None,
    # a single candidate has no range and no neighbour gap — the metric reports null, not 0
    "spread range is numeric or honestly null": (
        (job.get("spread") or {}).get("composite", {}).get("n_candidates") or 0) < 2
    or (job.get("spread") or {}).get("composite", {}).get("candidate_range") is not None,
    "spread carries the floor it used": (job.get("spread") or {}).get("composite", {}).get("floor")
    == _wc.CANDIDATE_FLOOR,
    "spread reports the dead zone": (job.get("spread") or {}).get("composite", {}).get("dead_zone") is not None,
    "ui shows the composite": 'composite' in js and "rankOf(" in js,
    "ui shows P(publish)": "pubProbs(" in js,
    "ui ranks the list": "rankOf(b) - rankOf(a)" in js,
    "ui renders the rank badge": 'class="rank"' in js,
    "ui spread panel": 'id="spread"' in html and "renderSpread(" in js,
    "ui stats carry composite + publish means": "mean composite" in js and "mean P(publish)" in js,
    "log[]": bool(job.get("log")),
    "warnings (unresolvable input)": isinstance(job.get("warnings"), list),
}
bad = [k for k, v in required.items() if not v]
print(f"\njob {jid}: status={job['status']} items={len(job['items'])} "
      f"scored={(job.get('stats') or {}).get('n_scored')}")
for k, v in required.items():
    print(f"  {'ok ' if v else 'MISSING'} {k}")
print("  unresolved handled:", [i["title"] for i in job["items"] if i["status"] != "done"])

# 3. the About panel: the payload must match the code that scores, not a hand-written blurb
print("\nabout:")
about, _h = get("/api/about")
req = about.get("request") or {}
about_html = " ".join(
    " ".join(f"{s.get('title') or ''} {s.get('html') or ''}"
             for s in about.get("sections") or []).split())
about_checks = {
    "about.state matches the live prompt": req.get("state") == _wc.STATE,
    "about.endpoint matches": req.get("endpoint") == _wc.LIFTWING,
    "about.model matches": req.get("model") == "llm-clef-flash",
    "about.thumb_width matches": req.get("thumb_width") == _wc.THUMB_WIDTH,
    "about.body_template carries every question": (req.get("body_template") or {}).get("questions")
    == _wc.QUESTIONS,
    "every question's instructions are quoted verbatim": all(
        (q.get("instructions") or "") == (_wc.QUESTIONS[q["id"]].get("instructions") or "")
        for q in about.get("questions") or []),
    "every question's criteria are quoted verbatim": all(
        q.get("criteria") == _wc.QUESTIONS[q["id"]].get("criteria")
        for q in about.get("questions") or []),
    "quality legend rendered": "unusable (blurred/miscropped)" in about_html
    and "excellent" in about_html,
    "noul semantics stated": "probability that the answer is true" in about_html,
    "composite explained as ours, not the model's": "not a model probability" in about_html
    and "min(face_visible, eyes_to_camera)" in about_html,
    "publish_ready explained": "publish_ready" in about_html and "P(publish)" in about_html,
    "candidate spread explained": "candidate spread" in about_html.lower()
    and "dead zone" in about_html.lower(),
    "composite weights stated": "head-and-shoulders 1.0" in about_html,
    "limitations stated": "does not do" in about_html.lower(),
    "app_version present": bool(about.get("app_version")),
}
bad_about = [k for k, v in about_checks.items() if not v]
for k, v in about_checks.items():
    print(f"  {'ok ' if v else 'MISSING'} {k}")
ui_about = {
    "About button in the header": 'id="about-open"' in html,
    "About modal scaffold": all(f'id="{i}"' in html for i in
                                ("about-overlay", "about-close", "about-body", "about-toc")),
    "About fetches the live payload": 'fetch("/api/about")' in js,
    "About closes on Escape": 'Escape' in js,
}
for k, v in ui_about.items():
    print(f"  {'ok ' if v else 'MISSING'} {k}")
bad_about += [k for k, v in ui_about.items() if not v]

# 4. reports over the public URL
print("\nreports:")
for fmt in ("md", "json", "csv", "pdf"):
    data, hdr = get(f"/api/job/{jid}/report.{fmt}", raw=True)
    ok = (fmt == "pdf" and data[:5] == b"%PDF-") or (fmt != "pdf" and len(data) > 200)
    print(f"  {fmt}: {len(data):>7} bytes  {hdr.get('Content-Type'):38s} valid={ok}")
    if fmt == "md":
        first = data.decode().splitlines()[0]
        print(f"      md first line: {first}")

# 5. the starter list (default_list.txt) is served, and parseable as pasted
print("\ndefault list:")
dl, _h = get("/api/default-list")
dl_lines = [l for l in (dl.get("text") or "").splitlines() if l.strip()]
bad_list = []
if not dl_lines:
    bad_list.append("default list is empty")
if dl.get("count") != len(dl_lines):
    bad_list.append(f"count {dl.get('count')} != {len(dl_lines)} lines served")
if not all(l.strip().startswith("File:") for l in dl_lines):
    bad_list.append("every line must be a File: title")
if any("#" in l for l in dl_lines):
    bad_list.append("a '#' in a title is silently truncated by the parser")
if len(dl_lines) > _pg.MAX_INPUT_ITEMS:
    bad_list.append(f"default list exceeds MAX_INPUT_ITEMS ({_pg.MAX_INPUT_ITEMS})")
print(f"  {len(dl_lines)} entries from {dl.get('source')} — "
      f"first: {dl_lines[0] if dl_lines else '(none)'}")
for b in bad_list:
    print(f"  MISSING {b}")

# 6. the cache-only GET: the API twin of a share link — it must read, never spend
print("\ncache-only GET /api/eval:")
ev, _h = get("/api/eval?list=" + urllib.parse.quote("File:Baba Sissoko-63495.jpg"))
bad_get = []
if not isinstance(ev.get("results"), list) or not ev["results"]:
    bad_get.append("no results[] in the response")
if not ev.get("cached_only"):
    bad_get.append("the payload does not claim cached_only")
row = (ev.get("results") or [{}])[0]
if row.get("status") not in ("done", "uncached"):
    bad_get.append(f"unexpected status {row.get('status')!r}")
if row.get("status") == "done" and not row.get("from_cache"):
    bad_get.append("a scored photo from a cache-only GET must be from_cache")
print(f"  1 file -> status={row.get('status')}, from_cache={row.get('from_cache')}")
for b in bad_get:
    print(f"  MISSING {b}")

all_bad = bad + bad_about + bad_list + bad_get
print("\nRESULT:", "PASS" if not missing and not all_bad
      else f"FAIL missing={sorted(missing)} bad={all_bad}")
