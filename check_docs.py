#!/usr/bin/env python3
"""Read-only check: docs exist, and every file they name actually exists in the repo."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = ["PRD.md", "README.md", "HANDOFF.md"]
# texts that name a *shape* rather than a file, and upstream paths verified elsewhere
NON_FILES = {"wiki-pageid-sha1-schemahash-width.json"}
UPSTREAM = {"scripts/parse-file-refs.py": "/Users/alih/.pi/repos/Wikipedia-AI-Skills/.claude/skills/commons-file-resolution/"
                                        "scripts/parse-file-refs.py"}
missing = []
for d in DOCS:
    p = os.path.join(ROOT, d)
    if not os.path.exists(p):
        missing.append(d)
        continue
    print(f"{d:12s} {os.path.getsize(p):>6d} bytes  {len(open(p).read().splitlines()):>4d} lines")

def resolves(tok: str) -> str | None:
    """Return the path a doc reference resolves to, or None."""
    if tok in UPSTREAM:
        return UPSTREAM[tok] if os.path.exists(UPSTREAM[tok]) else None
    cand = os.path.join(ROOT, tok)
    if os.path.exists(cand):
        return cand
    if "/" not in tok:
        for sub in ("research", "static"):
            if os.path.exists(os.path.join(ROOT, sub, tok)):
                return os.path.join(ROOT, sub, tok)
    return None


# every backticked filename that looks like a repo file must resolve
pat = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|js|md|html|json|sh|txt))`")
seen = set()
for d in DOCS:
    for tok in pat.findall(open(os.path.join(ROOT, d)).read()):
        if tok in seen or tok in NON_FILES or tok.startswith(("references/", "tests/")):
            continue
        seen.add(tok)
        p = resolves(tok)
        if p is None:
            missing.append(f"{d} -> {tok}")
        elif tok in UPSTREAM:
            print(f"  ok  {tok}  (upstream)")
print(f"\nfilenames referenced: {len(seen)}")
for tok in sorted(seen):
    if tok in UPSTREAM:
        continue
    print(f"  {'ok ' if resolves(tok) else 'MISSING'} {tok}")
print("\nMISSING:", missing if missing else "none")
sys.exit(1 if missing else 0)
