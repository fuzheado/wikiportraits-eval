#!/usr/bin/env python3
"""Route this process's `api.wikimedia.org` calls through the Toolforge bastion.

**Why this exists.** LiftWing classifies requests by the *source network*, not by anything
in the request (`research/wikitech-liftwing-limits-2026-10-14.txt`, "Rate Limits & Access"):

| client class    | limit                | who                                                        |
|-----------------|----------------------|------------------------------------------------------------|
| public/anonymous| 100 / hour           | anonymous, UA-only and authenticated — shared per client    |
| known network   | effectively unlimited| WMCS — Cloud VPS and Toolforge (CDN `x-trusted-request: A`) |

The deployed app runs in a Toolforge pod, so it is in the second row. A probe run from a
laptop is in the first. **Every latency and rate-limit number measured at home is therefore
a different regime than production**, which is exactly the confound this module removes.

**How.** `ssh -L` opens a plain TCP conduit: our local socket is forwarded, and the bastion
makes the real connection to `api.wikimedia.org:443`. TLS SNI and the `Host` header still
name `api.wikimedia.org`, so the bytes on the wire are identical to a direct call — only the
origin IP changes. The CDN's own `x-client-ip` response header then reports which IP it
classified, so the change is *verifiable* rather than assumed.

The upstream docs endorse the bastion for exactly this ("Accessing from Toolforge (higher
rate limit) … for a quick call you don't need to build anything"); they also note it is for
quick interactive use, and that a tool account + jobs framework is the right home for
anything sustained.

Usage
-----
    python3 run_via_toolforge.py probe_concurrency.py        # runs any probe through it
    python3 wmcs_tunnel.py --check                           # just prove the egress changed

    import wmcs_tunnel
    wmcs_tunnel.install()          # from here on, api.wikimedia.org goes via the bastion
    wmcs_tunnel.stop()

Env: `WMCS_BASTION` (default `alih@login.toolforge.org`), `WMCS_TUNNEL_PORT` (default: pick a free one).
"""
from __future__ import annotations

import atexit
import http.client
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BASTION = os.environ.get("WMCS_BASTION", "alih@login.toolforge.org")
API_HOST = "api.wikimedia.org"
API_PREFIX = f"https://{API_HOST}/"
DEFAULT_UA = os.environ.get(
    "WIKIMEDIA_USER_AGENT",
    "WikiPortraitsEval/dev (https://github.com/fuzheado/wikiportraits-eval)")

_proc = None
_port = None
_start_lock = threading.Lock()      # a concurrent probe must not race two tunnels into life
_real_urlopen = urllib.request.urlopen


# --------------------------------------------------------------- the tunnel
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.25)
    return False


def start(port: int | None = None) -> int:
    """Start the bastion tunnel and return the local port it listens on."""
    global _proc, _port
    with _start_lock:
        return _start_locked(port)


def _start_locked(port: int | None) -> int:
    global _proc, _port
    if _proc is not None and _proc.poll() is None:
        return _port                                            # already up
    port = port or int(os.environ.get("WMCS_TUNNEL_PORT") or _free_port())
    cmd = [
        "ssh", "-N",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=15",
        # NOTE: deliberately NO ControlMaster/ControlPath here. With a shared master the client
        # `ssh -N` process exits as soon as the forward is registered, so process-liveness
        # (`is_up()`) goes false while the tunnel still works — which silently sends a "tunnelled"
        # request direct. One dedicated process per tunnel is slower to start and honest about
        # whether it is up.
        "-L", f"{port}:{API_HOST}:443",
        BASTION,
    ]
    _proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    if not _wait_for_port(port):
        err = _proc.stderr.read() if _proc.stderr else ""
        stop()
        raise RuntimeError(f"tunnel to {BASTION} did not come up on 127.0.0.1:{port}. "
                           f"ssh said: {(err or '(nothing)').strip()[:300]}")
    _port = port
    atexit.register(stop)
    return port


def stop() -> None:
    global _proc, _port
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
    _port = None


