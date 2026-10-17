#!/usr/bin/env python3
"""The single inventory of what runs where — and a guard against forgetting a suite.

Three hand-maintained lists used to decide this (CI, `.githooks/pre-commit`, `run_all_tests.sh`) and
they had **already drifted** by 2026-10-16:

  * `test_pdf_images.py` ran nowhere at all — a suite that could not fail;
  * `check_pdf.py` ran only in `run_all_tests.sh`, and it had **no assertions** — it printed
    `present=True/False` and exited 0, so `reports.py` could say "six typed questions" in every
    downloaded PDF with nothing objecting;
  * CI's job called "offline suites" made a real Commons API call (`test_wsgi.py` -> `/api/parse` ->
    `wiclient.resolve`), so a Wikimedia outage or 429 could redden an unrelated commit — the exact
    thing the workflow's own comment says must never happen.

So the inventory lives here, once, in three classes. Every `tests/{check,test}_*.py|js` must be in
exactly one of them or this exits non-zero: adding a suite means *choosing* where it runs, not
forgetting. A suite that has been renamed away is also a failure, so the lists cannot rot silently.

    RUN    offline, stdlib-only, always runs
    NEEDS  offline, but needs a dev dependency; skipped LOUDLY when that is missing, so a missing
           dependency cannot quietly un-cover a suite
    JS     needs node (and jsdom for the DOM test)
    LIVE   needs a running server or the network — never in CI, because a Wikimedia outage must not
           turn an unrelated commit red

    python3 tests/suites.py            # run everything runnable, then check the inventory
    python3 tests/suites.py --list     # just print the classification
    python3 tests/suites.py --only-js | --without-js    # CI lanes; the inventory check still runs
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
PY = sys.executable

# --------------------------------------------------------------------------- the classification
# Order matters in one place: check_pdf.py reads the PDF that test_reports.py writes.
RUN = [
    "test_forms.py",
    "test_commas.py",
    "test_wsgi.py",
    "test_body_limits.py",      # the http.server layer: version header, body cap, read timeout
    "test_pipeline.py",
    "test_reports.py",          # writes /tmp/wpc_report_test/report.pdf, which check_pdf.py reads
    "test_thumb_width.py",
    "test_default_list.py",
    "test_privacy.py",
    "test_local.py",           # uploads: in memory only, never on disk (asserted by contrast)
    "check_compat.py",
    "check_docs.py",
    "check_js.py",              # python, but shells out to `node --check`
    "check_memo_refs.py",
]
NEEDS = {                       # name -> (every module it imports, why the dependency is dev-only)
    # Every module the suite actually imports, not a subset: probing only `pypdf, pypdfium2` selected
    # check_pdf.py on a machine without pillow, where it then died at `.to_pil()` — a suite that is
    # *skipped* is honest, a suite that is selected and then crashes is a false red on someone's PR.
    "check_pdf.py": (("pypdf", "pypdfium2", "PIL"),
                     "test-only deps; the app itself is stdlib-only by design"),
    "test_pdf_images.py": (("pypdfium2", "PIL"),
                           "test-only deps; renders the PDF and classifies its images"),
}
JS = {
    "test_render_dom.js": (["node", "tests/test_render_dom.js"], "needs node + jsdom"),
}
LIVE = {
    "test_ui_contract.py": "needs a running server (run_all_tests.sh points it at one)",
}


def _dep_python(modules):
    """An interpreter that can import `modules`, or None.

    CI installs the deps into its own python, so `sys.executable` is usually it. Locally they live in
    `.venv-pdf` (see HANDOFF §2) — accepting both is what lets the same inventory serve both.
    """
    for exe in (sys.executable, os.path.join(ROOT, ".venv-pdf", "bin", "python")):
        if exe != sys.executable and not os.path.exists(exe):
            continue
        probe = "import " + ", ".join(modules)
        if subprocess.run([exe, "-c", probe], capture_output=True).returncode == 0:
            return exe
    return None


def classify():
    """(runnable, skipped) — runnable is [(label, argv)], skipped is [(name, reason)]."""
    runnable = [(n, [PY, os.path.join("tests", n)]) for n in RUN]
    skipped = []
    for n in RUN:
        if n == "check_js.py" and not _which("node"):
            runnable = [r for r in runnable if r[0] != n]
            skipped.append((n, "no node on PATH (check_js.py extracts the page script for `node --check`)"))
    for n, (mods, why) in NEEDS.items():
        exe = _dep_python(mods)
        if exe:
            runnable.append((n, [exe, os.path.join("tests", n)]))
        else:
            skipped.append((n, f"needs {' + '.join(mods)} ({why}) — install it to cover this"))
    for n, (argv, why) in JS.items():
        # Ask node from tests/ whether jsdom resolves, rather than looking for a node_modules
        # directory: the directory check once silently skipped a test that was passing (the note is
        # inherited from the pre-commit hook, where that bug actually happened).
        if not _which("node"):
            skipped.append((n, why))
        elif not _jsdom_ok():
            skipped.append((n, f"{why} — run: cd tests && npm install"))
        else:
            runnable.append((n, argv))
    for n, why in LIVE.items():
        skipped.append((n, why))
    return runnable, skipped


def _which(cmd):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, cmd)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _jsdom_ok():
    return subprocess.run(["node", "-e", "require.resolve('jsdom')"], cwd=TESTS,
                          capture_output=True).returncode == 0


def inventory_problems():
    """Suites on disk that nothing classifies, or classified suites that are gone."""
    on_disk = sorted(f for f in os.listdir(TESTS)
                     if re.match(r"^(check|test)_.*\.(py|js)$", f))
    known = set(RUN) | set(NEEDS) | set(JS) | set(LIVE)
    problems = []
    for f in on_disk:
        if f not in known:
            problems.append(f"{f} is in tests/ but in no class here — it would never run on a PR. "
                            f"Put it in RUN, NEEDS, JS or LIVE.")
    for f in sorted(known):
        if f not in on_disk:
            problems.append(f"{f} is classified here but does not exist — renamed or deleted?")
    return problems


def main(argv):
    want_list = "--list" in argv
    # CI splits the inventory across jobs (the DOM test needs jsdom, the PDF suites need test-only
    # deps) without splitting the *inventory*: both jobs read this one file, and the orphan check at
    # the end runs either way.
    only_js = "--only-js" in argv
    without_js = "--without-js" in argv
    runnable, skipped = classify()
    if only_js:
        runnable = [r for r in runnable if r[0] in JS]
    if without_js:
        runnable = [r for r in runnable if r[0] not in JS]
    if want_list:
        print("runs:")
        for n, argv_ in runnable:
            print(f"  {n:<24} {' '.join(argv_)}")
        print("skipped:")
        for n, why in skipped:
            print(f"  {n:<24} {why}")
        return 0

    failures = []
    for name, argv_ in runnable:
        r = subprocess.run(argv_, cwd=ROOT, capture_output=True, text=True)
        tail = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
        last = tail[-1][:90] if tail else ""
        print(f"  {'ok  ' if r.returncode == 0 else 'FAIL'} {name:<24} exit={r.returncode} | {last}")
        if r.returncode != 0:
            failures.append(name)
            for ln in (r.stdout + r.stderr).strip().splitlines()[-25:]:
                print(f"        {ln}")
    for name, why in skipped:
        print(f"  skip {name:<24} {why}")

    problems = inventory_problems()
    print()
    if problems:
        print("INVENTORY:")
        for p in problems:
            print(f"  - {p}")
    print(f"result: {len(runnable)} run, {len(skipped)} skipped, {len(failures)} failed"
          + (f", {len(problems)} inventory problem(s)" if problems else ""))
    if failures:
        print("failed: " + ", ".join(failures))
    return 1 if (failures or problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
