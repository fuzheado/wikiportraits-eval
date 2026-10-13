#!/usr/bin/env python3
"""Resolve every filename-like token in the developer note, so the memo cannot cite a missing file.

Only tokens with a code/data extension (or a path containing a slash) are considered; ordinary prose
inside backticks — `composite`, `status`, `q.get()` — is ignored, as are globs and cache-key patterns.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "DEVELOPER-NOTE-v0.4.1.md")

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
        if not any(os.path.exists(p) for p in (os.path.join(HERE, n),
                                               os.path.join(HERE, "research", n))):
            missing.append(n)
    for n in sorted(names):
        print(("  ok      " if n not in missing else "  MISSING ") + n)
    print(f"\nchecked {len(names)} filename-like references · MISSING: {missing or 'none'}")
    return 1 if missing else 0


sys.exit(main())
