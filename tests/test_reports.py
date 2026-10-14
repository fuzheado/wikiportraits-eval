#!/usr/bin/env python3
"""Offline tests for the new numbers and the reports: composite, P(publish), candidate spread,
thumbnails in the Markdown report, and image embedding in the PDF.

No network and no PIL: the test image is a PNG built by hand, so it runs anywhere.
"""
import io
import os
import re
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
sys.path.insert(0, ROOT)
import reports  # noqa: E402
import wiclient  # noqa: E402

FAIL = []


def ok(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAIL.append(label)


def png(w=6, h=4, color=(200, 30, 30)):
    """A minimal non-interlaced 8-bit RGB PNG, built by hand (no dependencies)."""
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def answers(face=0.97, eyes=0.76, pose="head_shoulders", infobox=0.68, publish=0.88, quality=2.9):
    return {
        "face_visible": {"type": "noul", "noul": face},
        "pose": {"type": "choice", "choice": pose, "confidence": 0.7,
                 "probabilities": {"head_shoulders": 0.7, "three_quarter": 0.2, "full_body": 0.05,
                                   "group": 0.03, "candid": 0.02}},
        "quality": {"type": "score", "score": quality, "confidence": 0.5, "legend": ["a", "b", "c", "d", "e"],
                    "probabilities": {"0": 0.02, "1": 0.05, "2": 0.3, "3": 0.5, "4": 0.13}},
        "infobox_ready": {"type": "noul", "noul": infobox},
        "publish_ready": {"type": "choice", "choice": "publish" if publish >= 0.5 else "pass",
                          "confidence": publish,
                          "probabilities": {"publish": publish, "pass": round(1 - publish, 4)}},
        "eyes_to_camera": {"type": "noul", "noul": eyes},
    }


def item(title, pageid, a, thumb_file=None, status="done"):
    return {"title": title, "raw": title, "pageid": pageid, "sha1": "x" * 40, "status": status,
            "resolved": True, "orig_w": 960, "orig_h": 1200, "sent_px": "960x1440",
            "license": "CC BY-SA 4.0", "thumburl": "https://thumb.wikimedia.org/x.jpg",
            "thumb_file": thumb_file,
            "result": {"http": 200, "wall_s": 0.61, "ttfb_s": 0.55, "usage": {"input_tokens": 905,
                                                                              "output_tokens": 0},
                       "answers": a, "model": "llm-clef-flash"}}


print("composite / publish / spread:")
a = answers()
ok(wiclient.composite(a) == round(min(0.97, 0.76) * 1.0, 4), "composite = min(face, eyes) x pose weight")
# The obstruction factor: a face fully covered by a held object keeps OBSTRUCTION_FLOOR of its score,
# a clear face keeps all of it, and an answer set that predates the question is untouched (never
# silently demoted) — that last case is why this fixture has no `face_unobstructed` at all.
assert wiclient.obstruction_factor({}) == 1.0, "a missing answer must not be a penalty"
assert wiclient.obstruction_factor({"face_unobstructed": {"noul": 1.0}}) == 1.0
assert wiclient.obstruction_factor({"face_unobstructed": {"noul": 0.0}}) == wiclient.OBSTRUCTION_FLOOR
half = dict(a, face_unobstructed={"noul": 0.0})
ok(wiclient.composite(half) == round(wiclient.composite(a) * wiclient.OBSTRUCTION_FLOOR, 4),
   "a microphone in front of the face demotes the composite, and does not disqualify it")
ok(wiclient.obstruction_factor({"face_unobstructed": {"noul": 9.0}}) == 1.0,
   "an out-of-range answer is clamped, not trusted")
ok(wiclient.composite(answers(pose="group")) == round(0.76 * 0.25, 4), "group pose is weighted down")
ok(wiclient.composite(answers(pose="weird_pose")) == round(0.76 * 0.5, 4), "unknown pose falls back to 0.5")
ok(wiclient.publish_p(a) == 0.88, "publish_p reads the publish option")
ok(wiclient.publish_p({"publish_ready": {"choice": "pass", "confidence": 0.9,
                                         "probabilities": {"pass": 0.9, "publish": 0.1}}}) == 0.1,
   "publish_p finds the publish probability even when pass wins")
ok(wiclient.composite({"face_visible": {"noul": 0.5}}) is None, "composite is None without eyes")

rows = [("a", 0.90), ("b", 0.80), ("c", 0.70), ("d", 0.40), ("e", 0.10)]
sp = wiclient.candidate_spread(rows, floor=0.25)
ok(sp["n_candidates"] == 4, f"candidates counted above the floor (got {sp['n_candidates']})")
ok(sp["candidate_range"] == round(0.90 - 0.40, 4), "candidate range excludes non-candidates")
ok(sp["range_all"] == 0.8, "range_all covers everything")
ok(sp["dead_zone"] == 1, "dead zone counts 0.35-0.65")
ok(sp["candidate_median_gap"] is not None and sp["candidate_median_gap"] > 0, "median gap computed")
ok(sp["top"][0] == "a", "top list is ordered")

print("\nranking and stats:")
items = [item("File:Low.jpg", 1, answers(publish=0.20, infobox=0.30, face=0.40, eyes=0.30)),
         item("File:High.jpg", 2, answers(publish=0.95, infobox=0.70, face=0.98, eyes=0.90)),
         item("File:Mid.jpg", 3, answers(publish=0.60, infobox=0.50, face=0.90, eyes=0.55,
                                         pose="three_quarter"))]
ranked = reports.ranked(items)
ok([i["title"] for i in ranked] == ["File:High.jpg", "File:Mid.jpg", "File:Low.jpg"],
   f"ranked by composite ({[i['title'] for i in ranked]})")
st = reports.stats(items)
ok(st["composite_mean"] is not None and st["p_publish_mean"] is not None, "stats carry the new means")
ok(reports.publish(items[1]) == 0.95, "reports.publish reads the item")

print("\nMarkdown report:")
md = reports.build_md(items, {"spread": {"composite": sp, "publish_ready": sp,
                                         "infobox_ready": sp}, "elapsed_s": 3})
ok("## Ranked by composite" in md, "MD ranks by composite")
ok("![High.jpg](https://thumb.wikimedia.org/x.jpg)" in md, "MD embeds the thumbnail as an image")
ok("composite" in md and "P(publish)" in md, "MD table carries composite and P(publish)")
ok("Candidate spread" in md, "MD has a candidate-spread block")
ok("min(face, eyes) × pose weight" in md, "MD states the composite formula")
# an embedded photo is redistributed, so the licence belongs next to it
ok("Licence: CC BY-SA 4.0" in reports.build_md(
    [item("File:Credited.jpg", 7, answers(), thumb_file=None) | {"license": "CC BY-SA 4.0",
                                                                  "artist": "Jane Doe"}],
    {"spread": {}, "elapsed_s": 1}), "MD carries the licence and photographer of an embedded photo")
ok("see the Commons file page" in reports.build_md(
    [item("File:Uncredited.jpg", 8, answers()) | {"license": None, "artist": None}],
    {"spread": {}, "elapsed_s": 1}), "MD says so honestly when the API returned no licence")

print("\nCSV report:")
csv_txt = reports.build_csv(items)
head = csv_txt.splitlines()[0]
ok("composite" in head and "P_publish" in head, "CSV header has composite and P_publish")
ok(len(csv_txt.splitlines()) == 4, "CSV has a header plus three rows")

print("\nPDF report (image embedding):")
tmp = "/tmp/wpc_report_test"
os.makedirs(tmp, exist_ok=True)
img_path = os.path.join(tmp, "thumb.png")
open(img_path, "wb").write(png())
pdf_items = [item("File:High.jpg", 2, answers(), thumb_file=img_path, status="done"),
             item("File:Missing.jpg", 9, answers(), thumb_file=None, status="done")]
# a broken file must be reported, not crash the report
bad_path = os.path.join(tmp, "broken.png")
open(bad_path, "wb").write(b"not an image at all")
pdf_items.append(item("File:Broken.jpg", 10, answers(), thumb_file=bad_path, status="done"))
pdf_items[1]["thumburl"] = None                       # so it cannot fall back to the network

out = os.path.join(tmp, "report.pdf")
data = reports.build_pdf(pdf_items, {"spread": {"composite": sp, "publish_ready": sp,
                                                "infobox_ready": sp},
                                     "pdf_path": out, "elapsed_s": 2})
ok(data[:5] == b"%PDF-", "PDF header")
ok(b"/XObject" in data and b"/Subtype /Image" in data, "PDF embeds an image XObject")
ok(b"/FlateDecode" in data, "PNG became a FlateDecode image")
ok(data.count(b"/Type /Page ") >= 1, "at least one page")
ok(len(data) > 4000, f"PDF is a plausible size ({len(data)} bytes)")
ok(reports.__dict__.get("_thumb_bytes") is not None, "thumb bytes helper exists")
ok(os.path.exists(out) and os.path.getsize(out) == len(data), "PDF written to disk")
print(f"  pdf: {len(data)} bytes, {data.count(b'/Subtype /Image')} image object(s)")


# --- the PDF chart follows the view the reader had on screen (?view=pose) ----------------------
def pdf_text(blob: bytes) -> str:
    """Every string drawn in the PDF, joined with spaces.

    pdfmin writes text as `(escaped) Tj` in an *uncompressed* content stream, and `_wrap()` breaks
    long captions across several runs — so a plain `in data` test on a phrase that happens to span a
    line break fails for the wrong reason (that mistake was made once already). Joining the runs
    restores the phrases without needing a PDF library.
    """
    out = []
    for m in re.finditer(rb"\(((?:\\.|[^()\\])*)\)\s*Tj", blob):
        s = m.group(1).replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
        out.append(s.decode("latin-1"))
    return " ".join(out)


# one photo per pose, so both charts and the strata are exercised
posed = [item(f"File:{p}.jpg", 30 + n, answers(), thumb_file=img_path, status="done")
         for n, p in enumerate(["head_shoulders", "head_shoulders", "three_quarter", "full_body",
                                "group", "candid"])]
for n, it in enumerate(posed):
    it["composite"] = 0.2 + 0.1 * n
    it["result"]["answers"]["pose"]["choice"] = it["title"].split(":")[1].split(".")[0]

combined = pdf_text(reports.build_pdf(posed, {"spread": {}, "pdf_path": out, "elapsed_s": 1},
                                     chart_view="combined"))
strata = pdf_text(reports.build_pdf(posed, {"spread": {}, "pdf_path": out, "elapsed_s": 1},
                                   chart_view="pose"))
ok("Composite by photo" in combined and "thumbs stack" in combined,
   "the default PDF chart is the combined one")
ok("Composite by photo, by pose" in strata and "one stratum per pose" in strata,
   "?view=pose gives the PDF the by-pose chart")
ok("head & shoulders" in strata,
   "the PDF strata carry the pose names a reader can match to the screen")
ok("head & shoulders" not in combined,
   "and the combined chart draws no pose strata (the label exists only in the strata view)")
ok(all(x in strata for x in ("three-quarter", "full body", "group", "candid")),
   "every pose gets a labelled band, empty ones included")
ok("one stratum per pose" not in combined and "thumbs stack" not in strata,
   "the two views are not mixed up")
ok(pdf_text(reports.build_pdf(posed, {"spread": {}, "pdf_path": out, "elapsed_s": 1})) == combined,
   "chart_view defaults to the combined chart (no parameter, same report)")

# --- the PDF chart follows the AXIS too (?axis=candidates) -------------------------------------
# The web UI can fit the axis to the top N; the export has to agree, or a reader comparing the two sees
# a different chart. 24 photos with the top 12 crammed into a 0.05 band, the shape measured on real data.
spread_items = []
for n in range(24):
    it = item(f"File:S{n}.jpg", 60 + n, answers(), thumb_file=img_path, status="done")
    it["composite"] = 0.02 + n * 0.04 if n < 12 else 0.90 + (n - 12) * 0.004
    spread_items.append(it)
meta = {"spread": {}, "pdf_path": out, "elapsed_s": 1}
full_chart = pdf_text(reports.build_pdf(spread_items, meta, chart_axis="full"))
cand_chart = pdf_text(reports.build_pdf(spread_items, meta, chart_axis="candidates"))
ok("axis 0 ->" in full_chart, "the default PDF chart spans the whole field")
ok("the top 12 of 24 scored" in cand_chart, "axis=candidates fits the PDF chart to the top 12")
ok("12 below the window" in cand_chart, "and says how many photos are outside it")
ok("the top 12" not in full_chart, "the two axes are not mixed up")
ok(pdf_text(reports.build_pdf(spread_items, meta)) == full_chart,
   "chart_axis defaults to the whole field (no parameter, same report)")

def pdf_labels(blob: bytes):
    # raw docstring on purpose: it names a regex literal, and a non-raw one raised
    # `SyntaxWarning: invalid escape sequence '\d'` — harmless today, an error in a future Python
    r"""(x, y, text) for every free-positioned string — enough to check chart placement.

    `text_at` writes `x y Td (text) Tj`, so the position comes out with the string. The coordinates
    must be allowed to be NEGATIVE: a chart tall enough to run past the page emits negative `y`, and
    an earlier version of this helper matched only `\d+.\d+` — so it silently dropped 11 of 20 chart
    badges and "verified" a spacing property on a subset. The missed labels were the interesting ones.
    """
    out = []
    for m in re.finditer(rb"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) Td \(((?:\\.|[^()\\])*)\) Tj", blob):
        s = m.group(3).replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
        out.append((float(m.group(1)), float(m.group(2)), s.decode("latin-1")))
    return out


# --- the PDF chart must not stack two photos on one spot either --------------------------------
# The web chart had a lane cap that silently drew every collider at the same position; the PDF had the
# same defect with a lower cap (4), and the PDF is the artefact people actually pass around.
crowd = []
for n in range(20):
    it = item(f"File:C{n}.jpg", 90 + n, answers(), thumb_file=img_path, status="done")
    it["composite"] = 0.44 + n * 0.0002          # all inside one 56pt column
    crowd.append(it)
clabels = [(round(x), round(y), t) for x, y, t in pdf_labels(reports.build_pdf(crowd, meta))
           if re.fullmatch(r"#\d+", t)]      # the chart badge is exactly `#12`; a card heading is
                                              # `#1  File:x.jpg`, so match the bare form only
ok(len(clabels) == 20, f"every photo in a 20-photo PDF clump is charted (got {len(clabels)})")
cspots = [(x, y) for x, y, _t in clabels]
ok(len(cspots) == len(set(cspots)),
   f"no two charted photos share a position in the PDF (got {len(cspots) - len(set(cspots))} sharing)")

print(f"\nFAILURES: {len(FAIL)}")
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
