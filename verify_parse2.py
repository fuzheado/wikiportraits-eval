#!/usr/bin/env python3
"""Live verification of the parse/resolve rewrite (following the WAIS skills).

Checks, against the running app:
  1. the reported comma case, in three input forms
  2. a file that lives on another wiki (en.wikipedia.org), by /wiki/File: URL
     and by upload.wikimedia.org/wikipedia/en/... URL  -> SOP 5 cross-wiki
  3. a URL with '#fragment' -> must warn, not silently truncate
  4. a title with an illegal character -> must warn
  5. dedupe across duplicate forms
"""
import json, urllib.parse, urllib.request

UA = "HermesAgent/1.0 (https://en.wikipedia.org/wiki/User:Fuzheado) WikiPortraitsEval/0.4"
BASE = "https://shopping-wait-confidentiality-occasionally.trycloudflare.com"
API = "https://shopping-wait-confidentiality-occasionally.trycloudflare.com"


def api(host, **params):
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# --- find a file that exists on en.wikipedia but not on Commons ------------------
candidates = ["File:Example.jpg", "File:Wiki.png", "File:Test.jpg", "File:Commons-logo.svg"]
enwiki_file = None
for c in candidates:
    d = api("en.wikipedia.org", action="query", titles=c, prop="imageinfo", iiprop="url")
    p = (d.get("query", {}).get("pages") or [{}])[0]
    if "missing" not in p:
        dc = api("commons.wikimedia.org", action="query", titles=c, prop="imageinfo", iiprop="url")
        pc = (dc.get("query", {}).get("pages") or [{}])[0]
        on_commons = "missing" not in pc
        print(f"probe {c:22s} enwiki={'yes' if True else ''} commons={'yes' if on_commons else 'no'}")
        if not on_commons:
            enwiki_file = p["title"]
            break
if not enwiki_file:
    enwiki_file = "File:Example.jpg"
print("enwiki-only file chosen:", enwiki_file)

COMMA = "File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"
url_comma = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(COMMA.replace(" ", "_"))
host_url_comma = ("https://upload.wikimedia.org/wikipedia/commons/8/8f/"
                  + urllib.parse.quote(COMMA[len("File:"):].replace(" ", "_")))
enwiki_wiki_url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(enwiki_file.replace(" ", "_"))
enwiki_upload_url = ("https://upload.wikimedia.org/wikipedia/en/8/8f/"
                     + urllib.parse.quote(enwiki_file[len("File:"):].replace(" ", "_")))

text = "\n".join([
    COMMA,
    "File:Example.jpg",
    url_comma,
    host_url_comma,
    enwiki_wiki_url,
    enwiki_upload_url,
    "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(COMMA.replace(" ", "_")) + "#/media/File:x.jpg",
    "File:Bad|Name.jpg",
])

req = urllib.request.Request(BASE + "/api/parse", method="POST",
                             data=json.dumps({"text": text}).encode(),
                             headers={"Content-Type": "application/json", "User-Agent": UA})
with urllib.request.urlopen(req, timeout=180) as r:
    res = json.load(r)

print(f"\nitems: {res['n']}   warnings: {len(res['warnings'])}")
for it in res["items"]:
    print(f"  ok={str(it['ok']):5s} wiki={it['wiki']:24s} {it['title'][:64]:64s} "
          f"{it['orig_w']}x{it['orig_h']}")
for w in res["warnings"]:
    print(f"  WARN  {w['raw'][:52]:54s} {w['reason'][:90]}")

wikis = {it["wiki"] for it in res["items"] if it["ok"]}
print("\nhosts that resolved:", sorted(wikis))
assert any("wikipedia.org" in w and "commons" not in w for w in wikis), "cross-wiki resolution failed"
