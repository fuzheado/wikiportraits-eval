# WikiPortraits Eval

**Paste a list of Commons photos → get a ranked table of which ones work as a Wikipedia portrait,
with the reasons, and a report you can attach to an email.**

WikiPortraits events produce thousands of photographs, and the hard part is not taking or uploading
them — it is choosing. This tool puts every photo in front of a Wikimedia-hosted model that answers
six specific questions about it, then sorts them so the strongest candidates are at the top.

**Live:** <https://wikiportraits-eval.toolforge.org> · code:
<https://github.com/fuzheado/wikiportraits-eval> · all model calls are made by the server
(a Toolforge pod on the Wikimedia network), never by your browser.

It does not edit, tag or upload anything. It reads photos and tells you what a model thinks of them,
with the model's exact questions shown to you.

```
┌──────────────────────────────────────────────────────────────┐
│ 1 · Photos            [ paste your filenames or URLs ]       │
│                       [ Check inputs ]  [ Score them ]       │
├──────────────────────────────────────────────────────────────┤
│ 2 · Progress   8 / 12 images   endpoint normal · median 0.6s │
├──────────────────────────────────────────────────────────────┤
│ 3 · Results     [thumbnail] File:X at Y.jpg      done        │
│                 infobox ready       ▓▓▓▓▓▓▓░░░░  0.657       │
│                 face visible        ▓▓▓▓▓▓▓▓▓▓░  0.97        │
│                 pose → three_quarter                          │
│                 quality → 2.86/4                              │
│                 Download: Markdown · JSON · CSV · PDF         │
└──────────────────────────────────────────────────────────────┘
```

## What you get

- **A ranking**, not a verdict: photos are ordered by a **composite** we compute from the answers
  (face visible, eyes to camera, pose) — with the model's own **P(publish)** and **P(infobox)** shown
  beside it, so you can see all three and disagree with our weighting.
- **The reasoning**: face visibility, eye contact, pose and photographic quality (0–4) — each with its
  probability, so you can see *why* something ranked where it did.
- **A report** in Markdown, JSON, CSV or PDF — downloadable mid-run, and each one shows the photo
  next to its evaluations.
- **Candidate spread** for the run: how many photos are real candidates, how far apart they are, and
  how many sit in the uninformative 0.35–0.65 band.
- **Full transparency**: press **About** to see the exact request sent to the model, every question
  word for word, what each answer type means, and what the tool deliberately does not do.

## What it is not

- Not a notability, licence or consent check.
- Not a replacements for your eyes — it is a prioritisation aid, and the scores are model estimates.
- Not an identifier of people: it never names anyone and nothing about a person is sent.
- Not a wiki editor: nothing is written to any wiki.

## Using it

1. **Paste your list** — one photo per line, in almost any form (see below). The box starts **empty**
   with the accepted formats shown as a grey placeholder, so there is nothing runnable in it by
   accident. Press **Load test data** to fill it with the 12 real Commons portraits from
   **`default_list.txt`** (replace that file to change what everyone gets), or paste your own list —
   the placeholder disappears as soon as you click into the box.
2. **Check inputs** — the tool resolves each line against Commons and shows you what it understood
   (`Foo.jpg` → `File:Foo.jpg` → 4000×6000, CC BY-SA 4.0). Fix or delete anything flagged. If a
   previous run's results are on screen, you are asked first — starting a new run clears them.
3. **Score them** — tick **run fresh** if you want the answers re-computed instead of reusing the
   ones already saved (that is the honest way to judge speed: one model call per photo). The app
   first sends a **tiny text-only pre-flight question** to the endpoint
   and shows its verdict in the progress card ("endpoint check ✓ …"). If the endpoint does not
   answer, the run stops there instead of burning the queue on failing calls. One request per
   photo after that, cached, with live progress. A photo already scored shows instantly and costs
   nothing.
4. **Read the field at a glance** — when the run finishes, a horizontal bar chart under the stats
   shows every photo's composite, scaled to the best photo, with P(publish) and P(infobox) under
   each bar.
5. **Download** the Markdown/JSON/CSV/PDF report, or read the cards on screen.

### Forms you can paste

```
Foo.jpg
File:Foo.jpg
[[File:Foo.jpg|thumb|caption]]
commons.wikimedia.org/wiki/File:Foo.jpg
https://commons.wikimedia.org/w/index.php?title=File:Foo.jpg
https://commons.wikimedia.org/wiki/Special:FilePath/Foo.jpg
https://upload.wikimedia.org/wikipedia/commons/0/06/Foo.jpg
https://upload.wikimedia.org/wikipedia/commons/thumb/0/06/Foo.jpg/640px-Foo.jpg
```

