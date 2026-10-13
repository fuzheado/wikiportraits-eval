#!/usr/bin/env python3
"""Measure the current per-call latency of llm-clef-flash (2 calls: 1 text, 1 image).

The pipeline run on 2026-10-13 12:45 UTC showed ~29 s per call, against ~0.4-0.6 s for
warm calls earlier the same day. This separates "the endpoint got slower" from
"one slow pod / cold start" by timing a text-only question and a single image question.
"""
import base64
import json
import time
import urllib.parse
import urllib.request

UA = "HermesAgent/1.0 (https://en.wikipedia.org/wiki/User:Fuzheado) WikiPortraitsEval/0.4"
EP = "https://api.wikimedia.org/service/lw/inference/v1/models/llm-clef-flash:predict"
TEXT_QUESTIONS = {"q": {"type": "noul", "text": "Is this a photograph of a person?"}}


def call(payload, label):
    req = urllib.request.Request(EP, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.load(r)
        dt = time.time() - t
        print(f"{label:26s} HTTP {r.status} in {dt:6.2f}s")
        return dt, d
    except urllib.error.HTTPError as e:
        dt = time.time() - t
        print(f"{label:26s} HTTP {e.code} in {dt:6.2f}s  {e.read()[:120]}")
        return dt, None


print("--- text only (no image) ---")
call({"model": "llm-clef-flash", "state": "", "questions": TEXT_QUESTIONS}, "text question 1")
call({"model": "llm-clef-flash", "state": "", "questions": TEXT_QUESTIONS}, "text question 2")

print("\n--- one image, 640px thumbnail ---")
q = urllib.parse.urlencode({"action": "query", "format": "json", "formatversion": "2",
                            "titles": "File:Hila Meir at Deauville American Film festival.jpg",
                            "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 640})
req = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + q, headers={"User-Agent": UA})
with urllib.request.urlopen(req, timeout=60) as r:
    thumb = json.load(r)["query"]["pages"][0]["imageinfo"][0]["thumburl"]
img = urllib.request.urlopen(urllib.request.Request(thumb, headers={"User-Agent": UA}),
                             timeout=60).read()
print(f"thumbnail: {len(img)} bytes from {thumb[:80]}")
b64 = base64.b64encode(img).decode()
call({"model": "llm-clef-flash", "state": "", "questions": TEXT_QUESTIONS, "images": [b64]},
     "image question 1")
call({"model": "llm-clef-flash", "state": "", "questions": TEXT_QUESTIONS, "images": [b64]},
     "image question 2")
