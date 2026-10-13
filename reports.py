"""Aggregate statistics and report builders (MD, CSV, JSON, PDF) for a scoring job."""
import csv, io, json, os, statistics
from datetime import datetime, timezone

import wiclient
from pdfmin import PDF

ORDER = ["face_visible", "pose", "quality", "infobox_ready", "publish_ready", "eyes_to_camera"]
TYPE_LABEL = {"noul": "yes/no", "choice": "choice", "score": "score"}


def _ans(item):
    return (item.get("result") or {}).get("answers") or {}


def infobox(item):
    return (_ans(item).get("infobox_ready") or {}).get("noul")


def publish(item):
    """P(publish) — the model's answer to the two-option choice question."""
    v = item.get("publish_p")
    return wiclient.publish_p(_ans(item)) if v is None else v


def composite(item):
    """Our composite: min(face_visible, eyes_to_camera) x pose weight (not a model probability)."""
    v = item.get("composite")
    return wiclient.composite(_ans(item)) if v is None else v


def quality(item):
    return (_ans(item).get("quality") or {}).get("score")


def pose(item):
    return (_ans(item).get("pose") or {}).get("choice")


def label(item):
    return (item.get("title") or item.get("raw") or "").replace("File:", "")


def thumb(item):
    """A URL for the thumbnail, for the Markdown report (the PDF embeds bytes instead)."""
    return item.get("thumburl")


def _credit(item):
    """The attribution line for an embedded Commons photo.

    Reports embed image bytes, so they redistribute the photo: the licence and the photographer
    belong next to it, not just in the CSV. Falls back to a Commons link when the API did not
    return a licence or artist.
    """
    lic = (item.get("license") or "").strip()
    who = (item.get("artist") or "").strip()
    if not lic and not who:
        return "- Licence: see the Commons file page (the API returned none)"
    parts = []
    if lic:
        parts.append(lic)
    if who:
        parts.append(f"photo: {who}")
    return "- Licence: " + " · ".join(parts)


def _thumb_bytes(item):
    """Thumbnail bytes for embedding: the saved copy first (offline), else Commons."""
    p = item.get("thumb_file")
    if p and os.path.exists(p):
        try:
            with open(p, "rb") as fh:
                return fh.read()
        except Exception:                                        # noqa: BLE001
            pass
    url = item.get("thumburl")
    if url:
        try:
            return wiclient.fetch_bytes(url)
        except Exception:                                        # noqa: BLE001
            return None
    return None


def spread_of(items: list, key: str = "composite") -> dict:
    """Candidate spread over one of the three headline numbers."""
    getter = {"composite": composite, "infobox_ready": infobox, "publish_ready": publish}[key]
    return wiclient.candidate_spread([(label(i), getter(i)) for i in items])


def stats(items: list) -> dict:
    scored = [i for i in items if i.get("status") == "done" and infobox(i) is not None]
    pv = [infobox(i) for i in scored]
    pubv = [publish(i) for i in scored if publish(i) is not None]
    compv = [composite(i) for i in scored if composite(i) is not None]
    qv = [quality(i) for i in scored if quality(i) is not None]
    walls = [i["result"]["wall_s"] for i in scored if i["result"].get("wall_s")]
    tokens = [i["result"]["usage"].get("input_tokens", 0) for i in scored]
    poses = {}
    for i in scored:
        poses[pose(i)] = poses.get(pose(i), 0) + 1
    return {
        "n_input": len(items),
        "n_resolved": sum(1 for i in items if i.get("resolved")),
        "n_scored": len(scored),
        "n_failed": sum(1 for i in items if i.get("status") == "error"),
        "n_skipped": sum(1 for i in items if i.get("status") == "skipped"),
        "p_infobox_mean": round(sum(pv) / len(pv), 4) if pv else None,
        "p_infobox_median": round(statistics.median(pv), 4) if pv else None,
        "p_infobox_max": round(max(pv), 4) if pv else None,
        "n_above_half": sum(1 for p in pv if p >= 0.5),
        "p_publish_mean": round(sum(pubv) / len(pubv), 4) if pubv else None,
        "composite_mean": round(sum(compv) / len(compv), 4) if compv else None,
        "composite_max": round(max(compv), 4) if compv else None,
        "quality_mean": round(sum(qv) / len(qv), 3) if qv else None,
        "poses": poses,
        "tokens_total": sum(tokens),
        "wall_total_s": round(sum(walls), 2) if walls else None,
        "wall_median_s": round(statistics.median(walls), 3) if walls else None,
        "n_429": sum(1 for i in items if i.get("rate_limited")),
    }


