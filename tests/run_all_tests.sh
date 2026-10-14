#!/usr/bin/env bash
# Re-run every offline suite plus the live contract test over a running server.
#
# Lives in tests/, but everything is invoked from the REPO ROOT: the suites import the app's own
# modules (core, wiclient, …) and read repo-root files (default_list.txt, static/), so the working
# directory has to be the root — the same way CI and the pre-commit hook run them. Each suite also
# anchors sys.path at the root itself, so running one directly still works.
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
cd "$ROOT" || exit 1
PUB=$(cat serve/wp-eval-url.txt 2>/dev/null || echo http://127.0.0.1:8765)

run() {
  label="$1"; shift
  out=$("$@" 2>&1); rc=$?
  last=$(printf '%s\n' "$out" | grep -E 'FAILURES|RESULT|OK' | tail -1)
  printf '%-28s exit=%s | %s\n' "$label" "$rc" "$last"
}

run "test_forms.py"        python3 tests/test_forms.py
run "test_commas.py"       python3 tests/test_commas.py
run "test_wsgi.py"         python3 tests/test_wsgi.py
run "test_pipeline.py"     python3 tests/test_pipeline.py
run "test_reports.py"      python3 tests/test_reports.py
run "test_thumb_width.py"  python3 tests/test_thumb_width.py
run "test_default_list.py" python3 tests/test_default_list.py
run "test_privacy.py"     python3 tests/test_privacy.py
# check_pdf.py needs pypdf, which only the dev venv has (see HANDOFF §2). Skip loudly rather than
# reporting a failure that is not one — a runner whose output cannot be trusted gets ignored.
if [ -x .venv-pdf/bin/python ]; then
  run "check_pdf.py"       .venv-pdf/bin/python tests/check_pdf.py
else
  printf '%-28s %s\n' "check_pdf.py" "SKIPPED (no .venv-pdf — needs pip install pypdf)"
fi
run "check_js.py"          python3 tests/check_js.py
run "check_docs.py"        python3 tests/check_docs.py
run "check_compat.py"      python3 tests/check_compat.py
run "check_memo_refs.py"   python3 tests/check_memo_refs.py
run "test_render_dom.js"   node tests/test_render_dom.js
run "test_ui_contract.py"  python3 tests/test_ui_contract.py "$PUB"
echo
echo "public URL: $PUB"
curl -sS -o /dev/null -w "GET /            -> HTTP %{http_code} (%{size_download} bytes, %{time_total}s)\n" "$PUB/"
curl -sS -o /dev/null -w "GET /api/about    -> HTTP %{http_code} (%{size_download} bytes)\n" "$PUB/api/about"
