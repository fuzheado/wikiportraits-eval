# Memo: corrections for the Toolforge skills — `become`, and Build Service deploys

**To:** whoever maintains the Wikipedia-AI-Skills repository
**From:** Andrew Lih (`User:Fuzheado`), while deploying a real tool out of the `wikiportraits-eval` repo
**Date:** 2026-10-14
**Revision examined:** `ad48944` (`main`) · `wikimedia-toolforge` front-matter says `last_verified: 2026-10-05`
**How these findings were produced:** by deploying a Build Service Python tool to Toolforge end-to-end,
then testing each claim directly on the bastion. Every assertion below has a command and its output in
§6 — please reproduce them rather than taking my word for it.

Line numbers refer to `ad48944`; they will drift.

---

## TL;DR

1. **One thing is right, and it is the important one.** The `become` vs `sudo -niu` rule in
   `.claude/skills/wikimedia-toolforge/SKILL.md` is correct, well explained, and I confirmed it
   empirically on the bastion. Keep it.
2. **The same file contradicts it nine times.** Sixty lines below that warning, the *recipes* for
   deploying a Build Service web app use the exact shape the warning labels ❌ BROKEN. Followed
   literally, they run as the wrong account — so the recipe deploys nothing, or acts on the wrong tool.
3. **Two Build Service gotchas are missing repo-wide** (I checked: zero occurrences of either). They
   are the two that cost me the most time. Text ready to paste in §4.
4. **One correction about my own first pass**, in §2.3: a grep-based sweep over-reported, and six sites
   in `toolforge-python` / `toolforge-nodejs` that *look* wrong are in fact **fine**. Please don't
   "fix" those.

---

## 1. What is right, exactly

### 1.1 The pitfall entry (keep verbatim)

`.claude/skills/wikimedia-toolforge/SKILL.md`, §"Guardrails & Common Pitfalls", lines 555–568:

> 1. **Use `become` for interactive sessions, `sudo -niu` for SSH one-liners.**
>    Running `become my-tool-name` interactively (SSH in, then `become`, then commands)
>    works correctly. But `become` uses `exec` internally, which **replaces the shell** —
>    so command chaining over SSH fails:
>
>    ```bash
>    # ❌ BROKEN — second command runs as YOUR user, not the tool
>    ssh user@login.toolforge.org "become my-tool-name; webservice restart"
>
>    # ✅ CORRECT — use sudo -niu for chained SSH commands
>    ssh user@login.toolforge.org "sudo -niu tools.my-tool-name webservice restart"
>    ```

This is accurate, and the mechanism it names (`exec`) is the actual cause. The `become` script is:

```bash
tool="$1"
shift
…
exec /usr/bin/sudo -niu "$prefix.$tool" "$@"
```

So `become <tool>` with **no** remaining arguments becomes `exec sudo -niu tools.<tool>` — sudo starts
a shell of its own, replacing the process, and **everything after the `;` in the outer command is
never part of that shell**. It runs in the parent shell, as the original user. Exactly as documented.

### 1.2 Two other places that are right

- The warning box at lines 92–98 ("One-shot commands over SSH — don't use `become`") — same rule,
  same reasoning, correct.
- The nested-shells section, ~lines 922–997, whose diagrams (`local → SSH → bastion → sudo -niu
  tools.x`) are the right mental model.

There is genuine, hard-won knowledge in this file. The problem is only that the recipes disagree
with it.

---

## 2. The contradiction

### 2.1 Two shapes that look alike and are not

This distinction is the whole memo, so it is worth being pedantic:

| shape | how the shell expands it | who ends up running the command | measured |
|---|---|---|---|
| `become TOOL cmd args` | `exec sudo -niu tools.TOOL cmd args` | **the tool** | ✅ `uid=58240(tools.wikiportraits-eval)` |
| `become TOOL; cmd` | `become TOOL` (no args → sudo shell) **then** `cmd` in the *parent* shell | **you**, the bastion user | ❌ `uid=3522(alih)` |

Both were run on `dev.toolforge.org` on 2026-10-14; full transcript in §6.

### 2.2 The nine sites that use the broken shape

All of these are inside copy-me code blocks in
`.claude/skills/wikimedia-toolforge/SKILL.md` — i.e. presented as *recipes*, not as counter-examples:

