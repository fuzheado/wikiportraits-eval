#!/usr/bin/env python3
"""Live end-to-end check: run a small job through the running app, then inspect all four reports.

Asserts that the Markdown report carries the thumbnail and the new numbers, that the PDF has real
embedded images, and renders the PDF pages to PNG so a human (or vision) can check the layout.
"""
import os
import io
import json
import re
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
UA = "WikiPortraitsEval/0.4 (report verification)"
TITLES = [
    "File:Sundance Film Festival 2024 - Porcelain War - Andrey Stefanov-104A2045.jpg",
    "File:Irina Tjelle - true crime festival 2025-1.jpg",
    "File:Wikimedia Movement logo Arabic vertical in white.svg",
]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research")   # repo root; this file is in tools/
FAIL = []


def ok(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}", flush=True)
    if not cond:
        FAIL.append(label)


def call(path, data=None, raw=False, timeout=600):
    req = urllib.request.Request(URL + path, headers={"User-Agent": UA},
                                 data=json.dumps(data).encode() if data else None,
                                 method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return (body, dict(r.headers)) if raw else (json.loads(body), dict(r.headers))


print("starting a 3-photo job (fresh schema hash — expect fresh model calls)")
started, _ = call("/api/run", {"text": "\n".join(TITLES)})
if "job_id" not in started:
    print("  /api/run refused:", started)
    sys.exit(1)
jid = started["job_id"]
print("  job", jid, "| total", started.get("total"))
t0 = time.time()
while time.time() - t0 < 1500:
    job, _ = call(f"/api/job/{jid}")
    done = sum(1 for i in job["items"] if i["status"] in ("done", "error", "notfound"))
    print(f"  [{time.time() - t0:5.0f}s] {done}/{len(job['items'])} settled | "
          f"{job['items'][-1].get('status')}", flush=True)
    if done == len(job["items"]):
        break
    time.sleep(10)

print(f"\nstatus={job['status']} elapsed={job.get('elapsed_s')}s")
for it in job["items"]:
    a = (it.get("result") or {}).get("answers") or {}
    print(f"  {it['status']:9s} composite={it.get('composite')} publish={it.get('publish_p')} "
          f"infobox={(a.get('infobox_ready') or {}).get('noul')} :: {(it.get('title') or it.get('raw'))[:50]}")
print("  spread:", json.dumps(job.get("spread", {}).get("composite", {})))
ok(job.get("spread", {}).get("composite", {}).get("n", 0) >= 1, "job payload carries candidate spread")
ok(any(i.get("composite") is not None for i in job["items"]), "items carry the composite")
ok(any(i.get("publish_p") is not None for i in job["items"]), "items carry P(publish)")

print("\nreports:")
files = {}
for fmt in ("md", "json", "csv", "pdf"):
    data, hdr = call(f"/api/job/{jid}/report.{fmt}", raw=True)
    files[fmt] = data
    print(f"  {fmt}: {len(data):>8d} bytes  {hdr.get('Content-Type')}")
md = files["md"].decode("utf-8", "replace")
csv_txt = files["csv"].decode("utf-8", "replace")
js = json.loads(files["json"])
ok("## Ranked by composite" in md, "MD ranks by composite")
ok("Candidate spread" in md, "MD has a candidate-spread block")
ok(md.count("![") >= 2, f"MD embeds thumbnails as images ({md.count('![')} found)")
ok("min(face, eyes) × pose weight" in md, "MD states the composite formula")
ok("P(publish)" in md and "composite" in md.split("\n")[0:40][-1] or "composite" in md, "MD carries the new columns")
ok("composite" in csv_txt.splitlines()[0] and "P_publish" in csv_txt.splitlines()[0],
   "CSV header has the new columns")
ok(js.get("spread", {}).get("composite", {}).get("n") is not None, "JSON carries the spread")
ok(files["pdf"][:5] == b"%PDF-", "PDF header")
ok(files["pdf"].count(b"/Subtype /Image") >= 2, f"PDF embeds images "
   f"({files['pdf'].count(b'/Subtype /Image')} objects)")

open(f"{OUT}/live_report.md", "wb").write(files["md"])
open(f"{OUT}/live_report.pdf", "wb").write(files["pdf"])
open(f"{OUT}/live_report.csv", "wb").write(files["csv"])
open(f"{OUT}/live_report.json", "wb").write(files["json"])

try:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(f"{OUT}/live_report.pdf")
    print(f"\nrendering {len(pdf)} page(s)")
    for i in range(len(pdf)):
        page = pdf[i]
        img = page.render(scale=1.5).to_pil()
        img.save(f"{OUT}/live_report_p{i + 1}.png")
        print(f"  page {i + 1}: {img.size} -> live_report_p{i + 1}.png")
except ImportError:
    print("  (pypdfium2 not available in this interpreter — rendering skipped)")

print(f"\nFAILURES: {len(FAIL)}")
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
