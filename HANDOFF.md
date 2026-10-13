# HANDOFF — WikiPortraits Eval

For the next developer or agent working on this. Everything here is either measured in this repo or
attributable to the sources named at the end. Read **PRD.md** for *why* the product is shaped this
way; this file is *what will bite you* and *what to do next*.

App version 0.4.1 · stdlib Python only · last full test pass 2026-10-13

---

## 1. Where things are

| Path | What it is |
|---|---|
| `core.py` | routing (shared by both backends), job store, worker + prefetch thread, latency verdict, About payload |
| `wiclient.py` | input parsing → `File:` titles, per-wiki Commons API resolution, thumbnail fetch, model call, budget/pacing |
| `reports.py`, `pdfmin.py` | Markdown/JSON/CSV/PDF output; `pdfmin.py` is a dependency-free PDF writer |
| `static/index.html` | the whole UI: incremental list rendering, per-row clock, latency panel, About modal |
| `app.py` / `wsgi.py` | `http.server` backend / WSGI entry point for Toolforge's traditional backend |
| `cache/` | scored results, keyed `wiki-pageid-sha1-schemahash-width.json`. Safe to delete. |
| `cache/thumbs/` | the exact thumbnail bytes sent to the model, keyed `wiki-pageid-sha1.img` — what the reports embed. Safe to delete (reports then re-fetch from Commons). |
| `research/` | the question-design experiment: `wording-experiment-2026-10-13.md` (write-up), `wording_experiment.jsonl` (every raw answer), `wording_comparison.png` (chart), `wording_experiment_output.txt` (console log) — plus `sample-report.pdf` / `sample-report.md` / `sample-report-page1.png`, a real report served by the running app over HTTP (one photo, so it can be inspected without running anything) |
| `PRD.md`, `README.md` | product rationale; user-facing docs |
| dev scripts | `run_all_tests.sh`, `live_pipeline_check.py`, `verify_reports_live.py`, `verify_live_two.py`, `diag_contract_job.py`, `test_pdf_images.py`, `probe_latency_now.py`, `probe_spread.py`, `analyze_*.py`, `tune_infobox_question.py`, `summarize_wording_experiment.py`, `make_wording_chart.py`, `restart_app.py`, `start_tunnel.py`, `check_docs.py`, `check_js.py`, `package.sh` |

Run: `python3 app.py` (port 8765, or `PORT=…`). Restart helper: `python3 restart_app.py`
(kills by scanning `/proc` — this host has no `lsof`).

## 2. Tests, and which ones cost API calls

```bash
bash run_all_tests.sh                    # everything below, in order
python3 test_forms.py test_commas.py test_wsgi.py test_pipeline.py test_reports.py   # offline
python3 check_js.py                      # offline: the page script parses (node --check)
node test_render_dom.js                  # offline (needs `npm install` once — jsdom, ~25 MB)
.venv-pdf/bin/python test_pdf_images.py             # renders a PDF back to PNG for eyeballing
                                                    # (.venv-pdf: python3 -m venv .venv-pdf && .venv-pdf/bin/pip install pypdfium2 pypdf)
python3 verify_reports_live.py [URL]     # live: 3 photos end to end, all four reports, PDF rendered
python3 test_ui_contract.py [URL]        # live, but free when the files are already cached
WANT=4 python3 live_pipeline_check.py [URL]   # live and costs 1 call per fresh file
python3 probe_latency_now.py             # live and costs 4 calls (2 text-only, 2 image)
python3 analyze_question_spread.py       # offline: spread of every question in the cache (free)
python3 summarize_wording_experiment.py  # offline: table from research/wording_experiment.jsonl
python3 tune_infobox_question.py         # live and costs 20 calls (~19 min at 57 s/call, ~1 min warm)
```

`test_render_dom.js` is the only real UI test: it runs the page script in jsdom and asserts the
things that regressed before — row/`<img>` **element identity** across renders, `src` assigned
exactly once, one label per state, the clock, add/remove, and the About panel. It is a DOM, not a
paint engine: it proves the mechanism, not the pixels. **Ask a human to look at it before shipping
UI changes.**

## 3. Invariants (each one is an incident that already happened)

