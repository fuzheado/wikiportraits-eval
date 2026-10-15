# WikiPortraits Eval — Product Requirements Document

**Version:** 0.3.0 · **Status:** prototype, in daily use for triage · **Last updated:** 2026-10-13
**Owner:** Andrew Lih (fuzheado) · **Implementation:** this repository

---

## 1. Summary

A single-page web app that turns a pasted list of Wikimedia Commons photographs into a ranked,
downloadable assessment of how well each photo works as a Wikipedia biography portrait. Each photo
is sent **once** to `llm-clef-flash` — Cloudflare's decision model, served by the Wikimedia
Foundation on LiftWing — with a fixed six-question schema, and the app reports the model's
probabilities per photo plus aggregate statistics.

One sentence: *paste photo references, get a ranked, auditable triage table, and download it.*

## 2. Problem and context

WikiPortraits (and similar event-photography efforts) produce Commons photos in bulk — hundreds or
thousands per event — and the bottleneck is no longer capture or upload but **selection**: which of
these frames are usable as an article's lead image, and which deserve a photographer's or editor's
attention first.

Existing signals do not answer that. Commons categories describe provenance, not photographic
suitability; "quality" assessments for article *text* (the ORES/LiftWing article-quality models) do
not apply to images; and uploaders are not neutral judges of their own frames. Meanwhile a model
that answers exactly this kind of typed question arrived on Wikimedia infrastructure on 2026-10-09,
when `llm-clef-flash` was added to the public LiftWing LLM list — and nothing user-facing existed to
exercise it on a real photo set.

The app is also a **transparency vehicle**: because the model returns probabilities rather than
sentences, it is unusually easy to show a user exactly what was asked, what came back, and what it
does and does not mean. The About panel exists to make that the default, not an afterthought.

## 3. Users and jobs to be done

| User | Job | What they need from the app |
|---|---|---|
| WikiPortraits photographer / uploader | Triage my own batch before I nominate anything | Paste my filenames, see a ranking, download a PDF I can work from |
| WikiProject editor | Find the best available portrait for a biography | Ranked table with the reasons (`pose`, `eyes_to_camera`, `quality`) visible per photo |
| Event organiser / GLAM coordinator | Report which photos are ready for article use | Aggregate stats + a shareable report (PDF/Markdown) |
| Researcher / platform contributor | Understand what the Clef model actually decides | Verbatim prompt, per-question probabilities, timings, honest limits |

Non-users: nobody is asked to *act* on the scores automatically. The app never edits a wiki.

## 4. Goals and non-goals

**Goals**

1. Accept whatever a user has on the clipboard — bare filenames, `File:` titles, page URLs, upload
   or thumbnail URLs, wikitext — without preprocessing.
2. Score every photo with the same schema, deterministically (same photo ⇒ same answer, cached).
3. Show honest per-photo progress and timing, including when the *endpoint* is the bottleneck.
4. Produce reports a person can attach to an email: Markdown, JSON, CSV, PDF.
5. Be auditable: the exact request, the answer semantics and the limits are one click away.
6. Run on Toolforge with no dependency installation, and locally with plain `python3`.

**Non-goals**

- Editing, tagging, categorising or uploading anything to any wiki.
- Identifying people, or reasoning about notability, consent or licensing.
- Replacing human review, or producing a "score" presented as a fact about a photograph.
- Being a general Commons browser, gallery, or media-search tool.

## 5. Functional requirements