def ranked(items: list) -> list:
    """Ranked by the composite (our own arithmetic), falling back to P(infobox-readiness)."""
    def key(i):
        for v in (composite(i), infobox(i)):
            if v is not None:
                return -v
        return 0
    return sorted(items, key=key)


def _method_block(model_note=""):
    return [
        "Model: llm-clef-flash (Cloudflare/clef-flash, 9.41 B params BF16, Apache-2.0) served by "
        "Wikimedia LiftWing at api.wikimedia.org/service/lw/inference/v1/models/llm-clef-flash:predict "
        "(KServe on HuggingFace transformers, not vLLM).",
        "Schema: one state + six typed questions, all answered in a single forward pass; the model "
        "generates no text (usage.output_tokens = 0). Question types: "
        "face_visible (yes/no), pose (choice of 5), quality (score 0-4), infobox_ready (yes/no), "
        "publish_ready (choice of 2: publish / pass), eyes_to_camera (yes/no).",
        "Headline numbers: composite = min(face_visible, eyes_to_camera) x pose weight "
        "(head-and-shoulders 1.0, three-quarter 0.9, full body 0.6, candid 0.5, group 0.25). That is "
        "our arithmetic on three model answers, NOT a model probability; P(publish) and P(infobox) "
        "are the model's own. Ranking uses the composite.",
        "Spread: the run reports candidate spread (range and median gap among answers at or above "
        f"{wiclient.CANDIDATE_FLOOR}), not standard deviation — sd rises whenever answers pile up at "
        "either end, including at the ceiling, so it rewards the wrong thing.",
        "Inputs: Commons thumbnails at the standard step nearest above 640 px (the server downscales "
        "to 640 px wide before the encoder, so larger sends add no information).",
        "Note: probabilities depend on the question set — the head scores every question jointly, so "
        "only compare runs that used this identical five-question schema." + model_note,
    ]