| line | snippet | section |
|---|---|---|
| 627 | `"become my-web-tool; toolforge envvars create API_KEY …"` | Step-by-step web app recipe |
| 630 | `"become my-web-tool; webservice --backend=kubernetes python3.11 start"` | " |
| 633 | `"become my-web-tool; webservice --backend=kubernetes python3.11 status"` | " |
| 643 | `"become my-tool; crontab -l \| { cat; echo '0 3 * * * toolforge jobs run …` | Daily data collection (cron) |
| **657** | `"become my-build-tool; toolforge build start https://gitlab.wikimedia.org/…"` | **Build Service Web App** |
| **660** | `"become my-build-tool; toolforge webservice buildservice start --mount=none"` | " |
| **663** | `"become my-build-tool; toolforge webservice buildservice status"` | " |
| 666 | `#    ssh login.toolforge.org "become my-build-tool; toolforge build start <repo-url>"` | " (update step) |
| 667 | `#    ssh login.toolforge.org "become my-build-tool; toolforge webservice buildservice restart"` | " (update step) |

(Line 562 is the *intentional* counter-example inside the pitfall entry — leave it alone.)

The Build Service block (646–668) is the one that matters most, because it is the workflow people
follow for a modern deploy, and because two of its steps are already missing the information in §4.

**What actually happens at line 660**, per my measurements: `become my-build-tool` spawns a tool shell
with no command, which exits; `toolforge webservice buildservice start --mount=none` then runs as the
bastion user. The CLI has no tool context, so you get permission errors, or — worse on the mutating
steps (envvars, cron, build start) — the command runs but not as the intended tool.

### 2.3 A correction to my own first pass (please read before editing)

My first sweep was a `grep` for `ssh …"become`, and it flagged this in two sibling skills:

- `toolforge-nodejs/SKILL.md` lines 163, 170, 173, 554
- `toolforge-python/SKILL.md` lines 893, 896

Those use `become <tool> <cmd>` — **shape 1, which works.** e.g.

```bash
ssh <user>@login.toolforge.org "become my-tool webservice --backend=kubernetes node22 status"
```

expands to `exec sudo -niu tools.my-tool webservice … status`, which is correct. I only discovered this
by testing the two shapes separately; the grep could not tell them apart, and neither could I until I
ran them. **Do not change those six sites.** If it helps, the test that separates them is one command
(§6, step 1).

(If you want belt-and-braces consistency across the repo, converting those six to `sudo -niu` too is
harmless — but they are not bugs, and they are not the reason for this memo.)

---

## 3. Proposed edits

The mechanical rule for §2.2: **turn `become TOOL; CMD` into `sudo -niu tools.TOOL CMD`** (the
`tools.` prefix is required — that is the actual Unix account name).

Before:

```bash
ssh user@login.toolforge.org "become my-build-tool; toolforge build start https://gitlab.wikimedia.org/toolforge-repos/my-build-tool"
```

After:

```bash
ssh user@login.toolforge.org "sudo -niu tools.my-build-tool toolforge build start https://gitlab.wikimedia.org/toolforge-repos/my-build-tool"
```

The nested-quoting cases need one thing watched. Line 643's cron recipe wraps a shell pipeline, so
quote for the *remote* shell and pass the pipeline through `sh -c`:

Before:

```bash
ssh user@login.toolforge.org "become my-tool; crontab -l | { cat; echo '0 3 * * * toolforge jobs run daily-collect --command \"python3 /data/project/my-tool/collect_data.py\" --image python3.11 --wait --filelog >> /data/project/my-tool/logs/cron_trigger.log 2>&1'; } | crontab -"
```

After:

```bash
ssh user@login.toolforge.org "sudo -niu tools.my-tool sh -c 'crontab -l | { cat; echo \"0 3 * * * toolforge jobs run daily-collect --command \\\"python3 /data/project/my-tool/collect_data.py\\\" --image python3.11 --wait --filelog >> /data/project/my-tool/logs/cron_trigger.log 2>&1\"; } | crontab -'"
```

Quoting through ssh → sudo → sh is genuinely fiddly; if a version of this fights you, the reliable
fallback is to write the inner script to a file and run the file (see the "files, not inline text"
discipline in the wider skill set), or to keep the mutation interactive.

**One structural suggestion**, to stop this class of drift: put a single canonical line at the top of
both `toolforge-python` and `toolforge-nodejs` —

> ⚠️ **Over SSH, one-shot tool commands are `sudo -niu tools.<tool> <cmd>`.** Never
> `ssh host "become <tool>; <cmd>"` — see `wikimedia-toolforge` §Guardrails & Common Pitfalls.
> (`become <tool> <cmd>` with the command as an argument *is* fine.)

— and have the recipes in those files use one shape only.

---

## 4. Missing knowledge, with text to paste

Both of these are absent from the repo as of `ad48944`: I grepped for `buildservice-image`,
every variant of digest pinning, and the phrase "already running" — **zero hits**.

