#!/usr/bin/env bash
# Run a repo script inside the DEPLOYED Toolforge pod — the true production origin.
#
# Why not just run it locally? Because LiftWing classifies the *source network* (see
# wmcs_tunnel.py), and because the deployed pod is the only place that has production's exact
# network path. Measured 2026-10-14, same second, same images, median per-call wall time:
#
#     home connection        173.79.30.139          0.519 s
#     via bastion tunnel     2a02:ec80:a000:1::1c5   0.817 s   <- tunnel overhead, not real
#     inside the pod         172.16.21.201           0.327 s   <- production
#
# The bastion tunnel fixes the *client class* but inflates latency (TLS is end-to-end, so the
# handshake pays the extra hop). This script is the latency-faithful one.
#
# The pod runs a Heroku buildpack Python that is not on the default PATH for a bare `sh -s`,
# hence the explicit binary path and LD_LIBRARY_PATH.
#
# Usage:
#     ./run_in_pod.sh probe_concurrency.py --n 8 --streams 4
#     ./run_in_pod.sh analyze_question_spread.py
#
# Env: BASTION, TOOL (tool account), DEPLOY (k8s deployment name).
set -euo pipefail

BASTION=${BASTION:-alih@dev.toolforge.org}
TOOL=${TOOL:-tools.wikiportraits-eval}
DEPLOY=${DEPLOY:-wikiportraits-eval}
POD_PY=/layers/heroku_python/python/bin/python3.12
POD_LIB=/layers/heroku_python/python/lib
POD_CWD=/workspace

if [ $# -lt 1 ] || [ ! -f "$1" ]; then
  echo "usage: $0 <script-in-repo.py> [args…]" >&2
  exit 2
fi
script=$1; shift
base=$(basename "$script")

d=${TMPDIR:-/tmp}
runner=$(mktemp "$d/pi-podrun-XXXXXX")
trap 'rm -f "$runner"' EXIT

{
  # the script travels as base64 in a single-quoted var: no quoting or newline hazards
  echo "B64='$(base64 < "$script" | tr -d '\n')'"
  echo "printf %s \"\$B64\" | base64 -d > /tmp/$base"
  echo "cd $POD_CWD"
  echo "export LD_LIBRARY_PATH=$POD_LIB"
  echo "export PYTHONPATH=$POD_CWD"
  printf '%s /tmp/%s' "$POD_PY" "$base"
  printf ' %q' "$@"
  echo
} > "$runner"

echo "[run_in_pod] $base $* — inside deployment/$DEPLOY on $(hostname 2>/dev/null || echo local)" >&2
ssh -o BatchMode=yes -o ConnectTimeout=20 "$BASTION" \
  "sudo -niu $TOOL kubectl exec -i deployment/$DEPLOY -- sh -s" < "$runner"