1. **Never split pasted input on commas or semicolons unless what follows is clearly a new entry.**
   Commons file names contain commas: `File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes
   2025).jpg` is a real file (pageid 165943740). The Action API does *not* treat a comma as a title
   separator — `titles=File:A.jpg,File:B.jpg` returns one bogus page with HTTP 200 and no warning —
   so both the parser and any batch query must use newlines / `|`. Rules and the reusable
   `to_title()` are upstreamed: Wikipedia-AI-Skills `commons-file-resolution` SOP 6 (PR #93).
2. **A row must never claim to be downloading bytes it already holds.** The prefetch thread once set
   `status="downloading"` before the fetch and left it there while blocked on a full queue, so rows
   read "fetching thumbnail…" for a minute *while displaying the thumbnail*. States are now
   `queued → downloading → ready → scoring → done`, and `downloading` lasts only as long as the
   request. `test_pipeline.py` fails if an item ends in `downloading`/`ready`.
3. **Never rebuild the results list with `innerHTML` on a poll.** It destroyed and re-created every
   row, so every `<img>` was re-fetched — the whole list flashed once a second. Rows are created once
   into a keyed `Map` (see `static/index.html`) and patched in place. `test_ui_contract.py` asserts
   `$("items").innerHTML` does not appear in the JS.
4. **Cache keys are `(wiki, pageid, sha1, schema_hash, thumb_width)`.** Pageids collide across
   wikis; titles are renamed; SHA-1 catches file overwrites; the schema hash invalidates answers when
   the questions change. Do not "simplify" this.
5. **The About panel and the prompt are built from the same constants.** `about_payload()` in
   `core.py` interpolates `wiclient.STATE` / `wiclient.QUESTIONS`; `test_ui_contract.py` diffs the
   served payload against them field by field. If you edit the schema, the panel follows
   automatically — do not hand-write prompt text anywhere.
6. **All Wikimedia calls stay server-side.** The browser only calls this app. The one exception is
   the browser hotlinking result thumbnails (a deliberate bandwidth choice). If you add a proxy, mind
   the traffic.
7. **429 means stop, not retry harder.** One retry after the server's own `retry-after`, then report.
   No retry loops, no "just add workers".
8. **The composite is our arithmetic, not the model's.** `min(face_visible, eyes_to_camera) × pose
   weight` is computed in `wiclient.composite()` and must be labelled as ours everywhere it appears —
   the UI row, the About panel, the report method block, the README. Never present it as a model
   probability: that would make the number unattributable, which is the one thing this app is built
   not to do. `P(publish)` and `P(infobox)` are the model's own and are always shown beside it.
9. **Report candidate spread, never standard deviation.** sd rises whenever answers pile up at either
   end, *including at the ceiling*, so it rewards exactly the failure we already measured (rewrites
   that pushed every portrait to 0.81-0.96 and shrank the range between them from 0.486 to 0.021).
   `candidate_spread()` reports candidates above a floor, their range, the median gap between
   neighbours, and the dead-zone share — see §11 and `research/wording-experiment-2026-10-13.md`.
10. **Reports must not depend on the network.** Thumbnails are saved to `cache/thumbs/` at fetch time
    and the PDF embeds those bytes, so a report reads the same offline. If you add a new report
    format, embed from `thumb_bytes(item)` (saved copy first, Commons as fallback) rather than
    hotlinking. Also: draw the hairline frame around every embedded image — a white-on-transparent
    logo is otherwise invisible on white paper and the reader cannot tell an image was embedded.

## 4. Traps, measured

**A run that hangs at "n-1 of n" is usually a dead worker thread, not a slow endpoint.** The
pipeline consumer blocks on `q.get()` if the download producer thread dies, so a single traceback in
`_download` looks exactly like the model service stalling forever. Check the server log for
`Exception in thread` before blaming latency. (Real case, 2026-10-13: a `NameError` for a missing
`import re` killed the producer; the run sat at 1/3 for 25 minutes and was misread as the endpoint
degrading to >25 min/call.)

**Never wrap worker code in a bare `except Exception`.** The same NameError was swallowed by the
thumbnail-saving helper, which degraded silently to "no thumbnail in the report" and let the offline
test suite pass. Worker-level handlers catch `OSError` and let coding errors raise — and
`test_pipeline.py` now asserts that every scored photo has a saved thumbnail, so a silent regression
fails the suite instead of shipping.

- **Do not judge a question by its overall spread.** sd rises whenever answers move toward 0 or 1,
  including when they all pile up at the *ceiling*. On the same four photos, a reworded question took
  sd from 0.24 to 0.34 while collapsing the three real portraits into a 0.021 range. Track the range
  and order **among plausible candidates** instead — see §11.
- **The `score` type is not a fix for clustering.** Across 57 cached photos the 0–4 `quality` score is
  the *most* compressed thing we ask (sd 0.145, 60% of photos in 0.35–0.65); a 0–4 variant of the
  infobox question behaved the same way. What spreads is whether the criterion is *visible in the
  frame*, not whether the answer is ordered.
- **A "better-looking" question can rank worse.** Every reworded variant emptied the 0.35–0.65 band
  and every one of them separated the real portraits *worse* than the wording users complained about.
  Always check the candidate range before proposing a wording change.
- **The served thumbnail width is not the requested one.** Commons returns standard steps:
  request 120→120, 320→330, 500→500, **640→960**, **1024→1280**. Always read the served size; do not
  label a report from the request.
- **The server downscales to 640 px wide** before the vision encoder, whatever you send. Requesting
  1024 px costs ~40 % more bytes for identical tokens. Token counts are deterministic per aspect
  ratio for this schema: 865 (16:9), 905 (3:2), 1,045 (1:1), 1,245 (2:3). `usage.output_tokens` is
  always 0 — this model writes no text.
- **`#` in a title silently truncates** (`File:X#1.jpg` → `File:X`). The parser truncates and warns.
- **`Datei:`/`Fichier:`/`Archivo:` are not Commons aliases**; they belong to de/fr/es wikis.
  `upload.wikimedia.org/wikipedia/<project>/…` names the wiki in the path — resolve on that wiki.
- **`Special:FilePath` answers 301 *or* 302** (301 when the name needs normalising).
- **Timing fields are not retroactive.** `ttfb_s` exists only for calls made after 0.2.0; cached
  answers from before that legitimately lack it, and the contract test treats a fully cached run as
  "not applicable" rather than failing. **Never fabricate timings for old cache entries.**
- **The 0.5 threshold is arbitrary.** Nothing scored above ~0.71 in any sample (24-photo batch:
  mean 0.361, max 0.707, 5 ≥ 0.5). Rank, don't threshold.
- **`quality` is defined only by its five legend labels** (0 unusable … 4 excellent) and by the
  640 px image the model actually sees. Do not describe it as a sharpness or lighting measurement.

## 5. The endpoint can be ~50× slower than its baseline — and that is not your bug

Measured: warm calls (62 recorded, 2026-10-02 and 2026-10-12) had a **median of 0.54 s** and a worst
case of 6.95 s. On **2026-10-13, 12:16–12:45 UTC**, 20 calls had a **median of 28.63 s** (range
27.43–29.40), with no 429s; a text-only call took 27.8/29.0 s and the same question with an image
28.7/28.6 s — so it is not the image, the download, the aspect ratio or this app. For context, the
Wikitech page's only published benchmark (a different, larger model on vLLM) reports ~197 ms to
first token and 3.6 s for a 128-token response, p50 18 s at concurrency 64, and the page states the
platform is experimental with **no availability SLA**.

How the app handles it: every call records `wall_s` (total) and `ttfb_s` (time to first byte — the
gateway holding the request) plus `x-envoy-upstream-service-time`, and `latency_summary()` labels the
run `normal` / `elevated` / `service-side` against `BASELINE_WARM_S` (0.54 s, env-overridable). The
verdict shows in the UI, the run log and the reports.

**Before "fixing" a slow run: run `probe_latency_now.py` (4 calls) and check whether text-only is
also slow.** If it is, the app is innocent — record the number and move on. Do not add retries or
concurrency; you will only burn quota.

## 6. Deployment state

- **Canonical home: this repository** (`/Users/alih/Documents/ai/wikiportraits-eval` on the
  owner's Mac). The earlier dev host with `/opt/data/wikiportraits-app`, `/opt/data/serve/` and
  `/opt/data/pdfvenv` is **stale** — every script now resolves paths relative to the repo
  (`serve/` for tunnel logs, `.venv-pdf` for the pypdfium2/pypdf dev venv).
- Preview deployment: a quick Cloudflare tunnel (`start_tunnel.py`, URL and log in `serve/`).
  A quick tunnel dies with the process — it is a preview, not a deployment.
- **Deployed to Toolforge 2026-10-13** as the tool **`wikiportraits-eval`**
  (`https://wikiportraits-eval.toolforge.org`), Build Service backend, source
  `https://github.com/Fuzheado/wikiportraits-eval` (public, MIT). Deploy command set:
  `toolforge build start https://github.com/Fuzheado/wikiportraits-eval` then
  `toolforge webservice buildservice start --mount=none -m 512Mi`; logs via
  `kubectl logs deployment/wikiportraits-eval` from the tool account.
- At deploy time the **LiftWing `llm-clef-flash` engine was down** (HTTP 500
  `Error during inference: EngineCore encountered an issue`, ~0.1 s, text-only and image alike,
  from both the Mac and the WMCS origin) — Commons resolution, thumbnails, jobs and error
  handling all verified working around it. Re-run `python3 verify_live_two.py <URL>` (1 fresh
  call) and `python3 test_ui_contract.py <URL>` once the endpoint recovers.
- Preview alternative: a quick Cloudflare tunnel (`start_tunnel.py`, URL and log in `serve/`).
  A quick tunnel dies with the process — it is a preview, not a deployment.
- **The first thing to do on Toolforge (planned experiment):** run `probe_latency_now.py` from a
  Toolforge shell and compare with the same script from the development host — same client, same
  files, same minute, only the origin IP differs. Then `live_pipeline_check.py` against the deployed
  URL for the app path. This separates "inside the Wikimedia network is faster" from "the endpoint is
  having a bad week". WMCS also lifts the quota (known clients: 200k req/h, and WMCS is exempt from
  the generic API rate limits) — that benefit is documented and should show up as no more pacing.
- While testing, **do not restart the server** (`restart_app.py`) — someone may be mid-run; jobs are
  in-memory and a restart discards them.

## 7. Next steps, ranked (with the trap that makes each worth doing)

**Small, high value**
1. **Deploy to Toolforge** — stable URL, WMCS origin, higher quota; then repeat the latency probe.
2. **Periodic latency probe + trend** — the endpoint moved 50× in a day with no announcement; a cron
   that runs `probe_latency_now.py` hourly and records the median makes the app's verdict meaningful
   and gives a real regression record (see the observability reference in §9).
3. **Show the full distribution per photo on demand** — the API already returns the whole probability
   vector; exposing it costs nothing and helps users trust (or distrust) a score.
4. **Decide how the headline is presented** — research is done and the decision is pending (see §11).
   The evidence does *not* support swapping the question: today's `infobox_ready` separates candidates
   better than any rewrite tested. The cheap fix is presentational (rank + percentile within the run);
   any question change needs a 12+ portrait run and hand labels first.

**Medium**
5. **`Category:` / PetScan / SPARQL input** with an explicit member cap and a confirmation step
   ("Category:Foo has 1,240 files; score the first 200?"). The parser already warns instead of
   guessing; this needs the UX, not the API work.
6. **Run history + run-to-run diff** (small SQLite or JSON store): "these 12 photos got worse after
   the file was overwritten / after I changed the schema".
7. **Server-sent events instead of 1 s polling** — nicer clocks, less chatter; the incremental
   renderer is already the right shape for it.
8. **Thumbnail proxy + local cache** — makes the deployment fully server-origin (and offline-capable),
   at the cost of the app's bandwidth.

**Larger / research**
9. **Calibration study** — label ~100–200 photos by hand, produce a precision/recall curve, then
   replace the arbitrary 0.5 with something defensible. This is the single biggest credibility win,
   and it is the only thing that can adjudicate §11's ordering disagreement.
10. **Model-agreement study** — score the same set with `clef` (27 B sibling) and a Qwen LLM on the
   same platform; if the rankings disagree, the "which photo is best" question is model-specific and
   must be said out loud.
11. **Portfolio-style questions** — "is this the best available shot of this person?" is a ranking
    question, not five independent binaries; the schema (and possibly the cache key) would change.
12. **i18n** — message files for the UI and (carefully) for the schema wording, which is English-only
    today; changing the wording changes the numbers, so it belongs behind the schema hash.

## 8. Loose ends

- Category input is unimplemented (warned about in the UI, not silently ignored).
- The dev host has a 25 MB `node_modules` (jsdom) for the DOM test; it is **excluded** from the
  deploy tarball. Run `npm install` if you need the test.
- `analyze_latency_timeline.py` / `analyze_earlier_runs.py` are read-only forensic scripts that
  summarise `cache/*.json` and the earlier research logs; keep them, they are how the 0.54 s baseline
  was established.
- Multiple overlapping job runs are allowed (`MAX_PARALLEL_JOBS=2`) but the quota is per *client*,
  not per job — two concurrent runs exhaust the hour twice as fast.
- The `verify_parse2.py` / `verify_comma_case.py` scripts are one-off verifications kept for the
  record; they are not part of the suite.

## 9. Where the knowledge lives

- **Wikipedia-AI-Skills** (upstream, merged): `commons-file-resolution` SOP 6 — pasted references →
  `File:` titles, the comma rule, the per-wiki batch query — plus the `tests/test_commons_file_resolution.py`
  suite and `scripts/parse-file-refs.py`. PR #93, merge commit `b5c5b863`.
- **Hermes skills:** `llm-endpoint-capability-audit` carries the session's research references —
  `references/clef-flash-2026-10.md` (model shape, caps, semantics), `references/liftwing-budget-and-image-prep-2026-10.md`
  (quota, thumbnail ladder, pacing), `references/liftwing-wikiportraits-suite-2026-10.md` (the schema
  and its first results) and `references/endpoint-latency-regression-2026-10.md` (the 28 s episode and
  how it was isolated). `wikimedia-ml-services` covers LiftWing generally.
- **Wikitech:** `Machine_Learning/LiftWing/Large_Language_Models` — the model list (clef-flash is
  under "Task Specific models"), caps, the rate-limit tiers, the one published benchmark, and the
  "experimental, no SLA" caveat. **It changed on 2026-10-13 05:12 UTC**, hours before the slowdown
  began; that is a coincidence worth checking, not evidence.
- **Model card:** `huggingface.co/Cloudflare/clef-flash` — the authoritative wording for `noul`
  ("the probability of true"), `choice`, `score`, `criteria` and `legend`. Quote it rather than
  paraphrasing.

## 10. Troubleshooting

| Symptom | Look at |
|---|---|
| A pasted file is "not found" but exists | `python3 -c "import wiclient; print(wiclient.parse_entries('<line>'))"` — check the title and the wiki it picked |
| Everything takes ~30 s | `python3 probe_latency_now.py` — text-only slow too ⇒ endpoint, not app |
| The list flickers / thumbnails reload | someone reintroduced `innerHTML` on `#items` (invariant 3) |
| A row says "downloading" forever | the prefetch thread's status handling (invariant 2) |
| 429s | budget: stop, wait for the top of the hour, or run from Toolforge; check the UI budget line |
| Scores look wrong after a schema edit | the schema hash forces fresh calls; old cached values belong to the old wording |
| A run never finishes, stuck at n-1 of n | look for `Exception in thread` in the server log — a dead producer thread blocks the pipeline; this is not necessarily endpoint latency (see §4) |
| Numbers present in the job object but missing on the wire | check the field is set *before* the payload is built; per-photo values are refreshed by `_refresh_derived()` on every settle |
| A question wording "looks better" but the tool ranks worse | candidate range, not sd — read §11 |
| No photos in the Markdown/PDF report | `cache/thumbs/` missing for those items (deleted?) — reports re-fetch from Commons; a photo that still cannot be embedded is listed under "Images that could not be embedded" in the PDF |
| The list ordering looks wrong | it is ranked by the composite by design; `rankOf()` in `static/index.html` is the key, `reports.ranked()` the server-side equivalent |
| `jsdom is not installed` | `npm install` (dev-only dependency) |

## 11. Question design: the "infobox ready" experiment (2026-10-13)

Full write-up, raw answers and chart: `research/wording-experiment-2026-10-13.md`,
`research/wording_experiment.jsonl`, `research/wording_comparison.png`.

**What was actually adopted (same day, after the pilot):**

1. **`publish_ready` added to the schema** — a two-option `choice` ("this photograph is the one to
   use" / "choose a different photograph instead"), i.e. variant V5 from the pilot. It is the model's
   own answer; the app shows P(publish). First live call after the change: **publish 0.937 / pass
   0.063** on a photo where the infobox wording gave 0.746 — the two-way question is decisive where
   the threshold question was not.
2. **A composite is now the ranking key**, defined in `wiclient.composite()`:
   `min(face_visible, eyes_to_camera) × pose weight` (head-and-shoulders 1.0, three-quarter 0.9, full
   body 0.6, candid 0.5, group 0.25). `infobox_ready` is still asked and still shown, but it no longer
   orders the list. The composite must always be labelled as our arithmetic (invariant 8).
3. **Candidate spread is reported per run** for all three headline numbers (composite, P(publish),
   P(infobox)) — candidates above the floor, range, median gap between neighbours, dead-zone share and
   the leading three titles. Never sd (invariant 9).
4. **Because adding a question changes the schema hash, every cached answer was invalidated** and each
   photo needs one fresh call. That is by design, not a bug: answers to one question are only
   comparable within an identical question set.

**Still open from the pilot:** the wider validation (12+ portraits, composite vs P(publish) vs
P(infobox), then hand labels). The pilot could not settle whether the composite ranks the *middle* pair
correctly — it and P(publish) both put Jenni above Irina while every other wording did the opposite.

The complaint that started it: `infobox_ready` sits in 0.5–0.6 for almost everything, and its wording
("lead image (infobox portrait) of a Wikipedia biography") needs a lot of unpacking.

**The free half of the answer** (`analyze_question_spread.py`, 57 cached photos, no API calls): the
split is not by question *type* — the 0–4 `quality` score is the most compressed question we ask
(sd 0.145, 60% in 0.35–0.65) — it is by *criterion kind*. Questions about something **visible in the
frame** spread across nearly the full range (`face_visible` sd 0.372, `eyes_to_camera` sd 0.350,
2–7 of 57 in the band); questions that ask for an **editorial or aesthetic** judgement collapse
(`infobox_ready` sd 0.229 with 47% in the band; `quality` 60%).

**The live half** (`tune_infobox_question.py`, 4 photos × 5 wordings, 20 calls, all HTTP 200,
median 57 s/call) tested the rewrites. P(publish-ready):

| photo (today's value) | today | plain+criteria | professional portrait | score 0–4 | choice publish/pass | composite |
|---|---|---|---|---|---|---|
| Andrey (0.740) | 0.676 | 0.920 | 0.960 | 0.676 | 0.877 | 0.763 |
| Irina (0.528) | 0.357 | 0.834 | 0.944 | 0.642 | 0.708 | 0.398 |
| Jenni (0.208) | 0.190 | 0.814 | 0.939 | 0.594 | 0.846 | 0.544 |
| logo (0.010, control) | 0.024 | 0.052 | 0.164 | 0.071 | 0.037 | 0.003 |
| sd | 0.241 | 0.350 | 0.339 | 0.247 | 0.341 | 0.277 |
| in 0.35–0.65 | 1/4 | 0/4 | 0/4 | 2/4 | 0/4 | 2/4 |
| **range among the 3 portraits** | **0.486** | 0.106 | **0.021** | 0.082 | 0.169 | **0.365** |

**The finding that matters:** every rewording emptied the dead zone and improved global sd — and every
one of them separated the three real portraits *worse* than the wording users complained about. The
"professional portrait" wording leaves them at 0.939 / 0.944 / 0.960: range 0.021, three photographs
effectively tied. Rewording moves the pile-up from the middle to the ceiling.

**Recommendation (awaiting the owner's decision):**
1. Don't adopt the "plain + criteria", "professional portrait" or 0–4 score variants. Each emptied the
   dead zone and each separated the real portraits *worse* than the wording users complained about.
2. **Don't change the headline question yet.** On this sample the current wording separates the three
   candidates best (range 0.486 vs 0.365 for the composite, 0.169 for the `choice` variant, 0.021 for
   the "professional portrait" wording). "The numbers all look like 0.5" is a resemblance complaint,
   not an ordering complaint — and ordering is what triage consumes.
3. **Fix the presentation instead** (no schema change, no calls): show a rank and percentile within
   the run next to the probability ("3rd of 24 · top 13%").
4. If a switch is still wanted, the candidates to validate are the composite
   `min(face_visible, eyes_to_camera) × pose weight` (head-and-shoulders 1.0, three-quarter 0.9,
   full body 0.6, candid 0.5, group 0.25; widest corpus-level spread, sd 0.352 vs 0.229) or the
   two-option `choice` — and the composite must be labelled as *our arithmetic on three model
   answers*, not a model probability.
5. Report **candidate spread** alongside the run and stop using sd as a proxy for question quality.
6. Validate before shipping: 12+ portraits (today's wording vs the `choice` variant; the composite is
   free), then hand labels, measuring **ordering agreement rather than range**. Spread is not
   accuracy.

**Reopen / redo if:** the wider run reverses the portrait order, hand labels show the composite ranks
worse than today's question, or the platform swaps the model.

**Caveats, stated in the write-up:** n=4 images (3 portraits, two from the same event), single-question
records rather than the app's joint five-question schema, no human labels anywhere, and the whole run
happened while the endpoint was at ~57 s/call. The composite's pose weights are hand-chosen, not
fitted. One genuine open question fell out: the composite and the `choice` variant both rank Jenni
above Irina; every other wording does the opposite, and the difference is one dimension
(`eyes_to_camera`: 0.604 vs 0.398). Only human labels can settle it.
