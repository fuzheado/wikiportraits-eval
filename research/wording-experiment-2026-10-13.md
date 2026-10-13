# The "infobox ready" question: wording experiment, 2026-10-13

**Status:** research complete. **What was adopted the same day** (see §5 for the original
recommendation, which the owner adapted):

1. **`publish_ready` — the V5 two-option `choice` — was added to the schema.** First live call after
   the change returned publish **0.937** / pass 0.063 on a photo whose infobox answer was 0.746.
2. **A composite became the ranking key**: `min(face_visible, eyes_to_camera) × pose weight`, computed
   in `wiclient.composite()`, labelled *our arithmetic, not a model probability* everywhere it
   appears. `infobox_ready` is still asked and shown but no longer orders the list.
3. **Candidate spread is reported per run** for all three headline numbers (composite, P(publish),
   P(infobox)), replacing standard deviation as the measure of separation.
4. The two rewrites that this document rejects (plain-language V2, "publishable professional
   portrait" V3) were **not** adopted, and the reasons are recorded in §4.

Adding the question changed the schema hash, so **every cached answer was invalidated** — by design,
since answers to one question are only comparable within an identical question set.

**Still open:** the 12+ portrait validation run and hand labels (§5 item 6).

**Why this exists:** users reported that `infobox_ready` answers cluster in 0.5–0.6 and give a poor
spread, and that the wording ("lead image (infobox portrait) of a Wikipedia biography") requires a
lot of unpacking. This document tests whether rewording fixes it, and what the alternatives cost.

---

## 1. The question under test

Today's wording, verbatim from `wiclient.QUESTIONS`:

```json
{"type": "noul",
 "instructions": "Good enough to be the lead image (infobox portrait) of a Wikipedia biography?"}
```

`state`, shared by every call in the app:

```
WikiPortraits photograph of a public figure, taken by a volunteer photographer at an event photo booth. Assess the photograph itself, not the person's fame.
```

## 2. Free evidence first: how each question we already ask spreads (n=57 cached photos)

No API calls — computed from `cache/` by `analyze_question_spread.py`.

| question | type | sd | span | in 0.35–0.65 |
|---|---|---|---|---|
| `infobox_ready` (editorial judgement) | noul | 0.229 | 0.730 | **27/57 (47%)** |
| `quality` (aesthetic judgement) | score 0–4 | **0.145** | 0.572 | **34/57 (60%)** |
| `pose` confidence | choice | 0.171 | 0.679 | 32/57 (56%) |
| `face_visible` (visible fact) | noul | **0.372** | 0.973 | 2/57 (4%) |
| `eyes_to_camera` (visible fact) | noul | **0.350** | 0.946 | 7/57 (12%) |
| `min(face_visible, eyes_to_camera)` | composite | 0.360 | 0.957 | 6/57 (11%) |
| `min(face,eyes) × pose weight` | composite | 0.352 | 0.958 | 12/57 (21%) |

Two things follow. First, the split is not by question *type*: the `score` question is the most
compressed thing we ask, so switching to an ordered scale makes clustering worse, not better.
Second, the split is by *what the criterion is*: questions about something visible in the frame
spread across nearly the full range; questions that ask the model to apply an editorial or aesthetic
standard collapse into a band.

## 3. The wording experiment (`tune_infobox_question.py`)

Four photos, chosen by quartile from the 57 cached answers (so the sample spans the score range),
plus one deliberate negative control. One question per record (isolated wording comparison).

| photo | today's cached value |
|---|---|
| Andrey Stefanov (Sundance 2024) | 0.740 |
| Irina Tjelle (true crime festival 2025) | 0.528 |
| Jenni Trang Lê (Sundance 2024) | 0.208 |
| **Wikimedia logo** (control — not a photograph of a person) | 0.010 |

Five variants, all answered as P(publish-ready) (a `score` is divided by its top index):

- **V1 — today's wording, verbatim** (baseline; also a reproducibility check)
- **V2** `noul` — "Would a picture editor choose this photograph to represent this person in an
  encyclopedia article?" + criteria describing true/false in visual terms
- **V3** `noul` — "Is this a publishable professional portrait of this person?" + visual criteria
  (no Wikimedia nouns at all)
- **V4** `score` 0–4 — "How strong is this photograph as a professional portrait of this person?"
  (unusable → excellent)
- **V5** `choice` — "An editor needs one photograph of this person." with options
  `publish` ("This photograph is the one to use.") / `pass` ("Choose a different photograph instead.")

20 calls, 2026-10-13 14:23–14:42 UTC, all **HTTP 200**, median **56.9 s** per call (the endpoint was
in its degraded state all day), no 429s. Raw answers: `wording_experiment.jsonl`; chart:
`wording_comparison.png`.

### Results

| photo | V1 today | V2 plain+criteria | V3 professional | V4 score/4 | V5 publish/pass | composite |
|---|---|---|---|---|---|---|
| Andrey (0.740) | 0.676 | 0.920 | 0.960 | 0.676 | 0.877 | 0.763 |
| Irina (0.528) | 0.357 | 0.834 | 0.944 | 0.642 | 0.708 | 0.398 |
| Jenni (0.208) | 0.190 | 0.814 | 0.939 | 0.594 | 0.846 | 0.544 |
| logo (0.010) | 0.024 | 0.052 | 0.164 | 0.071 | 0.037 | 0.003 |
| **sd (all 4)** | 0.241 | 0.350 | 0.339 | 0.247 | 0.341 | 0.277 |
| **in 0.35–0.65** | 1/4 | 0/4 | 0/4 | 2/4 | 0/4 | 2/4 |
| **range among the 3 portraits** | **0.486** | 0.106 | **0.021** | 0.082 | 0.169 | **0.365** |
| rank agreement with baseline (ρ) | — | +1.00 | +1.00 | +1.00 | +0.80 | +0.80 |

## 4. Findings

1. **Rewording moves the pile-up from the middle to the ceiling.** Every reworded variant improved
   global spread (sd 0.24 → 0.34) and emptied the 0.35–0.65 dead zone — but V2 and V3 did it by
   lifting all three real portraits to 0.81–0.96. V3 leaves them at 0.939 / 0.944 / 0.960: a range of
   **0.021**, i.e. three different photographs are effectively tied. Today's wording, which "clusters
   around 0.5", actually separates those same three photos best of any single question (0.486).
2. **Therefore global spread is the wrong target.** sd rises when everything moves up. What triage
   consumes is the range and ordering *among plausible candidates* (here: the three portraits). The
   right metric to track is candidate spread, not sd.
3. **The ordered `score` type is not a fix** — V4 is still compressed (2 of 4 in the dead zone,
   nothing above 2.7/4), consistent with `quality` being the most clustered question we ask (sd
   0.145 across 57 photos).
4. **`choice` with two named options is the best single-question alternative tested**: no dead-zone
   entries, no ceiling saturation (max 0.877 vs 0.960), 0.169 range among the portraits, and it needs
   no threshold story in the UI ("the model chose *publish* over *pass*").
5. **The composite is second, not first, on separation in this sample — but it is explainable and
   spreads far wider across the whole corpus.** Its values 0.398 / 0.544 / 0.763 give a range of
   **0.365**: less than today's wording (0.486), ~2× V5 (0.169) and ~17× V3 (0.021). Across all 57
   cached photos it spreads much more than `infobox_ready` (sd 0.352 vs 0.229) and it decomposes into
   visible facts a user can check. It reorders the middle pair relative to V1–V4 (Jenni above Irina,
   as V5 does), and the difference is exactly one dimension: `eyes_to_camera` (Irina 0.398 vs Jenni
   0.604). Nothing in this experiment can say which order is correct — only human labels can.
6. **Reproducibility held at the degraded speed**: V1 re-runs kept the cached ordering with
   magnitudes within 0.17 (one pair moved 0.17; the rest ≤0.07). Slow ≠ erratic.

**The honest summary of the "range" column:** on the three portraits in this sample, the wording users
complained about separates them *best*. The complaint was that the numbers look alike, not that the
order is wrong — and ordering is what triage consumes. n=3 makes the range differences indicative at
best, which is why the next step is a wider run with hand labels rather than a schema change.

## 5. Recommendation (proposed, not adopted)

1. **Do not adopt V2, V3 or V4.** Each one answers the complaint and makes the tool worse at its
   actual job: separating good photos from better ones.
2. **Do not change the headline question yet.** On this sample the wording users complained about
   separates the three candidates *best* (range 0.486 vs 0.365 for the composite, 0.169 for V5 and
   0.021 for V3). "The numbers all look like 0.5" is a *resemblance* complaint, not an ordering
   complaint — and ordering is what triage consumes.
3. **Fix the presentation instead — no schema change, no new calls.** Show a rank and a percentile
   within the run next to the probability ("3rd of 24 · top 13%"), and show the run's spread. That
   directly addresses the thing that felt broken, without touching a question whose ordering we
   cannot currently improve on evidence.
4. **If a switch is still wanted**, the candidates to validate are the composite
   `min(face_visible, eyes_to_camera) × pose weight` (explainable, widest corpus-level spread: sd
   0.352 vs 0.229 for `infobox_ready`) or V5 as the single-question alternative — **not** the
   rewordings. Any switch must be labelled for what it is: the composite is our arithmetic on three
   model answers, not a model probability.
5. **Add candidate spread to the run report** (range and ordering among photos that pass a
   plausibility floor) and stop treating sd as a measure of question quality.
6. **Validate before shipping anything.** Re-run on 12+ portraits (today's wording, V5 and the
   composite — the composite is free), then against human labels, and measure *ordering agreement*,
   not range. Spread is not accuracy: a more polarising number is only an improvement if it tracks
   what an editor would actually pick.

**Reopen if:** the wider run reverses the portrait ordering, hand labels show a candidate ranks worse
than the current question, or the platform swaps the model.

## 6. Caveats

- **n=4 images, 3 of them portraits** and one of those three is a red-carpet frame of the same event
  as another. These are directional findings, not a study.
- Variants were asked as **single-question records** to isolate wording; the app asks all five
  questions jointly, and the joint answer for one question is not guaranteed identical.
- All 20 calls ran while the endpoint was ~57 s per call (vs a 0.54 s warm baseline); nothing in the
  results suggests the slowdown changed the answers, and V1's reproducibility supports that.
- **No human labels were involved.** Every claim here is about spread and agreement, not correctness.
- The composite's pose weights are hand-chosen (they only re-order group/candid below the
  portrait-scale poses); they are not fitted to anything.

## 7. Reproducing

```bash
python3 analyze_question_spread.py          # the n=57 census (free)
python3 tune_infobox_question.py            # the 4x5 experiment (20 calls, ~19 min today, ~1 min warm)
python3 summarize_wording_experiment.py     # table from the JSONL
.venv-pdf/bin/python make_wording_chart.py   # the chart (needs PIL; repo-relative)
```
