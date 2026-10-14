#!/usr/bin/env python3
"""Free: compare the spread of every question we have answers for, plus candidate headline
composites built only from the 'visible fact' questions. No API calls."""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (this file is in tools/)


def stats(name, vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        print(f"  {name:26s} no data")
        return
    n = len(vals)
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / n) ** 0.5
    band = sum(1 for v in vals if 0.35 <= v <= 0.65)
    print(f"  {name:26s} n={n:3d}  mean {m:.3f}  sd {sd:.3f}  min {min(vals):.3f} "
          f"max {max(vals):.3f}  span {max(vals) - min(vals):.3f}  in[.35,.65] {band}/{n}")


rows = []
for f in glob.glob(os.path.join(ROOT, "cache", "*.json")):
    try:
        d = json.load(open(f))
    except Exception:  # noqa: BLE001
        continue
    a = (d.get("result") or {}).get("answers") or {}
    if not a:
        continue
    face = (a.get("face_visible") or {}).get("noul")
    eyes = (a.get("eyes_to_camera") or {}).get("noul")
    ib = (a.get("infobox_ready") or {}).get("noul")
    q = (a.get("quality") or {}).get("score")
    pose = (a.get("pose") or {})
    probs = pose.get("probabilities") or {}
    top_pose = pose.get("choice")
    p_top = probs.get(top_pose) if top_pose else None
    rows.append({"title": d.get("title"), "face": face, "eyes": eyes, "ib": ib, "q": q,
                 "pose": top_pose, "p_pose": p_top})

print(f"cached photos with a full answer set: {len(rows)}\n")
print("individual questions:")
stats("infobox_ready (noul)", [r["ib"] for r in rows])
stats("quality/4 (score)", [None if r["q"] is None else r["q"] / 4 for r in rows])
stats("face_visible (noul)", [r["face"] for r in rows])
stats("eyes_to_camera (noul)", [r["eyes"] for r in rows])
stats("pose confidence (choice)", [r["p_pose"] for r in rows])

print("\ncomposites of the visible-fact questions only:")
stats("face * eyes", [None if r["face"] is None or r["eyes"] is None else r["face"] * r["eyes"]
                      for r in rows])
stats("min(face, eyes)", [None if r["face"] is None or r["eyes"] is None
                          else min(r["face"], r["eyes"]) for r in rows])
POSE_W = {"head_shoulders": 1.0, "three_quarter": 0.9, "full_body": 0.6, "candid": 0.5,
          "group": 0.25}
stats("face*eyes*pose_weight",
      [None if r["face"] is None or r["eyes"] is None
       else r["face"] * r["eyes"] * POSE_W.get(r["pose"], 0.5) for r in rows])
stats("sqrt(face*eyes)*pose_w",
      [None if r["face"] is None or r["eyes"] is None
       else (r["face"] * r["eyes"]) ** 0.5 * POSE_W.get(r["pose"], 0.5) for r in rows])

print("\npose mix:", {p: sum(1 for r in rows if r["pose"] == p) for p in sorted({r["pose"] for r in rows})})

# does the editorial question agree with the visible facts?
pairs = [(r["ib"], r["face"] * r["eyes"]) for r in rows if r["ib"] is not None and r["face"] is not None]


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


print(f"\nspearman(infobox_ready, face*eyes) = {spearman([p[0] for p in pairs], [p[1] for p in pairs]):.3f}"
      f"   n={len(pairs)}")
