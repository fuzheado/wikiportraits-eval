# Memo: URL-parameter-driven input (`?list=`, and what to refuse)

**Question asked:** can a list of files (and flags) be passed in via the page URL — either to prefill
the box, or to start processing immediately?

**Method:** a read-only subagent investigation of this repo (reading `static/index.html`, `core.py`,
`wiclient.py`, `app.py`, the tests, and primary sources for browser/proxy URL limits), followed by
**measurements on the live deployment** for the two claims that had to be facts rather than
inferences. Where those measurements contradicted the investigation, the measurement wins and the
correction is marked.

**Verdict, in one line:** build `?list=` prefill and a capped `?check=1`; **refuse `?listurl=` and
`?run=1` outright**.

| # | option | value | risk | verdict |
|---|---|---|---|---|
| 1 | `?list=<paste text>` (inert prefill) | high — a shareable link for a workshop, an event page, a report | very low — no model calls, no new parser, no server change | **build** |
| 2 | `?check=1` (presses the existing *Validate files*) | medium-high — the link lands showing "12 of 12 resolved" and an armed Score button | low — Commons lookups only, 0 model calls; needs an item cap | **build, capped ≤50** |
| 3 | `?fresh=1` (pre-tick the run-fresh box) | low-medium | low | build as a *visible* default only |
| 4 | `?list=default` alias for `/api/default-list` | low — a short demo link | very low | optional |
| 5 | `?job=<id>` results deep link | high for sharing | needs durable job storage (jobs are in-memory and wiped on restart, §7.4) | defer |
| 6 | `?listurl=<url>` (server fetches the list) | low — paste and `POST /api/run` already cover it | **high** — SSRF, open fetcher, proxy abuse | **refuse** |
| 7 | `?run=1` auto-start | low — `check=1` leaves one click | **high** — spends from a link, with the ceiling unenforced | **refused as asked; shipped in one bounded form** — see below |

**Built the same day (v0.9.0): options 1 and 2, with a 50-entry action cap.** One correction to the
grammar above, and it matters: **the `|` shorthand was dropped.** `|` cannot appear in a MediaWiki
title, which is why it looked like a safe separator — but this app also accepts the *wikitext* form
`[[File:Foo.jpg|thumb|caption]]` (About §2, README), so translating pipes to newlines would silently
corrupt a documented input form. `?list=` is therefore newline-separated (`%0A`) **only**, which is
also the safest possible rule: the URL layer performs no splitting at all, so
`wiclient._split_units()` remains the only splitter in the system. `test_render_dom.js` now asserts
that a piped wikitext value survives verbatim, and the comma case stays one entry.

Guardrails as shipped: a link may prefill and press *Validate files*, and **never starts a run**; the
params are stripped from the address bar with `replaceState` so a reload cannot replay them; over
~4,000 characters is refused outright rather than echoed (the pod logs the whole query string, as
measured in §3); auto-check is capped at 50 entries. All of it is client-side, so the server-side
surface is unchanged: `/api/parse` sees an ordinary request and keeps `PARSE_MAX` / `MAX_INPUT_ITEMS`.

**Later addition (v0.13.0): `?run=1` as a *cache-only replay*.** The owner asked for a shareable link
that "immediately loads the graph". The refusal above assumed auto-start must mean *spending*, and it
need not: a new `cached_only` mode resolves the list and serves saved answers with **zero model
calls**, marking anything unscored as unscored and leaving Score them armed. So the sharing case —
the one that motivated the request — is exactly the case where nothing is spent, because the sharer
already paid for those answers on the same server. The §5 objection (a link that spends without a
human deciding) is therefore untouched, and `test_pipeline.py` asserts the zero both warm and cold.
`?view=` came with it, so a link reproduces the chart view too.

**Still not built, deliberately:** `?listurl=` (§4) and `?fresh=1` — the last one is a
one-liner but it was not part of the approved scope, and it is the half of auto-start that would spend
without a click.

---

## 1. Why (1) and (2) are cheap: it is a client-side feature

- `app.py` hands `urlparse(self.path).path` to `core.route()`, so `/?list=…` already routes to the
  same `_read_static("index.html")` branch. **The server cannot even see the query string.** No route,
  cap or API change is needed.
- Both buttons read `$("input").value` at click time, so setting `.value` and then calling `.click()`
  reproduces the user path exactly — there is no second code path to keep in sync.
- The parse the URL list would go through is the one already tested: `POST /api/parse` /
  `POST /api/run` → `new_job()` → `wiclient.parse_entries()`.
