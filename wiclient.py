"""Wikimedia Commons input parsing + LiftWing llm-clef-flash client.

Stdlib only (urllib): the Toolforge build needs no pip install.
"""
import base64, hashlib, json, os, re, statistics, threading, time
import urllib.error, urllib.parse, urllib.request

# The app version lives here rather than in core.py because both need it: core reports it (About,
# reports) and this module puts it in the default User-Agent. One source of truth — before this, the
# UA hard-coded "0.4" and drifted three releases behind the version the app displayed.
APP_VERSION = "0.21.0"
UA = os.environ.get(
    "WIKIMEDIA_USER_AGENT",
    f"WikiPortraitsEval/{APP_VERSION} (https://github.com/fuzheado/wikiportraits-eval)")
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
LIFTWING = ("https://api.wikimedia.org/service/lw/inference/v1/models/"
            "llm-clef-flash:predict")
# Commons enforces `$wgThumbnailSteps` and quantises `iiurlwidth` UPWARD to the next step, while
# `imageinfo.thumbwidth` echoes the *request* back instead of describing the file (T388792). So a
# requested width only means something if it names a real step — 640 is not one, and a 640 request
# silently downloads the 960 asset. Ask for the step, and read the served width from the URL.
THUMB_LADDER = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)
MODEL_MAX_WIDTH = 640        # the vision encoder normalises to this width, whatever you send
THUMB_WIDTH = int(os.environ.get("THUMB_WIDTH", "960"))   # must be a THUMB_LADDER step
SPACING = float(os.environ.get("SPACING", "0.9"))          # seconds between scoring calls
# A 5xx from the gateway that failed *before headers* — Envoy's "upstream connect error or
# disconnect/reset before headers … connection timeout" — means the model was never reached, so a
# retry is worth it. A 4xx is a client-side fault (400 malformed body, 403 missing User-Agent) and
# retrying cannot fix it. Never a loop: this is a shared, experimental service.
#
# **How many retries, measured 2026-10-14:** ~3.5% of first attempts (4 of ~116) returned one of
# these — a connection-level fault in which the model was never reached, so the failed attempt costs
# no tokens. `UPSTREAM_MAX_ATTEMPTS = 3` is therefore a small, bounded bet, and the *marginal* load is
# tiny by construction: extra requests ≈ (failure rate × extra attempts), so the worst case is about
# +7% requests, not +200%. What is NOT measured is the recovery rate — no probe has yet exercised
# this path — which is exactly why every run now reports `retries` (see `core.retry_summary`):
# ordinary use answers "was the second attempt worth it?" without a dedicated load test. Backoff is
# exponential (2 s, 4 s, …) and every attempt is paced through `_next_slot()` like any other call.
UPSTREAM_RETRY_CODES = (502, 503, 504)
UPSTREAM_RETRY_BACKOFF = float(os.environ.get("UPSTREAM_RETRY_BACKOFF", "2.0"))
UPSTREAM_RETRY_BACKOFF_MAX = float(os.environ.get("UPSTREAM_RETRY_BACKOFF_MAX", "8.0"))
UPSTREAM_MAX_ATTEMPTS = max(1, int(os.environ.get("UPSTREAM_MAX_ATTEMPTS", "3")))

STATE = ("WikiPortraits photograph of a public figure, taken by a volunteer photographer at an "
         "event photo booth. Assess the photograph itself, not the person's fame.")

