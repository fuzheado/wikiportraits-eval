"""WSGI shim — for the traditional Toolforge backend (uWSGI), which wants an `app` callable.

Toolforge expects $HOME/www/python/src/app.py exposing `app`. Copy this repo's files into
$HOME/www/python/src/ and start the service as:
    webservice --backend=kubernetes python3.11 start
The Build Service path (Procfile `web: python3 app.py`) does not need this file.
"""
import core


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length else b""

    status, ctype, payload, extra = core.route(method, path, body, environ.get("QUERY_STRING", ""))
    headers = [("Content-Type", ctype), ("Content-Length", str(len(payload))),
               ("Cache-Control", "no-store")]
    headers += list((extra or {}).items())
    start_response(f"{status} {'OK' if status == 200 else 'ERROR'}", headers)
    return [payload]
