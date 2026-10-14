#!/usr/bin/env python3
"""Regression test: commas and semicolons inside file names must survive parsing.

Reported 2026-10-12: "File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"
was split on the comma into an "incomplete File: title" plus a bogus "File:Cannes 2025).jpg".
"""
import os
import sys

# this file lives in tests/; the app's modules and data files are at the repo root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wiclient

COMMA_FILE = "File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"
COMMA_NAME = "MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"
SEMI_FILE = "File:Willy Hernangómez 14 FC Barcelona (basketball) Euroleague 20251120 (7).jpg"

CASES = [
    # (input, expected titles)
    (COMMA_FILE, [COMMA_FILE]),
    (f"  -  {COMMA_FILE}  ", [COMMA_FILE]),
    (COMMA_NAME, [COMMA_FILE]),
    (f"https://commons.wikimedia.org/wiki/File:{COMMA_NAME.replace(' ', '_')}", [COMMA_FILE]),
    (f"[[File:{COMMA_NAME}|thumb|Sun, sun, sun]]", [COMMA_FILE]),
    (f"{COMMA_FILE}\n{SEMI_FILE}", [COMMA_FILE, SEMI_FILE]),
    (f"{COMMA_FILE}, {SEMI_FILE}", [COMMA_FILE, SEMI_FILE]),          # prefix after comma
    ("Foo.jpg, Bar.jpg", ["File:Foo.jpg", "File:Bar.jpg"]),           # bare list still splits
    ("Foo.jpg;Bar.jpg", ["File:Foo.jpg", "File:Bar.jpg"]),
    ("File:Example.svg, File:Example.pdf", ["File:Example.svg", "File:Example.pdf"]),
    ("https://commons.wikimedia.org/wiki/File:A.jpg, https://commons.wikimedia.org/wiki/File:B.jpg",
     ["File:A.jpg", "File:B.jpg"]),
    (COMMA_FILE, [COMMA_FILE]),                                       # dedupe of repeats
]

fails = 0
for text, expected in CASES:
    got = [i["title"] for i in wiclient.parse_entries(text)["items"]]
    ok = got == expected
    fails += 0 if ok else 1
    shown = text.replace("\n", " ⏎ ")[:64]
    print(f"{'ok  ' if ok else 'FAIL'} {shown:64s} -> {got}" +
          ("" if ok else f"   expected {expected}"))

print("\nwarnings for the comma file:", wiclient.parse_entries(COMMA_FILE)["warnings"])
print("FAILURES:", fails)

# live check: does it exist on Commons, and does the scoreable thumbnail resolve?
res = wiclient.resolve([COMMA_FILE])
info = list(res.values())[0]
print("\nCommons resolve:", {k: info.get(k) for k in ("ok", "pageid", "orig_w", "orig_h", "mime", "error")})
print("thumbnail URL:", (info.get("thumburl") or "")[:120])