QUESTIONS = {
    "face_visible": {"type": "noul",
                     "instructions": "Is at least one person's face clearly visible and unobstructed?"},
    "face_unobstructed": {"type": "noul",
                          "instructions": "Is the subject's face free of anything held in front of "
                                          "it, such as a microphone or a hand?"},
    "pose": {"type": "choice",
             "instructions": "What is the subject's pose and framing?",
             "criteria": {
                 "head_shoulders": "Head and shoulders, subject facing the camera",
                 "three_quarter": "Roughly three-quarter body visible",
                 "full_body": "Full body visible, head to feet",
                 "group": "Two or more people in frame",
                 "candid": "Not posing: candid, action or back-of-head shot",
             }},
    "quality": {"type": "score",
                "instructions": "Overall photographic quality for use in an encyclopedia article",
                "criteria": ["unusable (blurred/miscropped)", "poor", "acceptable", "good", "excellent"]},
    "infobox_ready": {"type": "noul",
                      "instructions": "Good enough to be the lead image (infobox portrait) of a Wikipedia biography?"},
    "publish_ready": {"type": "choice",
                      "instructions": "An editor needs one photograph of this person.",
                      "criteria": {
                          "publish": "This photograph is the one to use.",
                          "pass": "Choose a different photograph instead.",
                      }},
    "eyes_to_camera": {"type": "noul",
                       "instructions": "Are the eyes open and looking toward the camera?"},
}
SCHEMA_HASH = hashlib.sha1(json.dumps({"state": STATE, "questions": QUESTIONS},
                                      sort_keys=True).encode()).hexdigest()[:12]
MISSING = object()

# The composite: our arithmetic on four model answers, not a model probability. It exists because
# the visible-fact questions spread across the corpus (face_visible sd 0.372, eyes_to_camera 0.350)
# while the editorial one collapses toward the middle (infobox_ready sd 0.229) — see
# research/wording-experiment-2026-10-13.md. Reported separately from P(publish) and P(infobox).
POSE_WEIGHT = {"head_shoulders": 1.0, "three_quarter": 0.9, "full_body": 0.6, "candid": 0.5,
               "group": 0.25}
# A microphone — or a hand, or a phone — held in front of the face costs a BOUNDED share of the
# composite: a fully covered face keeps OBSTRUCTION_FLOOR of its score and a clear face keeps all of
# it. Deliberately a demotion and not a disqualification (the requirement, 2026-10-14): a mic over the
# mouth is a real defect in a lead image, but the photo may still be the best one that exists of that
# person. Reported per photo as its own `face_unobstructed` probability, so the cost is attributable
# rather than buried in another question's answer.
OBSTRUCTION_FLOOR = 0.5
CANDIDATE_FLOOR = float(os.environ.get("CANDIDATE_FLOOR", "0.25"))


def obstruction_factor(ans: dict) -> float:
    """What a held object in front of the face costs, in [OBSTRUCTION_FLOOR, 1].

    Returns 1.0 when the question is ABSENT: an answer recorded before this question existed, or a
    test fixture, means "not known", never "obstructed". Treating a missing answer as a penalty would
    silently demote every photo the moment the schema changed.
    """
    u = (ans.get("face_unobstructed") or {}).get("noul")
    if u is None:
        return 1.0
    return round(OBSTRUCTION_FLOOR + (1.0 - OBSTRUCTION_FLOOR) * max(0.0, min(1.0, u)), 4)


def publish_p(ans: dict):
    """P(publish) from the two-option `choice` question."""
    q = ans.get("publish_ready") or {}
    probs = q.get("probabilities") or {}
    if probs:
        for k, v in probs.items():
            if str(k).lower().startswith("publish"):
                return v
    if q.get("choice") and q.get("confidence") is not None and \
            str(q["choice"]).lower().startswith("publish"):
        return q["confidence"]
    return None


# The answers `composite()` actually consumes, named so the report's prose and its tests can be
# generated from the list rather than restating a count. It is not a second source of truth: the PDF
# said "our arithmetic on three model answers" for a day after the obstruction question landed, and
# nothing objected, because the only PDF-text suite had no assertions (tests/check_pdf.py, 2026-10-16).
COMPOSITE_INPUTS = ("face_visible", "eyes_to_camera", "pose", "face_unobstructed")


def composite(ans: dict):
    """min(face_visible, eyes_to_camera) x pose weight x the obstruction factor — ours, not the model's.

    The answers it consumes are named in `COMPOSITE_INPUTS`, so prose and tests can refer to that one
    list instead of restating a count by hand.
    """
    face = (ans.get("face_visible") or {}).get("noul")
    eyes = (ans.get("eyes_to_camera") or {}).get("noul")
    pose = (ans.get("pose") or {}).get("choice")
    if face is None or eyes is None:
        return None
    return round(min(face, eyes) * POSE_WEIGHT.get(pose, 0.5) * obstruction_factor(ans), 4)


