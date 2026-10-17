#!/usr/bin/env python3
"""Extract the inline <script> from static/index.html and syntax-check it with node.

No browser is available on this host, so `node --check` on the extracted script is the cheapest way
to catch a syntax error before a user sees a blank page. The DOM behaviour itself is covered by
test_render_dom.js (jsdom).
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
HTML = os.path.join(ROOT, "static", "index.html")
JS = os.path.join(ROOT, "static", "_index_extracted.js")

html = open(HTML, encoding="utf-8").read()
blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
if not blocks:
    sys.exit("no inline <script> found in index.html")
js = "\n".join(blocks)
open(JS, "w", encoding="utf-8").write(js)

r = subprocess.run(["node", "--check", JS], capture_output=True, text=True)
print(f"extracted {len(js)} chars from {len(blocks)} script block(s)")
print("node --check:", "OK" if r.returncode == 0 else "FAILED")
if r.returncode:
    print(r.stderr[:2000])

# ---- cross-language constants -------------------------------------------------------------------
# The "candidates" axis window is implemented twice — once in the page, once in the PDF export — and
# nothing about the two languages keeps them in step. A reader who switches to candidates on screen
# and then downloads the PDF would get a different chart, which is the sort of difference nobody
# reports as a bug. So assert the numbers agree, here, offline, in CI.
sys.path.insert(0, ROOT)
import reports  # noqa: E402

shared = {
    "CANDIDATE_WINDOW": reports.CANDIDATE_WINDOW,
    "CANDIDATE_FLOOR_RATIO": reports.CANDIDATE_FLOOR_RATIO,
    "SCALE_EXPONENT": reports.SCALE_EXPONENT,
}
bad = []
for name, value in shared.items():
    pat = re.compile(rf"\bconst {name} = {re.escape(str(value))}\b")
    if not pat.search(js):
        found = re.findall(rf"\bconst {name} = ([^;]+)", js)
        bad.append(f"{name}: reports.py says {value}, index.html says {found or 'nothing'}")
for b in bad:
    print(f"  MISMATCH {b}")
print(f"shared constants: {'agree' if not bad else 'DISAGREE (' + str(len(bad)) + ')'}")

# ---- the run cap is published, not duplicated ----------------------------------------------------
# `MAX_INPUT_ITEMS` is a *server* number the page cannot know at build time, so unlike the constants
# above there is nothing to compare — so assert the two halves of the mechanism instead. The failure
# this prevents was found on 2026-10-14: Validate accepted up to PARSE_MAX (1000) and reported "1000 of
# 1000 resolved", and Score them then refused with a 413 at 201 — the green light was a lie, and the
# limit cost a round trip. The page reads the cap from /api/config; a hardcoded copy would drift and
# bring exactly that back. **Assert an assignment, not a bare mention:** this comment contains the
# string, so a looser pattern would be satisfied by its own prose (the trap in AGENTS.md).
import json        # noqa: E402
import tempfile    # noqa: E402
os.environ.setdefault("CACHE_DIR", tempfile.mkdtemp())   # import core without touching the repo's cache/
import core        # noqa: E402

cfg = json.loads(core.route("GET", "/api/config", b"", "")[2])
if cfg.get("max_input_items") != core.MAX_INPUT_ITEMS:
    bad.append(f"/api/config does not publish max_input_items (got {cfg.get('max_input_items')!r})")
    print("  MISSING: /api/config has no max_input_items equal to core.MAX_INPUT_ITEMS")
elif not re.search(r"=\s*configCache\.max_input_items\b", js):
    bad.append("index.html never reads max_input_items from /api/config")
    print("  MISSING: the page never assigns configCache.max_input_items, so it cannot state the cap")
else:
    print(f"run cap: /api/config publishes max_input_items={core.MAX_INPUT_ITEMS} and the page reads it")

# The prune labels are built from the cap at runtime ("keep the first 200"), so the buttons must be
# EMPTY in the HTML. A number typed in here would be a second copy of the server's constant — drifting
# in exactly the way the check above exists to prevent — and the drift would be invisible, because a
# stale label still reads like a plausible limit.
for _bid in ("prune-first", "prune-sample"):
    _m = re.search(rf'<button id="{_bid}"[^>]*>(.*?)</button>', html, re.S)
    if not _m:
        bad.append(f"no #{_bid} button in the HTML")
        print(f"  MISSING: no #{_bid} button")
    elif _m.group(1).strip():
        bad.append(f"#{_bid} bakes a label into the HTML ({_m.group(1).strip()!r})")
        print(f"  STALE: #{_bid} has fixed label text — the cap must come from /api/config at runtime")
    else:
        print(f"#{_bid}: label built at runtime")

# ---- the layout invariant no other check can see -------------------------------------------------
# `.chartbox` is a flex item whose children are ALL absolutely positioned (thumbs, ticks, gridlines,
# the axis line), so its automatic width is its content width — which is zero. A zero-width box sends
# every `left: N%` tick to the SAME pixel, hides the `border-bottom` axis line, and drops `clientWidth`
# to 0 so the chart falls back to a hardcoded 800px and stops filling the card. It broke exactly this
# way in v0.10.0, when `.cchart{display:flex}` arrived with the by-pose view: `.cposes` got
# `flex:0 0 106px` and `.chartbox` got nothing. **jsdom cannot catch it** (no layout) and neither can
# any Python test, so this cheap CSS assertion is the only automated guard there is.
m = re.search(r"\.chartbox\{([^}]*)\}", html)
if not m:
    bad.append(".chartbox has no CSS rule at all")
    print("  MISSING: no .chartbox rule in the CSS")
elif not re.search(r"\b(flex|width)\s*:", m.group(1)):
    bad.append(".chartbox declares no flex/width")
    # hoisted out of the f-string on purpose: a backslash inside an f-string expression is a syntax
    # error before Python 3.12 (`check_compat.py` caught exactly this, 2026-10-14)
    body = re.sub(r"\s+", " ", m.group(1)).strip()[:60]
    print(f"  MISSING: .chartbox is a flex item with all-absolute children and no basis ({body})")
else:
    print(".chartbox keeps an explicit basis: OK")

# ---- uploads: the downscale width comes from the server, and never from a typed-in copy -------------
# A local image is resized in the browser (the server is stdlib-only: no Pillow, no decoder). The width
# it is resized to must be the SAME width the Commons thumbnails are fetched at, or a local photo and a
# Commons photo stop being comparable in one chart — and the difference would be invisible, because both
# would look like a plausible photo. So the page reads `thumb_width_requested` from /api/config, exactly
# as it reads `max_input_items` above, and the only literal in the file is a fallback for the moment
# before /api/config answers. **Assert the read, not the mention:** this comment names it too.
if not re.search(r"configCache\.thumb_width_requested", js):
    bad.append("index.html never reads thumb_width_requested, so the upload width is a hardcoded copy")
    print("  MISSING: the page does not read thumb_width_requested from /api/config")
else:
    _m = re.search(r"function uploadWidth\(\)\s*\{[^}]*\}", js)
    if not _m or "configCache.thumb_width_requested" not in _m.group(0):
        bad.append("uploadWidth() does not read the served thumbnail width")
        print("  MISSING: uploadWidth() does not read configCache.thumb_width_requested")
    else:
        print("upload width: the page resizes to /api/config's thumb_width_requested")

# ---- an uploaded image must never leak into a share link or a curl command --------------------------
# An upload exists only inside the session that uploaded it. Its "title" is a filename off somebody's
# laptop, so putting it in `?list=…` produces a link that (a) silently shows the recipient FEWER photos
# than the sender saw, and (b) if that filename happens to exist on Commons, resolves to a DIFFERENT
# photograph while looking entirely plausible. Both handlers must go through shareableTitles(), which is
# the one place that filters `local` items out.
_uses = len(re.findall(r"shareableTitles\(", js))
if _uses < 3:                     # the definition plus the share link and the API command
    bad.append(f"only {_uses} uses of shareableTitles() — both link builders must filter uploads out")
    print(f"  MISSING: shareableTitles() is used {_uses} time(s); expected 3 (definition + 2 callers)")
elif not re.search(r"filter\(it => !it\.local\)", js):
    bad.append("shareableTitles() does not filter out local items")
    print("  MISSING: shareableTitles() does not exclude uploaded images")
else:
    print(f"share links: {_uses - 1} callers of shareableTitles(), which drops uploads")

# ---- every id the script reaches for must exist in the markup ---------------------------------------
# A typo here is not a build error in a plain HTML page: `$("localtray")` returns null and the failure
# surfaces later as "Cannot read properties of null", possibly only in one UI path. The DOM test runs the
# real script, so it catches a *load-time* failure — but only as a jsdom error nobody reads. This costs
# nothing and names the id. Literal `$("x")` calls only: `$("dl-" + f)` is built by concatenation.
_ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js))
_missing = sorted(i for i in _ids if f'id="{i}"' not in html)
if _missing:
    bad.extend(f"$({i!r}) has no element with that id" for i in _missing)
    print(f"  MISSING: the script references ids that do not exist: {_missing}")
else:
    print(f"element ids: all {len(_ids)} ids the script looks up exist in the HTML")

sys.exit(1 if (r.returncode or bad) else 0)
