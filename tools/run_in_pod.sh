#!/usr/bin/env bash
# Run a repo script inside the DEPLOYED Toolforge pod — the true production origin.
#
# Why not just run it locally? Because LiftWing classifies the *source network* (see
# wmcs_tunnel.py), and because the deployed pod is the only place that has production's exact
# network path. Measured 2026-10-14, same images, same hour, median per-call wall time:
#
#     home connection     173.79.30.139          0.467-0.610 s
#     via bastion tunnel  2a02:ec80:a000:1::1c5   0.537-0.599 s   <- overlaps direct: not a tax
#     INSIDE the pod      172.16.21.201           0.327 s         <- production, and the fastest
#
# The bastion tunnel changes the CLIENT CLASS at little or no latency cost (see wmcs_tunnel.py), but
# the pod is the latency-faithful origin: ~1.4-1.8x faster than the same probe from home. (An early
# run showed the tunnel ~0.3 s/call slower; that was a bug in our harness, not the network — see
# `research/concurrency-2026-10-14.md`.)
#
# Usage:
#     ./tools/run_in_pod.sh tools/probe_concurrency.py --n 8 --streams 4
#     ./tools/run_in_pod.sh tools/analyze_question_spread.py
#     ./tools/run_in_pod.sh --sh 'env | sort'        # no push, just run a shell command
#
# Env: BASTION, TOOL (tool account), DEPLOY (k8s deployment name), POD_CWD (container working dir),
#      POD_PY (override the interpreter — the discovery below is usually better).
set -euo pipefail

BASTION=${BASTION:-alih@dev.toolforge.org}
TOOL=${TOOL:-tools.wikiportraits-eval}
DEPLOY=${DEPLOY:-wikiportraits-eval}
POD_CWD=${POD_CWD:-/workspace}

if [ $# -lt 1 ]; then
  echo "usage: $0 <script-in-repo.py> [args…]   |   $0 --sh '<command>'" >&2
  echo "  (env: BASTION, TOOL, DEPLOY, POD_CWD, POD_PY — see the header of this file)" >&2
  exit 2
fi

d=${TMPDIR:-/tmp}
label="$1"          # captured before any shift, because `set -u` makes a shifted-away $1 fatal
trap 'rm -f "$runner"' EXIT
runner=$(mktemp "$d/pi-podrun-XXXXXX")

# `kubectl exec … -- sh -s` does NOT source the image env, so a bare shell has none of the image's
# PATH: a Python app can be PID 1 while `python3` is "not found". Ask the running process what it is.
# Emitted inline with a heredoc inside the block below — deliberately NOT captured via
# `read -r -d '' VAR <<'X'`, which on bash 3.2 (the macOS default) reads only the first line.
{
  if [ "$1" = "--sh" ]; then
    [ $# -ge 2 ] || { echo "usage: $0 --sh '<command>'" >&2; exit 2; }
    echo "cd $POD_CWD"
    echo "$2"
  else
    script=$1; shift
    [ -f "$script" ] || { echo "no such file: $script" >&2; exit 2; }
    # the script travels as base64 in a single-quoted var: no quoting or newline hazards across
    # ssh -> sudo -> kubectl exec
    echo "B64='$(base64 < "$script" | tr -d '\n')'"
    echo "printf %s \"\$B64\" | base64 -d > /tmp/$(basename "$script")"
    # emitted as its own line so POD_CWD is expanded HERE, not read as an unset var in the pod
    printf 'cd %q\n' "$POD_CWD"
    cat <<'REMOTE'
PODPY="${POD_PY:-}"
[ -n "$PODPY" ] || PODPY=$(readlink -f /proc/1/exe 2>/dev/null)
ok() { [ -n "${1:-}" ] && "$1" -c 'import sys' >/dev/null 2>&1; }
# Fall back only when the candidate is ABSENT (`[ -n ]`), never on `ok ... ||`: a buildpack python
# can exist and still fail to load (interpreter off PATH, libpython beside its parent dir), and
# `ok ||` would then discard the correct path in favour of an empty `command -v` result.
[ -n "$PODPY" ] || PODPY=$(command -v python3 2>/dev/null)
[ -n "$PODPY" ] || PODPY=$(command -v python 2>/dev/null)
if [ -n "$PODPY" ] && ! ok "$PODPY"; then
  LIB="$(dirname "$(dirname "$PODPY")")/lib"
  if [ -d "$LIB" ]; then
    LD_LIBRARY_PATH="$LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_LIBRARY_PATH
  fi
fi
if ! ok "$PODPY"; then
  echo "[run_in_pod] no working Python in the container (tried: ${PODPY:-none})" >&2
  echo "[run_in_pod] its PID 1 is: $(readlink -f /proc/1/exe 2>/dev/null || echo unknown)" >&2
  exit 3
fi
# Python puts the SCRIPT's directory on sys.path, not the cwd — so a script copied to /tmp cannot
# import the app's own modules unless we say where they are.
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
REMOTE
    printf 'exec "$PODPY" /tmp/%s' "$(basename "$script")"
    printf ' %q' "$@"
    echo
  fi
} > "$runner"

echo "[run_in_pod] $label — inside deployment/$DEPLOY via $BASTION" >&2
ssh -o BatchMode=yes -o ConnectTimeout=20 "$BASTION" \
  "sudo -niu $TOOL kubectl exec -i deployment/$DEPLOY -- sh -s" < "$runner"
