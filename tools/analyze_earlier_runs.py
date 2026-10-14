#!/usr/bin/env python3
"""Read-only: earlier-today call latencies from the clef-liftwing research artifacts."""
import glob
import json
import os
import statistics
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (this file is in tools/)
ROOTS = [os.path.join(ROOT, "cache"), os.path.join(ROOT, "research")]
rows = []
for root in ROOTS:
    for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True) + \
             glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        try:
            raw = open(f, encoding="utf-8").read().strip()
        except Exception:
            continue
        recs = []
        if f.endswith(".jsonl"):
            for line in raw.splitlines():
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
        else:
            try:
                d = json.loads(raw)
                recs = d if isinstance(d, list) else [d]
            except Exception:
                recs = []
        for r in recs:
            if not isinstance(r, dict):
                continue
            w = r.get("wall_s") or (r.get("result") or {}).get("wall_s")
            if w is None:
                continue
            ts = r.get("ts") or r.get("started") or r.get("requested_at")
            rows.append({"file": os.path.basename(f), "wall_s": w, "ts": ts,
                         "mtime": os.path.getmtime(f),
                         "title": str(r.get("title") or r.get("file") or "")[:40]})
rows.sort(key=lambda r: (r["ts"] or r["mtime"]))
print(f"{len(rows)} records with wall_s\n")
buckets = {}
for r in rows:
    when = r["ts"] or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["mtime"]))
    buckets.setdefault(str(when)[:13], []).append(r["wall_s"])
print(f"{'hour':20s} {'n':>4s} {'median':>8s} {'min':>8s} {'max':>8s}")
for h in sorted(buckets):
    v = buckets[h]
    print(f"{h:20s} {len(v):>4d} {statistics.median(v):>8.2f} {min(v):>8.2f} {max(v):>8.2f}")
print("\noverall median:", round(statistics.median([r['wall_s'] for r in rows]), 3),
      "| overall max:", round(max(r['wall_s'] for r in rows), 2))
