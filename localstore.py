"""Uploaded images: held in memory, and nowhere else.

The tool's other images come from Commons, so they have a URL, a pageid and a licence, and the
reports can always go back for them. An image someone drags in from their own disk has none of that
— and that is a *privacy* condition, not a technical one: it is somebody's file, quite possibly a
photograph of a person who never agreed to Wikimedia storing it.

So the rule this module exists to enforce is that an upload never has a longer life than the process
that received it:

  * **in memory only** — never opened for writing on disk, so it is not in `cache/`, not in
    `cache/thumbs/`, not on NFS, not in a backup, and not in a container layer. Nothing here touches
    the filesystem, and that is the point (see HANDOFF §7.4: the cache becoming an NFS mount is
    planned, and uploads must not follow it there);
  * **bounded** — an anonymous endpoint on shared infrastructure cannot be allowed to grow memory
    without limit, so the store evicts its least-recently-used entries past `MAX_LOCAL_BYTES` /
    `MAX_LOCAL_ITEMS`, and sweeps anything idle past `LOCAL_TTL_S`;
  * **pinned while in use** — a run holds the ids it is about to score, so eviction cannot pull an
    image out from under a job that is mid-flight;
  * **content-addressed** — the id is the sha256 of the bytes, so re-adding the same file is
    recognised rather than duplicated, and no filename is ever trusted for identity (two different
    `IMG_0001.jpg` from two folders are two images; the same file under two names is one).

Everything mutable is behind one lock: uploads arrive on request threads while a run reads them on
its own threads.
"""
import hashlib
import os
import threading
import time
from contextlib import contextmanager

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(1024 * 1024)))            # one image
# **Keep this at or below the request-body ceiling** (`core.MAX_BODY_BYTES`, 1 MiB by default): the
# backends refuse an over-ceiling body before routing, so a larger number here would be unreachable —
# an advertised limit the server cannot accept. `core.MAX_UPLOAD_BYTES` takes the smaller of the two
# and that is the number published, and `tests/test_local.py` asserts the relationship, so a change to
# either constant is caught rather than becoming a lie in `/api/config`.
MAX_LOCAL_BYTES = int(os.environ.get("MAX_LOCAL_BYTES", str(64 * 1024 * 1024)))      # the whole store
MAX_LOCAL_ITEMS = int(os.environ.get("MAX_LOCAL_ITEMS", "400"))
LOCAL_TTL_S = int(os.environ.get("LOCAL_TTL_S", str(6 * 3600)))                      # idle lifetime


class Rejected(Exception):
    """The store will not take this. `status` is the HTTP code the route should answer with."""

    def __init__(self, message, status=413):
        super().__init__(message)
        self.status = status


_LOCK = threading.RLock()
_ITEMS = {}          # id -> entry (dict); insertion order is the LRU order, re-inserted on touch
_TOTAL = 0           # summed bytes of every entry, kept in step with _ITEMS
_PINNED = set()      # ids a live run is holding
_COUNTS = {"stored": 0, "evicted": 0, "rejected": 0, "duplicate": 0}


def digest(data: bytes) -> str:
    """The id of an image: its content, not its name."""
    return hashlib.sha256(data).hexdigest()


def version_of(data: bytes) -> str:
    """A short id for display — enough to tell two uploads apart in a report, not a security token."""
    return digest(data)[:12]


def _expire_locked(now: float):
    """Drop anything idle past the TTL. Never raises — a `get` must not fail because of a sweep."""
    global _TOTAL
    if LOCAL_TTL_S <= 0:
        return
    for k in [k for k, e in _ITEMS.items() if now - e["touched"] > LOCAL_TTL_S]:
        _TOTAL -= _drop_locked(k)


def _sweep_locked(now: float):
    """Drop idle entries and then, if still over the cap, the least recently touched."""
    global _TOTAL
    _expire_locked(now)
    while (_TOTAL > MAX_LOCAL_BYTES or len(_ITEMS) > MAX_LOCAL_ITEMS) and _ITEMS:
        oldest = next((k for k in _ITEMS if k not in _PINNED), None)
        if oldest is None:
            # Everything left is pinned by a run in flight. Refuse to go over the cap rather than
            # evict an image a job is mid-way through scoring.
            raise Rejected(f"local image memory is full ({_TOTAL} of {MAX_LOCAL_BYTES} bytes) and "
                           f"the run itself is holding the rest — wait for it to finish, or restart "
                           f"the service to clear the store")
        _TOTAL -= _drop_locked(oldest)
        _COUNTS["evicted"] += 1


def _drop_locked(key: str) -> int:
    entry = _ITEMS.pop(key, None)
    return len(entry["data"]) if entry else 0