| # | Requirement | Status |
|---|---|---|
| F1 | Paste box accepting all documented reference forms, one per line, with bullets/quoting tolerated | shipped |
| F2 | Input check step: resolve each line, show what it became, warn on unparseable/ambiguous lines | shipped |
| F3 | Per-wiki resolution (Commons and other Wikimedia wikis), normalisation and redirect mapping back to the pasted string | shipped |
| F4 | Duplicate merging on `(wiki, case-folded title)` | shipped |
| F5 | One scoring call per photo, fixed schema, results cached by `(wiki, pageid, sha1, schema, width)` | shipped (seven questions since 2026-10-14; six from 2026-10-13) |
| F6 | Live progress: overall bar + per-photo state (`queued`/`downloading`/`ready`/`scoring`/`done`) + per-photo elapsed seconds while scoring | shipped |
| F7 | Aggregate stats: mean/median/max per headline number, candidate spread, pose mix, tokens, median call time | shipped |
| F8 | Reports in Markdown, JSON, CSV, PDF — downloadable mid-run, each showing the photo next to its evaluations | shipped |
| F9 | About panel: verbatim prompt, question legend, answer semantics, limits, data flow, provenance | shipped |
| F10 | Latency verdict per run (`normal`/`elevated`/`service-side`) against a measured baseline | shipped |
| F11 | Budget/rate-limit display and stop-on-429 behaviour with a single retry; and a bounded, paced retry (default 3 attempts, exponential backoff) on a transient gateway 5xx, with a run-level circuit breaker after 5 consecutive failures | shipped |
| F12 | Paste a `Category:` name and score its members | **not shipped** (see roadmap) |
| F13 | Persist runs and compare two runs (deltas per photo) | **not shipped** |
| F14 | Calibrated threshold derived from labelled data | **not shipped** (0.5 today is a reporting convenience) |
| F15 | Show a rank within the run next to each photo, and a percentile | **rank shipped**, percentile pending |
| F16 | Report candidate spread for the run (range and ordering among plausible candidates) | shipped |
| F17 | Accept a **PagePile** ID (or a PetScan/SPARQL-produced list) as the input source, with an explicit member cap and a confirmation step | **not shipped** (see roadmap) |
| F18 | Accept a Commons **category** (optionally with depth) as the input source, with a cap, an explicit ordering rule and a confirmation step | **not shipped** (see roadmap) |
| F19 | Accept a **Commons search / SDC query** as the input source (`srsearch=`, e.g. `haswbstatement:P180=Q…` for "every photo of this person") | **not shipped** (see roadmap) |
| F20 | Accept a **PetScan PSID** as the input source — one integration that inherits categories, depth, templates, search and SPARQL, with union/subset/difference | **not shipped** (see roadmap) |
| F21 | Accept an uploaded **list file** (`.txt`/`.csv`) in the browser, for sets too long to travel in a URL | **not shipped** (see roadmap) |
| F22 | **Share a run as one URL** that replicates it: the list, the run, and the chart view, replayed from cache so opening it cannot spend a call | shipped |
| F23 | **Plot the chart live**, as results land, so it doubles as the progress display instead of appearing only when the run ends; the axis stretches and thumbnails move rather than the chart arriving all at once | shipped |
| F24 | **Fit the chart axis to the candidates** (top 12) as well as the whole field, so the photos a reader can use get the full width instead of being stacked deepest — measured on 100 photos, the top 9 need 9 lanes on the whole-field axis and 2 when fitted | shipped |
| F25 | **Summarise a clump too deep to draw** in a `+N` chip that names what it stands for, rather than letting a band grow without limit or hiding photos silently; placement is best-first so the top of a clump is never the summarised part | shipped |
| F26 | **Toggle the chart's axis scale** between even spacing and a top-weighted bend (`x = t²`) — the direction that gives the best photos room. A *logarithmic* axis is the opposite direction on a 0→1 range and is deliberately not offered | shipped |
| F27 | **Demote — not disqualify — a photo with something held in front of the face** (microphone, hand, phone), via its own model question folded into the composite as a bounded 0.5–1.0 factor | shipped |

## 6. Non-functional requirements

- **Portability:** Python 3.9+ standard library only (including the PDF writer). No pip install, no
  build step. Verified by running under Toolforge's build image and locally.
- **Deployability:** works under the Toolforge Build Service (`Procfile`) *and* the traditional
  uWSGI backend (`wsgi.py` exposing `app`).
- **Etiquette:** descriptive `User-Agent` (configurable), model-call **starts** paced `SPACING`
  (0.9 s) apart **globally** — that is the rate control and it is independent of worker count — with
  at most **2** in-flight scoring calls. Measured 2026-10-14: the endpoint serves two same-origin
  calls at serial latency, and because the pacing gate makes the second worker inert at healthy
  speeds, 2 self-gates rather than pacing harder (`docs/HANDOFF.md` §7.1). 429 respected via `retry-after`,
  never retried in a loop.
- **Read-only:** no write operations against any Wikimedia API. The only state kept is a local
  result cache and in-memory job state.
