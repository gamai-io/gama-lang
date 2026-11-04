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

# Framing limits: reject absurd frames instead of buffering them.
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024

# Sentinel returned only on a real end of input (not on malformed frames).
EOF = object()


class FramingError(Exception):
    """A malformed JSON-RPC frame; the loop reports it and keeps running."""


def read_msg():
    """Read one JSON-RPC frame.

    Returns the decoded message, or the ``EOF`` sentinel on real end of input.
    Raises ``FramingError`` for malformed headers/body; callers must report the
    error and continue reading instead of treating it as end of stream.
    """
    headers = {}
    header_bytes = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return EOF
        header_bytes += len(line)
        if header_bytes > MAX_HEADER_BYTES:
            raise FramingError("header section exceeds %d bytes" % MAX_HEADER_BYTES)
        if line in (b"\r\n", b"\n"):
            break
        k, sep, v = line.decode("ascii", "replace").partition(":")
        if not sep:
            # Not a header line: ignore garbage until the blank separator.
            continue
        headers[k.strip().lower()] = v.strip()
    raw_len = headers.get("content-length")
    if raw_len is None:
        raise FramingError("missing Content-Length header")
    try:
        n = int(raw_len)
    except (TypeError, ValueError):
        raise FramingError("invalid Content-Length: %r" % raw_len)
    if n < 0:
        raise FramingError("negative Content-Length: %d" % n)
    if n > MAX_BODY_BYTES:
        raise FramingError("body exceeds %d bytes" % MAX_BODY_BYTES)
    body = sys.stdin.buffer.read(n)
    if len(body) != n:
        raise FramingError("truncated body: expected %d bytes, got %d" % (n, len(body)))
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as ex:
        raise FramingError("invalid JSON body: %s" % ex)


