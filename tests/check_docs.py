#!/usr/bin/env python3
"""Read-only check: docs exist, and every file they name actually exists in the repo."""
import os
import re
import sys
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
DOCS = ["README.md", "docs/PRD.md", "docs/HANDOFF.md"]
# texts that name a *shape* rather than a file
NON_FILES = {"wiki-pageid-sha1-schemahash-width.json"}
# Paths the docs cite that live in a DIFFERENT repository. They can only be checked where that repo
# happens to be checked out, so they are optional: point SKILLS_DIR at a local Wikipedia-AI-Skills
# clone to verify them. Hard-coding the owner's home directory here once made this check pass on one
# machine only and fail in CI (found 2026-10-14) — a repo check must not depend on whose laptop it
# runs on.
SKILLS_DIR = os.environ.get("SKILLS_DIR", "")
UPSTREAM = {"scripts/parse-file-refs.py": os.path.join(SKILLS_DIR, "commons-file-resolution/scripts/parse-file-refs.py")
            if SKILLS_DIR else ""}
# …and the *local* skills tree (`~/.pi/agent/skills`), which is where the video pipeline's scripts live. Same
# rule as SKILLS_DIR above: empty by default so this check never depends on whose laptop it runs on, and
# verifiable when the caller points at the tree:
#
#     LOCAL_SKILLS_DIR=~/.pi/agent/skills python3 tests/check_docs.py
#
# Without it the two entries report as "not verified here (upstream)" rather than MISSING, because a doc that
# cites another tree is not citing a broken path in this one.
LOCAL_SKILLS_DIR = os.environ.get("LOCAL_SKILLS_DIR", "")
for _tok, _rel in (("scripts/beat-drift.mjs", "narrated-tutorial-video/scripts/beat-drift.mjs"),
                   ("scripts/frame-probe.py", "narrated-tutorial-video/scripts/frame-probe.py")):
    UPSTREAM[_tok] = os.path.join(LOCAL_SKILLS_DIR, _rel) if LOCAL_SKILLS_DIR else ""
missing = []
unverified = []
for d in DOCS:
    p = os.path.join(ROOT, d)
    if not os.path.exists(p):
        missing.append(d)
        continue
    print(f"{d:12s} {os.path.getsize(p):>6d} bytes  {len(open(p).read().splitlines()):>4d} lines")

def resolves(tok: str) -> Optional[str]:
    """Return the path a doc reference resolves to, or None."""
    if tok in UPSTREAM:
        return UPSTREAM[tok] if UPSTREAM[tok] and os.path.exists(UPSTREAM[tok]) else None
    cand = os.path.join(ROOT, tok)
    if os.path.exists(cand):
        return cand
    if "/" not in tok:
        for sub in ("tests", "tools", "docs", "research", "static"):
            if os.path.exists(os.path.join(ROOT, sub, tok)):
                return os.path.join(ROOT, sub, tok)
    return None


# every backticked filename that looks like a repo file must resolve
pat = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|mjs|js|md|html|json|sh|txt))`")
seen = set()
for d in DOCS:
    for tok in pat.findall(open(os.path.join(ROOT, d)).read()):
        if tok in seen or tok in NON_FILES or tok.startswith(("references/", "tests/")):
            continue
        seen.add(tok)
        if tok in UPSTREAM:
            # an upstream file: verifiable only if SKILLS_DIR is set; never a failure here
            if UPSTREAM[tok] and os.path.exists(UPSTREAM[tok]):
                print(f"  ok  {tok}  (upstream, via SKILLS_DIR)")
            else:
                unverified.append(tok)
            continue
        if resolves(tok) is None:
            missing.append(f"{d} -> {tok}")
print(f"\nfilenames referenced: {len(seen)}")
for tok in sorted(seen):
    if tok in UPSTREAM:
        continue
    print(f"  {'ok ' if resolves(tok) else 'MISSING'} {tok}")
if unverified:
    print(f"\nnot verified here ({len(unverified)}) — upstream, set SKILLS_DIR (or LOCAL_SKILLS_DIR for the "
          f"local skills tree) to check: {sorted(unverified)}")
print("\nMISSING:", missing if missing else "none")
sys.exit(1 if missing else 0)