def build_md(items: list, meta: dict) -> str:
    st = stats(items)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# WikiPortraits Eval report — {ts}", ""]
    L += ["> " + m for m in _method_block()] + [""]
    L += ["## Summary", ""]
    L += [f"- Images pasted: **{st['n_input']}** · resolved: **{st['n_resolved']}** · "
          f"scored: **{st['n_scored']}** · not found/failed: **{st['n_failed']}**"
          + (f" · skipped (cached): {st['n_skipped']}" if st["n_skipped"] else "")]
    L += [f"- P(infobox-ready): mean **{st['p_infobox_mean']}** · median {st['p_infobox_median']} · "
          f"max {st['p_infobox_max']} · **{st['n_above_half']} of {st['n_scored']}** at or above 0.5"]
    L += [f"- P(publish): mean **{st['p_publish_mean']}**  ·  composite (our arithmetic, "
          f"min(face, eyes) × pose weight): mean **{st['composite_mean']}**, max {st['composite_max']}"]
    L += [f"- Quality (0–4): mean **{st['quality_mean']}**"]
    L += [f"- Pose mix: " + ", ".join(f"{k} {v}" for k, v in sorted(st["poses"].items(), key=lambda kv: -kv[1]))]
    sp_all = meta.get("spread") or {}
    if (sp_all.get("composite") or {}).get("n"):
        sp = sp_all["composite"]
        L += [f"- **Candidate spread** (composite ≥ {sp['floor']}): {sp['n_candidates']} of {sp['n']} "
              f"photos are candidates · range **{sp['candidate_range']}** · median gap between "
              f"neighbours {sp['candidate_median_gap']} · {sp['dead_zone']} of {sp['n']} "
              f"({sp['dead_zone_share']:.0%}) sit in the uninformative 0.35–0.65 band"]
        for k in ("infobox_ready", "publish_ready"):
            s = sp_all.get(k) or {}
            if s.get("n"):
                L += [f"  - same measure for {k}: {s['n_candidates']} of {s['n']} candidates · "
                      f"range {s['candidate_range']} · median gap {s['candidate_median_gap']} · "
                      f"{s['dead_zone']} in the band"]
    L += [f"- Wall: median {st['wall_median_s']} s per call, {st['wall_total_s']} s total · "
          f"input tokens {st['tokens_total']}" + (f" · {st['n_429']} rate-limited" if st["n_429"] else "")]
    if meta.get("elapsed_s"):
        L += [f"- Job wall clock: {meta['elapsed_s']} s"]
    lat = meta.get("latency") or {}
    if lat.get("calls"):
        L += [f"- Endpoint latency ({lat['verdict']}): median **{lat['median_s']} s** per model call "
              f"({lat['min_s']}–{lat['max_s']} s, n={lat['calls']}) vs the measured warm baseline of "
              f"{lat['baseline_s']} s — {lat['factor']}x. {lat.get('note','')}"]
    L += ["", "## Ranked by composite", "",
          "| # | Photo | composite | P(publish) | P(infobox) | quality | pose | P(eyes→cam) | P(face) | tokens | wall |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, it in enumerate(ranked(items), 1):
        a = _ans(it)
        ib = infobox(it)
        if ib is None:
            L.append(f"| {i} | {label(it)} | — | — | — | — | — | — | — | — | "
                     f"{it.get('status')} |")
            continue
        comp = composite(it)
        pub = publish(it)
        L.append(f"| {i} | {label(it)} | **{'—' if comp is None else f'{comp:.3f}'}** | "
                 f"{'—' if pub is None else f'{pub:.3f}'} | {ib:.3f} | "
                 f"{quality(it):.2f} | {pose(it)} | {a['eyes_to_camera']['noul']:.3f} | "
                 f"{a['face_visible']['noul']:.3f} | {it['result']['usage'].get('input_tokens')} | "
                 f"{it['result']['wall_s']:.2f}s |")
    L += ["", "## Per-image determinations", ""]
    for it in ranked(items):
        if it.get("status") != "done":
            continue
        a = _ans(it)
        comp = composite(it)
        pub = publish(it)
        L += [f"### {it['title']}", ""]
        if thumb(it):
            L += [f"![{label(it)}]({thumb(it)})", ""]
        L += [f"- Commons: https://commons.wikimedia.org/wiki/{it['title'].replace(' ', '_')}",
              _credit(it),
              f"- Original {it.get('orig_w')}×{it.get('orig_h')} px; sent {it.get('sent_px')}; "
              f"{it['result']['usage'].get('input_tokens')} input tokens; {it['result']['wall_s']} s",
              f"- **composite** (ours): {'—' if comp is None else f'{comp:.3f}'} · "
              f"**P(publish)**: {'—' if pub is None else f'{pub:.3f}'}"
              + (f" ({a['publish_ready']['choice']})" if a.get("publish_ready", {}).get("choice") else ""),
              f"- **infobox_ready**: {a['infobox_ready']['noul']:.3f} · "
              f"**face_visible**: {a['face_visible']['noul']:.3f} · "
              f"**eyes_to_camera**: {a['eyes_to_camera']['noul']:.3f}",
              f"- **pose**: {a['pose']['choice']} (P={a['pose']['probabilities'][a['pose']['choice']]:.3f}) — "
              + ", ".join(f"{k} {v:.3f}" for k, v in sorted(a["pose"]["probabilities"].items(),
                                                             key=lambda kv: -kv[1])),
              f"- **quality**: {a['quality']['score']:.3f} of 4 — "
              + ", ".join(f"{k} {v:.3f}" for k, v in sorted(a["quality"]["probabilities"].items())), ""]
    bad = [i for i in items if i.get("status") in ("error", "notfound")]
    if bad:
        L += ["## Not scored", ""]
        for it in bad:
            L.append(f"- `{it.get('raw') or it.get('title')}` — {it.get('error') or 'failed'}")
        L += [""]
    L += ["---", "",
          "Produced by the WikiPortraits Eval prototype. Every probability above was returned by the "
          "endpoint; nothing is inferred. Photographs are from Wikimedia Commons under their own "
          "licences. Verify before any on-wiki use: model output is an assistive draft, not a source."]
    return "\n".join(L) + "\n"


def build_csv(items: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "title", "pageid", "status", "composite", "P_publish", "P_infobox_ready",
                "quality_0_4", "pose", "P_eyes_to_camera", "P_face_visible", "input_tokens", "wall_s",
                "orig_px", "sent_px", "license", "sha1", "error"])
    for i, it in enumerate(ranked(items), 1):
        a = _ans(it)
        w.writerow([i, it.get("title") or it.get("raw"), it.get("pageid"), it.get("status"),
                    composite(it), publish(it),
                    a.get("infobox_ready", {}).get("noul"), a.get("quality", {}).get("score"),
                    a.get("pose", {}).get("choice"), a.get("eyes_to_camera", {}).get("noul"),
                    a.get("face_visible", {}).get("noul"),
                    (it.get("result") or {}).get("usage", {}).get("input_tokens"),
                    (it.get("result") or {}).get("wall_s"),
                    f"{it.get('orig_w')}x{it.get('orig_h')}", it.get("sent_px"),
                    it.get("license"), it.get("sha1"), it.get("error")])
    return buf.getvalue()