- **Transparency:** every displayed number must be traceable to a field in the API response, and
  the prompt shown in the UI is generated from the same constants used for scoring.
- **Privacy:** the app sends only image bytes and a fixed `state` string to WMF infrastructure; no
  names, no user accounts, no analytics, no third-party services.
- **Performance:** a cached photo costs ~0 s and 0 API calls; an uncached photo costs one call
  (~0.5 s warm; see §8 for the current endpoint condition).
- **Accessibility:** keyboard-dismissable modal, semantic table/heading structure, no colour-only
  signalling (state pills carry text).

## 7. Architecture

```
browser (static/index.html)
   │  fetch /api/parse · /api/run · /api/job/<id> · /api/job/<id>/report.<fmt> · /api/about
   ▼
core.py  ── routing shared by both backends
   │  parse_entries() ──► wiclient.resolve()  ──► Commons/other-wiki Action API
   │  run_job() ──► prefetch thread ──► wiclient.fetch_bytes()  (thumbnail, server-side)
   │            └─► wiclient.score_image()  ──► api.wikimedia.org …/llm-clef-flash:predict
   │  reports.py ──► Markdown / JSON / CSV / PDF (pdfmin.py, no dependencies)
   ▼
app.py (http.server)            wsgi.py (uWSGI entry point for the traditional backend)
```

Files: `core.py` (routing, job store, worker, latency verdict, About payload) · `wiclient.py`
(parsing, Commons API, thumbnail fetch, model call, budget) · `reports.py` + `pdfmin.py` (reports) ·
`static/index.html` (UI: incremental list rendering, About panel) · `app.py` / `wsgi.py` (backends).

### Data flow of one photo

1. **Parse** the pasted line → `File:` title + owning wiki.
2. **Resolve** via the wiki's Action API (`prop=imageinfo`) → pageid, size, MIME, SHA-1, licence,
   revision timestamp, pre-rendered thumbnail URL.
3. **Cache check** on `(wiki, pageid, sha1, schema, width)`.
4. **Download** the thumbnail server-side (640 px requested).
5. **Score**: one `POST …:predict` with the image as base64 and the fixed schema.
6. **Record** answers, tokens, wall time, time-to-first-byte; cache the result.
7. **Report** from the collected answers.

Downloads for later photos run in a background thread while the model answers for an earlier one
(`PREFETCH=2`).

## 8. The model contract (as implemented)

- **Endpoint:** `POST https://api.wikimedia.org/service/lw/inference/v1/models/llm-clef-flash:predict`
  — the decision route. There is **no OpenAI-compatible chat route** for this model (returns 404).
- **Model:** `clef-flash` 9.41 B params, BF16, Apache-2.0, post-trained from Qwen3.5-9B; served by
  WMF on the HuggingFace transformers backend (not vLLM) on one GPU. Listed on Wikitech under
  "Public — Task Specific models (public API, not in Studio)".
- **Request:** `{model, state, questions{…}, images[]}`. Images base64 only (remote URLs rejected),
  max 4 images, ≤ 8 MiB base64 each, up to 32 questions, `state` ≤ 60,000 chars.
- **Response:** `answers` keyed by question id, plus `usage`. `usage.output_tokens` is **always 0** —
  the model generates no text. For `noul` (true/false) the value is *the probability of true*; for
  `choice`, a label + `confidence` + full `probabilities`; for `score`, an expected value over an
  ordered legend + `confidence` + `probabilities` + the `legend` echoed back.
- **Schema used here:** `face_visible` (noul), `pose` (choice of 5), `quality` (score 0–4 with the
  legend unusable/poor/acceptable/good/excellent), `infobox_ready` (noul), `publish_ready`
  (choice of 2 — "this photograph is the one to use" / "choose a different photograph instead"),
  `eyes_to_camera` (noul), with a fixed `state` describing the photographic context. The exact
  strings are in `wiclient.py` (`STATE`, `QUESTIONS`) and are rendered verbatim in the app's About
  panel. `publish_ready` was added 2026-10-13 after the wording experiment; adding it changed the
  schema hash, so it invalidated every cached answer by design.
