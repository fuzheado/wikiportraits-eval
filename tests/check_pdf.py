#!/usr/bin/env python3
"""Assert the PDF report's *text* — the one artifact a reader keeps, and the one nothing checked.

Until 2026-10-16 this file only *printed* `present=True/False` and exited 0 whatever it found, so it
reported "ok" on every commit while `reports.py` said "one state + six typed questions" and "our
arithmetic on three model answers" — both stale after `face_unobstructed` landed. A suite that cannot
fail is not a guard, and this is the file that proved it in this repo.

What it asserts now:

  1. the sections a reader navigates by are present (over a whitespace-normalised copy, because PDF
     text extraction inserts line breaks mid-phrase — a long literal probe matches nothing);
  2. every question in `wiclient.QUESTIONS` is *named* in the method block, and every input in
     `wiclient.COMPOSITE_INPUTS` too. That is the derived guard: it compares the document against the
     live schema, so adding or renaming a question fails here instead of shipping a stale PDF;
  3. the schema count in the prose equals `len(QUESTIONS)` — the hand-written number that actually
     went stale;
  4. the images survived (a real rasteriser, not just the file size), which is what the sibling
     `test_pdf_images.py` classifies in more detail.

Requires `pypdf` + `pypdfium2` (test-only deps — the app is stdlib-only by design). `tests/suites.py`
skips this loudly when they are missing, so a missing dep cannot quietly un-cover the PDF.

Reads the PDF written by tests/test_reports.py, so run that first — tests/suites.py orders them.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wiclient  # noqa: E402

PDF = "/tmp/wpc_report_test/report.pdf"

failures = []


def check(cond, msg):
    print(f"  {'ok  ' if cond else 'FAIL'} {msg}")
    if not cond:
        failures.append(msg)


if not os.path.exists(PDF):
    sys.exit(f"FAIL: {PDF} is missing — run tests/test_reports.py first (tests/suites.py does)")

from pypdf import PdfReader  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402

reader = PdfReader(PDF)
pages = len(reader.pages)
raw = "\n".join(pg.extract_text() or "" for pg in reader.pages)
flat = re.sub(r"\s+", " ", raw)          # extraction wraps mid-phrase; match against the flat copy
print(f"pages: {pages}  chars: {len(raw)}")

# --- 1. navigable sections ------------------------------------------------------------------------
for probe in ["WikiPortraits Eval report", "Method", "Summary", "Per-image determinations"]:
    check(probe.lower() in flat.lower(), f"section text present: {probe!r}")

# --- 2. the document agrees with the live schema --------------------------------------------------
missing_q = [q for q in wiclient.QUESTIONS if q not in flat]
check(not missing_q, f"every question in the schema is named in the PDF (missing: {missing_q or 'none'})")
missing_in = [k for k in wiclient.COMPOSITE_INPUTS if k not in flat]
check(not missing_in, f"every composite input is named (missing: {missing_in or 'none'})")
check("infobox_ready" in flat, "the ranked key is named (infobox_ready)")

# --- 3. the counts are derived, not typed ---------------------------------------------------------
n_q = len(wiclient.QUESTIONS)
check(f"{n_q} typed questions" in flat,
      f"the schema count in the prose equals len(QUESTIONS)={n_q} — this was the stale number")
check(f"{len(wiclient.COMPOSITE_INPUTS)} model answers" in flat,
      f"the composite names {len(wiclient.COMPOSITE_INPUTS)} answers — this was stale too")
for stale in ["six typed questions", "three model answers", "five questions"]:
    check(stale not in flat, f"no stale count in the text: {stale!r}")

# --- 4. the images are real ---------------------------------------------------------------------
doc = pdfium.PdfDocument(PDF)
rendered = 0
for i in range(len(doc)):
    page = doc[i].render(scale=1.5)
    arr = page.to_pil()
    if arr.size[0] > 100 and arr.size[1] > 100:
        rendered += 1
check(rendered == len(doc), f"every page rasterises ({rendered}/{len(doc)})")
# `/Image` objects, not `/DCTDecode`: this fixture's thumbnails are PNGs built by hand in
# tests/test_reports.py, so the PDF embeds them as Flate, not JPEG. Real Commons thumbnails are JPEG
# (`/DCTDecode`) and tests/test_pdf_images.py classifies those — asserting JPEG here would have been
# asserting something about a different fixture (which is how this line failed the first time).
check(b"/Image" in open(PDF, "rb").read(), "the PDF embeds image XObjects (thumbnails, not just text)")

print()
if failures:
    print(f"FAILURES: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("RESULT: PASS — the PDF text matches the live schema")
