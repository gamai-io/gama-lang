"""Hosted-style HTTP registry server for GAMA-L.

Serves a validated registry JSON document over plain HTTP so that
``gama_registry.load_registry`` can fetch it from an ``http://`` / ``https://``
URL.  The server is read-only: only ``GET`` is allowed, path traversal is
rejected, and there is no directory listing, authentication, or outbound
network access.

Routes:

    GET /health            -> {"ok": true}
    GET /index.json        -> the registry JSON document
    GET /packages/<name>   -> {"name", "version", "git", "rev"} or 404
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gama_registry

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 0
REGISTRY_ENV = "GAMA_L_REGISTRY_FILE"
SERVER_NAME = "GamaLRegistry/1.0"
_PACKAGE_PREFIX = "/packages/"


def make_handler(registry_path: pathlib.Path):
    """Build a request handler class bound to ``registry_path``."""
    registry_path = pathlib.Path(registry_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = SERVER_NAME

        def log_message(self, fmt, *args):  # keep stdout/stderr quiet
            pass

        # ------------------------------------------------------------------ io
        def _send(self, code: int, payload: object, allow: str | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if allow:
                self.send_header("Allow", allow)
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._send(404, {"error": "not found"})

        def _load(self):
            try:
                return gama_registry.load_registry(str(registry_path))
            except gama_registry.RegistryError as ex:
                self._send(500, {"error": str(ex)})
                return None

        def _request_path(self):
            """Decoded path, or ``None`` when it is not safe to resolve."""
            path = urllib.parse.unquote(
                urllib.parse.urlsplit(self.path).path or "/"
            )
            if "\\" in path:
                return None
            if any(part in ("..", ".") for part in path.split("/")):
                return None
            return path

        # -------------------------------------------------------------- routes
        def do_GET(self):
            path = self._request_path()
            if path is None:
                return self._not_found()

            if path == "/health":
                return self._send(200, {"ok": True})

            if path == "/index.json":
                index = self._load()
                if index is None:
                    return None
                return self._send(200, index)

            if path.startswith(_PACKAGE_PREFIX):
                name = path[len(_PACKAGE_PREFIX):]
                if not name or not gama_registry._safe_name(name):
                    return self._not_found()
                index = self._load()
                if index is None:
                    return None
                entry = index["packages"].get(name)
                if not isinstance(entry, dict):
                    return self._not_found()
                return self._send(200, gama_registry._record(name, entry))

            return self._not_found()

        def _method_not_allowed(self):
            self._send(
                405,
                {"error": "method not allowed", "allow": "GET"},
                allow="GET",
            )

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_HEAD = _method_not_allowed

    return Handler


def create_server(
    registry_path: pathlib.Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    """Create (but do not start) a registry HTTP server."""
    return ThreadingHTTPServer((host, int(port)), make_handler(registry_path))


def serve(httpd: ThreadingHTTPServer) -> None:
    """Run ``httpd`` until interrupted; SIGINT/SIGTERM shut it down cleanly."""

    def _stop(signum, frame):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, AttributeError, OSError):
        # Not running in the main thread; the caller controls the lifecycle.
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="gama_registry_server",
        description="Serve a GAMA-L registry JSON document over HTTP (read-only).",
    )
    parser.add_argument(
        "--registry",
        help="path to the registry JSON file (default: $%s)" % REGISTRY_ENV,
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    raw = args.registry or os.environ.get(REGISTRY_ENV)
    if not raw:
        print(
            "error: --registry or %s is required" % REGISTRY_ENV,
            file=sys.stderr,
        )
        return 2

    path = pathlib.Path(raw)
    try:
        gama_registry.load_registry(str(path))
    except gama_registry.RegistryError as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 2

    httpd = create_server(path, args.host, args.port)
    host, port = httpd.server_address[:2]
    print("GAMA-L registry on http://%s:%d/index.json" % (host, port), flush=True)
    try:
        serve(httpd)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
