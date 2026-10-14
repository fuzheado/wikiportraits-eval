#!/usr/bin/env python3
"""Restart the app server: kill any process whose cmdline contains app.py, then start fresh.

Portable: uses /proc on Linux, pgrep elsewhere (macOS has no /proc).
"""
import os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root — app.py and
# server.log live there, not in tools/ (this file is in tools/)
killed = []
me = os.getpid()


def find_servers():
    if os.path.isdir("/proc"):
        out = []
        for pid in [p for p in os.listdir("/proc") if p.isdigit()]:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().decode(errors="replace").replace("\x00", " ").strip()
            except Exception:
                continue
            if "app.py" in cmd:
                out.append((pid, cmd))
        return out
    r = subprocess.run(["pgrep", "-f", "app.py"], capture_output=True, text=True)
    return [(p, "pgrep match") for p in r.stdout.split()]


for pid, cmd in find_servers():
    if int(pid) == me or "restart_app" in str(cmd):
        continue
    try:
        os.kill(int(pid), signal.SIGTERM)
        killed.append((pid, str(cmd)[:70]))
    except Exception:
        pass
print("killed:", killed)
time.sleep(1)
log = open(os.path.join(HERE, "server.log"), "ab")
proc = subprocess.Popen([sys.executable, "app.py"], cwd=HERE, stdout=log, stderr=log,
                        env=dict(os.environ, PORT="8765", QUIET="1"), start_new_session=True)
print("started pid", proc.pid)
time.sleep(2)
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=10) as r:
    print("health:", r.read().decode())
