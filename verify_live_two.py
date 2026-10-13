#!/usr/bin/env python3
"""Live check after the import fix: one cached photo (free) and one fresh photo (1 API call).

Verifies the on-the-wire job payload (composite, P(publish), spread, thumb_file), the Markdown
report's inline image, the PDF's embedded image, and renders the served PDF back to PNG.
"""
import os
import json
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
HDR = {"User-Agent": "WikiPortraitsEval/0.4 (verification)", "Content-Type": "application/json"}
CACHED = "File:Sundance Film Festival 2024 - Porcelain War - Andrey Stefanov-104A2045.jpg"
FRESH = "File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research")
FAIL = []


def ok(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}", flush=True)
    if not cond:
        FAIL.append(label)


def call(path, data=None, raw=False, timeout=900):
    req = urllib.request.Request(URL + path, headers=HDR)
    if data is not None:
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return (body, dict(r.headers)) if raw else json.loads(body)


def run(title, label):
    print(f"\n=== {label}: {title[:60]}")
    started = call("/api/run", {"text": title})
    jid = started["job_id"]
    t0, seen = time.time(), None
    while time.time() - t0 < 1200:
        job = call(f"/api/job/{jid}")
        done = sum(1 for i in job["items"] if i["status"] in ("done", "error", "notfound"))
        if done and seen is None:          # first settle: the payload must already be readable
            seen = done
            i0 = next(i for i in job["items"] if i["status"] == "done")
            ok(i0.get("composite") is not None or i0.get("status") != "done",
               "mid-run: a settled photo already carries the composite on the wire")
            ok((job.get("spread") or {}).get("composite", {}).get("n", 0) >= 1,
               "mid-run: the spread is already populated on the wire")
        if done == len(job["items"]):
            break
        time.sleep(5)
    it = job["items"][0]
    a = (it.get("result") or {}).get("answers") or {}
    print(f"  status={it['status']} cache={it.get('from_cache')} wall_s={(it.get('result') or {}).get('wall_s')}")
    print(f"  composite={it.get('composite')} publish_p={it.get('publish_p')} "
          f"infobox={(a.get('infobox_ready') or {}).get('noul')} face={(a.get('face_visible') or {}).get('noul')} "
          f"eyes={(a.get('eyes_to_camera') or {}).get('noul')} pose={(a.get('pose') or {}).get('choice')}")
    print(f"  thumb_file={it.get('thumb_file')}")
    print(f"  spread={json.dumps((job.get('spread') or {}).get('composite', {}))}")
    ok(it["status"] == "done", "photo scored")
    ok(it.get("composite") is not None, "wire payload: composite")
    ok(it.get("publish_p") is not None, "wire payload: P(publish)")
    ok(it.get("thumb_file"), "wire payload: thumbnail path saved")
    ok((job.get("spread") or {}).get("composite", {}).get("n") == 1, "wire payload: spread")
    ok((job.get("stats") or {}).get("composite_mean") is not None, "wire payload: stats grid")

    files = {}
    for fmt in ("md", "json", "pdf", "csv"):
        files[fmt], _ = call(f"/api/job/{jid}/report.{fmt}", raw=True)
    md = files["md"].decode("utf-8", "replace")
    tag = "cached" if label.startswith("cached") else "fresh"
    open(f"{OUT}/v2_{tag}.md", "wb").write(files["md"])
    open(f"{OUT}/v2_{tag}.pdf", "wb").write(files["pdf"])
    ok("![[" not in md and md.count("![") >= 1, f"MD embeds the thumbnail as an image ({md.count('![')})")
    ok("Candidate spread" in md, "MD has the candidate-spread block")
    ok("min(face, eyes) × pose weight" in md, "MD states the composite formula")
    ok(files["pdf"].count(b"/Subtype /Image") >= 1,
       f"PDF embeds an image ({files['pdf'].count(b'/Subtype /Image')} objects, {len(files['pdf'])} bytes)")
    ok(b"/FlateDecode" in files["pdf"] or b"/DCTDecode" in files["pdf"], "PDF image uses a real filter")

    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(f"{OUT}/v2_{tag}.pdf")
        for i in range(len(pdf)):
            pdf[i].render(scale=1.5).to_pil().save(f"{OUT}/v2_{tag}_p{i + 1}.png")
        print(f"  rendered {len(pdf)} page(s) -> v2_{tag}_p*.png")
    except ImportError:
        print("  (pypdfium2 unavailable — rendering skipped)")
    return job


run(CACHED, "cached photo (no API call)")
run(FRESH, "fresh photo (1 API call)")

print(f"\nFAILURES: {len(FAIL)}")
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
