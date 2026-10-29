#!/usr/bin/env python3
"""Minimal GAMA-L 2 language server (stdio JSON-RPC).

Supports initialize, didOpen/didChange/didSave diagnostics, shutdown and exit.
No completion/hover yet.
"""
from __future__ import annotations
import json
import sys
import urllib.parse
from pathlib import Path

import gamal2 as G


def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        k, _, v = line.decode("ascii", "replace").partition(":")
        headers[k.strip().lower()] = v.strip()
    try:
        n = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    body = sys.stdin.buffer.read(n)
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def send(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def uri_path(uri: str) -> str:
    try:
        p = urllib.parse.urlparse(uri)
        return urllib.parse.unquote(p.path)
    except Exception:
        return uri


def diagnostics(uri: str, text: str):
    try:
        G.check(text)
        return []
    except G.GamaError as e:
        line = max(0, int(e.line or 1) - 1)
        col = max(0, int(e.col or 1) - 1)
        return [{
            "range": {"start": {"line": line, "character": col},
                      "end": {"line": line, "character": col + 1}},
            "severity": 1,
            "source": "gama-l",
            "message": e.msg,
        }]
    except Exception as e:
        return [{"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": 0, "character": 1}},
                 "severity": 1, "source": "gama-l", "message": str(e)[:300]}]


def publish(uri: str, text: str):
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "diagnostics": diagnostics(uri, text)}})


def main():
    docs: dict[str, str] = {}
    while True:
        msg = read_msg()
        if msg is None:
            break
        method = msg.get("method") or ""
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "capabilities": {"textDocumentSync": 1},
                "serverInfo": {"name": "gama-lsp", "version": "2.0-core"},
            }})
        elif method == "initialized":
            pass
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": mid, "result": None})
        elif method == "exit":
            break
        elif method in ("textDocument/didOpen", "textDocument/didChange", "textDocument/didSave"):
            td = params.get("textDocument") or {}
            uri = td.get("uri") or ""
            text = td.get("text")
            if text is None:
                text = docs.get(uri, "")
            docs[uri] = text
            if uri:
                publish(uri, text)
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": None})


if __name__ == "__main__":
    main()
