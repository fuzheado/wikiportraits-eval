#!/usr/bin/env python3
"""Local image uploads: scored like any other photo, and never written to disk.

Two things make this feature worth its own suite.

**The privacy rule is the feature.** An uploaded image is somebody's own file, so it lives in this
process's memory and nowhere else — not in `cache/`, not in `cache/thumbs/`, not in an answer
entry. That claim is only worth anything if it is checked where it could fail, so the test runs the
same pipeline twice: once with a Commons title (positive control — files *must* appear, or the
assertion below would pass for the wrong reason) and once with an upload (no new file anywhere).

**The identity rule prevents a specific, silent bug.** Everything in the page and the reports keys an
item by its title, and the answer cache is keyed by (pageid, sha1, served_w). An upload has neither a
pageid nor a sha1, so if it were allowed to reach the cache every upload would share the key
`(None, None, None)` and two different photographs would hand each other's answers back — a wrong
answer, presented with full confidence. Both that and the duplicate-title collision are asserted here.

Offline: the model call, the Commons lookup and the pre-flight probe are all stubbed.
"""
import hashlib
import json
import os
import struct
import sys
import tempfile
import zlib

CACHE = tempfile.mkdtemp(prefix="wp-local-cache-")
os.environ["CACHE_DIR"] = CACHE
os.environ["QUIET"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
sys.path.insert(0, ROOT)

import core            # noqa: E402
import localstore      # noqa: E402
import reports         # noqa: E402
import wiclient        # noqa: E402

FAIL = []


def ok(cond, label):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAIL.append(label)


def png(w=40, h=30, color=(200, 30, 30)):
    """A minimal 8-bit RGB PNG, built by hand — no dependencies, and a real IHDR.

    The dimensions matter: the server reads them out of the IHDR, so this doubles as the fixture for
    "the bytes are a readable image" and (with an absurd IHDR) for the pixel-cap refusal.
    """
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat)
            + chunk(b"IEND", b""))


