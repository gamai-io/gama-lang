#!/usr/bin/env python3
"""Minimal GAMA-L 2 language server (stdio JSON-RPC).

Supports initialize, didOpen/didChange/didSave diagnostics, completion, hover,
definition, references, shutdown and exit.
"""
from __future__ import annotations
import dataclasses
import json
import re
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


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def utf16_to_index(line: str, col: int) -> int:
    """Convert an LSP UTF-16 column to a Python character index."""
    units = 0; i = 0
    while i < len(line) and units < col:
        units += 2 if ord(line[i]) > 0xFFFF else 1
        i += 1
    return i


def diagnostics(uri: str, text: str):
    try:
        G.check(text)
        return []
    except G.GamaError as e:
        line = max(0, int(e.line or 1) - 1)
        rows = (text or "").split("\n")
        prefix = rows[line][:max(0, int(e.col or 1) - 1)] if line < len(rows) else ""
        col = utf16_len(prefix)
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



# ---------------------------------------------------------------- features
KIND_KEYWORD = 14
KIND_FUNCTION = 3
KIND_VARIABLE = 6

NAME_RE = re.compile(r"\b(let|fn)\s+([A-Za-z_]\w*)")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# short human descriptions for builtins and keyword-like builtins
DESCRIPTIONS = {
    "sql": "`sql`: Runs a read-only SQL query and returns a Table.",
    "ask": "`ask`: Asks the configured LLM endpoint and returns Text.",
    "wait_user": "`wait_user`: Human-in-the-loop gate; returns Bool (Denied when non-interactive).",
    "prove": "`prove`: Creates a proven value with a sha256 hash chain (`prove name = expr`).",
    "verify": "`verify`: Verifies a proven value's hash chain; result in `_verify`.",
    "print": "`print`: Prints a value to output.",
    "sum": "`sum`: Sums the values of a column in a pipe.",
    "count": "`count`: Counts rows in a pipe.",
    "filter": "`filter`: Filters rows in a pipe by a condition.",
    "select": "`select`: Projects columns in a pipe.",
    "limit": "`limit`: Limits the number of rows in a pipe.",
    "map": "`map`: Maps values in a pipe.",
    "len": "`len`: Returns the length of a list or text as Int.",
    "text": "`text`: Converts a value to Text.",
    "number": "`number`: Converts a value to Decimal.",
    "remember": "`remember`: Writes a key/value pair to memory.",
    "recall": "`recall`: Reads a value from memory.",
    "sql_write": "`sql_write`: Performs a SQL write; returns Bool.",
    "mail": "`mail`: Sends mail through the mail effect; returns Bool.",
}


def doc_names(text: str):
    """Return {name: kind} for let/fn declarations in the document."""
    out = {}
    for m in NAME_RE.finditer(text or ""):
        out[m.group(2)] = m.group(1)
    return out


def completion(uri: str, text: str):
    items = []
    seen = set()
    for kw in sorted(G.KEYWORDS):
        if kw in seen:
            continue
        seen.add(kw)
        items.append({"label": kw, "kind": KIND_KEYWORD})
    for name in sorted(G.BUILTINS):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_FUNCTION})
    for name in sorted(doc_names(text)):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_VARIABLE})
    return {"isIncomplete": False, "items": items}


def word_at(text: str, line: int, character: int):
    lines = (text or "").split("\n")
    if line < 0 or line >= len(lines):
        return None
    row = lines[line]
    pos = utf16_to_index(row, max(0, character))
    for m in WORD_RE.finditer(row):
        if m.start() <= pos <= m.end():
            return m.group(0)
    return None


def _walk(node):
    """Yield dataclass nodes recursively over all fields (list/dict/tuple aware)."""
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        yield node
        for f in dataclasses.fields(node):
            yield from _walk(getattr(node, f.name, None))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _walk(item)


def _utf16_pos(line: str, index: int) -> int:
    return utf16_len(line[:max(0, index)])


def _name_pos(line: str, start_index: int, name: str):
    """Character index of a whole-word `name` at/after start_index, or None."""
    idx = line.find(name, max(0, start_index))
    while idx != -1:
        before = line[idx - 1] if idx > 0 else ""
        after = line[idx + len(name)] if idx + len(name) < len(line) else ""
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            return idx
        idx = line.find(name, idx + 1)
    return None


