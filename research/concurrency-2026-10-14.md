# Concurrency: does a second in-flight call help, hurt, or do nothing? (2026-10-14)

Answers **HANDOFF §7.1** ("Settle the concurrency question, then maybe re-enable 2 streams") and
the follow-up question of whether an *adaptive* second stream — spin one up when a call goes slow,
retire it when it comes back — is worth building.

**Short answer:** a second stream is safe (the endpoint does not queue same-origin calls), and at
current production speeds it is **inert** — `SPACING` (0.9 s) is the binding constraint, not
concurrency. The adaptive behaviour asked for is therefore *already the behaviour of `INFLIGHT=2`
as written*; no monitor thread or 2-second timer is needed. What is still open is not a technical
question but a courtesy one (§13).

Raw logs: `concurrency-pacing-2026-10-14.txt` (offline, decisive),
`concurrency-pod-2streams-n12-2026-10-14.txt`, `concurrency-pod-4streams-n8-2026-10-14.txt`.

---

## 1. The instrument problem: latency depends on where you ask from

LiftWing classifies by **source network**, not by anything in the request
(`wikitech-liftwing-limits-2026-10-14.txt`, "Rate Limits & Access"): public/anonymous is 100 req/h
shared per client across all `llm-*` models; **WMCS (Cloud VPS and Toolforge) is a *known network*,
effectively unlimited**. Production runs in a Toolforge pod. A probe run from a home connection is a
different regime.

The CDN reports the class it assigned in the response header **`x-client-ip`**, so the change is
verifiable rather than assumed. Three origins, same images, same hour:

| origin | `x-client-ip` | serial median wall/call | note |
|---|---|---|---|
| home (dev Mac) | `173.79.30.139` | 0.467–0.610 s across runs | anonymous tier: 100 req/h shared |
| via Toolforge bastion (`wmcs_tunnel.py`) | `2a02:ec80:a000:1::1c5` | 0.537–0.599 s | WMF range; known-network tier |
| **inside the deployed pod** (`run_in_pod.sh`) | `172.16.21.201` | **0.327 s** (n=6) | production origin — and a *private* address, i.e. an in-cluster path |

Two things fall out of this table, and both matter:

1. **The tunnel changes the client class at little or no latency cost — and our first measurement of
   that was wrong.** An early bracketed run (home → tunnel → home, identical payloads) appeared to
   show the tunnel ~0.25–0.45 s/call *slower*, and a plausible mechanism was written down for it
   ("TLS is end-to-end, so the forward pays the extra hop"). **It was a bug in our own harness.**
   `wmcs_tunnel.py` passed `ControlMaster=auto`, and with a shared master the client `ssh -N`
   process **exits as soon as the forward is registered** — so the liveness check went false and
   every call re-spawned an ssh client *inside the timed region*. Dropping ControlMaster dropped the
   penalty:

   | round | direct | tunnelled |
   |---|---|---|
   | 1 | 0.467 s | 0.537 s |
   | 1 | 0.470 s | — |
   | 2 | 0.610 s | 0.599 s |
   | 2 | — | 0.571 s |

   Direct and tunnelled now **overlap** (0.467–0.610 vs 0.537–0.599), so any transport penalty is
   ≤ ~0.1 s and smaller than the direct path's own run-to-run spread. **Use the tunnel freely for
   the client class; it is not a latency tax.** Corroborating checks agree: a 400 KB POST to Commons
   through the same tunnel was *faster* than direct (0.272 s vs 0.362 s) and a 2.9 MB download ran at
   3.84 MB/s vs 3.49 MB/s, i.e. the forward is not bandwidth-limited.

   **Keep the lesson, which outlives the number:** a harness bug looked exactly like a property of
   the network, and it survived one round of "bracketing" because the bracketing itself used the
   buggy harness. The tell was that the claimed penalty was larger than the physics allowed — and
   that a *text-only* call through the same tunnel showed no gap at all (0.217 s vs 0.238 s direct),
   which no transport-level explanation could account for. The correct amount of confidence in an
   unexplained number is none.
2. **Production is ~1.4–1.8× faster than the home measurement** (0.327 s vs 0.467–0.610 s), and
   faster than the app's own `BASELINE_WARM_S` of 0.54 s. That baseline was measured "on this
   install" from a dev host, so a sane production run is being judged against a value ~1.6× too
   pessimistic.

Caveat, stated plainly: the tier is *inferred* from the source IP plus the upstream doc. There is no
observable header that says `x-trusted-request: A`, so nothing here proves the tier changed — only
that the origin did. No 429s occurred in any run.

## 2. Does the endpoint tolerate two concurrent calls? Yes

