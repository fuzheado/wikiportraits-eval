#!/usr/bin/env python3
"""Step 1: is the endpoint fast right now, and does it accept `criteria` on a noul question?
Two text-only calls, no image. Also prints the census of cached infobox_ready values."""
import glob
import json
import os
import time
import urllib.error
import urllib.request

import wiclient


def post(body, timeout=180):
    req = urllib.request.Request(wiclient.LIFTWING, data=json.dumps(body).encode(), method="POST")
    req.add_header("User-Agent", wiclient.UA)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, hdrs, payload = r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:  # noqa: PERF203
        code, hdrs, payload = e.code, dict(e.headers), e.read().decode()
    return code, hdrs, payload, round(time.time() - t0, 2)


STATE = ("A volunteer photographer's portrait of a person at a public event, uploaded to a free "
         "media repository that anyone may reuse.")

print("=== call 1: plain noul (baseline request shape) ===")
code, hdrs, payload, wall = post({"model": "llm-clef-flash", "state": STATE,
    "images": [], "questions": {"q": {"type": "noul",
        "instructions": "Is this a publishable professional portrait of this person?"}}})
print(f"  HTTP {code}  wall {wall}s  envoy_ms={hdrs.get('x-envoy-upstream-service-time')}")
print("  body:", payload[:200])

print("\n=== call 2: same question + criteria for true/false ===")
code2, hdrs2, payload2, wall2 = post({"model": "llm-clef-flash", "state": STATE,
    "images": [], "questions": {"q": {"type": "noul",
        "instructions": "Is this a publishable professional portrait of this person?",
        "criteria": {
            "true": "A picture editor would run this as the person's portrait: the subject is "
                    "sharply rendered, well lit, and unmistakably the point of the frame.",
            "false": "An editor would pass this over and pick a different frame: the subject is "
                     "blurred, badly lit, obscured, cropped badly, or not clearly the subject.",
        }}}})
print(f"  HTTP {code2}  wall {wall2}s  envoy_ms={hdrs2.get('x-envoy-upstream-service-time')}")
print("  body:", payload2[:300])

print("\n=== cached infobox_ready census ===")
rows = []
for f in sorted(glob.glob("cache/*.json")):
    try:
        d = json.load(open(f))
    except Exception:  # noqa: BLE001
        continue
    a = (d.get("result") or {}).get("answers") or {}
    ir = (a.get("infobox_ready") or {}).get("noul")
    if ir is None:
        continue
    rows.append((ir, d.get("wiki", "commonswiki"), d.get("title") or os.path.basename(f),
                 (a.get("quality") or {}).get("score"), d.get("thumb_served_px")))
rows.sort()
for ir, wiki, title, q, px in rows:
    print(f"  {ir:.4f}  q={q if q is None else round(q, 2)}  {title}")
if rows:
    vals = [r[0] for r in rows]
    n, mean = len(vals), sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    band = sum(1 for v in vals if 0.35 <= v <= 0.65)
    print(f"\n  n={n}  mean {mean:.3f}  sd {sd:.3f}  min {vals[0]:.3f}  max {vals[-1]:.3f}"
          f"  in [0.35,0.65]: {band}/{n} ({band / n:.0%})")
