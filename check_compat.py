#!/usr/bin/env python3
"""Offline lint: does this code still run on the Python version README.md advertises (3.9)?

Why a lint and not just CI: the 3.9 job in CI is the real gate, but it only speaks *after* a push.
This catches the common breakages locally in milliseconds. It found its own reason to exist — the
repo claimed 3.9 support while `pdfmin.py` used `-> str | None` (PEP 604), which raises
`TypeError: unsupported operand type(s) for |` at *class-definition* time on 3.9 and took out every
test that imported it (2026-10-14).

What it flags
  * `X | Y` inside an **annotation** (function args/returns, variable annotations) — PEP 604, 3.10+.
    Deliberately NOT flagged: `a | b` as an expression, because dict and set union are 3.9-safe
    (PEP 584 landed in 3.9) and this repo uses them heavily. Annotation context is what matters.
  * `match` statements (3.10+) and `except*` (3.11+).
  * A module that opts out with `from __future__ import annotations` is skipped: that makes every
    annotation a string and is a legitimate 3.9-safe style.

It uses `getattr(ast, ...)` for the newer node types so this file itself parses on 3.9.
"""
import ast
import os
import sys

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


findings = []
scanned = 0
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in sorted(filenames):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(dirpath, fn)
        rel = os.path.relpath(path, ROOT)
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
if findings:
    for rel, lineno, what in sorted(findings):
        print(f"  FAIL {rel}:{lineno}  {what}")
print(f"\nFAILURES: {len(findings)}")
sys.exit(1 if findings else 0)
