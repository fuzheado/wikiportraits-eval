#!/usr/bin/env python3
"""Does the model actually SEE a microphone? — validating the `face_unobstructed` question.

The composite gained a bounded obstruction factor on 2026-10-14 (HANDOFF §11): a mic, hand or phone
held in front of the face scales the score by 0.5–1.0. That is only worth having if the model's answer
tracks what a person can see, so this measures it rather than assuming it.

Run it against a **local** app (it spends one call per photo):

    python3 app.py &
    python3 tools/probe_obstruction.py [http://127.0.0.1:8765]

It prints, per photo, P(face unobstructed) next to the answers the factor sits on, the composite with
and without the factor, and writes each thumbnail to /tmp/obstruction/ so the model's verdict can be
checked against the picture.
"""
import json
import os
import shutil
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
HDR = {"User-Agent": "WikiPortraitsEval/probe (obstruction check)", "Content-Type": "application/json"}
OUTDIR = "/tmp/obstruction"

# Mixed on purpose: photos where a microphone (or a hand) is held over the face, and portraits with
# nothing in front of it as controls — a question that fires on both is not measuring anything.
TITLES = [
    "File:Portrait of an Arab man speaking at microphone.jpg",
    "File:Gerald Ford speaking into microphones, 9 Aug 1974.jpg",
    "File:Katsuya Okada-Public speaking-1-20050409.jpg",
    "File:Daniel Patrick Moynihan, speaking behind microphones.jpg",
    "File:Stuart Rintoul speaks at Adelaide Writers' Week 2021 (portrait).jpg",
    "File:Erica McLain speaking.jpg",
    "File:Matthew Taylor of the RSA speaking at The European Conference on Education in Brighton, UK.jpg",
    # controls: a mic-holding performer, then three studio portraits with nothing held up
    "File:Charity the Artist at Black Week 2025 - 01.jpg",
    "File:Leslie Cohen portrait at WikiPortraits Studio at Sundance 2026 08.jpg",
    "File:Erica Lockheimer at SXSW 2025 03.jpg",
    "File:Leslie Cohen portrait at WikiPortraits Studio at Sundance 2026 08.jpg",
]


def call(path, data=None, timeout=900):
    req = urllib.request.Request(URL + path, headers=HDR)
    if data is not None:
        req.data = json.dumps(data).encode()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    started = call("/api/run", {"text": "\n".join(TITLES)})
    jid = started["job_id"]
    print(f"job {jid}: {started['total']} photos — one model call each\n", flush=True)
    t0 = time.time()
    while time.time() - t0 < 1800:
        job = call(f"/api/job/{jid}")
        settled = sum(1 for i in job["items"] if i["status"] in ("done", "error", "notfound"))
        if settled == len(job["items"]):
            break
        print(f"  … {settled}/{len(job['items'])}", flush=True)
        time.sleep(5)

    rows = []
    for it in job["items"]:
        ans = (it.get("result") or {}).get("answers") or {}
        unobs = (ans.get("face_unobstructed") or {}).get("noul")
        face = (ans.get("face_visible") or {}).get("noul")
        eyes = (ans.get("eyes_to_camera") or {}).get("noul")
        pose = (ans.get("pose") or {}).get("choice")
        comp = it.get("composite")
        if it["status"] != "done" or None in (face, eyes):
            print(f"  !! {it.get('title')} — {it['status']} {it.get('error') or ''}")
            continue
        import wiclient
        raw = min(face, eyes) * wiclient.POSE_WEIGHT.get(pose, 0.5)
        rows.append((unobs, it.get("title") or "", face, eyes, pose, raw, comp, it))
        src = it.get("thumb_file")
        if src and os.path.exists(src):
            shutil.copy(src, os.path.join(OUTDIR, os.path.basename(src)))

    print(f"\n  {'P(unobstr)':>10} | {'face':>5} {'eyes':>5} {'pose':>14} | {'raw':>6} {'final':>6} | photo")
    print("  " + "-" * 104)
    for unobs, title, face, eyes, pose, raw, comp, _it in sorted(rows):
        print(f"  {unobs:>10.3f} | {face:>5.2f} {eyes:>5.2f} {str(pose):>14} | "
              f"{raw:>6.3f} {comp:>6.3f} | {title.replace('File:', '')[:46]}")

    if rows:
        import statistics
        held = [r for r in rows if r[0] < 0.5]
        clear = [r for r in rows if r[0] >= 0.5]
        print(f"\n  P(unobstructed) < 0.5 (model says something is in front of the face): {len(held)}")
        print(f"  P(unobstructed) >= 0.5 (model says the face is clear):                 {len(clear)}")
        if held and clear:
            print(f"  separated: held median {statistics.median(r[0] for r in held):.3f} vs "
                  f"clear median {statistics.median(r[0] for r in clear):.3f}")
        print(f"\n  {len(rows)} thumbnails in {OUTDIR}/ — LOOK AT THEM before trusting the numbers.")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
