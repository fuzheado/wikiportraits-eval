#!/usr/bin/env python3
"""Read-only: per-call latency timeline from the cached results (no network, no writes).

Each cache file is one scored image and holds result.wall_s (the server-side call time
as reported by the app) plus usage. The file mtime dates the call.
"""
import glob
import json
import os
import statistics
import time

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")   # repo root; this file is in tools/
rows = []
for f in glob.glob(os.path.join(CACHE, "*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    r = (d.get("result") or {})
    w = r.get("wall_s")
    if w is None:
        continue
    rows.append({"mtime": os.path.getmtime(f), "wall_s": w,
                 "tokens": (r.get("usage") or {}).get("input_tokens"),
                 "px": d.get("sent_px"), "title": d.get("title", "")[:44]})
rows.sort(key=lambda r: r["mtime"])
print(f"{len(rows)} cached calls\n")
print(f"{'local time':17s} {'wall_s':>7s} {'tokens':>7s} {'sent px':>10s}  title")
for r in rows:
    print(f"{time.strftime('%H:%M:%S', time.localtime(r['mtime'])):17s} {r['wall_s']:>7.2f} "
          f"{str(r['tokens']):>7s} {str(r['px']):>10s}  {r['title']}")

buckets = {}
for r in rows:
    buckets.setdefault(time.strftime("%Y-%m-%d %H:00", time.localtime(r["mtime"])), []).append(r["wall_s"])
print("\nper hour:")
for h in sorted(buckets):
    v = buckets[h]
    print(f"  {h}  n={len(v):>3}  median {statistics.median(v):6.2f}s  "
          f"min {min(v):6.2f}s  max {max(v):6.2f}s")

if rows:
    recent = [r["wall_s"] for r in rows if time.time() - r["mtime"] < 3600]
    earlier = [r["wall_s"] for r in rows if time.time() - r["mtime"] >= 3600]
    print(f"\nlast hour (n={len(recent)}): median {statistics.median(recent) if recent else 'n/a'}")
    print(f"earlier   (n={len(earlier)}): median {statistics.median(earlier) if earlier else 'n/a'}")
    if recent and earlier:
        print(f"slowdown factor: {statistics.median(recent) / statistics.median(earlier):.1f}x")
