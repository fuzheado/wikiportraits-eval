#!/usr/bin/env python3
"""Probe the Commons thumbnail ladder: what does `iiurlwidth=N` actually serve?

Answers, with measurements, the trap recorded in HANDOFF §4 ("requested 640 -> served
960"), and gives the repo a canary that fails loudly if Wikimedia changes the ladder.

Three facts it establishes, all of which the app depends on:

  1. Requests are quantized UPWARD to `$wgThumbnailSteps`. 640 is not a step, so
     `iiurlwidth=640` downloads the **960** asset.
  2. `imageinfo.thumbwidth` echoes the **request**, not the served file (T388792).
     The truth is the `/Npx-` segment of `thumburl`, and the decoded bytes.
  3. Requests that land in the same bucket return the **same thumburl** (640, 800
     and 960 all -> `960px-...`), so asking for 640 costs exactly what 960 costs.

Cache-first (standing policy): every API response is stored raw under
`cache/ladder/`, keyed by URL, so re-runs are free and deterministic. Thumb bytes are
cached too, so a second run touches the network zero times.

Usage:  python3 probe_thumb_ladder.py [File:Title ...]
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import wiclient  # noqa: E402  (UA, image_size, fetch_bytes — reuse, do not re-implement)

UA = wiclient.UA
CACHE = os.path.join(REPO, "cache", "ladder")
API = "https://commons.wikimedia.org/w/api.php"
PAUSE = 1.1                      # >= 1 s between live requests, per the etiquette rules

# A 4024x6048 original, 510 KB at the 960 bucket — large enough to exercise the ladder.
DEFAULT_TITLES = ["File:CedricTheEntertainer-byPhilipRomano.jpg"]
WIDTHS = [120, 250, 320, 500, 600, 640, 800, 960, 1024, 1280]


def _cache_path(kind, url, ext):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, hashlib.sha1((kind + " " + url).encode()).hexdigest() + ext)


def cached_json(url):
    """GET url once; cache the raw body by URL. Returns parsed JSON or None."""
    path = _cache_path("API", url, ".json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)["body"]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    except Exception as e:                                      # noqa: BLE001
        print(f"  request failed: {e}", file=sys.stderr)
        return None
    if status == 429:
        wait = 30
        print(f"  429 — backing off {wait}s", file=sys.stderr)
        time.sleep(wait)
        return cached_json(url)
    if status != 200:                                           # check status BEFORE parsing
        print(f"  HTTP {status} (not parsed): {body[:120]!r}", file=sys.stderr)
        return None
    try:
        data = json.loads(body)
    except ValueError:
        print(f"  HTTP 200 but not JSON — refusing to parse: {body[:120]!r}", file=sys.stderr)
        return None
    with open(path, "w") as fh:
        json.dump({"url": url, "body": data}, fh)
    time.sleep(PAUSE)
    return data


def imageinfo(title, width):
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": "2", "redirects": "1",
        "prop": "imageinfo", "iiprop": "url|size|mime|sha1",
        "titles": title, "iiurlwidth": str(width),
    })
    d = cached_json(f"{API}?{q}")
    if not d:
        return None
    page = (d.get("query", {}).get("pages") or [{}])[0]
    if page.get("missing"):
        return {"missing": True, "title": page.get("title")}
    ii = (page.get("imageinfo") or [{}])[0]
    ii["_title"] = page.get("title")
    return ii


# read the served width with the app's own parser, so the probe cannot drift from the app
def served_width_from_url(url):
    return wiclient.served_width_from_url(url)


def bytes_for(url):
    """(nbytes, decoded_w, decoded_h) for a thumburl, cached by URL. Fetches once."""
    if not url:
        return None
    path = _cache_path("BYTES", url, ".bin")
    if os.path.exists(path):
        d = open(path, "rb").read()
    else:
        try:
            d = wiclient.fetch_bytes(url)
        except Exception as e:                                  # noqa: BLE001
            print(f"  fetch failed: {e}", file=sys.stderr)
            return None
        with open(path, "wb") as fh:
            fh.write(d)
        time.sleep(PAUSE)
    size = wiclient.image_size(d)
    return (len(d), size[0] if size else None, size[1] if size else None)


def main():
    titles = [a for a in sys.argv[1:] if not a.startswith("-")] or DEFAULT_TITLES
    print(f"Commons thumbnail ladder probe · {len(titles)} file(s) × {len(WIDTHS)} widths")
    print(f"raw responses cached in {os.path.relpath(CACHE, REPO)}/ (re-runs are free)\n")
    for title in titles:
        print(f"== {title}")
        print(f"{'asked':>6} {'thumbwidth':>10} {'url Npx':>8} {'decoded':>11} {'bytes':>9} "
              f"{'B/px':>6}  verdict")
        seen = {}
        for w in WIDTHS:
            ii = imageinfo(title, w)
            if not ii:
                print(f"{w:>6}  no response")
                continue
            if ii.get("missing"):
                print(f"{w:>6}  MISSING")
                continue
            url = ii.get("thumburl")
            sw = served_width_from_url(url)
            got = bytes_for(url)
            n, dw, dh = got if got else (None, None, None)
            alias = seen.get(url)
            seen[url] = seen.get(url) or w
            if ii.get("width") is not None and sw and ii["width"] <= w:
                verdict = "original (no upscale)"
            elif alias is not None:
                verdict = f"same asset as {alias} (bucket!)"
            elif dw and sw and dw == sw:
                verdict = "exact step" if sw == w else f"snapped up +{sw - w}"
            else:
                verdict = f"snapped up +{sw - w}" if sw and sw != w else "served as asked"
            if dw and sw and dw != sw:
                verdict += f"  !! decoded {dw} != URL {sw}"
            bpp = f"{n / (dw * dh):.3f}" if (n and dw and dh) else "-"
            print(f"{w:>6} {ii.get('thumbwidth'):>10} {sw:>8} "
                  f"{f'{dw}×{dh}' if dw else '-':>11} {n if n else '-':>9} {bpp:>6}  {verdict}")
        print()
    print("legend  asked      = iiurlwidth we sent")
    print("        thumbwidth = imageinfo.thumbwidth — ECHOES THE REQUEST, not the file (T388792)")
    print("        url Npx    = the /Npx-/ segment of thumburl — the served rendition")
    print("        decoded    = dimensions of the bytes actually downloaded")
    print("        $wgThumbnailSteps = [20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840]")


if __name__ == "__main__":
    main()