- **Three headline numbers, only two of which are the model's.** The list is ranked by a
  **composite** this app computes — `min(face_visible, eyes_to_camera) × pose weight × unobstructed`
  — which is
  labelled as our arithmetic everywhere it appears; `P(publish)` and `P(infobox)` are the model's own
  answers and are shown beside it. Run-level separation is reported as **candidate spread** (range and
  median gap among answers at or above the candidate floor), never as standard deviation.

  **`obstruction_factor()`** (2026-10-14) answers "is anything held in front of the face?" and scales
  the composite by `0.5 + 0.5 × P(clear)` — a demotion, never a disqualification, since the photo may
  still be the best one that exists of that person. It is its own question rather than a rewording,
  because `face_visible` already asks about visibility and the model reads that as *can I see the
  face*: a microphone over the mouth scores 0.83 on it, and 0.14 on this one. Adding it changed the
  schema hash, so every cached answer was invalidated by design, as with `publish_ready`.
- **Image handling:** the server downscales to 640 px wide before the vision encoder, so requesting
  larger thumbnails adds bandwidth but no information. Input tokens are deterministic per aspect
  ratio (measured: 865 at 16:9, 905 at 3:2 … 1,245 at 2:3 for this schema).
- **The wording is a first-class variable.** Across 57 cached photos, questions about something
  *visible in the frame* spread widely (`face_visible` sd 0.372, `eyes_to_camera` 0.350) while
  questions demanding an *editorial or aesthetic* judgement collapse toward the middle
  (`infobox_ready` 0.229, `quality` 0.145 — the most compressed). On four fixed photos, five wordings
  of the same question moved one image between 0.190 and 0.960 and cut the range among three
  candidate portraits from 0.486 to 0.021. Any change to wording must be measured, not assumed:
  see `research/wording-experiment-2026-10-13.md`.
- **Quota:** ~100 calls/hour per anonymous client, shared across LiftWing models; a 429 arrives with
  `retry-after` counting to the top of the hour and `x-envoy-ratelimited: true`. Toolforge/WMCS
  server-side requests are exempt from the generic API limits, and LiftWing lists known clients on a
  200,000 req/h tier.

## 9. Decisions and rejected alternatives