Bullets, numbering and quotes are stripped; upper/lower case, underscores and `%20` are tolerated;
duplicates merge. **File names containing commas are safe** — the tool only splits on commas when
another entry clearly follows (`Foo.jpg, Bar.jpg` splits; `…(In die Sonne schauen, Cannes 2025).jpg`
does not). Files hosted on other Wikimedia wikis work too and are labelled with their wiki.

Non-photo media (SVG, PDF, video) also resolve, because Commons renders a raster preview.

Not supported yet: `Category:` listings — paste the file names instead.

## What the numbers mean

Six questions are asked in one pass; the model generates no text, it returns probabilities. Three
headline numbers come out of them, and they are not the same kind of thing:

- **composite** — *our* arithmetic, not the model's: `min(face_visible, eyes_to_camera) × pose
  weight` (head-and-shoulders 1.0, three-quarter 0.9, full body 0.6, candid 0.5, group 0.25). **This
  is the ranking key**, and every ingredient is shown on the card so you can disagree with the
  weighting. It exists because questions about something *visible in the frame* separate photos well
  while editorial ones collapse toward the middle (measured on 57 photos: face_visible spread 0.372,
  eyes_to_camera 0.350, infobox_ready 0.229, quality 0.145).
- **P(publish)** — the model's own two-option answer to *"an editor needs one photograph of this
  person"*: it chooses **publish** ("this photograph is the one to use") or **pass**. Chosen because
  it spreads without piling up at the ceiling.
- **P(infobox)** — the model's own yes/no on the original wording. Kept for continuity, shown, but no
  longer what the list is ranked by.

Also per photo: **face visible**, **eyes → camera**, **pose**, and **quality** (0–4: 0 unusable,
1 poor, 2 acceptable, 3 good, 4 excellent — that wording is the *entire* definition, and the model
sees a 640 px-wide thumbnail, so treat quality as a comparison between photos, not a technical grade).

The run finishes with **candidate spread**: how many photos clear the candidate floor (0.25), the gap
between the best and worst candidate, the median gap between neighbours, and how many answers sit in
the uninformative 0.35–0.65 band — reported for all three headline numbers. It replaces standard
deviation, which rises whenever answers pile up at either end, including at the ceiling.

A caution worth repeating: **"≥ 0.5" is a counting convenience, not a validated cut-off.** In our
samples nothing scored above ~0.71, so trust the ranking (what is best among these?) more than any
absolute threshold. Calibrating a real threshold needs human-labelled photos — it is on the roadmap.

**On the clustering question (2026-10-13):** we tested five rewrites of the infobox question on the
same photos. Every rewrite emptied the middle band but did it by pushing all the real portraits up to
0.81–0.96, which makes it *harder* to choose between good photos — the spread among candidates fell
from 0.486 to as little as 0.021. So the current wording stays, and the finding is recorded rather
than the wording changed. Practical takeaway for now: read the order, not the decimal — and if the
numbers all look similar, that is a presentation problem we will fix with a rank and percentile rather
than a new prompt. Full write-up and raw answers: `research/wording-experiment-2026-10-13.md`.

## When the model is slow

The app measures every call and labels the run against a baseline measured on the machine it runs
on:

- **normal** — calls match the warm baseline (~0.5 s when the model is healthy)
- **elevated** — 1.5–4× (usually a cold start or a queued request)
- **service-side** — over 4× the baseline with no errors: the endpoint itself is slow. Each row
  shows a live `waiting for the model — 18.4s` counter so a slow run looks slow for the right
  reason.

On 2026-10-13 the endpoint ran at ~28.6 s per call — 53× the baseline — for both text-only and
image requests, with no rate-limit errors. The tool reports that as `service-side` rather than
hiding it. The platform is officially experimental and has no availability SLA.

The model itself is new and still being adjusted: `llm-clef-flash` is Cloudflare's Clef, deployed on
LiftWing under [Phabricator **T440223**](https://phabricator.wikimedia.org/T440223) — still
`Open, Needs Triage`, with a stated target of **under one second per image**. That is why the same
tool measured 28.6 s/call on 2026-10-13 and 0.42 s/call on 2026-10-14: the slow runs are the service
missing its own target, not this app misbehaving. The ticket is also the place to look for the
endpoint's known failure modes, including the `EngineCore` outage the tool reports as a failed
pre-flight check.

## Privacy and data flow

- The browser only talks to this app. Commons lookups, thumbnail downloads and the model call are
  made **by the server**, so the IP the endpoint sees is the host running the tool.
- The one exception: the thumbnails shown in the results list are loaded by your browser from
  `thumb.wikimedia.org`.
- Sent to the model: the image bytes and a fixed, published context sentence. **Not sent**: file
  names, page text, user names, or anything about who is in the photo.
- Results are cached locally; job state lives in memory. No accounts, no analytics, no third parties.

## Running it yourself

Nothing to install — Python 3.9+ standard library only (the PDF writer included).

```bash
python3 app.py                 # http://127.0.0.1:8765
PORT=8080 python3 app.py       # honour a supplied port (Toolforge does this)
```

Optional settings: `THUMB_WIDTH` (960 — must be a step of Commons' `$wgThumbnailSteps` ladder, since
Commons quantises the request upward; 640 is not a step, so a 640 request downloads the 960 asset and
reports 960 as its width), `SPACING` (0.9 s between call *starts*, global across
`INFLIGHT` streams), `INFLIGHT` (1 = serial, the default; set 2 for two concurrent calls within a run — call starts stay SPACING apart globally), `HOURLY_BUDGET` (100 — the app's *own* courtesy cap, not the platform's: see **Be a good citizen**),
`MAX_PARALLEL_JOBS` (2), `MAX_INPUT_ITEMS` (200 images per run), `PARSE_MAX` (1000 per parse),
`EVAL_MAX` (25, the per-call cap for `POST /api/eval`), `MAX_THUMB_BYTES` (20 MB per fetched
thumbnail), `MAX_SEND_PIXELS` (40 MP per sent image), `MAX_JOBS_KEPT` (100 finished jobs kept),
`PROBE_TTL` (600 s, how long a pre-flight verdict is reused), `CACHE_DIR` (`./cache`),
`BASELINE_WARM_S` (0.54), `WIKIMEDIA_USER_AGENT` (please set your own contact URL if you deploy
this).

### Deploying to Toolforge

Build Service (no pip install needed — `Procfile` is already `web: python3 app.py`):

```bash
ssh <user>@login.toolforge.org
become <tool>
toolforge build start https://github.com/Fuzheado/wikiportraits-eval
toolforge webservice buildservice start --mount=none -m 512Mi
curl https://wikiportraits-eval.toolforge.org/api/health
```

Traditional uWSGI backend: copy `core.py`, `wiclient.py`, `reports.py`, `pdfmin.py`, `static/` and
`wsgi.py` (as `app.py`) into `$HOME/www/python/src/` and start the webservice.

Running from Toolforge/Cloud VPS lifts the quota entirely: LiftWing classifies WMCS egress as a
**known network** (`x-trusted-request: A`), which is *effectively unlimited* rather than the shared
100 requests/hour anonymous budget. No API key or configuration is needed — it follows from where the
code runs, not from how it authenticates.

## Be a good citizen

Which limit applies depends entirely on **where the app runs**, and the two cases are not close
([Rate Limits & Access](https://wikitech.wikimedia.org/wiki/Machine_Learning/LiftWing/Large_Language_Models#Rate_Limits_&_Access)):

| the app runs on… | tier | practical limit |
|---|---|---|
| **Toolforge / Cloud VPS** (the deployment) | known network, `x-trusted-request: A` | **effectively unlimited** |
| **a laptop**, or anywhere else (local dev) | public / anonymous | **100 requests/hour**, shared per client across *all* `llm-*` models |

So the 100 calls/hour cap in this app is **self-imposed**, not imposed: on the deployment no external
quota requires it. It stays because being a good neighbour to a brand-new service (§13 of
`HANDOFF.md`) matters more than speed here, and because a public tool with no login should not be
usable as a load generator pointed at the endpoint. Either way the app scores one photo at a time
with a small gap between calls, stops when the server says 429, and caches every answer by
`(wiki, pageid, sha1, schema, width)` — re-scoring a photo you have already scored costs nothing, and
re-uploading a file invalidates its cache entry automatically.

## HTTP API

Everything the page does is also available programmatically (also documented in the **About**
panel, generated live from the code):

```bash
POST /api/parse  '{"text": "Foo.jpg\nBar.jpg"}'              # resolve + metadata, no scoring
POST /api/eval   '{"files": ["File:A.jpg", "File:B.jpg"]}'   # blocked; final ranked results (≤ EVAL_MAX)
POST /api/run    '{"text": …}'                               # async; poll GET /api/job/<id>
GET  /api/job/<id>/report.{md,json,csv,pdf}                   # reports for a finished job
```

`/api/eval` returns `results[]` (composite, P(publish), per-question probabilities, pose, quality,
licence, Commons link), the run's `spread` and `stats`, and links to the four reports.

## Reports

Markdown, JSON, CSV and PDF — downloadable while a run is still going (they cover whatever has
finished so far), and every one of them **shows the photo next to its evaluations**:

- **Markdown** — the ranked table, then one section per photo with the thumbnail inline above its
  determinations.
- **PDF** — the same, with each photo embedded as a real image (JPEG passthrough, PNG decoded) in a
  card beside its numbers, **plus the composite-distribution chart**: every photo's thumbnail plotted
  on a 0 → best-score axis, stacked when they would overlap — the same picture the web UI shows. Self-contained: no external assets, so it looks the same wherever it is
  opened.
- **JSON** — everything, including the raw answers, the composite, the served thumbnail width
  (`served_w`) and the run's spread.
- **CSV** — one row per photo with `composite` and `P_publish` columns, for spreadsheets.

A photo that cannot be embedded is listed at the end of the PDF rather than silently dropped.

## Tests

```bash
bash run_all_tests.sh        # everything below, in order + the live contract test
python3 test_forms.py        # input formats (offline)
python3 test_commas.py       # file names containing commas (offline)
python3 test_wsgi.py         # Toolforge uWSGI entry point (offline)
python3 test_pipeline.py     # download/scoring overlap + state machine (offline)
python3 test_reports.py      # composite, P(publish), spread, thumbnails in MD/PDF (offline)
python3 test_thumb_width.py  # served width, ladder steps, cache keying (offline)
python3 test_default_list.py # the starter list + the "run fresh" flag (offline)
python3 test_default_list.py --live      # live: 1 batched Commons call, 0 model calls
python3 check_js.py          # the inline script still parses (offline)
python3 check_compat.py      # no syntax newer than the advertised Python 3.9 floor (offline)
.venv-pdf/bin/python test_pdf_images.py  # renders the PDF back to PNG (venv: pypdfium2+pillow)
node test_render_dom.js      # the results list, ranking and spread in a DOM (needs `npm install`)
python3 test_ui_contract.py [URL]        # live: UI wiring, payload fields, About content
WANT=4 python3 live_pipeline_check.py [URL]   # live: pipeline, states, latency on fresh files
python3 verify_reports_live.py [URL]     # live: 3 photos end to end, all four reports inspected
```

**Two checks run automatically.** With `git config core.hooksPath .githooks` (once per clone), a
pre-commit hook runs the offline suites on every commit — and verifies the starter list against
Commons **only when `default_list.txt` is staged**, because a network check should never block an
unrelated commit on Wikimedia being up. `SKIP_LIVE=1` and `SKIP_HOOKS=1` override it. The same split
runs in CI (`.github/workflows/checks.yml`): offline suites on Python 3.9 and 3.12 plus the DOM test
on every push, and the Commons check on demand. **Neither ever scores a photo** — that spends the
hour's quota and calls a service that is still new (see `HANDOFF.md` §13).

## Project documents

- **`default_list.txt`** — the example list behind the **Load test data** button (12 real Commons
  portraits, one `File:` title per line). **Replace this file** to change the default for everyone: it
  is read at request time, so it needs no code change. It is never pre-loaded into the box — the page
  starts empty and the list arrives only when someone asks for it. No comment lines in it — a `#` is
  part of a Commons filename, and the parser would treat your comment as a photo name.
- **`PRD.md`** — what this is for, requirements, architecture, decisions (including rejected
  alternatives and what would reopen them), risks, roadmap.
- **`HANDOFF.md`** — for whoever maintains or extends this next: invariants, traps, measured
  numbers, and the queue of improvements.
- **`research/`** — the question-design pilot (2026-10-13): write-up, raw answers, chart. Read this
  before changing any question wording.

Model: `clef-flash` 9.41 B (Apache-2.0, Cloudflare), served by the Wikimedia Foundation on
[LiftWing](https://wikitech.wikimedia.org/wiki/Machine_Learning/LiftWing/Large_Language_Models) —
deployed under [Phabricator T440223](https://phabricator.wikimedia.org/T440223), a new service whose
stated target is under a second per image.
Photographs remain under their own Commons licences. App code MIT.
