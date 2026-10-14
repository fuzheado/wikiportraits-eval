#!/usr/bin/env python3
"""Verify the reported comma case end-to-end against the running server."""
import json, time, urllib.request

API = "https://shopping-wait-confidentiality-occasionally.trycloudflare.com"
LINE = "File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg"


def post(path, obj):
    req = urllib.request.Request(API + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(API + path, timeout=180) as r:
        return json.load(r)


print("input line:", LINE)
d = post("/api/parse", {"text": LINE})
print("parse ->", json.dumps(d, indent=1))

job = post("/api/run", {"text": LINE})
jid = job["job_id"]
for _ in range(90):
    j = get(f"/api/job/{jid}")
    if j["status"] in ("done", "error"):
        break
    time.sleep(1)
print("\njob:", j["status"], "| items:", len(j["items"]))
for it in j["items"]:
    a = ((it.get("result") or {}).get("answers") or {})
    print(f"  {it['title']}")
    print(f"  status={it['status']} sent={it.get('sent_px')} original={it.get('orig_w')}x{it.get('orig_h')} "
          f"cached={it.get('from_cache')}")
    if a:
        print(f"  face_visible={a['face_visible']['noul']:.3f} pose={a['pose']['choice']} "
              f"quality={a['quality']['score']:.3f} infobox_ready={a['infobox_ready']['noul']:.3f} "
              f"eyes_to_camera={a['eyes_to_camera']['noul']:.3f}")
    else:
        print("  error:", it.get("error"))
print("stats:", json.dumps({k: v for k, v in j["stats"].items() if k in
                            ("n_input", "n_resolved", "n_scored", "p_infobox_mean", "n_failed")}))
