#!/usr/bin/env python3
"""Offline tests for thumbnail-width handling (HANDOFF §12). Costs nothing: no network.

Each assertion encodes a measured fact about Commons' thumbnail ladder rather than a convention:
Commons quantises `iiurlwidth` UPWARD to a `$wgThumbnailSteps` step, and `imageinfo.thumbwidth`
echoes the request instead of describing the file (T388792). They exist so the plumbing cannot
silently drift back to "we ask for 640, we report 640, we download 960".
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
import wiclient

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got!r}" + ("" if ok else f"  want {want!r}"))
    if not ok:
        FAIL.append(name)


# --- the request must name a real ladder step ------------------------------------------------
check("THUMB_WIDTH is a ladder step", wiclient.THUMB_WIDTH in wiclient.THUMB_LADDER, True)
check("640 is NOT a ladder step (the original bug)", 640 in wiclient.THUMB_LADDER, False)
check("ladder is ascending", list(wiclient.THUMB_LADDER) == sorted(wiclient.THUMB_LADDER), True)
check("encoder normalisation width is 640", wiclient.MODEL_MAX_WIDTH, 640)

# --- reading the served width out of a thumburl ----------------------------------------------
check("plain thumb", wiclient.served_width_from_url(
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Foo.jpg/960px-Foo.jpg"), 960)
check("multi-page PDF", wiclient.served_width_from_url(
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/27/D.pdf/page1-960px-D.pdf.jpg"), 960)
check("video keyframe", wiclient.served_width_from_url(
    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/E.webm/960px--E.webm.jpg"), 960)
check("page number is not the width", wiclient.served_width_from_url(
    "https://upload.wikimedia.org/wikipedia/commons/thumb/2/27/D.pdf/page12-500px-D.pdf.jpg"), 500)
check("query string is ignored", wiclient.served_width_from_url(
    "https://thumb.wikimedia.org/x/y/330px-Z.jpg?utm_source=commons"), 330)
# a small original is served as `url` (no /Npx- segment): None, not an exception
check("original with no size segment", wiclient.served_width_from_url(
    "https://upload.wikimedia.org/wikipedia/commons/c/c7/Z.png?utm_content=thumbnail_unscaled"),
    None)
check("no url at all", wiclient.served_width_from_url(None), None)

# --- the cache key records the SERVED width, so ladder aliasing cannot split the cache --------
with tempfile.TemporaryDirectory() as d:
    core.CACHE_DIR = d                      # cache_path() reads this at call time
    p960 = core.cache_path(1, "abc", "commons.wikimedia.org", 960)
    check("served width lands in the filename", p960.endswith("-960.json"), True)
    check("a different served width is a different key",
          core.cache_path(1, "abc", "commons.wikimedia.org", 500) != p960, True)
    check("no served width falls back to the request",
          core.cache_path(1, "abc", "commons.wikimedia.org", None)
          .endswith(f"-{wiclient.THUMB_WIDTH}.json"), True)
    # the 640 request and the 960 request hit the same asset, so they must share one entry
    url_for_a_640_request = "https://thumb.wikimedia.org/x/y/960px-Foo.jpg"
    check("aliased requests share one key",
          core.cache_path(1, "abc", "commons.wikimedia.org",
                          wiclient.served_width_from_url(url_for_a_640_request)) == p960, True)

    core.cache_put(7, "sha", {"result": {"http": 200}, "sent_px": "960x1440"},
                   "commons.wikimedia.org", 960)
    check("cache round-trips on the served width",
          (core.cache_get(7, "sha", "commons.wikimedia.org", 960) or {}).get("sent_px"), "960x1440")
    check("a different served width misses",
          core.cache_get(7, "sha", "commons.wikimedia.org", 500), None)

# --- the About payload explains the ladder instead of implying the request is the truth ------
ab = core.about_payload()
req = ab.get("request") or {}
check("about.request.thumb_width is the requested step",
      req.get("thumb_width"), wiclient.THUMB_WIDTH)
check("about marks it as a ladder step", req.get("thumb_width_is_ladder_step"), True)
check("about carries the ladder", req.get("thumb_ladder"), list(wiclient.THUMB_LADDER))
check("about carries the encoder normalisation width", req.get("model_max_width"), 640)
about_html = " ".join(f"{s.get('title') or ''} {s.get('html') or ''}"
                      for s in ab.get("sections") or [])
check("about names the ladder config", "$wgThumbnailSteps" in about_html, True)
check("about explains served_w", "served_w" in about_html, True)
check("about explains sent_px", "sent_px" in about_html, True)

print()
print(f"FAILURES: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
