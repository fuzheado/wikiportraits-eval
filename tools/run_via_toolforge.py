#!/usr/bin/env python3
"""Run any repo probe script with its LiftWing calls egressing from the Toolforge bastion.

    python3 run_via_toolforge.py probe_concurrency.py --serial 3 --concurrent 2

Nothing about the target script changes: `wmcs_tunnel.install()` redirects only
`api.wikimedia.org` (see that module for why the origin network matters). Commons, WDQS and
every other host still go direct, so a probe keeps its real behaviour and changes only which
network LiftWing sees.

The child runs in this process (runpy), so it inherits the interception and its own argv.
"""
import os
import runpy
import sys

import wmcs_tunnel

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)

target, rest = sys.argv[1], sys.argv[2:]
if not os.path.exists(target):
    sys.exit(f"no such script: {target}")

port = wmcs_tunnel.install()
print(f"[via-toolforge] {target} — LiftWing through {wmcs_tunnel.BASTION} "
      f"(local port {port}); everything else direct\n", file=sys.stderr)

sys.argv = [target] + rest
try:
    runpy.run_path(target, run_name="__main__")
finally:
    wmcs_tunnel.stop()