`probe_concurrency.py` calls LiftWing directly (bypassing the app's pacing on purpose) in three
phases — serial, then N calls across C streams released together behind a barrier, then serial again
as a drift check. Run inside the pod, n=12, 2 streams:

| phase | ok | median wall | mean wall | phase elapsed |
|---|---|---|---|---|
| A serial | 11 (+1× 503) | 0.337 s | 0.340 s | 4.02 s |
| B concurrent (2 streams) | 12 | 0.332 s | 0.370 s | **2.36 s** |
| C serial | 12 | 0.321 s | 0.312 s | 3.76 s |

- **Per-call latency is unchanged**: B/A median ratio **0.985**, B/C **1.03**.
- **Throughput improves ~1.6×**: 0.313 s/call serial → 0.197 s/call concurrent.
- **Drift check stable**: C/A = 0.951, so the endpoint did not move during the run.

Four streams (n=8) shows where the knee is:

| phase | median wall | phase elapsed |
|---|---|---|
| A serial | 0.326 s | 2.73 s |
| B concurrent (4 streams) | 0.525 s (+61 %) | 1.15 s |
| C serial | 0.331 s | 2.58 s |

4 streams buys 2.24× throughput but pays 61 % on per-call latency, with a bimodal distribution
(half the calls at serial speed, half ~2× slower) — the signature of the endpoint serving about two
calls well and queueing the rest. **Two is the right cap, and the owner's suspicion that the
endpoint "punishes" two concurrent calls is not supported.**

## 3. But `INFLIGHT=2` does nothing at current speeds — and that is the important result

`core._next_slot()` paces every model-call **start** `SPACING` apart, globally across workers and
jobs, *before* the call is issued. So the only thing that can overlap is a call that **outlasts**
`SPACING`.

`probe_inflight_pacing.py` runs the **real `core.run_job`** against a stubbed model (zero API calls)
with production's actual numbers — `SPACING=0.90`, six photos — and counts genuinely overlapping
intervals:

| per-call | SPACING | INFLIGHT | wall | max overlap |
|---|---|---|---|---|
| 0.33 s | 0.90 s | 1 | 4.86 s | 1 |
| 0.33 s | 0.90 s | 2 | **4.86 s** | **1** |
| 3.00 s | 0.90 s | 1 | 18.05 s | 1 |
| 3.00 s | 0.90 s | 2 | **9.94 s** | **2** |

At 0.33 s/call the two settings are **identical to the second** and the second worker never overlaps
once. At 3 s/call the same setting is worth **1.82×**. Nothing else changes between those two rows
but the call duration.

(The pre-existing `test_pipeline.py` does not contradict this: it sets `SPACING=0.05` with
`MODEL_S=0.50`, i.e. it deliberately puts the pipeline in the second row of that table.)

**This is why the adaptive idea needs no new code.** `INFLIGHT=2` *is* the requested algorithm,
because the pacing gate makes it self-gating:

- calls fast (< 0.9 s) → the second worker sleeps through the gap; behaviour is byte-for-byte the
  serial pipeline;
- calls slow (> 0.9 s) → the pacing gate goes slack and the second worker fills it, for ~1.8×.

A 2-second timer would be a worse version of the same thing: it fires too late (the gate already
goes slack at 0.9 s) and it needs state that the pipeline does not currently need.

## 4. What is actually open

1. **The courtesy question, not the technical one.** In a 3 s-and-worse regime, 2 streams doubles the
   *draw* on the endpoint (starts go from 1/per-call to 2/per-call) even though the start *rate* is
   still SPACING-governed. At 28 s/call that is ~257 calls/h — above the anonymous 100/h cap, but
   Toolforge is the unlimited tier. §13's "do not compensate by raising `INFLIGHT`" was written
   before this measurement, and about *reacting* to slowness; a static `INFLIGHT=2` is not a
   reaction. Still, it doubles draw during an incident, on a service that is `Open, Needs Triage`.
   **That is a judgement call for the owner, and arguably for the ML team on T440223.**
2. **`upstream_ms` is always `None`.** `x-envoy-upstream-service-time` is absent on this path — 0/24
   in the app's own cache, 0/~140 in these probes. HANDOFF §5 says every call records it. The field
   is dead, so the app's most server-authoritative timing signal does not exist; wall/ttfb only.
3. **~3.5 % of calls return a transient 503.** 4 confirmed out of ~116 (`upstream connect error or
   disconnect/reset before headers`), all in *serial* phases, none in the concurrent phases. The
   app's one-retry-on-5xx policy already covers exactly this (`UPSTREAM_RETRY_CODES`), which
   validates that decision — but a 4 % first-attempt failure rate on a production endpoint is a
   datapoint for T440223. n is too small to say anything about correlation with concurrency.
4. **`BASELINE_WARM_S` (0.54 s) may want revisiting for the deployment** — see §1, production
   measured 0.33 s.

## 5. Reproducing

```bash
python3 probe_inflight_pacing.py                     # offline, decisive, zero API calls
python3 wmcs_tunnel.py --check                       # prove the egress changed
./run_in_pod.sh probe_concurrency.py --n 12 --streams 2     # production origin
python3 run_via_toolforge.py probe_concurrency.py --n 6 --streams 2   # via the bastion
```

`wmcs_tunnel.py` + `run_via_toolforge.py` route only `api.wikimedia.org` through the bastion, so
Commons and every other host still go direct. `run_in_pod.sh` runs a repo script inside the deployed
pod (needs `LD_LIBRARY_PATH=/layers/heroku_python/python/lib` and `PYTHONPATH=/workspace`, because
the buildpack Python is not on the default PATH for a bare `sh -s`).
