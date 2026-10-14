#!/usr/bin/env python3
"""Summarise the wording experiment JSONL: per-variant spread, per-image table, and how well
each variant agrees (Spearman) with the current wording and with the cached baseline."""
import os
import json
from collections import defaultdict

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "wording_experiment.jsonl")   # repo root; this file is in tools/


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    n = len(xs)
    if n < 3:
        return float("nan")
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


rows = []
with open(PATH) as fh:
    for line in fh:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass

if not rows:
    raise SystemExit("no results yet")

order, by_variant, by_image = [], defaultdict(list), defaultdict(dict)
for r in rows:
    if r["variant"] not in order:
        order.append(r["variant"])
    if r["value"] is not None:
        by_variant[r["variant"]].append(r["value"])
        by_image[r["title"]][r["variant"]] = r["value"]

print(f"{len(rows)} calls, {len(by_image)} images, {len(order)} variants")
codes = defaultdict(int)
for r in rows:
    codes[r["http"]] += 1
walls = sorted(r["wall_s"] for r in rows)
print(f"http codes: {dict(codes)}   median wall {walls[len(walls) // 2]:.1f}s\n")

print(f"{'variant':34s} {'n':>2s} {'mean':>6s} {'sd':>6s} {'min':>6s} {'max':>6s} "
      f"{'span':>6s} {'in[.35,.65]':>12s}")
for v in order:
    vals = by_variant[v]
    if not vals:
        print(f"{v:34s}  no data")
        continue
    n = len(vals)
    m = sum(vals) / n
    sd = (sum((x - m) ** 2 for x in vals) / n) ** 0.5
    band = sum(1 for x in vals if 0.35 <= x <= 0.65)
    print(f"{v:34s} {n:2d} {m:6.3f} {sd:6.3f} {min(vals):6.3f} {max(vals):6.3f} "
          f"{max(vals) - min(vals):6.3f} {f'{band}/{n}':>12s}")

print("\nper image:")
hdr = f"  {'image':44s} {'cached':>6s} " + " ".join(f"V{i + 1:>6d}" for i in range(len(order)))
print(hdr)
for title, got in sorted(by_image.items(), key=lambda kv: -max(kv[1].values())):
    cached = next((r["current_infobox_ready"] for r in rows if r["title"] == title), None)
    cells = " ".join("   n/a" if v not in got else f"{got[v]:6.3f}" for v in order)
    print(f"  {title[:44]:44s} {cached:6.3f} {cells}")

print("\nagreement with the cached baseline (Spearman, only informative with >=3 images):")
base = [(r["current_infobox_ready"], r["value"]) for r in rows
        if r["value"] is not None and r["variant"] == order[0]]
for v in order[1:]:
    pairs = [(r["current_infobox_ready"], r["value"]) for r in rows
             if r["value"] is not None and r["variant"] == v]
    if len(pairs) >= 3:
        print(f"  {v:34s} rho={spearman([p[0] for p in pairs], [p[1] for p in pairs]):+.3f}  "
              f"n={len(pairs)}")
