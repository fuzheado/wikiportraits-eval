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

sys.exit(1 if (r.returncode or bad) else 0)