- Every cap a hostile link could need is already enforced server-side: `MAX_INPUT_ITEMS` 200,
  `PARSE_MAX` 1000, `MAX_PARALLEL_JOBS` 2 with a `409`.
- Nothing in `static/index.html` reads `location.search` today.

## 2. The grammar

```
GET /?list=<paste-text>&check=1&fresh=1
```

- **`list` is paste text, not a structured list.** Entries are separated by newlines (`%0A`);
  `\r\n` is normalised.
- **`|` (`%7C`) is accepted as shorthand**, because `|` cannot occur in a MediaWiki title
  (`wiclient._TITLE_ILLEGAL = "#<>[]|{}"`). It is translated to a newline *client-side*.
- **Commas and semicolons are never delimiters in the URL layer.** The decoded value — commas and all
  — goes into `#input.value` verbatim, so `?list=Foo.jpg,Bar.jpg` is split by the *existing* tested
  rule, while `…(In die Sonne schauen%2C Cannes 2025).jpg` stays one entry.

**Why this cannot resurrect the comma bug (invariant 1).** The bug class is "a splitter that treats
`,` as a separator". This grammar adds zero new splitters: one `|`→newline replace (a character that
is illegal in a title) plus a `\n` split for a display count. The URL layer never inspects a comma, so
`wiclient._split_units()` remains the only splitter in the system — which is the rule to write down.

Build links with `encodeURIComponent`. **Document that a literal `+` must be `%2B`**: `URLSearchParams`
applies form decoding, so a hand-written raw `+` in a filename becomes a space, which can silently
resolve to *a different real file*. The existing "resolved inputs" table shows what the link meant
before anything is scored, which is the mitigation.

## 3. Measured URL limits — the earlier assumption was too pessimistic, and the shape of the failure matters

The investigation assumed a default 8 KB nginx request line returning a clean `414`. **Measured
against the live deployment (2026-10-14), one request each, no model calls:**

| `?list=` value length | result |
|---|---|
| 1 000 · 4 000 · 8 000 · 10 000 · 12 000 · 14 000 | **HTTP 200** |
| 15 000 · 16 000 · 32 000 · 60 000 | **no HTTP status at all** — `HTTP/2 stream not closed cleanly: PROTOCOL_ERROR`, curl exit 92 |

So: the ceiling is between 14 and 15 KB, and it does **not** fail cleanly — the connection is dropped,
so a too-long link produces no page and no error message a user could act on. Two consequences:

- **The starter list fits comfortably.** `default_list.txt` is 736 raw bytes → **978 percent-encoded**
  → a **~1 025-character URL**. That is emailable, bookmarkable and pasteable.
- **A 200-item run does not.** 61.2 chars/entry average → 200 entries ≈ 12 250 raw / **16 300 encoded**,
  which is past the measured ceiling *and* past Firefox's 2 000-char history limit. `?list=` is a
  starter-list-sized transport; a big run belongs in `POST /api/run`'s body, or in a future
  *server-named* source (Category/PetScan, §7.6 of the handoff).

**Recommendation:** soft-warn above ~2 000 chars, hard-refuse above ~4 000, and never auto-act on more
than ~50 items.

## 4. `?listurl=` — refuse

`wiclient._clean_host()` is a **classifier, not a fetch guard**: it fails closed for userinfo, IP
literals and non-Wikimedia hosts, but its output only selects *which wiki's Action API* to query. The
actual fetch guard is a different rule — fetches only ever target thumbnail URLs returned by a
Wikimedia Action API, never a URL parsed out of user input (invariant 8).

Two properties make it unsuitable as a fetch guard even if reused:

1. It **strips a port rather than rejecting it** (`host = netloc.rsplit(":", 1)[0]`), while its own
   docstring says it rejects ports. Harmless for classification; material for a fetcher.
2. It validates the **input** URL, and `urllib` follows redirects automatically, so nothing
   re-validates the effective target after a `301/302`. (OWASP SSRF guidance: disable redirect
   following; domain allowlisting alone does not stop DNS rebinding.)

A `?listurl=` link would make the *server* dereference a URL chosen by whoever wrote the link, with no
user gesture — from inside WMCS, where the app is documented to reach an internal-only service. That
is a lot of new attack surface for a convenience feature that paste and `POST /api/run` already
provide.

## 5. Auto-start (`?run=1`) — refuse, because the ceiling is not a ceiling

Verified in code: **`HOURLY_BUDGET` is counted and displayed, never enforced.** The only gate before a
call is `budget.wait_seconds()` (the 429 cooldown); nothing compares `used()` with `limit`, and
`used()` is read only by `status()` for display. So the counter can pass its own ceiling — the UI would
simply read `budget: 137/100`.