def put(data: bytes, name: str = "", mime: str = "", orig=None, now: float = None) -> dict:
    """Store one uploaded image and return its entry (without duplicating it if it is already here).

    `orig` is the (w, h) the *browser* read before it resized, kept so the result can say what the
    model was shown out of what the user had — the same honesty the Commons path applies to a
    5000px original.
    """
    global _TOTAL
    if not data:
        raise Rejected("empty upload", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        _COUNTS["rejected"] += 1
        raise Rejected(f"upload is {len(data)} bytes — the cap is {MAX_UPLOAD_BYTES} bytes "
                       f"per image (MAX_UPLOAD_BYTES)", 413)
    now = time.time() if now is None else now
    key = digest(data)
    with _LOCK:
        _sweep_locked(now)
        entry = _ITEMS.pop(key, None)          # re-insert: LRU order is the dict's insertion order
        if entry:
            _COUNTS["duplicate"] += 1
            entry["touched"] = now
            _ITEMS[key] = entry
            return dict(entry, duplicate=True)
        _TOTAL += len(data)
        _ITEMS[key] = {"id": key, "data": data, "name": (name or "").strip(), "mime": mime,
                       "bytes": len(data), "added": now, "touched": now,
                       "orig_w": (orig or (None, None))[0], "orig_h": (orig or (None, None))[1]}
        _COUNTS["stored"] += 1
        try:
            _sweep_locked(now)                 # the new arrival itself may push us over the cap
            if key not in _ITEMS:
                # The sweep had to evict the arrival itself, which means this one image is larger
                # than the store's entire budget — no configuration makes it fit, so say so instead
                # of returning an entry that no longer exists (which raised KeyError before this
                # guard; found by tests/test_local.py).
                _COUNTS["rejected"] += 1
                raise Rejected(f"a {len(data)}-byte image does not fit the store's "
                               f"{MAX_LOCAL_BYTES}-byte budget (MAX_LOCAL_BYTES)", 413)
        except Rejected:
            _TOTAL -= _drop_locked(key)        # no-op when the sweep already took it
            raise
        return dict(_ITEMS[key], duplicate=False)


def get(key: str, now: float = None):
    """The bytes, or None if this id is unknown here (never stored, evicted, or the pod restarted).

    Sweeps expired entries on the way in, so the store's "dropped after N hours idle" promise is true
    of a `get` and not only of the next upload. Without this, an idle image was served happily
    until somebody happened to upload something new.
    """
    with _LOCK:
        now = time.time() if now is None else now
        _expire_locked(now)
        entry = _ITEMS.get(key)
        if not entry:
            return None
        entry["touched"] = now
        return entry["data"]


def meta(key: str):
    """Everything the store knows about an image except the bytes themselves."""
    with _LOCK:
        entry = _ITEMS.get(key)
        return {k: v for k, v in entry.items() if k != "data"} if entry else None


def exists(key: str) -> bool:
    with _LOCK:
        return key in _ITEMS


def ids() -> list:
    with _LOCK:
        return list(_ITEMS)


@contextmanager
def hold(keys):
    """Pin ids for the duration of a run, so eviction cannot take them mid-flight."""
    keys = {k for k in keys if k}
    with _LOCK:
        _PINNED.update(keys)
    try:
        yield
    finally:
        with _LOCK:
            _PINNED.difference_update(keys)


def unique_names(keys) -> dict:
    """A display name per id, made unique within this set.

    Two files can share a basename (`IMG_0001.jpg` in two folders), and the page keys its rows, its
    chart nodes and the detail popup on the title — so a duplicate title would silently collapse two
    photos into one row. Their bytes still identify them; only the label is disambiguated.
    """
    out, seen = {}, {}
    for k in keys:
        m = meta(k) or {}
        base = (m.get("name") or "").strip() or f"local-{version_of(b'')}"
        if not m:
            base = f"missing-{k[:8]}"
        seen[base] = seen.get(base, 0) + 1
        out[k] = base if seen[base] == 1 else f"{base} ({seen[base]})"
    return out


def stats() -> dict:
    with _LOCK:
        return {"items": len(_ITEMS), "bytes": _TOTAL, "pinned": len(_PINNED),
                "max_bytes": MAX_LOCAL_BYTES, "max_items": MAX_LOCAL_ITEMS,
                "max_upload_bytes": MAX_UPLOAD_BYTES, "ttl_s": LOCAL_TTL_S,
                "retention": "memory only — never written to disk; dropped when the service "
                             "restarts or when the store grows past its cap",
                **_COUNTS}


def clear():
    """Empty the store. Only tests and a deliberate reset should need this."""
    global _TOTAL
    with _LOCK:
        _ITEMS.clear()
        _TOTAL = 0
        _PINNED.clear()
