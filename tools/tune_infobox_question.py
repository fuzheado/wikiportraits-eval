#!/usr/bin/env python3
"""Wording experiment: 4 images x 5 variants of the 'infobox ready' question.

Runs in the background against the live endpoint (each call currently takes ~1 min).
Writes one JSON line per (image, variant) to wording_experiment.jsonl, then a summary.

Only the infobox_ready question is asked (single-question records), so the comparison is
isolated to the wording; the app's full 5-question schema is re-validated separately.
"""
import glob
import json
import os
import sys
import time
from typing import Optional

# this file lives in tools/; the app's modules and data files are at the repo root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wiclient

OUT = os.path.join(ROOT, "research", "wording_experiment.jsonl")
STATE = wiclient.STATE
LIMIT_S = 1500          # hard stop: 25 minutes

CURRENT = wiclient.QUESTIONS["infobox_ready"]

VARIANTS = {
    # V1 — today's wording, kept verbatim as the baseline
    "V1 current (Wikipedia infobox)": CURRENT,
    # V2 — same intent, plain language, no Wikipedia nouns, explicit visual criteria
    "V2 plain + criteria": {"type": "noul",
        "instructions": ("Would a picture editor choose this photograph to represent this person "
                         "in an encyclopedia article?"),
        "criteria": {
            "true": "The person is clearly the subject and is sharply rendered and well lit; an "
                    "editor would run this photograph.",
            "false": "The subject is blurred, badly lit, obscured, awkwardly cropped, or not "
                     "clearly the point of the frame; an editor would pass it over.",
        }},
    # V3 — no encyclopedia at all: a craft question about the photograph
    "V3 professional portrait": {"type": "noul",
        "instructions": "Is this a publishable professional portrait of this person?",
        "criteria": {
            "true": "Subject is sharply rendered, well lit, well framed, eyes open, and clearly "
                    "the subject of the photograph.",
            "false": "Blurred, badly lit, obscured, eyes closed, badly cropped, subject too small, "
                     "or the person is a minor part of a group.",
        }},
    # V4 — ordered score instead of a threshold; expected value over 0-4
    "V4 score 0-4 portrait": {"type": "score",
        "instructions": "How strong is this photograph as a professional portrait of this person?",
        "criteria": ["Unusable — the person cannot be seen properly",
                     "Poor — technically or compositionally flawed",
                     "Acceptable — usable but unremarkable",
                     "Good — a competent professional portrait",
                     "Excellent — publishable as it stands"]},
    # V5 — two named options, hardest to answer in the middle
    "V5 choice publish/pass": {"type": "choice",
        "instructions": "An editor needs one photograph of this person.",
        "criteria": {
            "publish": "This photograph is the one to use.",
            "pass": "Choose a different photograph instead.",
        }},
}


def value_of(ans: dict) -> Optional[float]:
    """Normalise an answer to a 0-1 number (score divided by its top index)."""
    if not ans:
        return None
    t = ans.get("type")
    if t == "noul":
        return ans.get("noul")
    if t == "score":
        s = ans.get("score")
        return None if s is None else s / 4.0
    if t == "choice":
        probs = ans.get("probabilities") or {}
        for k, v in probs.items():
            if "publish" in k.lower():
                return v
        return ans.get("confidence")
    return None


