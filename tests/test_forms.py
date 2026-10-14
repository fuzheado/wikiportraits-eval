#!/usr/bin/env python3
"""Offline unit test for input parsing: every accepted form must map to the same File: title."""
import os
import sys
import urllib.parse

# this file lives in tests/; the app's modules and data files are at the repo root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wiclient

NAME = "Hila Meir at Deauville American Film festival.jpg"
T = "File:" + NAME
U = urllib.parse.quote(NAME.replace(" ", "_"))
UND = NAME.replace(" ", "_")

GOOD = {
    "bare filename": NAME,
    "File: prefix": T,
    "lowercase file:": "file:" + NAME,
    "underscores": f"File:{UND}",
    "wikitext embed": f"[[File:{NAME}|thumb|A caption with a | pipe]]",
    "commons /wiki/ URL": f"https://commons.wikimedia.org/wiki/{T.replace(' ', '_')}",
    "commons URL no scheme": f"commons.wikimedia.org/wiki/{T.replace(' ', '_')}",
    "protocol-relative": f"//commons.wikimedia.org/wiki/{T.replace(' ', '_')}",
    "percent-encoded": f"https://commons.wikimedia.org/wiki/File:{U}",
    "with query+fragment": f"https://commons.wikimedia.org/wiki/{T.replace(' ', '_')}?uselang=en#media",
    "index.php?title=": f"https://commons.wikimedia.org/w/index.php?title={U}&action=view",
    "Special:FilePath": f"https://commons.wikimedia.org/wiki/Special:FilePath/{U}",
    "Special:Redirect/file": f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{U}",
    "Special:FilePath bare": f"Special:FilePath/{U}",
    "upload original URL": f"https://upload.wikimedia.org/wikipedia/commons/d/dc/{U}",
    "upload thumb URL": f"https://upload.wikimedia.org/wikipedia/commons/thumb/d/dc/{U}/640px-{U}",
    "thumb.wikimedia.org": f"https://thumb.wikimedia.org/wikipedia/commons/thumb/d/dc/{U}/960px-{U}",
    "mobile host": f"https://commons.m.wikimedia.org/wiki/{T.replace(' ', '_')}",
    "bullet + spaces": f"   -   {T}   ",
    "numbered": f"1. {T}",
    "quoted": f'"{T}"',
    "SVG file": "File:Example.svg",
    "PDF file": "File:Example.pdf",
}
BAD = {
    "category": "Category:WikiPortraits",
    "template": "Template:Infobox",
    "non-Wikimedia URL": "https://example.com/photos/foo.jpg",
    "plain sentence": "please score my photos",
    "empty File:": "File:",
}

fails = 0
for label, raw in GOOD.items():
    parsed = wiclient.parse_entries(raw)
    got = parsed["items"][0]["title"] if parsed["items"] else None
    expected = "File:Example.svg" if "SVG" in label else ("File:Example.pdf" if "PDF" in label else T)
    ok = got == expected
    fails += 0 if ok else 1
    print(f"{'ok ' if ok else 'FAIL'} {label:24s} -> {got}")

print()
for label, raw in BAD.items():
    parsed = wiclient.parse_entries(raw)
    warned = bool(parsed["warnings"]) and not parsed["items"]
    fails += 0 if warned else 1
    print(f"{'ok ' if warned else 'FAIL'} {label:24s} -> "
          f"{(parsed['warnings'][0]['reason'] if parsed['warnings'] else 'ACCEPTED?!')}")

# multi-line, separators, dedupe
multi = wiclient.parse_entries(f"{T}\n{U}\n- {T}\nexample.jpg, File:Example.svg; File:Example.svg")
print(f"\nmulti-input: {len(multi['items'])} unique items from 6 units "
      f"(expect 3: {[i['title'] for i in multi['items']]})")
fails += 0 if len(multi["items"]) == 3 else 1

print("\nFAILURES:", fails)