def candidate_spread(rows: list, floor: float = None) -> dict:
    """Measure the spread that triage actually consumes.

    `rows` is a list of (label, value). Global sd rises whenever answers pile up at either end,
    so we report instead: the range among *candidates* (values at or above `floor`), the median
    gap between adjacent candidates, and how many answers sit in the uninformative 0.35-0.65 band.
    """
    floor = CANDIDATE_FLOOR if floor is None else floor
    vals = [(lab, v) for lab, v in rows if v is not None]
    if not vals:
        return {"n": 0}
    vals.sort(key=lambda kv: -kv[1])
    numbers = [v for _, v in vals]
    cands = [v for v in numbers if v >= floor]
    gaps = [round(cands[i] - cands[i + 1], 4) for i in range(len(cands) - 1)]
    dead = [v for v in numbers if 0.35 <= v <= 0.65]
    med = statistics.median(gaps) if gaps else None
    return {
        "n": len(numbers),
        "floor": floor,
        "n_candidates": len(cands),
        "range_all": round(max(numbers) - min(numbers), 4),
        "candidate_range": round(max(cands) - min(cands), 4) if len(cands) > 1 else None,
        "candidate_median_gap": round(med, 4) if med is not None else None,
        "dead_zone": len(dead),
        "dead_zone_share": round(len(dead) / len(numbers), 3),
        "top": [lab for lab, _ in vals[:3]],
    }


class Budget:
    """Track calls in the rolling hour and remember a 429 Retry-After."""

    def __init__(self, limit: int = 100):
        self.limit = limit
        self.calls = []                        # timestamps of scored calls
        self.blocked_until = 0.0
        self.last_429 = None
        self.lock = threading.Lock()
        # when this counter started. The list is in memory only, so a server restart silently resets
        # the count while the endpoint's own per-client quota does not — hence `counting_since` in
        # status(): the UI reports "N since HH:MM" instead of implying a fact about the world.
        self.started = time.time()

    def note_call(self):
        with self.lock:
            self.calls.append(time.time())

    def note_429(self, retry_after):
        with self.lock:
            self.blocked_until = time.time() + float(retry_after or 60)
            self.last_429 = {"at": time.time(), "retry_after": retry_after}

    def used(self):
        cutoff = time.time() - 3600
        with self.lock:
            self.calls = [t for t in self.calls if t > cutoff]
            return len(self.calls)

    def wait_seconds(self):
        return max(0.0, self.blocked_until - time.time())

    def status(self):
        return {"used_last_hour": self.used(), "limit_per_hour": self.limit,
                "counting_since": self.started,
                "blocked_for_s": round(self.wait_seconds(), 1), "last_429": self.last_429}


# --------------------------------------------------------------------- inputs
# Hard limits on fetched media. The app never fetches originals — only the API-returned
# ~640 px-wide thumbnail — but a public tool must assume hostile input, so the fetch itself is
# capped (streamed read) and anything that still arrives absurdly large is rejected before the
# base64 payload is built. Both env-overridable.
MAX_THUMB_BYTES = int(os.environ.get("MAX_THUMB_BYTES", str(20 * 1024 * 1024)))   # 20 MB
MAX_SEND_PIXELS = int(os.environ.get("MAX_SEND_PIXELS", str(40 * 1000 * 1000)))   # 40 MP

# A Wikimedia project host only (any subdomain depth, but the tail must be a Wikimedia
# project domain). `re.fullmatch` — a substring/suffix test alone would admit
# `commons.wikimedia.org@evil.com` (userinfo) or `commons.wikimedia.org.evil.com`.
_MEDIA_HOST = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:wikipedia|wiktionary|wikibooks|"
                         r"wikiquote|wikisource|wikiversity|wikivoyage|wikinews|wikidata|"
                         r"wikimedia|wikifunctions|wikimediafoundation)\.org$")