| Decision | Alternative rejected | Why (with evidence) | Reopen if |
|---|---|---|---|
| All Wikimedia calls made **server-side** | Call LiftWing directly from the browser | Every call costs quota; a browser-embedded call cannot set a proper User-Agent, exposes the endpoint to abuse, and offers no cache | LiftWing adds CORS + per-user keys and a browser-native app is wanted |
| One image per request, **2** concurrent calls (was 1) | 3–5 concurrent workers | Originally 1: a 3-worker run burned the anonymous hourly budget in 4 calls, then hit 429 for 162 s — on that tier concurrency only emptied the window faster. Reopened and **re-measured 2026-10-14** on the deployment (WMCS, no quota): 2 same-origin calls run at serial latency (**0.332 s vs 0.337 s median, n=12**) for **1.6× throughput**, while 4 streams cost **+61 %** per call for only 2.24×. The second worker is also *inert* below `SPACING` (`docs/HANDOFF.md` §7.1), so it self-gates instead of adding pace | The endpoint is shown to queue same-origin calls, or more than 2 is shown to scale |
| Request the **960 px** step of Commons' thumbnail ladder (was 640 px; changed 2026-10-14) | Send 1024 px or originals | The model server downscales to 640 px whatever we send, so a larger rendition is wasted bytes (~80 % more for the 1280 bucket). **640 is not a `$wgThumbnailSteps` step**, so a 640 request was silently served the 960 asset anyway, while `imageinfo.thumbwidth` reported 640 (T388792). Asking for the step makes the request honest and costs nothing extra | A future model serves larger inputs, or Commons changes the ladder (canary: `tools/probe_thumb_ladder.py`) |
| Keep the model's five labels as the definition of "quality" | Write our own rubric/prompt | A custom rubric would make the output unattributable and impossible to compare with other Clef users | A labelled calibration set justifies a domain-specific rubric |
| Report the raw probability | Apply a 0.5 threshold as "ready / not ready" | In every sample nothing exceeded ~0.71 and the distribution is model-specific; a fixed cut-off would manufacture false confidence | A calibration study produces a defensible curve |
| Keep the editorial `infobox_ready` question as the headline | Reword it in plain language, remove the Wikipedia nouns, or switch to a 0–4 score (4 photos × 5 wordings, 20 calls, 2026-10-13) | Every rewrite emptied the 0.35–0.65 band by lifting all real portraits to 0.81–0.96, cutting the range among the three candidate portraits from **0.486 to 0.106 / 0.021 / 0.082**; the score type stayed compressed. The complaint ("all the numbers look like 0.5") is about *resemblance*, not ordering — and ordering is what triage uses (`research/wording-experiment-2026-10-13.md`) | A 12+ portrait run with hand labels shows a different wording orders photos better, or the platform swaps the model |
| Show a rank and percentile within the run next to the probability | Keep presenting raw probabilities only | The clustering complaint is cheapest to answer in the presentation layer ("3rd of 24 · top 13%"), with no schema change and no new calls | Users report the percentile is misleading on small runs (n < ~8) |
| Adopted 2026-10-13: add the two-option `publish_ready` question and rank by a composite we compute | Keep the single editorial question as the sole headline | The pilot showed the editorial question separates candidates best of the *rewrites* but still clusters; `publish_ready` returns a decisive two-way answer (first live call: publish 0.937 / pass 0.063 where the infobox question gave 0.746), and the composite is explainable ingredient by ingredient (`research/wording-experiment-2026-10-13.md`) | A hand-labelled run shows the composite orders photos worse than P(infobox) |
| Embed the thumbnail beside each evaluation in the Markdown and PDF reports | Link to Commons and let the reader click through | Reports are used offline and in email; a photo next to its numbers is what makes an evaluation legible, and the PDF embeds the exact bytes sent to the model (no network needed to read a report) | Reports get large enough to matter (hundreds of photos) — then a thumbnail-size or opt-out switch |
| Thumbnails kept on disk at fetch time (`cache/thumbs/`) | Re-fetch from Commons when a report is built | The report then embeds exactly what the model saw, and works offline; costs ~100-300 KB per photo | Disk pressure on a shared host |
| Cache by `(wiki, pageid, sha1, schema, width)` | Cache by title, or by pageid alone | Titles are renamed and overwritten; pageids collide across wikis; the schema hash invalidates old answers when the questions change | — |
| In-memory job store + disk cache | Add a database | Single-user prototype; jobs are ephemeral and results are reproducible from cache | Opening the tool to many concurrent users |
| No `Category:` / PagePile input yet | Enumerate category members or fetch a PagePile inline | Silent caps and slow runs on big categories; needs a limit/confirmation UX first. PagePile is the cheapest of them: `GET pagepile.toolforge.org/api.php?action=get_data&id=<id>` is a **fixed host with a numeric id** (verified 2026-10-14), so it is a bounded fetch — unlike a user-supplied `?listurl=`, which was refused as an open-fetch/SSRF risk (`docs/MEMO-url-parameters-2026-10-14.md`) | A cap-and-confirm interaction is designed (roadmap) |
| Browser hotlinks result thumbnails | Proxy every thumbnail through the app | Saves the app's bandwidth; the images are public Commons thumbnails | A deployment must be fully server-origin or offline-capable |

## 10. Success metrics

| Metric | How measured | Value |
|---|---|---|
| Cost per photo | API calls per image | 1 (0 when cached) |
| Throughput under quota | Photos/hour | ~90 uncached photos/hour on the anonymous tier (100 calls/h shared) |
| Warm call latency | `wall_s` per call | median 0.54 s over 62 recorded calls from the **dev host** (2026-10-02/12); 6.95 s worst. **0.327 s median measured inside the deployed pod** (n=6, 2026-10-14) — production is ~1.6× faster than the dev-host figure, so `BASELINE_WARM_S` reads pessimistic there |
| Two-stream throughput | phase elapsed, serial vs 2 streams | **1.59×** at unchanged per-call latency (3.76 s → 2.36 s for 12 calls, in-pod 2026-10-14); 4 streams 2.24× but **+61 %** per call (`research/concurrency-2026-10-14.md`) |
| Endpoint condition today | `wall_s`, `ttfb_s`, verdict | **0.33 s median, verdict `normal`**, measured in-pod 2026-10-14 (it was 28.6 s median / `service-side` on 2026-10-13) |
| Ranking usefulness | Spearman vs a second image size | 0.972 (n=32) — the ranking is stable to input scaling |
| Question wording sensitivity | same photo, 5 wordings of one question | one photo moved **0.190 → 0.960**; range among three candidates fell 0.486 → 0.021 on the most "sensible-sounding" rewrite (n=4, indicative) |
| Candidate separation (the metric that matters for triage) | range + ordering among photos that pass a plausibility floor | 0.486 for today's wording on the pilot sample; 0.365 (composite), 0.169 (`choice`), 0.106 / 0.082 / 0.021 (rewrites) |
| Question spread across the corpus | sd per question, n=57 | visible-fact questions 0.350–0.372; editorial `infobox_ready` 0.229; aesthetic `quality` 0.145 (the most compressed) |
| Answer stability | token-identical rescores | 32/32 identical tokens; no threshold flips |
| Report usability | A report is produced for every run | 4 formats, valid mid-run |