def _declarations(text: str):
    """Return Let/Fn declaration nodes, or [] when parsing fails."""
    try:
        program = G.parse(text)
    except Exception:
        return []
    return [n for n in _walk(program) if isinstance(n, (G.Let, G.Fn)) and n.name]


def _decl_start(uri: str, text: str, node, name: str):
    """Location of a declaration's name (start/end), or None."""
    lines = (text or "").split("\n")
    line0 = max(0, int(node.line) - 1)
    if line0 >= len(lines):
        return None
    idx = _name_pos(lines[line0], max(0, int(node.col) - 1), name)
    if idx is None:
        return None
    col = _utf16_pos(lines[line0], idx)
    return {"uri": uri, "range": {
        "start": {"line": line0, "character": col},
        "end": {"line": line0, "character": col + utf16_len(name)},
    }}


def definition(uri: str, text: str, line: int, character: int):
    name = word_at(text, line, character)
    if not name:
        return None
    for node in _declarations(text):
        if node.name == name:
            loc = _decl_start(uri, text, node, name)
            if loc is not None:
                return loc
    return None


def references(uri: str, text: str, line: int, character: int):
    name = word_at(text, line, character)
    if not name:
        return []
    try:
        program = G.parse(text)
    except Exception:
        return []
    lines = (text or "").split("\n")
    out = []
    seen = set()

    def add(line0: int, col: int):
        if line0 < 0 or line0 >= len(lines):
            return
        key = (line0, col)
        if key in seen:
            return
        seen.add(key)
        out.append({"uri": uri, "range": {
            "start": {"line": line0, "character": col},
            "end": {"line": line0, "character": col + 1},
        }})

    for node in _walk(program):
        if isinstance(node, (G.Var, G.Call)) and getattr(node, "name", None) == name:
            line0 = max(0, int(node.line) - 1)
            if line0 < len(lines):
                add(line0, _utf16_pos(lines[line0], int(node.col) - 1))
        elif isinstance(node, (G.Let, G.Fn)) and getattr(node, "name", None) == name:
            line0 = max(0, int(node.line) - 1)
            if line0 < len(lines):
                idx = _name_pos(lines[line0], max(0, int(node.col) - 1), name)
                if idx is not None:
                    add(line0, _utf16_pos(lines[line0], idx))
    return out


def hover(uri: str, text: str, line: int, character: int):
    name = word_at(text, line, character)
    if not name:
        return None
    value = None
    if name in DESCRIPTIONS:
        value = DESCRIPTIONS[name]
    elif name in doc_names(text):
        value = "GAMA-L %s `%s`" % (doc_names(text)[name], name)
    elif name in G.KEYWORDS:
        value = "GAMA-L keyword `%s`" % name
    if value is None:
        return None
    return {"contents": {"kind": "markdown", "value": value}}


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
                "capabilities": {
                    "textDocumentSync": 1,
                    "completionProvider": {"triggerCharacters": [" ", "|", "."]},
                    "hoverProvider": True,
                    "definitionProvider": True,
                    "referencesProvider": True,
                },
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
            changes = params.get("contentChanges") or []
            text = changes[-1].get("text") if changes else td.get("text")
            if text is None:
                text = docs.get(uri, "")
            docs[uri] = text
            if uri:
                publish(uri, text)
        elif method == "textDocument/completion":
            td = params.get("textDocument") or {}
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            send({"jsonrpc": "2.0", "id": mid, "result": completion(uri, text)})
        elif method == "textDocument/hover":
            td = params.get("textDocument") or {}
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = params.get("position") or {}
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": hover(uri, text, line, ch)})
        elif method == "textDocument/definition":
            td = params.get("textDocument") or {}
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = params.get("position") or {}
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": definition(uri, text, line, ch)})
        elif method == "textDocument/references":
            td = params.get("textDocument") or {}
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = params.get("position") or {}
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": references(uri, text, line, ch)})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": None})


if __name__ == "__main__":
    main()