def png_header_only(w, h):
    """A PNG whose IHDR claims any size, with no pixel data: 29 bytes, and enough to test the cap."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    crc = zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", crc)


def upload(data: bytes, name="", orig="", route=None):
    q = []
    if name:
        q.append("name=" + name)
    if orig:
        q.append("orig=" + orig)
    st, ctype, body, headers = core.route("POST", "/api/upload", data, "&".join(q))
    return st, json.loads(body or b"{}"), headers


def cached_files():
    return sorted(f for f in os.listdir(CACHE) if f.endswith(".json"))


def thumb_files():
    d = os.path.join(CACHE, "thumbs")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


print("\n1. the store itself (unit)")
img, img2 = png(color=(10, 120, 40)), png(w=41, color=(30, 30, 200))
e1 = localstore.put(img, name="a.jpg", mime="image/png", orig=(5152, 7728))
ok(e1["id"] == hashlib.sha256(img).hexdigest(), "the id is the sha256 of the bytes, not the name")
ok(localstore.get(e1["id"]) == img, "the bytes come back unchanged")
ok(e1["dup" if False else "duplicate"] is False, "a first upload is not reported as a duplicate")
e2 = localstore.put(img, name="copied.jpg")
ok(e2["duplicate"] is True and localstore.stats()["items"] == 1,
   "the same bytes under another name are ONE image (content identity, not filename)")
ok(localstore.meta(e1["id"])["orig_w"] == 5152, "the browser-reported original size is kept")

localstore.clear()
a = localstore.put(img, name="IMG_0001.jpg")
b = localstore.put(img2, name="IMG_0001.jpg")
names = localstore.unique_names([a["id"], b["id"]])
ok(len(set(names.values())) == 2,
   f"two different photos with one basename get distinct labels ({sorted(names.values())})")
ok(all(x.startswith("IMG_0001.jpg") for x in names.values()), "…and they stay recognisable")

# caps: rebind the module globals (the functions read them at call time, so this is the real check)
keep = (localstore.MAX_LOCAL_BYTES, localstore.MAX_LOCAL_ITEMS)
localstore.clear()
# Size the cap against the fixture rather than guessing. A loose cap (`3000` against two ~100-byte
# PNGs) never evicts anything, so the assertion below would pass whatever the code did — it would not
# be a guard. Sizes here: img 100 bytes, png(44) 103 bytes, so a cap of img+10 fits one and not two.
localstore.MAX_LOCAL_BYTES, localstore.MAX_LOCAL_ITEMS = len(img) + 10, 100
old = localstore.put(img, name="old.jpg")
localstore.put(png(w=44), name="new.jpg")
ok(localstore.get(old["id"]) is None, "past the byte cap the least recently used upload is evicted")
ok(localstore.stats()["items"] == 1, "…and the store stays inside its cap")
live = old["id"] if localstore.exists(old["id"]) else localstore.ids()[0]
localstore.clear()
localstore.MAX_LOCAL_BYTES = len(img) + 10
pinned = localstore.put(img, name="pinned.jpg")
localstore.MAX_LOCAL_BYTES = len(img) + len(png(w=44))     # room for exactly two more-ish
with localstore.hold([pinned["id"]]):
    localstore.put(png(w=44), name="filler.jpg")
    ok(localstore.exists(pinned["id"]),
       "a pinned upload survives a sweep that was free to take it (it is the oldest entry)")
    localstore.MAX_LOCAL_BYTES = 1                         # now nothing can be made to fit
    try:
        localstore.put(png(w=45), name="over.jpg")
        raised = None
    except localstore.Rejected as exc:
        raised = str(exc)
    ok(raised and "run itself is holding" in raised,
       f"when everything left is pinned the store REFUSES rather than evicting a running job's "
       f"image ({(raised or 'no refusal')[:52]}…)")
ok(localstore.exists(pinned["id"]), "…and the pinned image is still there afterwards")
localstore.MAX_LOCAL_BYTES, localstore.MAX_LOCAL_ITEMS = keep
localstore.clear()
# One image larger than the store's whole budget: the sweep would have to evict the arrival itself.
# That raised KeyError before the guard — a 500 from a misconfiguration instead of a refusal.
localstore.MAX_LOCAL_BYTES = len(img) // 2
localstore.MAX_LOCAL_ITEMS = 100
try:
    localstore.put(img, name="too-big-for-the-store.jpg")
    too_big = None
except localstore.Rejected as exc:
    too_big = exc.status
localstore.MAX_LOCAL_BYTES, localstore.MAX_LOCAL_ITEMS = keep
localstore.clear()
ok(too_big == 413, f"an image bigger than the whole store is refused, not a crash ({too_big})")

localstore.LOCAL_TTL_S = 0.01
import time as _t   # noqa: E402
localstore.put(img, name="stale.jpg")
_t.sleep(0.02)
ok(localstore.get(e1["id"]) is None and localstore.stats()["items"] == 0,
   "an upload idle past the TTL is swept — memory is not kept forever")
localstore.LOCAL_TTL_S = 6 * 3600

try:
    localstore.put(b"", name="empty.jpg")
    empty_raised = None
except localstore.Rejected as exc:
    empty_raised = exc.status
ok(empty_raised == 400, "an empty body is refused as a client error")
big = b"\xff\xd8\xff" + b"0" * localstore.MAX_UPLOAD_BYTES
try:
    localstore.put(big, name="huge.jpg")
    big_raised = None
except localstore.Rejected as exc:
    big_raised = exc.status
ok(big_raised == 413, f"an over-size upload is refused with 413 (cap {localstore.MAX_UPLOAD_BYTES})")

print("\n2. POST /api/upload and GET /api/upload/<id>")
localstore.clear()
st, body, _ = upload(img, name="portrait.png", orig="5152x7728")
ok(st == 200 and body.get("id"), f"a real PNG uploads ({st})")
ok(body.get("w") == 40 and body.get("h") == 30, "the response reports the size read from the bytes")
ok(body.get("orig_w") == 5152 and body.get("orig_h") == 7728, "and the size the browser reported")
ok(body.get("bytes") == len(img), "and the byte count")
ok(body.get("url") == f"/api/upload/{body['id']}", "and the URL the page will display it from")
uid = body["id"]

st, ct, back, headers = core.route("GET", f"/api/upload/{uid}", b"", "")
ok(st == 200 and back == img, "GET /api/upload/<id> serves the exact bytes back")
ok(ct == "image/png", f"…with the image's own content type ({ct})")
ok(headers.get("Cache-Control") == "private, no-store",
   "…and no-store, so no shared cache keeps a copy of somebody's photo")
ok("noindex" in (headers.get("X-Robots-Tag") or ""), "…and out of search indexes")

st, body, _ = upload(b"this is not an image at all, it is a text file")
ok(st == 400 and "JPEG" in body.get("error", ""), f"a non-image is refused with an explanation ({st})")
ok(localstore.stats()["items"] == 1, "…and is NOT stored")
st, body, _ = upload(png_header_only(25000, 25000))
ok(st == 413 and "px" in body.get("error", ""), f"an image above MAX_SEND_PIXELS is refused ({st})")
ok(localstore.stats()["items"] == 1, "…and is NOT stored either")
st, body, _ = upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)
ok(st == 400, "a truncated image is refused rather than stored on a guess")
st, body, _ = upload(png(w=42), name="huge-name-" + "x" * 400 + ".png", orig="not-dimensions")
ok(st == 200 and body.get("orig_w") is None,
   "an unparseable orig= is ignored, not fatal (the bytes are still measured)")
ok(len(core.route("GET", "/api/upload/deadbeef", b"", "")[2]) > 0, "an unknown id answers with a body")
ok(core.route("GET", "/api/upload/deadbeef", b"", "")[0] == 404, "…and a 404")
cfg = json.loads(core.route("GET", "/api/config", b"", "")[2])
ok(cfg.get("local", {}).get("max_upload_bytes") == localstore.MAX_UPLOAD_BYTES,
   "/api/config publishes the upload cap so the page can state it")
ok("memory only" in cfg.get("local", {}).get("retention", ""),
   "…and the retention rule, in words")
ok(isinstance(cfg["local"].get("items"), int), "…and how much is held right now")

print("\n3. an upload becomes an ordinary item in a run")
localstore.clear()
st, body, _ = upload(img, name="my-photo.png", orig="5152x7728")
uid = body["id"]
job = core.new_job("", local_ids=[uid])
it = job["items"][0]
ok(it["title"] == "my-photo.png" and it.get("local") == uid,
   "the item is labelled with the filename and keyed by the content id")
ok(it.get("wiki") is None, "it has no wiki, so the Commons lookup skips it")
ok(it["thumburl"] == f"/api/upload/{uid}", "its thumbnail is the endpoint that serves it back")
ok(it.get("orig_w") == 5152, "the original size travels with it, for the report")

job2 = core.new_job("", local_ids=["f" * 64])
gone = job2["items"][0]
ok(gone["status"] == "error" and gone.get("local_gone"),
   "an id the server no longer holds becomes an honest error row, not a scored one")
ok("memory" in gone["error"], f"…explaining why: {gone['error'][:60]}…")

job3 = core.new_job("my-photo.png\nmy-photo.png (2)\n", local_ids=[uid])
titles = [i["title"] for i in job3["items"]]
ok(len(set(titles)) == len(titles),
   f"titles are unique across a mixed run, so no two rows can merge ({titles})")
ok(any(i.get("local") for i in job3["items"]), "…and the upload is still in there")

dupes = core.new_job("", local_ids=[uid, uid, uid])
ok(len(dupes["items"]) == 1, "a repeated id is one image, not three")

print("\n4. running it: the model call happens, the disk write does not")

ANSWERS = {
    "face_visible": {"noul": 0.91}, "face_unobstructed": {"noul": 0.97},
    "eyes_to_camera": {"noul": 0.88}, "infobox_ready": {"noul": 0.62},
    "infobox_ready_caption": None,
    "publish_ready": {"choice": "publish", "confidence": 0.8,
                      "probabilities": {"publish": 0.8, "pass": 0.2}},
    "pose": {"choice": "head_shoulders", "probabilities": {"head_shoulders": 0.7, "candid": 0.3}},
    "quality": {"score": 2.5, "confidence": 0.4, "probabilities": {"2": 0.5, "3": 0.5}},
}
ANSWERS.pop("infobox_ready_caption")

calls = {"score": 0, "resolve": [], "fetch": []}


def fake_score(img_b64, budget, timeout=180):
    calls["score"] += 1
    return {"http": 200, "wall_s": 0.31, "ttfb_s": 0.30, "model": "llm-clef-flash",
            "usage": {"input_tokens": 941, "output_tokens": 0}, "answers": ANSWERS}


def fake_resolve(items):
    calls["resolve"].append([i["title"] for i in items])
    out = {}
    for n, it in enumerate(items, start=1):
        out[(it.get("wiki") or "commons.wikimedia.org", it["title"])] = {
            "ok": True, "pageid": 7100 + n, "thumburl": "https://example.invalid/t.jpg",
            "orig_w": 4000, "orig_h": 3000, "mime": "image/jpeg", "sha1": f"sha{n}",
            "license": "CC BY-SA 4.0", "artist": "Tester"}
    return out


def fake_fetch(url):
    calls["fetch"].append(url)
    return png(w=60, h=45) if url.startswith("http") else img


wiclient.score_image = fake_score
wiclient.resolve = fake_resolve
wiclient.fetch_bytes = fake_fetch
core.wiclient = wiclient
core._PROBE.update(t=__import__("time").time(),
                   result={"ok": True, "http": 200, "wall_s": 0.05, "error": None})

# --- positive control first: a Commons photo MUST leave files behind, or the assertion below is
# --- vacuous (it would pass on a machine where nothing works at all).
before = (cached_files(), thumb_files())
job_c = core.new_job("File:Commons portrait.jpg")
core.run_job(job_c)
after_c = (cached_files(), thumb_files())
ok(job_c["items"][0]["status"] == "done", "the Commons photo in the control run scores")
ok(len(after_c[0]) > len(before[0]) or len(after_c[1]) > len(before[1]),
   "CONTROL: a Commons photo writes a cache entry and a thumbnail (so the next check can fail)")
ok(calls["resolve"] and any("Commons portrait.jpg" in t for t in calls["resolve"][0]),
   f"CONTROL: resolve() was called for the Commons title ({calls['resolve']})")

# --- now the same pipeline, with an upload
calls["resolve"] = []
calls["score"] = 0
calls["fetch"] = []
before = (cached_files(), thumb_files())
job_l = core.new_job("", local_ids=[uid])
core.run_job(job_l)
after_l = (cached_files(), thumb_files())
item = job_l["items"][0]
ok(item["status"] == "done", f"the upload scores through the same pipeline ({item['status']})")
ok(item.get("composite") is not None and item.get("publish_p") is not None,
   "…and gets the same derived numbers as any photo")
ok(item.get("sent_px") == "40x30", f"…with the size actually sent, read from the bytes ({item.get('sent_px')})")
ok(calls["score"] == 1, "…on exactly one model call")
ok(calls["resolve"] == [], "no Commons lookup is made for an upload (nothing to look up)")
ok(calls["fetch"] == [], "and nothing is fetched over the network for it either")
ok(cached_files() == before[0],
   f"NO answer is written to the cache for an upload ({len(cached_files())} files, was {len(before[0])})")
ok(thumb_files() == before[1],
   f"NO thumbnail is written to disk for an upload ({len(thumb_files())} files, was {len(before[1])})")
ok(not os.path.exists(os.path.join(CACHE, "thumbs", "commons-None-None.img")),
   "and specifically no file named after the None pageid an upload does not have")

# the same bytes twice in one run must be ONE item, and the answers must not be crossed
twin = core.new_job("", local_ids=[uid])
ok(len(twin["items"]) == 1, "one id, one row — even though both would key to (None, None, None)")

print("\n5. replay (cached_only) cannot spend on an upload either")
calls["score"] = 0
job_r = core.new_job("", local_ids=[uid], cached_only=True)
core.run_job(job_r)
ok(job_r["items"][0]["status"] == "uncached" and calls["score"] == 0,
   "a cache-only replay leaves an upload unscored rather than calling the model")
ok("Score them" in (job_r["items"][0].get("error") or ""), "…and says what to press instead")

print("\n6. reports describe an upload as an upload")
mixed = core.new_job("File:Commons portrait.jpg", local_ids=[uid])
core.run_job(mixed)
meta = {"state": "s", "questions": wiclient.QUESTIONS, "chart_view": "combined",
        "chart_axis": "full", "chart_scale": "linear", "elapsed_s": 1.0,
        "latency": {"median_s": 0.3, "baseline_s": 0.5}, "spread": {}, "no_cache": False,
        "cached_only": False, "pdf_path": os.path.join(CACHE, "report-test.pdf")}
md = reports.build_md(mixed["items"], meta)
local_line = [l for l in md.splitlines() if "Commons: https://commons.wikimedia.org/wiki/my-photo" in l]
ok(not local_line,
   "the Markdown does not invent a Commons page for an upload (that URL would be a DIFFERENT photo)")
ok("uploaded from the operator's computer" in md,
   "it says the image was uploaded, in the method block and/or the source line")
ok("my-photo.png" in md, "the upload is still labelled by name")
csv = reports.build_csv(mixed["items"])
rows = csv.splitlines()
ok("local" in rows[0], "the CSV gained a `local` column")
ok(any(uid in r for r in rows[1:]), "…and the upload's id is in it")
ok(any("my-photo.png" in r for r in rows[1:]), "…on the row for the upload")
j = json.loads(reports.build_json(mixed["items"], meta))
by_title = {i.get("title"): i for i in j["items"]}
ok(by_title["my-photo.png"].get("local") == uid, "the JSON carries the local id on the item")
pdf = reports.build_pdf(mixed["items"], meta)
ok(pdf[:5] == b"%PDF-" and len(pdf) > 10000, f"the PDF builds with an upload embedded ({len(pdf)} bytes)")

print("\n7. the API shape the page and curl users read")
row = core._eval_row(mixed["items"][1])
ok(row["local"] is True, "/api/eval marks the row as local")
ok(row["commons_url"] is None, "…and offers no Commons URL for it")
ok(row["image_url"] == f"/api/upload/{uid}", "…but does give the URL it can be displayed from")
row_c = core._eval_row(mixed["items"][0])
ok(row_c["commons_url"] and row_c["local"] is False, "CONTROL: a Commons row still has its page URL")

print("\n8. the caps count uploads too")
core.JOBS.clear()      # earlier sections built jobs directly and never ran them
keep_cap = core.MAX_INPUT_ITEMS
core.MAX_INPUT_ITEMS = 2
ids = [body["id"]]
for n in range(2):
    ids.append(upload(png(w=50 + n), name=f"extra{n}.png")[1]["id"])
st, ctype, out, _ = core.route("POST", "/api/run",
                               json.dumps({"text": "", "local": ids}).encode(), "")
ok(st == 413, f"three uploads under a 2-per-run cap are refused ({st})")
core.MAX_INPUT_ITEMS = keep_cap
st, ctype, out, _ = core.route("POST", "/api/run", json.dumps({"text": "", "local": ids[:2]}).encode(), "")
job_id = json.loads(out).get("job_id")
ok(st == 200 and job_id, "two uploads under the cap start a job")
ok(len(core.JOBS[job_id]["items"]) == 2, "…with both uploads in it")
ok(all(i.get("local") for i in core.JOBS[job_id]["items"]), "…and no phantom Commons entries")
core.JOBS.clear()      # the job started just above is still queued/running and would answer 409
st, ctype, out, _ = core.route("POST", "/api/eval", json.dumps({"local": [uid]}).encode(), "")
ok(st == 200 and json.loads(out)["results"][0]["local"] is True,
   "POST /api/eval scores an upload on its own, with no files[] (the curl path)")

localstore.clear()
print("\nFAILURES:", len(FAIL))
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