### 4.1 `--buildservice-image` wants `namespace/name`, not the registry path

`toolforge build start` prints the image with its full registry path, which invites a copy-paste that
the CLI rejects:

```
Built image tools-harbor.wmcloud.org/tool-wikiportraits-eval/tool-wikiportraits-eval:latest@sha256:fc017a2b…
$ toolforge webservice buildservice start --buildservice-image tools-harbor.wmcloud.org/tool-wikiportraits-eval/tool-wikiportraits-eval@sha256:fc017a2b… 
toolforge webservice: error: argument --buildservice-image: Expected build service image in
'namespace/name' format, got 'tools-harbor.wmcloud.org/tool-wikiportraits-eval/tool-wikiportraits-eval@sha256:fc01…'
```

Drop the registry host and keep the digest:

```bash
toolforge webservice buildservice start \
  --buildservice-image tool-wikiportraits-eval/tool-wikiportraits-eval@sha256:<digest> \
  --mount=none -m 512Mi
```

Suggested wording for the skill: *"`--buildservice-image` takes `namespace/name@sha256:<digest>` — the
tool's namespace, then the image name. The full `tools-harbor.wmcloud.org/…` path that `build start`
prints is rejected; strip the host."*

### 4.2 `start` is a no-op while the webservice is already running

This is the one that silently wastes a deploy:

```
$ toolforge webservice buildservice start --buildservice-image …@sha256:<new-digest> --mount=none -m 512Mi
Your job is already running
$ toolforge webservice buildservice status
Your webservice of type buildservice is running on backend kubernetes
```

The image is never swapped. The result is a running service on the **old** image, which looks like a
successful deploy until you notice the code did not change. The fix is to stop first:

```bash
toolforge webservice buildservice stop
toolforge webservice buildservice start --buildservice-image <ns>/<name>@sha256:<digest> --mount=none -m 512Mi
```

And the way to *prove* the new image is live — because `uptime` cannot tell you, and a restart resets
it anyway — is to expose a version string. In my tool that is `APP_VERSION` on `/api/about`; anything
equivalent works:

```bash
curl -s https://<tool>.toolforge.org/api/about | python3 -c "import json,sys; print(json.load(sys.stdin)['app_version'])"
```

Suggested wording: *"`start` will not replace a running service — it answers `Your job is already
running` and keeps serving the old image. Stop first, start with the digest pinned, then verify with a
version endpoint (not with uptime). A `--mount=none` deploy also starts with an empty in-container
cache."*

### 4.3 The Build Service recipe, corrected

Replace lines 646–668 with something like:

````markdown
### Build Service Web App (Modern Workflow)

```bash
# 1. Create tool (web UI only: https://toolsadmin.wikimedia.org/tools/create)
# No CLI equivalent — `toolforge tools` was removed in CLI 0.3.x.

# 2. Push code to a public Git repository
#    Ensure a Procfile exists at the root:
#      web: python3 app.py           (or gunicorn --bind=0.0.0.0:$PORT app:app)

# 3. Build the container image from the Git repo.
#    This PRINTS the new digest — copy it for step 5.
ssh user@login.toolforge.org "sudo -niu tools.my-build-tool toolforge build start https://gitlab.wikimedia.org/toolforge-repos/my-build-tool"

# 4. Stop any running version. This is NOT optional: `start` is a no-op while a
#    webservice is already running, so without this you keep serving the old image.
ssh user@login.toolforge.org "sudo -niu tools.my-build-tool toolforge webservice buildservice stop"

# 5. Start, pinned to the digest from step 3. Note namespace/name — NOT the full
#    tools-harbor.wmcloud.org/... path that step 3 printed.
ssh user@login.toolforge.org "sudo -niu tools.my-build-tool toolforge webservice buildservice start --buildservice-image my-build-tool/my-build-tool@sha256:<digest> --mount=none -m 512Mi"