def _clean_host(netloc: str):
    """Hostname from a URL netloc, or None when it cannot be a Wikimedia host.

    Rejects userinfo (`user@host`), ports on anything but the standard 443-less form, IP
    literals, and any host that does not end on a Wikimedia project domain. This is the only
    place input URLs may direct our server-side fetches, so it fails closed.
    """
    if "@" in netloc:                       # userinfo — never legitimate in pasted references
        return None
    host = netloc.rsplit(":", 1)[0].strip().lower().rstrip(".")
    if not host or _MEDIA_HOST.fullmatch(host) is None:
        return None
    return host


_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".svg", ".pdf",
              ".djvu", ".xcf", ".webm", ".ogv", ".mp4", ".mp3", ".ogg", ".flac", ".wav", ".stl")
_STRIP_PREFIX = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")
_WIKITEXT = re.compile(r"\[\[\s*(?:File|Image)\s*:", re.I)
_THUMB_PREFIX = re.compile(r"^\d+px-")
# A comma/semicolon separates entries only when a new entry clearly starts after it.
_SEP_WITH_PREFIX = re.compile(r"[,;]\s*(?=(?:File|Image|\[\[|https?://|//|Special:))", re.I)


def _prep(s: str) -> str:
    """Strip list markers/quotes and unwrap a wikitext embed down to its title."""
    s = _STRIP_PREFIX.sub("", s or "").strip().strip('"\'')
    if s.startswith("[["):
        s = _WIKITEXT.sub("File:", s.lstrip("[").rstrip("]"))
        s = s.split("|")[0].strip()
    return s


def _split_units(text: str):
    """Yield candidate entries, one per line, honouring commas inside file names."""
    for raw_line in re.split(r"[\n\r]+", text or ""):
        line = _prep(raw_line)
        if not line:
            continue
        prefixed = _SEP_WITH_PREFIX.split(line)
        if len(prefixed) > 1:                  # "File:A.jpg, File:B.jpg" / "url, url"
            for p in prefixed:
                p = _prep(p)
                if p:
                    yield p
            continue
        pieces = [p.strip() for p in re.split(r"[,;]+", line) if p.strip()]
        if len(pieces) > 1 and all(os.path.splitext(p)[1].lower() in _IMAGE_EXT for p in pieces):
            for p in pieces:                   # "Foo.jpg, Bar.jpg" — every piece is a file
                yield _prep(p)
            continue
        # one entry, commas and all: "…(In die Sonne schauen, Cannes 2025).jpg"
        yield line


def _wiki_for(raw: str) -> str:
    """Which wiki's API can resolve this reference. Defaults to Commons.

    Mirrors `commons-file-resolution` SOP 5 (files can live on other wikis) and the
    upload.wikimedia.org path convention: /wikipedia/<project>/… names the project.
    Fails closed to Commons on anything not provably a Wikimedia host — the strict
    `_clean_host` keeps userinfo/ports/IP shapes from ever selecting a per-wiki API host,
    and fetches in this app always target API-returned thumbnail URLs anyway.
    """
    low = raw.lower()
    if not (re.match(r"^(https?:)?//", low) or re.match(r"^[\w.-]+\.(?:org|com|net|io)/", low)):
        return "commons.wikimedia.org"
    url = raw if low.startswith("http") else ("https:" + raw if raw.startswith("//") else "https://" + raw)
    u = urllib.parse.urlsplit(url.replace("\\", "%5C"))
    host, path = _clean_host(u.netloc), urllib.parse.unquote(u.path)
    if host is None:
        return "commons.wikimedia.org"
    if host.startswith(("upload.", "thumb.")):
        return _project_from_upload_path(path)
    if host.startswith("commons.m."):
        return "commons.wikimedia.org"
    if host.endswith(".wikimedia.org"):
        return host
    if _MEDIA_HOST.fullmatch(host):
        return host
    return "commons.wikimedia.org"


# Characters that cannot appear in a MediaWiki title. `|` is the Action API's own title
# separator, `#` starts a fragment; the rest are hard-rejected by the uploader (pattypan
# skill, "Filename validation rules": `# < > [ ] | { }`).
_TITLE_ILLEGAL = "#<>[]|{}"


