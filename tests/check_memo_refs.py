#!/usr/bin/env python3
"""Resolve every filename-like token in the developer note, so the memo cannot cite a missing file.

Only tokens with a code/data extension (or a path containing a slash) are considered; ordinary prose
inside backticks — `composite`, `status`, `q.get()` — is ignored, as are globs and cache-key patterns.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
DOC = os.path.join(ROOT, "docs", "DEVELOPER-NOTE-v0.4.1.md")
# the note cites files all over the tree, so search the same roots the docs do
SEARCH_DIRS = ("", "tests", "tools", "docs", "research", "static")

EXTS = ("py", "md", "json", "jsonl", "pdf", "png", "sh", "html", "js", "txt", "img", "yaml", "yml")
NAMEISH = re.compile(r"^(?:[\w.-]+/)*[\w.-]+\.(" + "|".join(EXTS) + r")$")
PATTERN = re.compile(r"[*{}<>]")


def main():
    text = open(DOC, encoding="utf-8").read()
    names = set()
    for span in re.findall(r"`([^`]+)`", text):
        for tok in re.split(r"[,\s]+", span.strip()):
            tok = tok.strip("`.,;:()[]")
            if not NAMEISH.match(tok) or PATTERN.search(tok):
                continue
            names.add(tok)
    missing = []
    for n in sorted(names):
        if not any(os.path.exists(os.path.join(ROOT, sub, n)) for sub in SEARCH_DIRS):
            missing.append(n)
    for n in sorted(names):
        print(("  ok      " if n not in missing else "  MISSING ") + n)
    print(f"\nchecked {len(names)} filename-like references · MISSING: {missing or 'none'}")
    return 1 if missing else 0


sys.exit(main())