def send(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def send_error(mid, code, message):
    """Send a JSON-RPC error response (id may be None for parse errors)."""
    send({"jsonrpc": "2.0", "id": mid,
          "error": {"code": code, "message": message}})


def _obj(value):
    """Return ``value`` if it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


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
        rows = (text or "").split("\n") or [""]
        line = max(0, int(e.line or 1) - 1)
        # Clamp to the last line: EOF/unterminated errors can point past it.
        line = min(line, len(rows) - 1)
        row = rows[line]
        char_index = min(max(0, int(e.col or 1) - 1), len(row))
        line_len = utf16_len(row)
        col = min(utf16_len(row[:char_index]), line_len)
        end_col = min(col + 1, line_len)
        return [{
            "range": {"start": {"line": line, "character": col},
                      "end": {"line": line, "character": end_col}},
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
KIND_TYPE = 25

NAME_RE = re.compile(r"\b(let|fn)\s+([A-Za-z_]\w*)")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IMPORT_RE = re.compile(
    r'(?m)^\s*import\s+"([^"]+)"(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?')
IMPORT_ALIAS_CTX_RE = re.compile(
    r'^\s*import\s+"([^"]*)"\s+as\s+([A-Za-z_][A-Za-z0-9_]*)?$')
MEMBER_CTX_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)?$")

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

# Call signatures for the current builtins: (parameter labels, return, effects).
# Effects mirror ``G.BUILTINS``; the labels are tooling-only (the core does not
# expose parameter names for builtins).
SIGNATURES = {
    "sql": (["query: Text"], "Table", {"sql.read"}),
    "ask": (["question: Text"], "Text", {"ask"}),
    "remember": (["key: Text", "value: Any"], "Bool", {"memory.write"}),
    "recall": (["key: Text"], "Any", {"memory.read"}),
    "wait_user": (["prompt: Text"], "Bool", {"hitl"}),
    "len": (["value: Any"], "Int", set()),
    "text": (["value: Any"], "Text", set()),
    "number": (["value: Any"], "Decimal", set()),
    "sql_write": (["query: Text"], "Bool", {"sql.write"}),
    "mail": (["to: Text", "subject: Text", "body: Text"], "Bool", {"mail"}),
    "round": (["value: Decimal", "digits: Int"], "Decimal", set()),
    "Ok": (["value: Any"], "Result", set()),
    "Err": (["error: Any"], "Result", set()),
    "unwrap": (["value: Result"], "Any", set()),
    "unwrap_or": (["value: Result", "default: Any"], "Any", set()),
    "is_ok": (["value: Result"], "Bool", set()),
    "is_err": (["value: Result"], "Bool", set()),
    "split": (["text: Text", "separator: Text"], "List", set()),
    "join": (["values: List", "separator: Text"], "Text", set()),
    "contains": (["haystack: Text", "needle: Any"], "Bool", set()),
    "trim": (["text: Text"], "Text", set()),
    "upper": (["text: Text"], "Text", set()),
    "lower": (["text: Text"], "Text", set()),
    "replace": (["text: Text", "old: Text", "new: Text"], "Text", set()),
    "starts_with": (["text: Text", "prefix: Text"], "Bool", set()),
    "ends_with": (["text: Text", "suffix: Text"], "Bool", set()),
    "keys": (["value: Json"], "List", set()),
    "values": (["value: Json"], "List", set()),
    "has": (["value: Json", "key: Text"], "Bool", set()),
}

TYPE_HOVERS = {
    "Function": "GAMA-L type `Function`: a callable value (lambda or named "
                "function used as a value); pass it to parameters typed "
                "`Function`.",
    "Result": "GAMA-L type `Result`: an `Ok(value)` or `Err(error)` value.",
    "Table": "GAMA-L type `Table`: a list of row maps returned by `sql`.",
    "Json": "GAMA-L type `Json`: a map value with text keys.",
    "List": "GAMA-L type `List`: an ordered sequence of values.",
    "Any": "GAMA-L type `Any`: the type checker does not constrain this value.",
    "Null": "GAMA-L type `Null`: the `null` literal.",
}


def _effects_text(effects) -> str:
    items = sorted(effects or ())
    return (" effects { " + " ".join(items) + " }") if items else ""


def signature_text(name: str, params, ret: str, effects) -> str:
    return "%s(%s) -> %s%s" % (
        name, ", ".join(params), ret or "Any", _effects_text(effects))


def builtin_signature(name: str):
    """(label, parameter labels) for a builtin, or None when unknown."""
    if name not in SIGNATURES:
        return None
    params, ret, effects = SIGNATURES[name]
    return signature_text(name, params, ret, effects), params


def _param_label(param) -> str:
    type_name = getattr(param, "type_name", None) or "Any"
    return "%s: %s" % (param.name, type_name)


def fn_signature(node) -> str:
    return signature_text(node.name, [_param_label(p) for p in node.params],
                          node.ret, node.effects)


def lambda_signature(node, prefix="fn") -> str:
    return signature_text(prefix, [_param_label(p) for p in node.params],
                          node.ret, node.effects)


def doc_names(text: str):
    """Return {name: kind} for let/fn declarations in the document."""
    out = {}
    for m in NAME_RE.finditer(text or ""):
        out[m.group(2)] = m.group(1)
    return out


def _line_prefix(text: str, line: int, character: int) -> str:
    rows = (text or "").split("\n")
    if line < 0 or line >= len(rows):
        return ""
    row = rows[line]
    return row[:utf16_to_index(row, max(0, character))]


def _line_text(text: str, line: int) -> str:
    rows = (text or "").split("\n")
    return rows[line] if 0 <= line < len(rows) else ""


def _imports(text: str):
    """Return import statements, parsing when possible and regex as fallback."""
    out = []
    seen = set()
    try:
        program = G.parse(text)
    except Exception:
        program = None
    if program is not None:
        for node in _walk(program):
            if isinstance(node, G.Import):
                key = (node.path, node.alias)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"path": node.path, "alias": node.alias,
                            "line": node.line})
    for m in IMPORT_RE.finditer(text or ""):
        path, alias = m.group(1), m.group(2)
        key = (path, alias)
        if key in seen:
            continue
        seen.add(key)
        line = (text or "")[:m.start()].count("\n")
        out.append({"path": path, "alias": alias, "line": line})
    return out


def _alias_map(text: str):
    out = {}
    for imp in _imports(text):
        if imp.get("alias"):
            out[imp["alias"]] = imp
    return out


def _doc_root(uri: str):
    p = uri_path(uri)
    if not p:
        return None
    try:
        return Path(p).resolve().parent
    except Exception:
        return None


def _resolve_module(uri: str, path: str):
    """Resolve an import relative to the document, confined to its root."""
    root = _doc_root(uri)
    if root is None or not path:
        return None
    try:
        candidates = [root / path,
                      root / ".gamal" / "modules" / path,
                      root / "gama_modules" / path]
    except Exception:
        return None
    for candidate in candidates:
        try:
            target = candidate.resolve()
            if target != root and root not in target.parents:
                continue
            if target.is_file():
                return target
        except (OSError, ValueError):
            continue
    return None


def _module_functions(uri: str, path: str):
    """Direct top-level ``fn`` declarations of an imported module, or None."""
    target = _resolve_module(uri, path)
    if target is None:
        return None
    try:
        source = target.read_text(encoding="utf-8")
        program = G.parse(source)
    except Exception:
        return None
    return [n for n in program if isinstance(n, G.Fn)]


def _namespaced_member(text: str, line: int, character: int):
    """Return ``(alias, member)`` when the cursor is on ``alias.member``."""
    row = _line_text(text, line)
    if not row:
        return None
    pos = utf16_to_index(row, max(0, character))
    for m in WORD_RE.finditer(row):
        if m.start() <= pos <= m.end():
            before = row[:m.start()]
            am = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*$", before)
            if am:
                return am.group(1), m.group(0)
            return None
    return None


def _alias_suggestion(path: str, taken) -> str | None:
    try:
        stem = Path(path.replace("\\", "/")).stem
    except Exception:
        return None
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", stem)
    if not candidate:
        return None
    if candidate[0].isdigit():
        candidate = "_" + candidate
    if not ALIAS_RE.match(candidate) or candidate in taken:
        return None
    return candidate


def completion(uri: str, text: str, line: int = 0, character: int = 0):
    prefix = _line_prefix(text, line, character)

    # Alias position: ``import "lib.gm" as <cursor>``. Only a name derived
    # from the module filename is inferable; arbitrary aliases are not.
    am = IMPORT_ALIAS_CTX_RE.match(prefix)
    if am:
        path, typed = am.group(1), am.group(2) or ""
        taken = set(doc_names(text)) | set(_alias_map(text))
        alias = _alias_suggestion(path, taken)
        items = []
        if alias and alias.startswith(typed):
            items.append({
                "label": alias,
                "kind": KIND_VARIABLE,
                "detail": "namespace alias for %s" % path,
                "documentation": "Import `%s` as `%s`; call members as "
                                 "`%s.fn(...)`." % (path, alias, alias),
            })
        return {"isIncomplete": False, "items": items}

    # Member position: ``alias.<cursor>``. Offer the module's own top-level
    # functions; re-exported imports and top-level lets are not exposed.
    mm = MEMBER_CTX_RE.search(prefix)
    if mm:
        alias = mm.group(1)
        imp = _alias_map(text).get(alias)
        if imp is None:
            return {"isIncomplete": False, "items": []}
        fns = _module_functions(uri, imp["path"])
        if fns is None:
            return {"isIncomplete": False, "items": []}
        items = []
        for node in sorted(fns, key=lambda n: n.name):
            items.append({
                "label": node.name,
                "kind": KIND_FUNCTION,
                "detail": fn_signature(node),
                "documentation": "From `%s` (imported as `%s`)."
                                 % (imp["path"], alias),
            })
        return {"isIncomplete": False, "items": items}

    items = []
    seen = set()
    # ``as`` is an import keyword lexed as an identifier by the core parser.
    for kw in sorted(set(G.KEYWORDS) | {"as"}):
        if kw in seen:
            continue
        seen.add(kw)
        items.append({"label": kw, "kind": KIND_KEYWORD})
    for name in sorted(TYPE_NAMES):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_TYPE})
    for name in sorted(G.BUILTINS):
        if name in seen:
            continue
        seen.add(name)
        item = {"label": name, "kind": KIND_FUNCTION}
        sig = builtin_signature(name)
        if sig is not None:
            item["detail"] = sig[0]
        if name in DESCRIPTIONS:
            item["documentation"] = DESCRIPTIONS[name]
        items.append(item)
    for name, kind in sorted(doc_names(text).items()):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_VARIABLE})
    # Import aliases are callable through ``alias.fn`` and therefore useful
    # completions in expression position.
    for alias, imp in sorted(_alias_map(text).items()):
        if alias in seen:
            continue
        seen.add(alias)
        items.append({"label": alias, "kind": KIND_VARIABLE,
                      "detail": "namespace alias for %s" % imp["path"]})
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


def _range_seg(rng):
    """Return (start, end) as (line, character) tuples for an LSP range."""
    start = (rng or {}).get("start") or {}
    end = (rng or {}).get("end") or {}
    return ((int(start.get("line") or 0), int(start.get("character") or 0)),
            (int(end.get("line") or 0), int(end.get("character") or 0)))


def _overlaps(diag_range, req_range) -> bool:
    """True when a diagnostic range intersects the requested range."""
    ds, de = _range_seg(diag_range)
    rs, re_ = _range_seg(req_range)
    if rs == re_:
        # collapsed request (cursor position): same-line diagnostics count
        return ds[0] <= rs[0] <= de[0]
    # half-open intersection
    return not (de <= rs or re_ <= ds)


def code_actions(uri: str, text: str, req_range, context):
    """Return quickfix code actions that comment out offending lines."""
    ctx = context or {}
    diags = ctx.get("diagnostics")
    if diags is None:
        diags = [d for d in diagnostics(uri, text)
                 if _overlaps(d.get("range") or {}, req_range)]
    else:
        diags = [d for d in diags
                 if _overlaps(d.get("range") or {}, req_range)]
    lines = (text or "").split("\n")
    actions = []
    seen = set()
    for d in diags:
        start = (d.get("range") or {}).get("start") or {}
        line = int(start.get("line") or 0)
        if line in seen:
            continue
        if line < 0 or line >= len(lines):
            continue
        original = lines[line]
        seen.add(line)
        if original.lstrip().startswith("#"):
            continue
        actions.append({
            "title": "Comment out line %d" % (line + 1),
            "kind": "quickfix",
            "diagnostics": [d],
            "edit": {"changes": {uri: [{
                "range": {"start": {"line": line, "character": 0},
                          "end": {"line": line,
                                  "character": utf16_len(original)}},
                "newText": "# " + original,
            }]}},
        })
    return actions


RENAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def formatting(uri: str, text: str):
    """Full-document formatting: one TextEdit when the source changes."""
    src = text or ""
    try:
        formatted = G.fmt_source(src)
    except Exception:
        return []
    if formatted == src:
        return []
    lines = src.split("\n")
    last = len(lines) - 1
    return [{
        "range": {"start": {"line": 0, "character": 0},
                  "end": {"line": last, "character": utf16_len(lines[last])}},
        "newText": formatted,
    }]


def rename(uri: str, text: str, line: int, character: int, new_name: str):
    """Rename a let/fn declaration and all Var/Call references to it."""
    if not RENAME_RE.match(new_name or ""):
        return None
    name = word_at(text, line, character)
    if not name:
        return None
    try:
        program = G.parse(text)
    except Exception:
        return None
    lines = (text or "").split("\n")
    width = utf16_len(name)
    edits = []
    seen = set()

    def add(line0: int, col: int) -> bool:
        if line0 < 0 or line0 >= len(lines):
            return False
        key = (line0, col)
        if key in seen:
            return True
        seen.add(key)
        edits.append({"range": {"start": {"line": line0, "character": col},
                                "end": {"line": line0, "character": col + width}},
                      "newText": new_name})
        return True

    found = False
    for node in _walk(program):
        if isinstance(node, (G.Var, G.Call)) and getattr(node, "name", None) == name:
            line0 = max(0, int(node.line) - 1)
            col = _utf16_pos(lines[line0], int(node.col) - 1) if line0 < len(lines) else 0
            if add(line0, col):
                found = True
        elif isinstance(node, (G.Let, G.Fn)) and getattr(node, "name", None) == name:
            line0 = max(0, int(node.line) - 1)
            if line0 < len(lines):
                idx = _name_pos(lines[line0], max(0, int(node.col) - 1), name)
                if idx is not None and add(line0, _utf16_pos(lines[line0], idx)):
                    found = True
    if not found or not edits:
        return None
    return {"changes": {uri: edits}}


def _decl_by_name(text: str, name: str):
    for node in _declarations(text):
        if getattr(node, "name", None) == name:
            return node
    return None


def _param_by_name(text: str, name: str):
    for node in _declarations(text):
        if isinstance(node, G.Fn):
            for param in node.params:
                if param.name == name:
                    return param
    return None


def _decl_hover(text: str, name: str):
    node = _decl_by_name(text, name)
    if node is None:
        return None
    if isinstance(node, G.Fn):
        return "`fn %s`\n\nGAMA-L function `%s`." % (fn_signature(node), name)
    expr = getattr(node, "expr", None)
    if isinstance(expr, G.Lambda):
        return ("`let %s = %s`\n\nGAMA-L function value `%s`."
                % (name, lambda_signature(expr), name))
    type_name = getattr(node, "type_name", None)
    if type_name:
        return "GAMA-L let `%s`: `%s`" % (name, type_name)
    return "GAMA-L let `%s`" % name


def _member_hover(uri: str, text: str, line: int, character: int):
    member = _namespaced_member(text, line, character)
    if member is None:
        return None
    alias, name = member
    imp = _alias_map(text).get(alias)
    if imp is None:
        return None
    for node in _module_functions(uri, imp["path"]) or []:
        if node.name == name:
            return ("`%s.%s`\n\nGAMA-L function from `%s`."
                    % (alias, fn_signature(node), imp["path"]))
    return None


def hover(uri: str, text: str, line: int, character: int):
    name = word_at(text, line, character)
    if not name:
        return None
    value = _member_hover(uri, text, line, character)
    if value is None and name in TYPE_HOVERS:
        value = TYPE_HOVERS[name]
    elif value is None and name in TYPE_NAMES:
        value = "GAMA-L type `%s`" % name
    if value is None and name in DESCRIPTIONS:
        value = DESCRIPTIONS[name]
        sig = builtin_signature(name)
        if sig is not None:
            value = "`%s`\n\n%s" % (sig[0], value)
    if value is None and name in G.BUILTINS:
        sig = builtin_signature(name)
        if sig is not None:
            value = "`%s`\n\nGAMA-L builtin `%s`." % (sig[0], name)
        else:
            value = "GAMA-L builtin `%s`" % name
    if value is None:
        value = _decl_hover(text, name)
    if value is None:
        param = _param_by_name(text, name)
        if param is not None:
            value = "GAMA-L parameter `%s`: `%s`" % (
                name, param.type_name or "Any")
    if value is None and name in _alias_map(text):
        imp = _alias_map(text)[name]
        value = ("GAMA-L namespace alias `%s` for `%s`; call members as "
                 "`%s.fn(...)`." % (name, imp["path"], name))
    if value is None and name in G.KEYWORDS:
        value = "GAMA-L keyword `%s`" % name
    if value is None:
        return None
    return {"contents": {"kind": "markdown", "value": value}}


def _call_context(prefix: str):
    """Return (callee, argument_text) for the innermost open call, or None."""
    stack = []
    quote = None
    i = 0
    while i < len(prefix):
        ch = prefix[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch == "#":
            break
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            stack.pop()
        i += 1
    if not stack:
        return None
    open_idx = stack[-1]
    j = open_idx - 1
    while j >= 0 and prefix[j] in " \t":
        j -= 1
    end = j
    while j >= 0 and (prefix[j].isalnum() or prefix[j] in "_."):
        j -= 1
    callee = prefix[j + 1:end + 1]
    return callee, prefix[open_idx + 1:] if callee else None


def _active_parameter(args: str) -> int:
    depth = 0
    quote = None
    commas = 0
    i = 0
    while i < len(args):
        ch = args[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            commas += 1
        i += 1
    return commas


def _signature_help(signature, params):
    active = 0
    return {
        "signatures": [{
            "label": signature,
            "parameters": [{"label": p} for p in params],
        }],
        "activeSignature": 0,
        "activeParameter": active,
    }


def signature_help(uri: str, text: str, line: int, character: int):
    prefix = _line_prefix(text, line, character)
    ctx = _call_context(prefix)
    if ctx is None:
        return None
    callee, args = ctx
    active = _active_parameter(args)
    sig = None

    if "." in callee:
        alias, member = callee.split(".", 1)
        imp = _alias_map(text).get(alias)
        if imp is not None:
            for node in _module_functions(uri, imp["path"]) or []:
                if node.name == member:
                    sig = (fn_signature(node),
                           [_param_label(p) for p in node.params])
                    break
    elif callee in SIGNATURES:
        sig = builtin_signature(callee)
    else:
        node = _decl_by_name(text, callee)
        if isinstance(node, G.Fn):
            sig = (fn_signature(node),
                   [_param_label(p) for p in node.params])
        elif isinstance(node, G.Let) and isinstance(node.expr, G.Lambda):
            sig = (lambda_signature(node.expr, "let " + node.name),
                   [_param_label(p) for p in node.expr.params])
        elif node is not None:
            sig = ("%s(...)" % callee, [])
        else:
            param = _param_by_name(text, callee)
            if param is not None and param.type_name == "Function":
                sig = ("%s(...)" % callee, [])

    if sig is None:
        return None
    result = _signature_help(sig[0], sig[1])
    if result["signatures"][0]["parameters"]:
        result["activeParameter"] = min(
            active, len(result["signatures"][0]["parameters"]) - 1)
    else:
        result["activeParameter"] = 0
    return result


# ------------------------------------------------- document symbols & themes
KIND_SYMBOL_FUNCTION = 12
KIND_SYMBOL_VARIABLE = 13

TOKEN_TYPES = ["keyword", "type", "function", "variable", "string", "number", "operator"]
TYPE_NAMES = {"Int", "Decimal", "Text", "Bool", "List", "Table", "Result",
              "Json", "Null", "Any", "Function"}

T_KEYWORD, T_TYPE, T_FUNCTION, T_VARIABLE, T_STRING, T_NUMBER, T_OP = range(7)


def _fn_names(text: str) -> set:
    return {n.name for n in _declarations(text) if isinstance(n, G.Fn)}


def document_symbols(uri: str, text: str):
    """Return LSP DocumentSymbol[] for top-level let/fn declarations."""
    out = []
    for node in _declarations(text):
        is_fn = isinstance(node, G.Fn)
        loc = _decl_start(uri, text, node, node.name)
        if loc is None:
            continue
        rng = loc["range"]
        out.append({
            "name": node.name,
            "detail": "%s(%s)" % ("fn" if is_fn else "let", node.name),
            "kind": KIND_SYMBOL_FUNCTION if is_fn else KIND_SYMBOL_VARIABLE,
            "range": rng,
            "selectionRange": rng,
            "children": [],
        })
    return out


def _line_offsets(src: str):
    offsets = []
    pos = 0
    for line in src.split("\n"):
        offsets.append(pos)
        pos += len(line) + 1
    return offsets


def _raw_lexeme(src: str, offsets, tok) -> str:
    """Source spelling of a token (strings keep quotes/escapes)."""
    line0 = max(0, int(tok.line) - 1)
    if line0 >= len(offsets):
        return str(tok.val)
    start = offsets[line0] + max(0, int(tok.col) - 1)
    if tok.kind == "STRING":
        start = offsets[line0] + max(0, int(tok.col) - 2)
        i = start + 1
        n = len(src)
        while i < n:
            if src[i] == "\\":
                i += 2
                continue
            if src[i] == src[start]:
                i += 1
                break
            i += 1
        line_end = offsets[line0] + len(src.split("\n")[line0])
        return src[start:min(i, line_end)]
    if tok.kind == "NUMBER":
        return str(tok.val)
    return str(tok.val)


def _token_type(tok, fns):
    if tok.kind == "KW":
        return T_KEYWORD
    if tok.kind == "IDENT":
        if tok.val in TYPE_NAMES:
            return T_TYPE
        if tok.val in G.BUILTINS or tok.val in fns:
            return T_FUNCTION
        return T_VARIABLE
    if tok.kind == "STRING":
        return T_STRING
    if tok.kind == "NUMBER":
        return T_NUMBER
    if tok.kind == "OP":
        return T_OP
    return None


def semantic_tokens(uri: str, text: str):
    """Full-document semantic tokens (LSP delta encoding)."""
    try:
        toks = G.lex(text)
    except Exception:
        return {"data": []}
    src = text or ""
    lines = src.split("\n")
    offsets = _line_offsets(src)
    fns = _fn_names(text)
    data = []
    prev_line = 0
    prev_start = 0
    for tok in toks:
        if tok.kind in ("NL", "EOF"):
            continue
        ttype = _token_type(tok, fns)
        if ttype is None:
            continue
        line0 = max(0, int(tok.line) - 1)
        if line0 >= len(lines):
            continue
        char_idx = max(0, int(tok.col) - 1)
        if tok.kind == "STRING":
            char_idx = max(0, int(tok.col) - 2)
        start = utf16_len(lines[line0][:char_idx])
        length = utf16_len(_raw_lexeme(src, offsets, tok))
        if length <= 0:
            continue
        if line0 == prev_line:
            delta_line = 0
            delta_start = start - prev_start
        else:
            delta_line = line0 - prev_line
            delta_start = start
        data.extend([delta_line, delta_start, length, ttype, 0])
        prev_line = line0
        prev_start = start
    return {"data": data}


def main():
    docs: dict[str, str] = {}
    while True:
        try:
            msg = read_msg()
        except FramingError as ex:
            # Malformed frame: report ParseError and keep serving.
            send_error(None, -32700, str(ex))
            continue
        if msg is EOF:
            break
        if not isinstance(msg, dict):
            send_error(None, -32600, "invalid request: expected object")
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if not isinstance(method, str):
            send_error(mid, -32600, "invalid request: missing method")
            continue
        params = msg.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            # Never crash on scalar params (e.g. "oops"); report and continue.
            if mid is not None:
                send_error(mid, -32602, "invalid params: expected object")
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "capabilities": {
                    "textDocumentSync": 1,
                    "completionProvider": {"triggerCharacters": [" ", "|", "."]},
                    "hoverProvider": True,
                    "signatureHelpProvider": {
                        "triggerCharacters": ["(", ","],
                        "retriggerCharacters": [","],
                    },
                    "definitionProvider": True,
                    "referencesProvider": True,
                    "renameProvider": True,
                    "documentFormattingProvider": True,
                    "documentSymbolProvider": True,
                    "codeActionProvider": {"codeActionKinds": ["quickfix"]},
                    "semanticTokensProvider": {
                        "legend": {"tokenTypes": TOKEN_TYPES,
                                   "tokenModifiers": []},
                        "full": True,
                    },
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
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            changes = params.get("contentChanges") or []
            last_change = _obj(changes[-1]) if changes else {}
            text = last_change.get("text") if changes else td.get("text")
            if text is None:
                text = docs.get(uri, "")
            if not isinstance(text, str):
                text = str(text)
            docs[uri] = text
            if uri:
                publish(uri, text)
        elif method == "textDocument/completion":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid,
                  "result": completion(uri, text, line, ch)})
        elif method == "textDocument/signatureHelp":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid,
                  "result": signature_help(uri, text, line, ch)})
        elif method == "textDocument/hover":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": hover(uri, text, line, ch)})
        elif method == "textDocument/definition":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": definition(uri, text, line, ch)})
        elif method == "textDocument/references":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": references(uri, text, line, ch)})
        elif method == "textDocument/formatting":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            send({"jsonrpc": "2.0", "id": mid,
                  "result": formatting(uri, text)})
        elif method == "textDocument/rename":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid, "result": rename(
                uri, text, line, ch, str(params.get("newName") or ""))})
        elif method == "textDocument/documentSymbol":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            send({"jsonrpc": "2.0", "id": mid,
                  "result": document_symbols(uri, text)})
        elif method == "textDocument/semanticTokens/full":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            send({"jsonrpc": "2.0", "id": mid,
                  "result": semantic_tokens(uri, text)})
        elif method == "textDocument/codeAction":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            send({"jsonrpc": "2.0", "id": mid, "result": code_actions(
                uri, text, _obj(params.get("range")),
                _obj(params.get("context")))})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": None})


if __name__ == "__main__":
    main()