That is tolerable while a human presses a button each time, and is the reason auto-start is the one
feature that could spend without anyone deciding to. On a fresh tab there is no confirmation dialog
(`askNewRun()` only prompts when results are already on screen). Combined with `MAX_PARALLEL_JOBS = 2`,
a link posted to a mailing list could run two 200-call jobs from the tool's origin and hand everyone
else a `409`.

`?check=1` gets most of the value at zero model cost: it performs Commons lookups only, leaves Score
one deliberate click away, and shows what the link meant first.

**Also measured:** the query string is **logged in full**. `kubectl logs` shows
`"GET /?list=…(the whole 14 000 characters) HTTP/1.1" 200`. Commons filenames are public, so this is
low risk — but users should be told not to put anything private in a link, and a 14 KB query per hit
is non-trivial log volume.

## 6. Tests

- `test_render_dom.js` **keeps passing** provided the feature is strictly gated on a parameter being
  present: it loads `index.html` with no URL, so `location.search === ""` and invariant 12's
  assertions still hold.
- `test_ui_contract.py` constrains *where* the code goes: it scans only the **last** inline `<script>`
  block and `check_js.py` concatenates all blocks before `node --check`. So the new code must live
  inside the existing single inline block — a second block would silently stop the contract test
  checking the real script.
- `test_forms.py` / `test_commas.py` are unaffected, and that is the point: they do not cover the new
  layer. New cases are needed — most importantly **`?list=Foo.jpg,Bar.jpg` must arrive as one line**
  (the URL layer adds no comma semantics), and `?list=…(…%2C…).jpg` must stay one entry.
- Add to `test_ui_contract.py`: `'split(",")' not in js` as a direct invariant-1 guard on the URL
  layer, and a positive check that the handling is gated (`'location.search' in js`).

## 7. Implementation sketch (~30 lines, JS only, no `core.py` change)

```js
const URL_LIST_SOFT_MAX = 2000, URL_LIST_HARD_MAX = 4000, URL_LIST_ACTION_MAX = 50;

function applyUrlParams() {
  const qs = new URLSearchParams(location.search);
  const raw = qs.get("list");
  if (!raw) return;                        // no param => box stays empty (invariant 12)
  if (raw.length > URL_LIST_HARD_MAX) {
    $("parseinfo").textContent = "the ?list= link is too long — paste the list instead";
    return;
  }
  const text = raw.replace(/\r\n?/g, "\n").replace(/\|/g, "\n").trim();
  const n = text.split("\n").filter(s => s.trim()).length;
  $("input").value = text;                 // paste text, verbatim — commas and all
  $("input").classList.remove("hint-off");
  setStage("idle");                        // loaded but unvalidated: Validate files is the next step
  $("parseinfo").textContent = `${n} photo${n === 1 ? "" : "s"} came from the link — press Validate files`;
  if (qs.get("fresh") === "1") $("nocache").checked = true;   // visible, reversible, spends nothing
  history.replaceState(null, "", location.pathname);          // a refresh must not re-run anything
  if (qs.get("check") === "1" && n > 0 && n <= URL_LIST_ACTION_MAX) $("check").click();
}
applyUrlParams();
```

## 8. The docs change this forces

HANDOFF invariant 12 says *"The image box must never arrive pre-filled"*. A `?list=` prefill does not
violate its **purpose** (nothing appears unbidden — it requires an explicit parameter), but it
contradicts its **letter**. Amend it explicitly rather than shipping against it: *"…unless the URL
carries an explicit `?list=`; a URL-borne list may prefill and, at most, run the input check — it must
never start a run."* Also add the grammar rule from §2 to the About panel's input-formats section.

## 9. Confidence: what is measured vs inferred

**Measured on the live deployment (2026-10-14):** the ~14 KB ceiling and its failure mode; the full
query string appearing in `kubectl logs`; the starter list's 978-encoded-char size (computed from the
file).

**Verified by reading code:** the server never sees the query string; the caps and the `409`; nothing
reads `location.search` today; **`HOURLY_BUDGET` is not enforced**; `_clean_host` strips rather than
rejects ports, and fetches follow redirects; `test_ui_contract.py` reads only the last `<script>`
block.

**Inference, not measured:** that Toolforge's ingress is what drops the 15 KB request (the connection
error is consistent with an ingress buffer limit, but no config was read); the SSRF reachability of
internal services from the pod (credited as evidence, not proof); `URLSearchParams`' exact escaping set
(the `+`→space trap is standard behaviour, and the mitigation is a decoded-text display); Firefox's
2 000-char history pref; and the exact caps in §3, which are judgement calls.