def parse_entries(text: str) -> dict:
    """Split pasted input into candidate entries and classify each one.

    Accepted (case-insensitive, %-encoding tolerated, underscores = spaces):
        Foo.jpg                                     bare filename
        File:Foo.jpg                                wikitext title
        [[File:Foo.jpg|thumb|Caption]]              embedded wikitext
        https://commons.wikimedia.org/wiki/File:Foo.jpg
        https://commons.wikimedia.org/w/index.php?title=File:Foo.jpg
        https://commons.wikimedia.org/wiki/Special:FilePath/Foo.jpg
        https://upload.wikimedia.org/wikipedia/commons/0/06/Foo.jpg
        https://upload.wikimedia.org/wikipedia/commons/thumb/0/06/Foo.jpg/640px-Foo.jpg
        https://thumb.wikimedia.org/wikipedia/commons/thumb/0/06/Foo.jpg/960px-Foo.jpg
        Special:FilePath/Foo.jpg                    (with or without a host)

    Commas and semicolons inside a file name are preserved — a separator only splits
    the line when what follows looks like the start of another entry (File:, Image:,
    [[, http(s)://, //, Special:) — and a line that already parses as one entry is
    never split.
    """
    items, warnings = [], []
    for raw in _split_units(text):
        title, why = _classify(raw)
        if title:
            # `#` is a fragment separator in URLs and cannot appear in a title: MediaWiki
            # silently drops everything after it, so truncate but say so.
            if "#" in title:
                trunc = title.split("#", 1)[0]
                warnings.append({"raw": raw, "reason":
                                 f"'{title}' — a '#' starts a fragment; scoring the part before it "
                                 f"({trunc!r})"})
                title = trunc
            bad = sorted(set(_TITLE_ILLEGAL) & set(title))
            if bad and title:
                warnings.append({"raw": raw, "reason":
                                 f"'{title}' contains {' '.join(bad)} — not valid in a MediaWiki "
                                 f"title (see the pattypan skill's filename rules), so it cannot "
                                 f"exist on Commons"})
            if title:
                items.append({"raw": raw, "title": title, "wiki": _wiki_for(raw)})
        else:
            warnings.append({"raw": raw, "reason": why})
    # dedupe on (wiki, normalised title), keeping first occurrence
    seen, deduped = set(), []
    for it in items:
        key = (it["wiki"], it["title"].lower().replace("_", " "))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return {"items": deduped, "warnings": warnings}


def _norm_title(name: str) -> str:
    name = urllib.parse.unquote(name).replace("_", " ").strip()
    name = re.sub(r"\s+", " ", name)
    return "File:" + name[5:] if name.lower().startswith("file:") else "File:" + name