def is_up() -> bool:
    """True only when a live ssh process is holding the forward.

    Deliberately process-based, and only sound because `start()` passes no ControlMaster options:
    with a shared master the client exits immediately and this would report False while the tunnel
    still worked, quietly routing "tunnelled" requests direct.
    """
    return _proc is not None and _proc.poll() is None and _port is not None


# --------------------------------------------------- the urllib interception
class _TunnelHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPSConnection whose TCP leg goes to the local forwarded port, not to the host.

    `host` stays `api.wikimedia.org` so SNI and the Host header are unchanged — only the
    socket's destination differs.
    """

    def __init__(self, host: str, local_port: int, timeout: float | None = None):
        super().__init__(host, 443, timeout=timeout)
        self._local_port = local_port

    def connect(self) -> None:                                  # noqa: D102
        self.sock = socket.create_connection(("127.0.0.1", self._local_port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _Response:
    """Just enough of the urlopen() return value for `wiclient` (`.status`, `.headers`, `.read()`)."""

    def __init__(self, raw, url: str):
        self._raw, self.url = raw, url

    @property
    def status(self) -> int:
        return self._raw.status

    @property
    def headers(self):
        return self._raw.headers

    def read(self, *a):
        return self._raw.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._raw.close()
        return False


def _via_tunnel(req, timeout=None):
    port = start()
    url = req.full_url if hasattr(req, "full_url") else str(req)
    path = url[len(f"https://{API_HOST}"):] or "/"
    headers = dict(req.headers) if hasattr(req, "headers") else {}
    headers.setdefault("Host", API_HOST)
    conn = _TunnelHTTPSConnection(API_HOST, port, timeout=timeout or 180)
    try:
        conn.request(getattr(req, "get_method", lambda: "GET")(), path,
                     body=getattr(req, "data", None), headers=headers)
        raw = conn.getresponse()
    except Exception:
        conn.close()
        raise
    if raw.status >= 400:
        # wiclient catches urllib.error.HTTPError and reads .code/.headers/.read()
        raise urllib.error.HTTPError(url, raw.status, raw.reason, raw.headers, raw)
    return _Response(raw, url)


def install(port: int | None = None) -> int:
    """Start the tunnel and send every `api.wikimedia.org` urllib call through it.

    Only that host is redirected — Commons, WDQS and everything else still go direct, so a
    probe keeps its real Commons behaviour and changes only the LiftWing origin.
    """
    start(port)

    def _urlopen(req, timeout=None, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.startswith(API_PREFIX) or url.startswith(f"https://{API_HOST}:"):
            return _via_tunnel(req, timeout)
        return _real_urlopen(req, timeout=timeout, **kw)

    urllib.request.urlopen = _urlopen
    return _port


def client_ip(timeout: float = 60) -> dict:
    """Make one tiny text-only call and report what the CDN thought our source IP was."""
    body = (b'{"model": "llm-clef-flash", "state": "", "questions": '
            b'{"q": {"type": "noul", "text": "Is this a photograph of a person?"}}}')
    req = urllib.request.Request(f"{API_PREFIX}service/lw/inference/v1/models/"
                                 "llm-clef-flash:predict", data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": DEFAULT_UA})
    t = time.time()
    res = urllib.request.urlopen(req, timeout=timeout)
    return {"x_client_ip": res.headers.get("x-client-ip"), "status": res.status,
            "wall_s": round(time.time() - t, 3), "via_tunnel": is_up(),
            "x_cache": res.headers.get("x-cache"), "server": res.headers.get("server")}


if __name__ == "__main__":
    if "--check" in sys.argv:
        direct = client_ip()
        print(f"direct        : x-client-ip={direct['x_client_ip']}  {direct['wall_s']}s")
        install()
        tunnelled = client_ip()
        print(f"via {BASTION:24s}: x-client-ip={tunnelled['x_client_ip']}  {tunnelled['wall_s']}s")
        same = direct["x_client_ip"] == tunnelled["x_client_ip"]
        print("\n" + ("!! egress IP did NOT change — the tunnel is not doing what you think"
                      if same else f"OK: egress changed, tunnel port {_port}"))
        sys.exit(1 if same else 0)
    print(__doc__)
