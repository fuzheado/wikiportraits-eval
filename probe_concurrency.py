#!/usr/bin/env python3
"""Does the endpoint have spare capacity for a second in-flight call? (HANDOFF §7.1)

The open question behind `INFLIGHT`: the owner suspects the endpoint queues same-origin calls,
so the app ships serial by default. Nobody has measured it. This script does.

It runs three phases against the real six-question schema with real Commons thumbnails:

    A. serial      — N calls, one at a time          (the shipped behaviour)
    B. concurrent  — N calls across C streams, released together behind a barrier
    C. serial      — N calls again                   (drift check: the endpoint wanders)

The number that answers the question is **not** wall-clock. It is
`x-envoy-upstream-service-time` — the server's own compute time for the request, excluding our
network and any gateway queueing. So:

    ratio = median(upstream_ms, concurrent) / median(upstream_ms, serial)

    ~1.0  → the server has capacity; a second stream nearly halves wall-clock. INFLIGHT=2 is a win.
    ~2.0  → the calls serialise. A second stream buys *nothing* and adds queueing. Stay at 1.
    between → partial contention; judge against how much you need the speed.

`wall_s` is reported too, but it also moves when the *origin network* changes — which is why this
should be run through `run_via_toolforge.py` (or inside the pod), never from a home connection.
See `wmcs_tunnel.py` for why the origin network matters.

    python3 run_via_toolforge.py probe_concurrency.py --n 4 --streams 2
    python3 probe_concurrency.py --n 4 --streams 2            # direct (dev host) — different regime
"""
import argparse
import base64
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import wiclient

# One real portrait per file, so a phase never repeats an image and no server-side memoisation
# can make the concurrent phase look artificially cheap. Taken from default_list.txt.
FILES = [
    "File:Erica Lockheimer at SXSW 2025 03.jpg",
    "File:Kay Greyson June 2025 SXSW 2.jpg",
    "File:Hassan El Raddad at the 2025 Red Sea Film Festival 20251206 (2).jpg",
    "File:Alexa Kinas 2 G LOYOLA CHICAGO VS ST BONAVENTURE (55134279465).jpg",
    "File:Tamika Williams-Jeter HC Dayton (55138373222).jpg",
    "File:Leslie Cohen portrait at WikiPortraits Studio at Sundance 2026 08.jpg",
    "File:Linette Kasper at RavenCon 04242026-2.jpg",
    "File:Predrag Zimonjić KK Partizan Euroleague 20260225 (1).jpg",
    "File:Daiane Sodre at 2026 Cannes Film Festival 01 (cropped).jpg",
    "File:Jay Buchanan Rival Sons Tons of Rock 2026 07.jpg",
    "File:Moses Mensah 33 DF Spokane Velocity 07182026 vs Richmond Kickers 018.jpg",
    "File:Pierfrancesco Favino at 83rd Venice International Film Festival-26.jpg",
]
SEND_WIDTH = 960            # the ladder step the app requests (wiclient.THUMB_WIDTH)


