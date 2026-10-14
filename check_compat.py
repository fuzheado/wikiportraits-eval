#!/usr/bin/env python3
"""Offline lint: does this code still run on the Python version README.md advertises (3.9)?

Why a lint and not just CI: the 3.9 job in CI is the real gate, but it only speaks *after* a push.
This catches the common breakages locally in milliseconds. It found its own reason to exist — the
repo claimed 3.9 support while `pdfmin.py` used `-> str | None` (PEP 604), which raises
`TypeError: unsupported operand type(s) for |` at *class-definition* time on 3.9 and took out every
test that imported it (2026-10-14).

What it flags
  * **Anything the floor interpreter itself rejects** (see below) — the strongest check available.
  * `X | Y` inside an **annotation** (function args/returns, variable annotations) — PEP 604, 3.10+.
    Deliberately NOT flagged: `a | b` as an expression, because dict and set union are 3.9-safe
    (PEP 584 landed in 3.9) and this repo uses them heavily. Annotation context is what matters.
  * `match` statements (3.10+) and `except*` (3.11+).
  * A module that opts out with `from __future__ import annotations` is skipped: that makes every
    annotation a string and is a legitimate 3.9-safe style.

The floor-interpreter parse is here because the AST checks above **cannot see f-strings at all**:
`ast.parse(..., feature_version=(3, 9))` does NOT enforce pre-3.12 f-string rules (verified — the
3.12+ tokenizer accepts PEP 701 unconditionally). So `f"...{x\n...}"` parses happily on a modern
interpreter while 3.9 raises `SyntaxError: EOL while scanning string literal`. That shipped to a red
CI run twice on 2026-10-14. When a real `python3.9` is on PATH this file is parsed with it; when it
is not, the gap is announced rather than silently skipped.

It uses `getattr(ast, ...)` for the newer node types so this file itself parses on 3.9.
"""
import ast
import json
import os
import shutil
import subprocess
import sys

FLOOR = "3.9"
# Runs under the FLOOR interpreter: parse every file, report the failures as JSON.
_FLOOR_PROBE = (
    "import ast, json, sys\n"
    "bad = []\n"
    "for p in sys.argv[1:]:\n"
    "    try:\n"
    "        ast.parse(open(p, encoding='utf-8').read())\n"
    "    except SyntaxError as e:\n"
    "        bad.append([p, e.lineno, e.msg])\n"
    "print(json.dumps(bad))\n"
)

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"node_modules", "cache", ".git", "__pycache__", ".venv-pdf", ".venv", ".playwright-cli",
             "serve", ".githooks"}

MATCH = getattr(ast, "Match", None)
TRYSTAR = getattr(ast, "TryStar", None)


def annotations(tree):
    """Yield every annotation expression in the module (and so its subtree)."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            for arg in list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs):
                if arg.annotation is not None:
                    yield arg.annotation
            for extra in (a.vararg, a.kwarg):
                if extra is not None and extra.annotation is not None:
                    yield extra.annotation
            if n.returns is not None:
                yield n.returns
        elif isinstance(n, ast.AnnAssign) and n.annotation is not None:
            yield n.annotation


def floor_failures(pairs):
    """Parse every file with a REAL interpreter at the advertised floor, if one is installed.

    This is the only reliable check for the f-string class of breakage. `ast.parse(... ,
    feature_version=(3, 9))` does NOT enforce pre-3.12 f-string rules — the 3.12+ tokenizer accepts
    PEP 701 unconditionally, verified — so `f"...{x\n...}"` and nested same-delimiter quotes parse
    happily on a modern interpreter while 3.9 rejects them. Two red CI runs on 2026-10-14 came from
    exactly that, so the check now runs the real thing when it can.

    Returns [] and prints a warning when no floor interpreter exists, so the gap is announced
    instead of being silently skipped.
    """
    exe = shutil.which(f"python{FLOOR}") or shutil.which(f"python{FLOOR[0]}{FLOOR[2]}")
    if not exe:
        print(f"NOTE: no python{FLOOR} on PATH — f-string and other token-level syntax is NOT checked "
              f"here. CI runs the offline suite on {FLOOR} and will catch it.")
        return []
    try:
        r = subprocess.run([exe, "-c", _FLOOR_PROBE] + [p for p, _ in pairs],
                           capture_output=True, text=True, timeout=180)
        bad = json.loads(r.stdout or "[]")
    except Exception as e:                                     # noqa: BLE001 - report, don't crash
        print(f"NOTE: the python{FLOOR} parse check did not run ({type(e).__name__}: {e})")
        return []
    rel_of = {os.path.abspath(p): rel for p, rel in pairs}
    return [(rel_of.get(os.path.abspath(p), p), ln, f"python{FLOOR} rejects this: {msg}")
            for p, ln, msg in bad]


findings = []
py_files = []
scanned = 0
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in sorted(filenames):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(dirpath, fn)
        rel = os.path.relpath(path, ROOT)
        py_files.append((path, rel))
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError as e:
            findings.append((rel, e.lineno, f"SyntaxError: {e.msg}"))
            continue
        scanned += 1
        lazy = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                   and any(a.name == "annotations" for a in n.names) for n in tree.body)
        for n in ast.walk(tree):
            if MATCH is not None and isinstance(n, MATCH):
                findings.append((rel, n.lineno, "match statement (needs 3.10)"))
            if TRYSTAR is not None and isinstance(n, TRYSTAR):
                findings.append((rel, n.lineno, "except* (needs 3.11)"))
        if lazy:
            continue
        for ann in annotations(tree):
            for n in ast.walk(ann):
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                    findings.append((rel, n.lineno, "`X | Y` in an annotation (PEP 604, needs 3.10)"
                                                     " — use Optional[X] or `from __future__ import"
                                                     " annotations`"))

print(f"scanned {scanned} python files for syntax newer than 3.9")
findings = floor_failures(py_files) + findings
if findings:
    for rel, lineno, what in sorted(findings):
        print(f"  FAIL {rel}:{lineno}  {what}")
print(f"\nFAILURES: {len(findings)}")
sys.exit(1 if findings else 0)
