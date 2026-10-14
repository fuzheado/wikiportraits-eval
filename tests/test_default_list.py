#!/usr/bin/env python3
"""Offline checks for the starter list (default_list.txt) and the "run fresh" cache bypass.

`default_list.txt` at the repo root is the first thing anyone runs, so it is worth a test: it is fed
to the parser *verbatim*, and a `#` is a character in a Commons filename rather than a comment, so a
"comment line" in that file would silently become a bogus entry. Costs nothing — no network.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this file is in tests/
sys.path.insert(0, ROOT)

import core          # noqa: E402
import wiclient      # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got!r}" + ("" if ok else f"  want {want!r}"))
    if not ok:
        FAIL.append(name)


path = os.path.join(ROOT, "default_list.txt")
check("default_list.txt is at the repo root", os.path.exists(path), True)
text = open(path, encoding="utf-8").read()
lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

check("the starter list is not empty", len(lines) > 0, True)
check("every line is a File: title", all(ln.startswith("File:") for ln in lines), True)
check("no '#' anywhere (the parser truncates at it)", "#" in text, False)
check("no duplicate entries", len(set(lines)), len(lines))

# the app's own parser is the only thing that decides what these lines mean
parsed = wiclient.parse_entries(text)
check("parse_entries finds every line", len(parsed["items"]), len(lines))
check("parse_entries warns about nothing", parsed["warnings"], [])
check("every parsed entry keeps its File: title",
      all(i["title"].startswith("File:") for i in parsed["items"]), True)
check("the list fits MAX_INPUT_ITEMS", len(lines) <= core.MAX_INPUT_ITEMS, True)
check("core.default_list_text() is the file's contents", core.default_list_text(), text.strip())

# the route the UI actually calls on load
status, ctype, payload, _hdr = core.route("GET", "/api/default-list")
body = json.loads(payload)
check("GET /api/default-list is 200", status, 200)
check("served as JSON", ctype, "application/json")
check("the route serves the file verbatim", body.get("text"), text.strip())
check("the route counts the entries", body.get("count"), len(lines))
check("the route names its source", body.get("source"), "default_list.txt")

# --- "run fresh": the flag must reach the job, and must default to off ---------------------
j = core.new_job("File:Test 1.jpg")
check("a normal job does not bypass the cache", j.get("no_cache"), False)
j2 = core.new_job("File:Test 1.jpg", no_cache=True)
check("no_cache is recorded on the job", j2.get("no_cache"), True)
# /api/run accepts either a text blob or an item list; both must carry the flag
j3 = core.new_job(["File:X.jpg"], no_cache=True)
check("no_cache works for an item list too", j3.get("no_cache"), True)
check("no_cache defaults to off for item lists", core.new_job(["File:X.jpg"]).get("no_cache"), False)

# --- optional live check: needs the network (one batched Commons API call, zero model calls) ---
# Deliberately opt-in. A pre-commit hook or CI job must never fail because Wikimedia is having an
# outage or has rate-limited us, so this never runs as part of the offline suite — the hook runs it
# only when default_list.txt itself changed, and CI runs it on demand.
if "--live" in sys.argv:
    print("\nlive Commons check (one batched API call, no model calls):")
    parsed_live = wiclient.parse_entries(text)
    reqs = [{"title": i["title"], "wiki": i.get("wiki")} for i in parsed_live["items"]]
    try:
        resolved = wiclient.resolve(reqs)
    except Exception as exc:                                       # noqa: BLE001
        check(f"the live check reached the Commons API ({exc})", False, True)
        resolved = {}
    problems, pids = [], []
    for i in parsed_live["items"]:
        m = resolved.get(("commons.wikimedia.org", i["title"])) or {}
        if not m.get("ok"):
            problems.append(f"{i['title']}: {m.get('error')}")
            print(f"    MISS  {i['title']}")
            continue
        if not (m.get("license") or "").strip():
            problems.append(f"{i['title']}: no licence in Commons metadata")
        pids.append(m.get("pageid"))
        print(f"    ok    {m.get('orig_w')}x{m.get('orig_h'):<5} served {str(m.get('served_w')):>4}  "
              f"{(m.get('license') or '(none)')[:15]:17} {i['title'][:56]}")
    check("every starter file resolves on Commons", problems, [])
    check("all pageids are distinct", len(set(pids)), len(pids))

print()
print(f"FAILURES: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