def fetch_thumbs(files=None):
    """One batched Commons call for every thumbnail — never one call per file."""
    files = files or FILES
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": "2",
        "titles": "|".join(files), "prop": "imageinfo", "iiprop": "url",
        "iiurlwidth": SEND_WIDTH})
    req = urllib.request.Request(wiclient.COMMONS_API + "?" + q,
                                 headers={"User-Agent": wiclient.UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        pages = json.load(r)["query"]["pages"]
    out = []
    for p in pages:
        if "imageinfo" not in p:
            print(f"  ! {p.get('title')} — no imageinfo (missing?)", file=sys.stderr)
            continue
        url = p["imageinfo"][0]["thumburl"]
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": wiclient.UA}),
                                    timeout=90) as r:
            data = r.read()
        served = wiclient.served_width_from_url(url) if hasattr(wiclient, "served_width_from_url") else None
        out.append({"title": p["title"], "served_w": served,
                    "b64": base64.b64encode(data).decode(), "bytes": len(data),
                    "sha1": p["imageinfo"][0].get("sha1")})
        print(f"  thumb {p['title'][:56]:58s} served_w={served} {len(data):>7,} B", file=sys.stderr)
    return out


def _attempt(img_b64, label, timeout=180):
    body = {"model": "llm-clef-flash", "state": wiclient.STATE,
            "images": [img_b64], "questions": wiclient.QUESTIONS}
    req = urllib.request.Request(wiclient.LIFTWING, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json",
                                         "User-Agent": wiclient.UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            hdrs = dict(r.headers)
            ttfb = round(time.time() - t0, 3)
            r.read()
        wall = round(time.time() - t0, 3)
        up = hdrs.get("x-envoy-upstream-service-time")
        return {"label": label, "ok": True, "http": r.status, "wall_s": wall, "ttfb_s": ttfb,
                "upstream_ms": int(up) if str(up).isdigit() else None,
                "client_ip": hdrs.get("x-client-ip")}
    except urllib.error.HTTPError as e:
        wall = round(time.time() - t0, 3)
        detail = e.read().decode(errors="replace")[:200]
        return {"label": label, "ok": False, "http": e.code, "wall_s": wall,
                "ttfb_s": round(time.time() - t0, 3), "upstream_ms": None,
                "client_ip": e.headers.get("x-client-ip"), "detail": detail}
    except Exception as exc:                                            # noqa: BLE001 - report it
        return {"label": label, "ok": False, "http": None,
                "wall_s": round(time.time() - t0, 3), "error": f"{type(exc).__name__}: {exc}"}


RETRY = False          # mirrors the app's policy: one retry, only for a transient gateway 5xx
UPSTREAM_RETRY_CODES = getattr(wiclient, "UPSTREAM_RETRY_CODES", (502, 503, 504))
UPSTREAM_RETRY_BACKOFF = getattr(wiclient, "UPSTREAM_RETRY_BACKOFF", 2)


def one_call(img_b64, label, timeout=180):
    """One call, with the app's own retry policy applied when --retry is set."""
    r = _attempt(img_b64, label, timeout)
    if RETRY and r.get("http") in UPSTREAM_RETRY_CODES:
        time.sleep(UPSTREAM_RETRY_BACKOFF)
        r2 = _attempt(img_b64, label + "*", timeout)
        r2["retried"] = True
        r2["first_http"] = r.get("http")
        return r2
    return r


def phase_serial(imgs, n, tag):
    return [one_call(imgs[i % len(imgs)]["b64"], f"{tag}-{i}") for i in range(n)]


def phase_concurrent(imgs, n, streams, tag):
    """N calls across `streams` threads, released together so the first batch truly overlaps."""
    results: list = [None] * n
    barrier = threading.Barrier(streams)
    lock = threading.Lock()
    nxt = [0]

    def worker(k):
        barrier.wait()
        while True:
            with lock:
                i = nxt[0]
                nxt[0] += 1
            if i >= n:
                return
            results[i] = one_call(imgs[i % len(imgs)]["b64"], f"{tag}-{i}")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(streams)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def describe(name, rows, elapsed):
    walls = [r["wall_s"] for r in rows if r.get("ok")]
    ups = [r["upstream_ms"] for r in rows if r.get("ok") and r.get("upstream_ms") is not None]
    ttfb = [r["ttfb_s"] for r in rows if r.get("ok")]
    bad = [r for r in rows if not r.get("ok")]
    med_wall = statistics.median(walls) if walls else None
    med_up = statistics.median(ups) if ups else None
    print(f"  {name:11s} n={len(walls):<2d} ok  wall median "
          f"{('%.3f' % med_wall) if med_wall else '   -  ':>6s}s  "
          f"ttfb median {('%.3f' % statistics.median(ttfb)) if ttfb else '   -  ':>6s}s  "
          f"upstream median {str(int(med_up)) + 'ms' if med_up else '-':>7s}  "
          f"phase elapsed {elapsed:6.2f}s")
    if bad:
        recovered = sum(1 for b in rows if b.get("retried") and b.get("ok"))
        print(f"      {len(bad)} failed: {[ (b.get('http'), b.get('detail', b.get('error'))[:60]) for b in bad ]}"
              + (f"  (retried: {recovered} recovered)" if recovered else ""))
    retried = sum(1 for r in rows if r.get("retried"))
    if retried:
        print(f"      {retried} call(s) needed the app's one retry; "
              f"{sum(1 for r in rows if r.get('retried') and r.get('ok'))} recovered")
    return {"wall": med_wall, "upstream": med_up, "all_wall": walls, "all_upstream": ups,
            "elapsed": elapsed, "failures": len(bad), "retried": retried}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="calls per phase (default 4)")
    ap.add_argument("--streams", type=int, default=2, help="concurrent streams (default 2)")
    ap.add_argument("--skip-drift", action="store_true", help="omit the second serial phase")
    ap.add_argument("--retry", action="store_true",
                    help="apply the app's policy: one retry on a transient 5xx")
    args = ap.parse_args()

    global RETRY
    RETRY = args.retry

    print(f"=== probe_concurrency: n={args.n}/phase, streams={args.streams}"
          f"{' , retry-on-5xx' if args.retry else ''} ===")
    print("fetching thumbnails (1 batched Commons call)…", file=sys.stderr)
    imgs = fetch_thumbs()
    if len(imgs) < args.n:
        sys.exit(f"need {args.n} distinct images, got {len(imgs)}")

    print("\nA. serial")
    t = time.time()
    a = describe("serial A", (rows_a := phase_serial(imgs, args.n, "A")), time.time() - t)

    print(f"\nB. concurrent ({args.streams} streams)")
    t = time.time()
    b = describe("concurrent", (rows_b := phase_concurrent(imgs, args.n, args.streams, "B")),
                 time.time() - t)

    c = None
    if not args.skip_drift:
        print("\nC. serial again (drift check)")
        t = time.time()
        c = describe("serial C", (rows_c := phase_serial(imgs, args.n, "C")), time.time() - t)

    print("\n=== per-call detail ===")
    for r in (rows_a + rows_b + (rows_c if c else [])):
        if r.get("ok"):
            print(f"  {r['label']:5s} wall {r['wall_s']:6.3f}s  ttfb {r['ttfb_s']:6.3f}s  "
                  f"upstream {str(r['upstream_ms']) + 'ms':>7s}  client_ip {r.get('client_ip')}")
        else:
            print(f"  {r['label']:5s} FAILED HTTP {r.get('http')} {r.get('detail', r.get('error', ''))[:90]}")

    ips = {r.get("client_ip") for r in (rows_a + rows_b + (rows_c if c else [])) if r.get("client_ip")}
    print(f"\norigin as the CDN saw it: {', '.join(sorted(ips)) or 'unknown'}")

    if a["upstream"] and b["upstream"]:
        ratio = b["upstream"] / a["upstream"]
        print("\n=== verdict ===")
        print(f"  upstream ratio (concurrent / serial): {ratio:.2f}   "
              f"[{int(b['upstream'])}ms vs {int(a['upstream'])}ms]")
        print(f"  wall ratio      (concurrent / serial): {b['wall'] / a['wall']:.2f}   "
              f"[{b['wall']:.3f}s vs {a['wall']:.3f}s]")
        print(f"  phase elapsed: serial {a['elapsed']:.2f}s vs concurrent {b['elapsed']:.2f}s "
              f"({(1 - b['elapsed'] / a['elapsed']) * 100:.0f}% saved)")
        if c and c["upstream"]:
            drift = c["upstream"] / a["upstream"]
            # NOTE: the ternary is hoisted out of the f-string on purpose. Inlining it as
            # f"({...})" spanning two source lines is PEP 701 (3.12+) only — on the advertised 3.9
            # floor it is `SyntaxError: EOL while scanning string literal`. check_compat.py now
            # parses every file with a real python3.9 when one is on PATH.
            stable = "stable" if 0.8 <= drift <= 1.25 else (
                "UNSTABLE — endpoint moved during the run, treat the concurrent comparison as "
                "unreliable")
            print(f"  drift check: serial C/A upstream ratio {drift:.2f} ({stable})")
        if ratio < 1.35:
            print("  → the server has spare capacity. A second stream is close to free: INFLIGHT=2 helps.")
        elif ratio > 1.7:
            print("  → the calls serialise. A second stream buys no throughput and adds queueing: keep INFLIGHT=1.")
        else:
            print("  → partial contention. A second stream helps less than half; weigh it against §13's courtesy rule.")
    else:
        print("\n(no upstream timing to compare — was every call a 200?)")


if __name__ == "__main__":
    main()
