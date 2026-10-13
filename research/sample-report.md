# WikiPortraits Eval report — 2026-10-13 17:10 UTC

> Model: llm-clef-flash (Cloudflare/clef-flash, 9.41 B params BF16, Apache-2.0) served by Wikimedia LiftWing at api.wikimedia.org/service/lw/inference/v1/models/llm-clef-flash:predict (KServe on HuggingFace transformers, not vLLM).
> Schema: one state + six typed questions, all answered in a single forward pass; the model generates no text (usage.output_tokens = 0). Question types: face_visible (yes/no), pose (choice of 5), quality (score 0-4), infobox_ready (yes/no), publish_ready (choice of 2: publish / pass), eyes_to_camera (yes/no).
> Headline numbers: composite = min(face_visible, eyes_to_camera) x pose weight (head-and-shoulders 1.0, three-quarter 0.9, full body 0.6, candid 0.5, group 0.25). That is our arithmetic on three model answers, NOT a model probability; P(publish) and P(infobox) are the model's own. Ranking uses the composite.
> Spread: the run reports candidate spread (range and median gap among answers at or above 0.25), not standard deviation — sd rises whenever answers pile up at either end, including at the ceiling, so it rewards the wrong thing.
> Inputs: Commons thumbnails at the standard step nearest above 640 px (the server downscales to 640 px wide before the encoder, so larger sends add no information).
> Note: probabilities depend on the question set — the head scores every question jointly, so only compare runs that used this identical five-question schema.

## Summary

- Images pasted: **1** · resolved: **1** · scored: **1** · not found/failed: **0**
- P(infobox-ready): mean **0.6496** · median 0.6496 · max 0.6496 · **1 of 1** at or above 0.5
- P(publish): mean **0.9155**  ·  composite (our arithmetic, min(face, eyes) × pose weight): mean **0.9406**, max 0.9406
- Quality (0–4): mean **2.56**
- Pose mix: head_shoulders 1
- **Candidate spread** (composite ≥ 0.25): 1 of 1 photos are candidates · range **None** · median gap between neighbours None · 0 of 1 (0%) sit in the uninformative 0.35–0.65 band
  - same measure for infobox_ready: 1 of 1 candidates · range None · median gap None · 1 in the band
  - same measure for publish_ready: 1 of 1 candidates · range None · median gap None · 0 in the band
- Wall: median 56.735 s per call, 56.73 s total · input tokens 1323
- Job wall clock: 0.13 s
- Endpoint latency (service-side): median **56.735 s** per model call (56.735–56.735 s, n=1) vs the measured warm baseline of 0.54 s — 105.1x. model calls are 105.1x the measured warm baseline (56.735s vs 0.54s) with no 429s — the slowdown is server-side, not the input, the image size, or this app

## Ranked by composite

| # | Photo | composite | P(publish) | P(infobox) | quality | pose | P(eyes→cam) | P(face) | tokens | wall |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg | **0.941** | 0.915 | 0.650 | 2.56 | head_shoulders | 0.941 | 0.971 | 1323 | 56.73s |

## Per-image determinations

### File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg

![MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg](https://thumb.wikimedia.org/wikipedia/commons/thumb/6/67/MKr377445_Susanne_Wuest_%28In_die_Sonne_schauen%2C_Cannes_2025%29.jpg/960px-MKr377445_Susanne_Wuest_%28In_die_Sonne_schauen%2C_Cannes_2025%29.jpg?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail)

- Commons: https://commons.wikimedia.org/wiki/File:MKr377445_Susanne_Wuest_(In_die_Sonne_schauen,_Cannes_2025).jpg
- Licence: CC BY-SA 4.0 · photo: Martin Kraft
- Original 3465×5197 px; sent 960x1440; 1323 input tokens; 56.735 s
- **composite** (ours): 0.941 · **P(publish)**: 0.915 (publish)
- **infobox_ready**: 0.650 · **face_visible**: 0.971 · **eyes_to_camera**: 0.941
- **pose**: head_shoulders (P=0.946) — head_shoulders 0.946, three_quarter 0.025, candid 0.021, full_body 0.005, group 0.003
- **quality**: 2.560 of 4 — 0 0.040, 1 0.063, 2 0.325, 3 0.440, 4 0.132

---

Produced by the WikiPortraits Eval prototype. Every probability above was returned by the endpoint; nothing is inferred. Photographs are from Wikimedia Commons under their own licences. Verify before any on-wiki use: model output is an assistive draft, not a source.
