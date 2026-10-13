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

HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "_index_extracted.js")

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
sys.exit(r.returncode)