def main() -> None:
    os.chdir(ROOT)          # so cache/ lookups below mean the repo's cache, not tools/

    # ---- census: what we already have, and pick four with a spread -----------------
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "cache", "*.json"))):
        try:
            d = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        a = (d.get("result") or {}).get("answers") or {}
        ir = (a.get("infobox_ready") or {}).get("noul")
        title = d.get("title")
        if ir is None or not title:
            continue
        rows.append({"cur": ir, "wiki": "commons.wikimedia.org", "title": title})
    rows.sort(key=lambda r: r["cur"])
    print(f"cached photos with a current infobox_ready: {len(rows)}", flush=True)
    if len(rows) >= 4:
        pick = [rows[0], rows[len(rows) // 3], rows[(2 * len(rows)) // 3], rows[-1]]
    else:
        pick = rows
    for p in pick:
        print(f"  chosen: {p['cur']:.3f}  {p['title']}", flush=True)

    # ---- resolve the chosen titles (Commons API only — no model calls) -------------
    meta = wiclient.resolve([{"title": p["title"], "wiki": p["wiki"]} for p in pick])
    for p in pick:
        m = meta.get((p["wiki"], p["title"])) or {}
        p["thumb"] = m.get("thumburl")
        if not m.get("ok"):
            print(f"  resolve FAILED for {p['title']}: {m.get('error')}", flush=True)

    # ---- fetch each image once, base64 once ---------------------------------------
    for p in pick:
        if not p.get("thumb"):
            p["b64"] = None
            continue
        try:
            p["b64"] = __import__("base64").b64encode(wiclient.fetch_bytes(p["thumb"])).decode()
            print(f"  image ready: {len(p['b64']) // 1024} KB b64  {p['title']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  thumbnail FAILED for {p['title']}: {type(e).__name__} {e}", flush=True)
            p["b64"] = None

    # ---- run the matrix ------------------------------------------------------------
    budget = wiclient.Budget()
    t_start = time.time()
    results = []
    with open(OUT, "a") as fh:
        for vi, (vname, q) in enumerate(VARIANTS.items(), 1):
            for p in pick:
                if p.get("b64") is None:
                    continue
                if time.time() - t_start > LIMIT_S:
                    print("  time limit reached — stopping", flush=True)
                    break
                body = {"model": "llm-clef-flash", "state": STATE, "images": [p["b64"]],
                        "questions": {"q": q}}
                t0 = time.time()
                try:
                    import urllib.error
                    import urllib.request
                    req = urllib.request.Request(wiclient.LIFTWING, data=json.dumps(body).encode(),
                                                 method="POST")
                    req.add_header("User-Agent", wiclient.UA)
                    req.add_header("Content-Type", "application/json")
                    with urllib.request.urlopen(req, timeout=240) as r:
                        code, payload = r.status, r.read().decode()
                except urllib.error.HTTPError as e:
                    code, payload = e.code, e.read().decode()
                except Exception as e:  # noqa: BLE001
                    code, payload = 0, json.dumps({"error": f"{type(e).__name__}: {e}"})
                wall = round(time.time() - t0, 2)
                budget.note_call()
                try:
                    ans = (json.loads(payload).get("answers") or {}).get("q") or {}
                except Exception:  # noqa: BLE001
                    ans = {}
                val = value_of(ans)
                rec = {"variant": vname, "variant_index": vi, "title": p["title"],
                       "wiki": p["wiki"], "current_infobox_ready": p["cur"], "http": code,
                       "wall_s": wall, "answer": ans, "value": val, "ts": time.time()}
                results.append(rec)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                shown = "    n/a" if val is None else f"{val:.3f}"
                print(f"  {vname:30s} {p['title'][:44]:44s} http={code} {wall:6.2f}s -> {shown}",
                      flush=True)
                time.sleep(wiclient.SPACING)
            else:
                continue
            break

    # ---- summary -------------------------------------------------------------------
    def stats(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return "no data"
        n = len(vals)
        mean = sum(vals) / n
        sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
        span = max(vals) - min(vals)
        mid = sum(1 for v in vals if 0.35 <= v <= 0.65)
        return (f"n={n} mean {mean:.3f} sd {sd:.3f} min {min(vals):.3f} max {max(vals):.3f} "
                f"span {span:.3f} in[.35,.65] {mid}/{n}")

    print("\n=== summary by variant ===", flush=True)
    for vname in VARIANTS:
        vals = [r["value"] for r in results if r["variant"] == vname]
        print(f"  {vname:30s} {stats(vals)}", flush=True)

    print("\n=== per image (rows = image, cols = variant) ===", flush=True)
    for p in pick:
        cells = []
        for vname in VARIANTS:
            hit = [r for r in results if r["variant"] == vname and r["title"] == p["title"]]
            cells.append("  n/a  " if not hit or hit[0]["value"] is None
                         else f"{hit[0]['value']:.3f}")
        print(f"  {p['title'][:40]:40s} cur={p['cur']:.3f} | " + " ".join(cells), flush=True)

    med = sorted(r["wall_s"] for r in results)
    if med:
        print(f"\ncalls: {len(results)}  median wall {med[len(med) // 2]:.1f}s  "
              f"total {(time.time() - t_start) / 60:.1f} min", flush=True)
    print("JSONL:", OUT, flush=True)


if __name__ == "__main__":
    main()
