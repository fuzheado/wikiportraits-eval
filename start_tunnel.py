#!/usr/bin/env python3
"""Start (or restart) the cloudflared quick tunnel for the app, logging to a file we can grep.

Logs and the URL file live in ./serve/ under the repo (created if missing). Portability: the
process scan uses /proc on Linux and pgrep elsewhere (macOS has no /proc).
"""
import os, shutil, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVE = os.path.join(HERE, "serve")
os.makedirs(SERVE, exist_ok=True)
PORT = os.environ.get("APP_PORT", "8765")
LOG = os.path.join(SERVE, "wp-eval.log")
URLFILE = os.path.join(SERVE, "wp-eval-url.txt")
CLOUDFLARED = shutil.which("cloudflared") or "/opt/data/bin/cloudflared"
me = os.getpid()


def find_tunnels():
    if os.path.isdir("/proc"):
        out = []
        for pid in [p for p in os.listdir("/proc") if p.isdigit()]:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().decode(errors="replace").replace("\x00", " ").strip()
            except Exception:
                continue
            if "cloudflared" in cmd and f":{PORT}" in cmd:
                out.append((pid, cmd))
        return out
    r = subprocess.run(["pgrep", "-f", f"cloudflared.*:{PORT}"], capture_output=True, text=True)
    return [(p, "pgrep match") for p in r.stdout.split()]


for pid, cmd in find_tunnels():
    if int(pid) == me:
        continue
    try:
        os.kill(int(pid), signal.SIGTERM)
        print("stopped old tunnel pid", pid, cmd[:80])
    except Exception:
        pass
time.sleep(1)
logfh = open(LOG, "ab")
proc = subprocess.Popen([CLOUDFLARED, "tunnel", "--url", f"http://127.0.0.1:{PORT}",
                         "--no-autoupdate", "--logfile", LOG],
                        stdout=logfh, stderr=logfh, start_new_session=True)
print("tunnel pid", proc.pid, "logging to", LOG)
url = None
for _ in range(40):
    time.sleep(1)
    try:
        with open(LOG, "r", errors="replace") as fh:
            for line in fh:
                if "trycloudflare.com" in line and "https://" in line:
                    part = line.split("https://")[1].split()[0].strip('"')
                    if "trycloudflare.com" in part:
                        url = "https://" + part
    except FileNotFoundError:
        pass
    if url:
        break
print("URL:", url)
if url:
    open(URLFILE, "w").write(url + "\n")