## 11. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Endpoint latency or availability (platform is "experimental", no SLA) | Runs take minutes instead of seconds | Per-run latency verdict against a measured baseline; time-to-first-byte captured; results cached; the finding is reported rather than hidden |
| Model calibration unknown | A "0.65" may be read as ground truth | Ranking language throughout; ≥0.5 reported as a count, not a verdict; About panel states the limits |
| Quota exhaustion (100/h anonymous) | Runs stall with 429s | Spacing, paced call starts, a single retry on 429, visible budget; deploy to WMCS for the higher tier |
| Transient gateway 5xx (~3.5% of first attempts, measured 2026-10-14) | A photo silently drops out of the run | Up to 3 paced attempts with exponential backoff, and the run stops after 5 consecutive failures so an outage is not multiplied across the queue; every run reports `retries` so the recovery rate becomes measurable |
| Cache staleness after a file is overwritten | A stale answer shown | Cache key includes the file's SHA-1, which changes on re-upload |
| Question wording drift | Numbers become incomparable between runs | Schema hash in the cache key; About panel generated from the live constants; contract test diffs it |
| A rewrite that *looks* better ranks worse | Users distrust the tool, or we ship a regression | Measured, not assumed: five wordings were benchmarked on fixed photos (0.486 → 0.021 candidate range) and the "improvements" were rejected; candidate spread is now the tracked metric |
| Misreading the model as identifying people | Ethical/reputational | No identification in the schema or output; About panel states it explicitly |

## 12. Open questions

1. What threshold, if any, corresponds to "an editor would actually use this as the infobox image"?
   Needs ~100–200 photos labelled by a human, then a precision/recall curve.
2. Does the ranking hold across photographers, events and camera bodies, or is it tuned to studio
   lighting? Our samples are all event/red-carpet portraits.
3. Would the larger sibling model (`clef`, 27 B) or a Qwen LLM on the same platform agree on the
   ranking? An agreement study would tell us whether the choices are model-specific.
4. Should "ready" be a *portfolio* question (is this the best shot of this person) rather than a
   per-photo question? That might be better modelled as a ranking question in the schema.
5. Does origin from inside the Wikimedia network change latency as well as quota? (Test planned on
   Toolforge — see HANDOFF.)
6. Which of the candidate questions orders photos the way an editor would? The pilot could not settle
   it: the composite and the two-option `choice` rank one pair of photos opposite to every other
   wording, and the difference is a single dimension (`eyes_to_camera`). Hand labels are the only
   arbiter.
7. Is a *resemblance* complaint fixable in the presentation layer? If rank + percentile within the run
   satisfies users, no schema change is needed at all — worth knowing before touching the prompt.

## 13. Roadmap

**Now (blocked or trivial)**
- Deploy to Toolforge for a stable URL and a WMCS origin; re-measure latency from inside the network.
- Repeat the latency probe periodically and record the trend (`BASELINE_WARM_S` makes the app's own
  verdict meaningful).
- Show rank + percentile within the run beside each probability (answers the clustering complaint
  without a schema change; see §9).

