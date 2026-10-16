#!/usr/bin/env bash
# Everything the offline inventory runs, PLUS the one thing it cannot: the live contract test against
# a running server. The offline half is delegated to tests/suites.py — the same inventory CI and the
# pre-commit hook use, so there is no third list to drift out of step (there used to be, and it did).
#
# Usage:  bash tests/run_all_tests.sh                 # local server on :8765, or serve/wp-eval-url.txt
#         bash tests/run_all_tests.sh https://wikiportraits-eval.toolforge.org
#
# The live half spends nothing on the model: test_ui_contract.py resolves the starter list (one
# Commons call) and reads cached answers only.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
cd "$ROOT" || exit 1
PUB=${1:-$(cat serve/wp-eval-url.txt 2>/dev/null || echo http://127.0.0.1:8765)}

echo "--- offline inventory (tests/suites.py) ---"
python3 tests/suites.py
offline=$?

echo
echo "--- live contract test against $PUB ---"
out=$(python3 tests/test_ui_contract.py "$PUB" 2>&1); rc=$?
printf '%s\n' "$out" | grep -E 'RESULT|FAIL|Error' | tail -5
[ "$rc" = 0 ] && echo "  PASS" || { echo "  FAILED (exit $rc)"; printf '%s\n' "$out" | tail -25; }

echo
[ "$offline" = 0 ] && [ "$rc" = 0 ] && echo "ALL OK" || echo "FAILURES: offline=$offline live=$rc"
exit $(( offline != 0 || rc != 0 ))