# 6. Verify it is RUNNING and is the NEW IMAGE — a version endpoint, not uptime.
curl -s https://my-build-tool.toolforge.org/api/health
ssh user@login.toolforge.org "sudo -niu tools.my-build-tool toolforge webservice buildservice status"
ssh user@login.toolforge.org "sudo -niu tools.my-build-tool kubectl logs deployment/my-build-tool --tail=20"
```
````

Two smaller notes worth adding near it: a `--mount=none` deploy has no persistent storage (any cache
under the container is wiped on restart, which is usually the right default for stateless tools); and
because restarting drops in-memory state, never restart a service that may be mid-job.

---

## 5. Why this matters more than a typo

Three reasons this is worth a PR rather than a shrug:

1. **It is the recipe, not a stray line.** People copy the Build Service block wholesale. Nine
   occurrences across four sections means the wrong shape is what a careful reader learns.
2. **The failure is quiet.** A wrong-user `webservice status` looks like a permissions quirk; a
   wrong-user `envvars create` or `crontab` may appear to work. The documented symptom in your own
   pitfalls table ("Permission denied", "tool account is not a member of…") is exactly the sort of
   thing that gets worked around instead of diagnosed.
3. **The knowledge is already there** — this is a consistency fix, not new research. The only *new*
   facts are the two in §4.

---

## 6. Reproduce my measurements

Run on `dev.toolforge.org` (or `login.toolforge.org`), 2026-10-14, substituting a tool you belong to.

**1. Separate the two shapes** — this is the test that corrected my own first pass:

```bash
# ✅ shape 1: the command is an argument to become -> runs as the tool
ssh alih@dev.toolforge.org "become wikiportraits-eval id"
#   uid=58240(tools.wikiportraits-eval) gid=58240(tools.wikiportraits-eval) …

# ❌ shape 2: semicolon ends become's arguments -> the command runs as YOU
ssh alih@dev.toolforge.org "become wikiportraits-eval; id"
#   uid=3522(alih) gid=500(wikidev) …            ← alih, not the tool
```

**2. Read the mechanism** (it is 40 lines and settles the question):

```bash
ssh alih@dev.toolforge.org "cat \$(command -v become) | tail -5"
#   exec /usr/bin/sudo -niu "$prefix.$tool" "$@"
```

**3. The two Build Service findings:**

```bash
ssh alih@dev.toolforge.org "sudo -niu tools.wikiportraits-eval toolforge build start https://github.com/fuzheado/wikiportraits-eval"
#   Built image tools-harbor.wmcloud.org/tool-wikiportraits-eval/tool-wikiportraits-eval:latest@sha256:<digest>

# with the printed path:
ssh alih@dev.toolforge.org "sudo -niu tools.wikiportraits-eval toolforge webservice buildservice start --buildservice-image tools-harbor.wmcloud.org/tool-wikiportraits-eval/tool-wikiportraits-eval@sha256:<digest> --mount=none -m 512Mi"
#   error: Expected build service image in 'namespace/name' format, got 'tools-harbor.wmcloud.org/…'

# with the running service untouched:
ssh alih@dev.toolforge.org "sudo -niu tools.wikiportraits-eval toolforge webservice buildservice start --buildservice-image tool-wikiportraits-eval/tool-wikiportraits-eval@sha256:<new-digest> --mount=none -m 512Mi"
#   Your job is already running          ← new image NOT deployed
```

---

## 7. What I did **not** verify (please treat these as claims, not measurements)

- **Interactive `become`** (SSH in, then `become <tool>`, then commands). The pitfall entry says this
  works; I had no reason to test it and did not. Only the chained-over-SSH case is measured here.
- **The "a plain `restart` serves the node's cached `:latest`" half.** I verified that `start` is a
  no-op while running. I did *not* reproduce the stale-`:latest` behaviour of `buildservice restart`;
  that is reported to me second-hand from a 2026-10-13 deploy, so treat it as a hypothesis the stop/start
  sequence makes moot.
- **`--buildservice-image` behaviour across CLI versions.** My evidence is one tool and whatever CLI
  version the bastion had on 2026-10-14. The *message* may change; the `namespace/name` requirement is
  what the CLI enforced then.
- **Every one of the nine sites at runtime.** I reasoned about each from the shell semantics confirmed
  in step 1 of §6, and reproduced the pattern at 657/660. I did not execute each recipe line by line,
  and lines that look like comments (666–667) matter less than 627–663.

If any of those turns out different on your side, the shape test in §6 is the arbiter — it is one
command and it does not depend on my reading of anything.

---

## 8. Suggested PR shape

- One commit: `Fix: use sudo -niu in Toolforge SSH recipes (become + ';' runs unbecome'd)`
  — the nine sites, plus the one-line canonical rule at the top of `toolforge-python` /
  `toolforge-nodejs`, plus a note in the pitfalls entry that `become TOOL CMD` (command as an argument)
  is fine so nobody "fixes" those six sites.
- One commit: `Add Build Service deploy gotchas: image spec, stop-before-start, verify the image`
  — §4.1, §4.2, the §4.3 recipe, and `--mount=none` / mid-job-restart notes.
- If the repo tracks `last_verified` in front-matter, bump `wikimedia-toolforge` to the date you
  re-verify.

Happy to open these as a PR (I have the account, `Fuzheado`), or to hand over patches — your call.
Everything above is reproducible from §6 without me.