**Next**
- Report candidate spread for each run (range and ordering among plausible candidates).
- **Input sources, in the order worth doing them** (F17–F21). Each returns `File:`
  titles, which the app already resolves — the work is the *selection* UX and the caps, not the API:
  1. **PagePile ID** (F17) — one fixed-host call with a numeric id. Smallest and most shareable.
  2. **Commons category, with depth** (F18) — depth needs `deepcategory:` (CirrusSearch, ≤5 levels) or
     PetScan. A cap is not enough: *which* 200 of 1,240 is a product decision (name order? newest?
     random?) and must be stated at the point of confirmation.
  3. **Commons search / SDC** (F19) — `srsearch=…&srnamespace=6`, e.g. `haswbstatement:P180=Q42`
     ("files depicting this person"), `hastemplate:`, `deepcategory:`. **`srnamespace` is
     mandatory**: without it the search runs in namespace 0 and "0 hits" reads as an empty corpus
     (0 vs 363,459 in the audit recorded in the search skill).
  4. **Uploaded list file** (F21) — purely client-side, so it has no fetch or security surface, and it
     is the answer to the ~4,000-character link ceiling for a spreadsheet paste.
  5. **PetScan PSID** (F20) — widest coverage (it can express 1–4 as one id), at the price of depending
     on another tool staying up and its result set staying reproducible.
  Two more, deliberately out of rank order because they need a decision, not plumbing: **"uploads by a
  user"** (`list=allimages&aiuser=…`, which **requires `aisort=timestamp`** — without it the API answers
  `invalidparammix`, verified 2026-10-14) and **non-Commons hosts** (Flickr, Internet Archive, a GLAM
  DAM), which would mean fetching foreign URLs, displaying foreign licences and — per §9 — a new trust
  decision rather than a new source. Prefer the Flickr→Commons path the skills already document.
- Validation run: 12+ portraits, today's wording vs the two-option `choice` vs the composite.
- `Category:` / PetScan / SPARQL / **PagePile ID** input with an explicit cap and confirmation step.
  PagePile (F17) is the least work of the four and the most shareable: one fixed-host call with a
  numeric id returns a title list, and the shape is the one already proven for `?list=` — prefill
  the box, then let a human press *Validate files* rather than starting anything by itself.
- Run history + run-to-run comparison (needs a small store).
- Thumbnail proxy + local cache option for fully server-origin deployments.
- Server-sent events instead of 1 s polling (nicer clocks, less chatter).
- i18n via message files (translatewiki conventions) — the schema wording itself is English-only.

**Later**
- Calibration study against human labels → a defensible threshold and a published curve.
- Model-agreement study (clef vs clef-flash vs a Qwen LLM on LiftWing).
- Portfolio/rank questions in the schema (best-of-set) rather than per-photo binaries.
- Optional per-photo "why" panel exposing the full probability distribution side by side.

## Appendix — measured numbers referenced above

- Warm latency: 62 recorded calls across 2026-10-02 (23) and 2026-10-12 (39): median 0.54 s, worst
  6.95 s. 2026-10-13, 12:16–12:45 UTC: 20 calls, median 28.63 s, range 27.43–29.40 s. Text-only call
  with no image: 27.84 s / 28.99 s; with a 640 px image: 28.74 s / 28.65 s ⇒ not input-related.
- Official platform benchmark (Wikitech, for `llm-qwen36-27b` on vLLM, internal endpoint):
  ~197 ms TTFT, ~35 output tok/s, 3.6 s for a 128-token response; p50 18 s at concurrency 64.
  The page also states the platform is experimental with no availability SLA.
- Thumbnail served widths (Commons): requested 120→120, 320→330, 500→500, **640→960**,
  **1024→1280** px — always measure the served URL rather than trusting the requested width.
- Question design pilot, 2026-10-13 (`research/wording-experiment-2026-10-13.md`): 4 photos (a logo
  control plus portraits cached at 0.740 / 0.528 / 0.208) × 5 wordings = 20 calls, all HTTP 200,
  median 56.9 s per call. Global sd rose 0.241 → 0.339–0.350 for the rewrites while the range among
  the three candidate portraits fell 0.486 → 0.106 / 0.021 / 0.082. Across 57 cached photos: visible-
  fact questions sd 0.350–0.372 vs `infobox_ready` 0.229 and `quality` 0.145.
- Sample score distribution (24 WikiPortraits photos): mean P(infobox) 0.361, max 0.707, 5 of 24
  ≥ 0.5; quality mean 2.51/4; median call 0.62 s (on 2026-10-12).
