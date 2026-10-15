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

sys.exit(1 if (r.returncode or bad) else 0)