def _classify(raw: str):
    low = raw.lower()
    # Any scheme:// other than http(s) is refused outright — ftp://, file://, ssh:// … none of
    # them name a Commons file and none of them are ever fetched here. (Bare `File:`/`Image:`
    # namespace titles have no `//` and fall through to the title handling below.)
    if re.match(r"^[a-z][a-z0-9+.\-]*://", low) and not re.match(r"^https?://", low):
        return None, "unsupported URL scheme (only http(s) to a Wikimedia project)"
    # Anything that looks like a URL or bare host/path (with or without a scheme).
    looks_like_url = bool(re.match(r"^(https?:)?//", low)) or bool(
        re.match(r"^[\w.-]+\.(?:org|com|net|io)/", low))
    if looks_like_url:
        url = raw
        if raw.startswith("//"):
            url = "https:" + raw
        elif not raw.lower().startswith("http"):
            url = "https://" + raw
        u = urllib.parse.urlsplit(url.replace("\\", "%5C"))
        host, path = _clean_host(u.netloc) or "", urllib.parse.unquote(u.path)
        query = urllib.parse.parse_qs(u.query)
        if not host:
            return None, ("not a Wikimedia URL (userinfo, port, IP literal, or not a "
                          "Wikimedia project domain)")
        if "title" in query:                                   # index.php?title=File:…
            return _norm_title(query["title"][0]), None
        if host.startswith(("upload.", "thumb.")):             # direct media host
            parts = [p for p in path.split("/") if p]
            if not parts:
                return None, "no filename in that upload URL"
            name = _THUMB_PREFIX.sub("", parts[-1])            # 640px-Foo.jpg -> Foo.jpg
            if not os.path.splitext(name)[1]:
                return None, "no filename extension in that upload URL"
            return _norm_title(name), None
        tail = path.split("/wiki/", 1)[-1] if "/wiki/" in path else ""
        lowtail = tail.lower()
        for marker in ("special:filepath/", "special:redirect/file/"):
            if lowtail.startswith(marker):
                return _norm_title(tail[len(marker):]), None
        if lowtail.startswith(("file:", "image:")):
            return _norm_title(tail), None
        return None, "Wikimedia URL without a File: path"
    # Special:FilePath without any scheme or host
    m = re.match(r"^(?:Special:(?:FilePath|Redirect/file)/)(.+)$", raw, re.I)
    if m:
        return _norm_title(m.group(1)), None
    # titles
    if low.startswith("category:"):
        return None, "categories are not supported yet — paste the files, or request this feature"
    if low.startswith("template:"):
        return None, "templates are not images"
    if low.startswith(("file:", "image:")):
        rest = raw.split(":", 1)[1]
        name = rest.split("|")[0].strip()      # a pipe cannot appear in a title …
        if not name or not os.path.splitext(name)[1]:
            bad = sorted(set(_TITLE_ILLEGAL) & set(rest))
            if bad:
                return None, ("%r contains %s, which cannot appear in a MediaWiki title "
                              "(so this file cannot exist)" % (rest.strip(), " ".join(bad)))
            return None, "incomplete File: title (no filename)"
        return _norm_title(name), None
    # bare filename
    if os.path.splitext(low)[1] in _IMAGE_EXT:
        return _norm_title(raw), None
    return None, "unrecognised input (no File: prefix, URL and not a known media extension)"