def build_json(items: list, meta: dict) -> str:
    return json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(),
        "endpoint": "https://api.wikimedia.org/service/lw/inference/v1/models/llm-clef-flash:predict",
        "model": "llm-clef-flash",
        "schema": {"state": meta.get("state"), "questions": meta.get("questions")},
        "stats": stats(items),
        "job": meta,
        "items": items,
    }, indent=1, ensure_ascii=False)


def build_pdf(items: list, meta: dict) -> bytes:
    st = stats(items)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc = PDF(title="WikiPortraits Eval report", subtitle=ts)
    doc.h1("WikiPortraits Eval report")
    doc.small(f"Generated {ts} · model llm-clef-flash on Wikimedia LiftWing · "
              f"{st['n_scored']} of {st['n_input']} images scored")
    doc.spacer(6)
    doc.rule()
    doc.spacer(4)
    doc.text("Method", size=11, bold=True)
    for m in _method_block():
        doc.small(m)
        doc.spacer(2)
    doc.spacer(6)
    doc.text("Summary", size=11, bold=True)
    lines = [
        f"Images pasted {st['n_input']}  ·  resolved {st['n_resolved']}  ·  scored {st['n_scored']}  ·  "
        f"not scored {st['n_failed']}",
        f"P(infobox-ready): mean {st['p_infobox_mean']}  ·  median {st['p_infobox_median']}  ·  "
        f"max {st['p_infobox_max']}  ·  {st['n_above_half']} of {st['n_scored']} at or above 0.5",
        f"P(publish): mean {st['p_publish_mean']}   ·   composite (ours, min(face, eyes) x pose "
        f"weight): mean {st['composite_mean']}, max {st['composite_max']}",
        f"Quality (0-4): mean {st['quality_mean']}",
        "Pose mix: " + ", ".join(f"{k} {v}" for k, v in sorted(st["poses"].items(), key=lambda kv: -kv[1])),
        f"Wall: median {st['wall_median_s']} s per call, {st['wall_total_s']} s total  ·  "
        f"input tokens {st['tokens_total']}"
        + (f"  ·  job wall clock {meta['elapsed_s']} s" if meta.get("elapsed_s") else ""),
    ]
    sp_all = meta.get("spread") or {}
    if (sp_all.get("composite") or {}).get("n"):
        sp = sp_all["composite"]
        lines.append(f"CANDIDATE SPREAD (composite >= {sp['floor']}): {sp['n_candidates']} of {sp['n']} "
                     f"photos are candidates  ·  range {sp['candidate_range']}  ·  median gap "
                     f"{sp['candidate_median_gap']}  ·  {sp['dead_zone']} of {sp['n']} "
                     f"({sp['dead_zone_share']:.0%}) in the uninformative 0.35-0.65 band")
        for k in ("infobox_ready", "publish_ready"):
            s = sp_all.get(k) or {}
            if s.get("n"):
                lines.append(f"    same measure for {k}: {s['n_candidates']} of {s['n']} candidates, "
                             f"range {s['candidate_range']}, median gap {s['candidate_median_gap']}, "
                             f"{s['dead_zone']} in the band")
    lat = meta.get("latency") or {}
    if lat.get("calls"):
        lines.append(f"Endpoint latency ({lat['verdict']}): median {lat['median_s']} s per call "
                     f"({lat['min_s']}-{lat['max_s']} s, n={lat['calls']}) vs a measured warm "
                     f"baseline of {lat['baseline_s']} s - {lat['factor']}x")
    for l in lines:
        doc.text(l, size=9)
    doc.spacer(8)
    doc.h2("All images, ranked by composite")
    widths = [14, 128, 44, 44, 40, 44, 34, 36, 32]
    doc.row(["#", "Photo", "composite", "P(publish)", "P(infobox)", "pose", "quality", "P(eyes)",
             "wall"], widths, size=7.4, bold=True)
    for i, it in enumerate(ranked(items), 1):
        a = _ans(it)
        ib = infobox(it)
        if ib is None:
            doc.row([str(i), label(it), "-", "-", "-", "-", "-", "-", it.get("status", "")], widths)
            continue
        comp, pub = composite(it), publish(it)
        doc.row([str(i), label(it), "-" if comp is None else f"{comp:.3f}",
                 "-" if pub is None else f"{pub:.3f}", f"{ib:.3f}", str(pose(it)),
                 f"{quality(it):.2f}", f"{a['eyes_to_camera']['noul']:.3f}",
                 f"{it['result']['wall_s']:.2f}s"], widths)
        doc.hr_small()
    doc.spacer(10)
    doc.h2("Per-image determinations, with the photo the model saw")
    for it in ranked(items):
        if it.get("status") != "done":
            continue
        a = _ans(it)
        comp, pub = composite(it), publish(it)
        data = _thumb_bytes(it)
        name = doc.add_image(data, label=label(it)) if data else None
        doc.spacer(4)
        top = sorted(a["pose"]["probabilities"].items(), key=lambda kv: -kv[1])
        doc.card([("h", label(it)),
                  f"composite {comp:.3f} (ours)   ·   P(publish) "
                  + ("-" if pub is None else f"{pub:.3f}")
                  + (f" ({a['publish_ready']['choice']})" if a.get("publish_ready", {}).get("choice") else "")
                  + f"   ·   P(infobox) {a['infobox_ready']['noul']:.3f}",
                  f"face_visible {a['face_visible']['noul']:.3f}   ·   eyes_to_camera "
                  f"{a['eyes_to_camera']['noul']:.3f}   ·   pose {a['pose']['choice']}",
                  "pose " + "  ".join(f"{k} {v:.3f}" for k, v in top),
                  f"quality {a['quality']['score']:.3f} of 4  ·  "
                  + "  ".join(f"{k} {v:.3f}" for k, v in sorted(a["quality"]["probabilities"].items())),
                  f"original {it.get('orig_w')}x{it.get('orig_h')} px, sent {it.get('sent_px')}  ·  "
                  f"{it['result']['usage'].get('input_tokens')} input tokens  ·  "
                  f"{it['result']['wall_s']} s  ·  HTTP {it['result'].get('http')}",
                  _credit(it).lstrip("- "),
                  f"commons.wikimedia.org/wiki/{it['title'].replace(' ', '_')}"],
                 name=name)
        doc.hr_small()
    if doc.skipped:
        doc.spacer(6)
        doc.h2("Images that could not be embedded")
        for lab, why in doc.skipped:
            doc.small(f"{lab} — {why}")
    bad = [i for i in items if i.get("status") in ("error", "notfound")]
    if bad:
        doc.spacer(8)
        doc.h2("Not scored")
        for it in bad:
            doc.small(f"{it.get('raw') or it.get('title')} — {it.get('error') or 'failed'}")
    doc.spacer(8 if doc.remaining_h() > 46 else 0)
    doc.small("Every probability above was returned by the endpoint; nothing is inferred. "
              "Photographs are from Wikimedia Commons under their own licences. Verify before any "
              "on-wiki use: model output is an assistive draft, not a source.")
    path = meta.get("pdf_path")
    doc.save(path)
    with open(path, "rb") as fh:
        return fh.read()
