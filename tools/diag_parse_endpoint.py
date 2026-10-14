#!/usr/bin/env python3
"""Exercise the /api/parse endpoint with every accepted input form (Commons API only,
no scoring calls, so it costs none of the hourly LLM budget)."""
import json, urllib.parse, urllib.request

API = "http://127.0.0.1:8765"
UA = "WikiPortraitsEval/0.4 (test)"
TITLE = "Hila Meir at Deauville American Film festival.jpg"
TITLE2 = "Emma Čechová 22 Czechia 20250625 (1).jpg"


def commons(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
        dict(params, format="json", formatversion="2"))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def post(text):
    req = urllib.request.Request(API + "/api/parse", data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


info = commons({"action": "query", "titles": f"File:{TITLE}", "prop": "imageinfo",
                "iiprop": "url", "iiurlwidth": 640})
ii = info["query"]["pages"][0]["imageinfo"][0]
upload_url = ii["url"].split("?")[0]
thumb_url = ii["thumburl"].split("?")[0]
thumb_new_host = thumb_url.replace("upload.wikimedia.org", "thumb.wikimedia.org")
dash = urllib.parse.quote(TITLE.replace(" ", "_"))
enc2 = urllib.parse.quote(TITLE2.replace(" ", "_"))

cases = {
    "bare filename": TITLE,
    "File: prefix": f"File:{TITLE}",
    "lowercase file:": f"file:{TITLE}",
    "underscores": f"File:{TITLE.replace(' ', '_')}",
    "commons wiki URL": f"https://commons.wikimedia.org/wiki/File:{dash}",
    "commons URL, no scheme": f"commons.wikimedia.org/wiki/File:{dash}",
    "commons URL with query+frag": f"https://commons.wikimedia.org/wiki/File:{dash}?uselang=en#mediaviewer",
    "index.php?title=": f"https://commons.wikimedia.org/w/index.php?title=File:{dash}&action=view",
    "Special:FilePath": f"https://commons.wikimedia.org/wiki/Special:FilePath/{dash}",
    "Special:Redirect/file": f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{dash}",
    "upload URL (original)": upload_url,
    "upload URL (thumb)": thumb_url,
    "thumb.wikimedia.org": thumb_new_host,
    "wikitext embed": f"[[File:{TITLE}|thumb|A caption with a | pipe]]",
    "percent-encoded title": f"File:{enc2}",
    "bulleted + numbered + commas": f"- {TITLE}\n1. File:{TITLE2}\nFoo.jpg, Bar.jpg",
    "duplicate of first": f"File:{TITLE}",
    "m.wikimedia host": f"https://commons.m.wikimedia.org/wiki/File:{dash}",
    "other media (SVG)": "File:Example.svg",
    "category (unsupported)": "Category:WikiPortraits",
    "non-Wikimedia URL": "https://example.com/photos/foo.jpg",
    "garbage line": "please score my photos",
}

text = "\n".join(cases.values())
out = post(text)
print(f"units sent: {len(cases)} · parsed: {out['n']} · warnings: {len(out['warnings'])}\n")
seen = {}
for it in out["items"]:
    seen[it["title"]] = it

for label, value in cases.items():
    hit = None
    for it in out["items"]:
        if value.split("\n")[0].strip().lstrip("-* ").strip() in (it["raw"] or "") or it["raw"] == value:
            hit = it
            break
    if hit is None:
        for it in out["items"]:
            if value.split(",")[0].split()[-1] in (it["raw"] or ""):
                hit = it
                break
    if hit:
        print(f"  {label:28s} -> {hit['title'][:60]:60s} ok={hit['ok']} "
              f"{hit['orig_w']}x{hit['orig_h']}" + (f" ERR:{hit['error']}" if not hit["ok"] else ""))
    else:
        print(f"  {label:28s} -> (no item returned)")

print("\nwarnings:")
for w in out["warnings"]:
    print(f"  skipped {w['raw']!r}: {w['reason']}")
print("\ndedupe check: unique titles:", len({i['title'] for i in out['items']}), "items:", len(out['items']))