# ----------------------------------------------------------------- Commons API
def _api(host: str, params: dict, timeout: int = 60) -> dict:
    """Query any Wikimedia wiki's Action API (multi-title lists are pipe-separated; see below)."""
    params = dict(params, format="json", formatversion="2")
    req = urllib.request.Request(f"https://{host}/w/api.php?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# Project host inferred from a media URL path, so upload.wikimedia.org links resolve on the right wiki.
_UPLOAD_PROJECT = {"commons": "commons.wikimedia.org", "meta": "meta.wikimedia.org",
                   "species": "species.wikimedia.org", "wikidata": "www.wikidata.org"}


def _project_from_upload_path(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "wikipedia" and re.fullmatch(r"[a-z0-9-]{2,20}", parts[1]):
        code = parts[1]
        return _UPLOAD_PROJECT.get(code, f"{code}.wikipedia.org" if len(code) <= 3 else
                                  f"{code}.wikimedia.org")
    return "commons.wikimedia.org"


def served_width_from_url(url):
    """The width in the `/960px-` segment of a thumburl — i.e. the rendition that exists.

    `imageinfo.thumbwidth` cannot be used for this: it echoes the *request*, so a 640 request
    reports 640 while the bytes are 960 (T388792). The `/Npx-` segment of the URL we actually
    fetched is authoritative, and the decoded bytes are the cross-check. Handles the multi-page
    and video forms (`/page1-960px-X.pdf.jpg`, `/960px--X.webm.jpg`). Returns None when the URL
    carries no size segment at all — which is what MediaWiki returns for an original smaller than
    the request (it serves the original rather than upscaling).
    """
    m = re.search(r"/(?:page\d+-)?(\d+)px-", url or "")
    return int(m.group(1)) if m else None


def resolve(items: list, batch: int = 50) -> dict:
    """Resolve File: titles to metadata + a thumbnail URL, batched per wiki.

    `items` is a list of {"title": ..., "wiki": ...}. The key of the returned mapping is
    (wiki, title). Titles are joined with "|" — **never** with commas: the Action API does not
    treat a comma as a title separator, so `titles=A,B` is one (bogus) title, and Commons file
    names legitimately contain commas.
    """
    out = {}
    by_wiki = {}
    norm_items = [({"title": it, "wiki": None} if isinstance(it, str) else it) for it in items]
    for it in norm_items:
        by_wiki.setdefault(it.get("wiki") or "commons.wikimedia.org", []).append(it["title"])
    for host, titles in by_wiki.items():
        for i in range(0, len(titles), batch):
            chunk = titles[i:i + batch]
            try:
                d = _api(host, {"action": "query", "titles": "|".join(chunk), "redirects": 1,
                                "prop": "imageinfo", "iiprop": "url|size|mime|sha1|extmetadata",
                                "iiurlwidth": THUMB_WIDTH})
            except Exception as e:
                for t in chunk:
                    out[(host, t)] = {"ok": False, "error": f"API request failed: {e}"}
                continue
            for page in d.get("query", {}).get("pages", []):
                t = page.get("title", "")
                key = (host, t)
                if page.get("missing"):
                    out[key] = {"ok": False, "error": f"not found on {host}"}
                    continue
                ii = (page.get("imageinfo") or [{}])[0]
                if not ii:
                    out[key] = {"ok": False, "error": "no imageinfo returned"}
                    continue
                em = ii.get("extmetadata", {})
                out[key] = {"ok": True, "wiki": host, "pageid": page.get("pageid"), "title": t,
                            "thumburl": ii.get("thumburl"), "url": ii.get("url"),
                            "orig_w": ii.get("width"), "orig_h": ii.get("height"),
                            "requested_w": THUMB_WIDTH,
                            "served_w": served_width_from_url(ii.get("thumburl")),
                            "mime": ii.get("mime"), "sha1": ii.get("sha1"),
                            "timestamp": ii.get("timestamp"),
                            "license": em.get("LicenseShortName", {}).get("value", ""),
                            "artist": re.sub(r"<[^>]+>", "",
                                             em.get("Artist", {}).get("value", ""))[:120]}
            # MediaWiki normalises titles (underscores, `file:` -> `File:`, first letter) and
            # follows redirects; map both back onto the title the user actually pasted.
            norm = {n["from"]: n["to"] for n in d.get("query", {}).get("normalized", [])}
            for rd in d.get("query", {}).get("redirects", []):
                norm[rd["from"]] = rd["to"]
            for requested in chunk:
                if (host, requested) in out:
                    continue
                target = norm.get(requested) or norm.get(norm.get(requested, ""), None)
                if target and (host, target) in out:
                    merged = dict(out[(host, target)])
                    merged["normalized_from"] = requested
                    out[(host, requested)] = merged
                else:
                    out[(host, requested)] = {"ok": False,
                                              "error": f"could not resolve on {host}"}
    return out


def fetch_bytes(url: str, timeout: int = 120) -> bytes:
    """Fetch a thumbnail with a hard byte cap (MAX_THUMB_BYTES, streamed — never trusts a
    Content-Length header, which can lie or be absent). Raises ValueError past the cap."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    got = 0
    chunks = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            got += len(chunk)
            if got > MAX_THUMB_BYTES:
                raise ValueError(f"media exceeds MAX_THUMB_BYTES ({MAX_THUMB_BYTES} bytes)")
            chunks.append(chunk)
    return b"".join(chunks)


def _size_ok(w, h):
    """Reject a degenerate size: a zero dimension means a truncated header, not a 0-pixel image.

    Without this a 12-byte file beginning with the PNG magic reads as `0×0`, and `0×0` sails through
    the `w*h > MAX_SEND_PIXELS` check — so a corrupt upload would be stored and scored rather than
    refused. Found by a negative control in tests/test_local.py, 2026-10-16.
    """
    return (w, h) if w > 0 and h > 0 else None


def image_size(data: bytes):
    """Width/height without Pillow: JPEG, PNG, GIF, WebP."""
    try:
        if data[:2] == b"\xff\xd8":                      # JPEG
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                              0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return _size_ok(w, h)
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            return _size_ok(int.from_bytes(data[16:20], "big"),
                            int.from_bytes(data[20:24], "big"))
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            return _size_ok(int.from_bytes(data[6:8], "little"),
                            int.from_bytes(data[8:10], "little"))
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X":
                return _size_ok(int.from_bytes(data[24:27], "little") + 1,
                                int.from_bytes(data[27:30], "little") + 1)
            if data[12:16] == b"VP8 ":
                return _size_ok(int.from_bytes(data[26:28], "little") & 0x3FFF,
                                int.from_bytes(data[28:30], "little") & 0x3FFF)
    except Exception:
        return None
    return None


# -------------------------------------------------------------- LiftWing call
def image_mime(data: bytes):
    """The format of an image from its magic bytes — the same four `image_size` can measure.

    Only used to label an upload (and to answer with the right Content-Type when the page shows it
    back). A file whose magic bytes are not one of these is not an image this server can measure, and
    is refused rather than stored on a guess: an unmeasurable upload could not be checked against
    MAX_SEND_PIXELS either, and "some bytes we hoped were a photo" is not a thing to hold on to.
    """
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def probe_endpoint(timeout: int = 45) -> dict:
    """One tiny text-only call: is the endpoint answering at all, before a run spends the queue?

    Deliberately minimal — no image, a single throwaway yes/no question — so it costs a fraction of
    a real decision call and answers the 'EngineCore 500 / service-side hang' failure modes in
    seconds. Counts against the budget like any other call.
    """
    body = {"model": "llm-clef-flash", "state": "",
            "questions": {"q": {"type": "noul", "text": "Is this a photograph of a person?"}}}
    req = urllib.request.Request(LIFTWING, data=json.dumps(body).encode(), method="POST")
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, detail = r.status, ""
    except urllib.error.HTTPError as e:
        code, detail = e.code, e.read().decode()[:120]
    except Exception as exc:
        return {"ok": False, "wall_s": round(time.time() - t0, 2), "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": code == 200, "http": code, "wall_s": round(time.time() - t0, 2),
            "error": None if code == 200 else detail}


def score_image(img_b64: str, budget: Budget, timeout: int = 180) -> dict:
    """One decision call. Honours a 429 `Retry-After`.

    The only gate here is the cooldown. `budget` *counts* calls and `HOURLY_BUDGET` is what that
    count is displayed against, but nothing here (or in `run_job`) compares `used()` with `limit`,
    so the hourly ceiling never refuses a call. The brake against overuse is the 429 stop policy
    plus a human pressing the button — do not describe this as enforcement.
    """
    wait = budget.wait_seconds()
    if wait > 0:
        time.sleep(min(wait, 300))
    body = {"model": "llm-clef-flash", "state": STATE, "images": [img_b64], "questions": QUESTIONS}
    req = urllib.request.Request(LIFTWING, data=json.dumps(body).encode(), method="POST")
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, hdrs = r.status, dict(r.headers)
            ttfb = round(time.time() - t0, 3)       # headers in hand = queue + compute done
            payload = r.read().decode()
    except urllib.error.HTTPError as e:
        code, hdrs = e.code, dict(e.headers)
        ttfb = round(time.time() - t0, 3)
        payload = e.read().decode()
    wall = round(time.time() - t0, 3)
    served = {"ttfb_s": ttfb, "upstream_ms": hdrs.get("x-envoy-upstream-service-time"),
              "server": hdrs.get("server"), "envoy_limited": hdrs.get("x-envoy-ratelimited")}
    budget.note_call()
    if code == 429:
        ra = hdrs.get("retry-after")
        budget.note_429(ra)
        return {"http": code, "wall_s": wall, "error": "rate limited",
                "retry_after": ra, "detail": payload[:200], **served}
    if code != 200:
        return {"http": code, "wall_s": wall, "error": "request failed",
                "detail": payload[:300], **served}
    try:
        j = json.loads(payload)
    except Exception:
        return {"http": code, "wall_s": wall, "error": "unparseable response",
                "detail": payload[:200], **served}
    return {"http": 200, "wall_s": wall, "answers": j.get("answers", {}),
            "usage": j.get("usage", {}), "model": j.get("model"), **served}
