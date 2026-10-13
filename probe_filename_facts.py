#!/usr/bin/env python3
"""Live probes behind the proposed commons-file-resolution addition.

Facts under test (2026-10-13, all against live Commons):
  A. Special:FilePath with and without the File: prefix
  B. comma-separated titles= is a real separator in the Action API -> breaks filenames with commas
  C. illegal title characters
  D. namespace aliases from other languages on Commons
  E. what a title with a pipe/encoded pipe does
"""
import json, urllib.error, urllib.parse, urllib.request

UA = "HermesAgent/1.0 (https://en.wikipedia.org/wiki/User:Fuzheado) CommonsFileRefProbe/1.0"
API = "https://commons.wikimedia.org/w/api.php"
COMMA = "File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"
OTHER = "File:Example.jpg"


def api(**params):
    params.setdefault("format", "json")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def head(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    op = urllib.request.build_opener(urllib.request.HTTPRedirectHandler) if False else None
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        with op.open(req, timeout=60) as r:
            return r.status, r.headers.get("Location") or r.headers.get("location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location") or e.headers.get("location")


print("A. Special:FilePath forms (HEAD, no redirect follow)")
for form in (f"https://commons.wikimedia.org/wiki/Special:FilePath/File:{urllib.parse.quote(OTHER)}",
             f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(OTHER)}",
             f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(COMMA)}",
             f"https://commons.wikimedia.org/wiki/Special:FilePath/File:{urllib.parse.quote(COMMA)}?width=640"):
    code, loc = head(form)
    print(f"   {code}  {form[:110]}")
    print(f"        -> {(loc or '')[:120]}")

print("\nB. titles= separator semantics (comma vs pipe)")
for label, titles in (("pipe-separated", f"{COMMA}|{OTHER}"),
                      ("comma-separated", f"{COMMA},{OTHER}"),
                      ("comma inside one title", COMMA)):
    status, d = api(action="query", titles=titles, prop="imageinfo", iiprop="url", redirects=1,
                    formatversion=2)
    pages = [p.get("title") for p in d.get("query", {}).get("pages", [])]
    print(f"   {label:24s} -> HTTP {status}, {len(pages)} page(s): {pages}")

print("\nC/D/E. illegal characters and namespace aliases")
for label, title in (("pipe (raw)", "File:Example.jpg|thumb"),
                     ("percent-encoded pipe", "File:Example%7C.jpg"),
                     ("hash", "File:Example#1.jpg") if False else ("hash", "File:Example#1.jpg"),
                     ("bracket", "File:Exa[mple].jpg"),
                     ("brace", "File:Exa{mple}.jpg"),
                     ("angle", "File:Example<1>.jpg"),
                     ("German Datei:", "Datei:Example.jpg"),
                     ("French Fichier:", "Fichier:Example.jpg"),
                     ("Spanish Archivo:", "Archivo:Example.jpg"),
                     ("underscore+case", "file:example.jpg")):
    status, d = api(action="query", titles=title, prop="imageinfo", iiprop="url", formatversion=2)
    q = d.get("query", {})
    pages = q.get("pages", [])
    norm = q.get("normalized", [])
    err = d.get("error", {})
    detail = (pages[0].get("title") if pages else None) or err.get("info") or "?"
    print(f"   {label:22s} HTTP {status} -> {detail}  normalized={norm}")
