#!/usr/bin/env python3
"""Minimal GAMA-L 2 language server (stdio JSON-RPC).

Supports initialize, didOpen/didChange/didSave diagnostics, completion, hover,
definition, references, shutdown and exit.
"""
from __future__ import annotations
import dataclasses
import json
import os
import re
import sys
import tempfile
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


# Emitted by the in-memory checker whenever it meets a top-level `import`.
# It is an environment limitation of `check(text)`, not a source error.
IMPORT_IN_MEMORY_MSG = "import requires check_file/run_file"


def _file_backed(uri: str):
    """Return the existing on-disk Path for a file:// document, else None."""
    try:
        if urllib.parse.urlparse(uri).scheme != "file":
            return None
        path = Path(uri_path(uri))
    except (ValueError, OSError):
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _check_disk_aware(path: Path, text: str):
    """Type-check ``text`` with imports resolved against ``path``'s directory.

    When the buffer differs from disk (an unsaved edit with a real path), the
    text is written to a hidden sibling temp file so relative imports still
    resolve; the temp file is always removed.
    """
    try:
        on_disk = path.read_text(encoding="utf-8")
    except OSError:
        on_disk = None
    if on_disk == text:
        G.check_file(str(path))
        return
    tmp = None
    try:
        try:
            fd, name = tempfile.mkstemp(
                prefix=".gama_lsp_", suffix=".gm", dir=str(path.parent))
        except OSError:
            # Read-only directory: diagnose the on-disk revision instead of
            # turning an environmental failure into a source diagnostic.
            G.check_file(str(path))
            return
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp = Path(name)
        G.check_file(str(tmp))
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


_IMPORT_SITE_RE = re.compile(r'(?m)^[ \t]*(import[ \t]+"([^"]+)")')


def _import_targets(path: Path, root: Path, rel: str):
    """Candidate resolved paths for an import, mirroring gamal2._flatten."""
    for c in (path.parent / rel,
              root / ".gamal" / "modules" / rel,
              root / "gama_modules" / rel):
        try:
            if c.exists():
                return c.resolve()
        except OSError:
            continue
    return None


def _imports_in_text(text: str):
    for m in _IMPORT_SITE_RE.finditer(text or ""):
        yield m, m.group(2)


def _module_reaches(start: Path, root: Path, target: Path, seen=None) -> bool:
    """True when ``start`` (transitively) imports ``target``."""
    try:
        start = start.resolve()
    except OSError:
        return False
    if start == target:
        return True
    if seen is None:
        seen = set()
    if start in seen:
        return False
    seen.add(start)
    try:
        source = start.read_text(encoding="utf-8")
    except OSError:
        return False
    for _m, rel in _imports_in_text(source):
        nxt = _import_targets(start, root, rel)
        if nxt is not None and _module_reaches(nxt, root, target, seen):
            return True
    return False


def _site_position(text: str, match):
    pos = match.start(1)
    line = (text or "")[:pos].count("\n")
    line_start = (text or "").rfind("\n", 0, pos) + 1
    return line, utf16_len((text or "")[line_start:pos])


def _import_site(path: Path, text: str, src: Path):
    """Return (line, character, import_path) of the main import that pulls
    in ``src``, preferring direct imports but following nested ones."""
    root = G._project_root(path)
    first = None
    for m, rel in _imports_in_text(text):
        target = _import_targets(path, root, rel)
        pos = _site_position(text, m)
        if first is None:
            first = (pos[0], pos[1], rel)
        if target is not None and _module_reaches(target, root, src):
            return pos[0], pos[1], rel
    return first


def diagnostics(uri: str, text: str):
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    path = _file_backed(uri)
    try:
        if path is not None:
            _check_disk_aware(path, text)
        else:
            G.check(text)
        return []
    except G.GamaError as e:
        # Only untitled/unsaved documents use the in-memory checker; its
        # import complaint is spurious there. Other syntax/type errors pass.
        if path is None and e.msg == IMPORT_IN_MEMORY_MSG:
            return []
        src = getattr(e, "src", None)
        if src is not None and path is not None:
            try:
                src_path = Path(src).resolve()
            except OSError:
                src_path = None
            if src_path is not None:
                site = _import_site(path, text, src_path)
                if site is not None:
                    line, col, rel = site
                    try:
                        display = str(src_path.relative_to(G._project_root(path)))
                    except (ValueError, OSError):
                        display = src_path.name
                    msg = "%s (in %s:%d:%d)" % (
                        e.msg, display, int(e.line or 0), int(e.col or 0))
                    rows = (text or "").split("\n") or [""]
                    line = min(max(0, line), len(rows) - 1)
                    end_col = min(col + 1, utf16_len(rows[line]))
                    return [{
                        "range": {"start": {"line": line, "character": col},
                                  "end": {"line": line, "character": end_col}},
                        "severity": 1,
                        "source": "gama-l",
                        "message": msg,
                    }]
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
KIND_CONSTANT = 21
KIND_TYPE = 25
KIND_FIELD = 5
KIND_CLASS = 7
KIND_ENUM = 13
KIND_ENUM_MEMBER = 20

NAME_RE = re.compile(r"\b(let|fn)\s+([A-Za-z_]\w*)")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IMPORT_RE = re.compile(
    r'(?m)^\s*import\s+"([^"]+)"(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?')
IMPORT_ALIAS_CTX_RE = re.compile(
    r'^\s*import\s+"([^"]*)"\s+as\s+([A-Za-z_][A-Za-z0-9_]*)?$')
MEMBER_CTX_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)?$")
# Dotted chain immediately before the cursor: `p.`, `p.x`, `l.a.`.
MEMBER_CHAIN_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*)"
    r"\s*\.\s*$")
# Explicit type-argument position: `id<` or `id<Int, ` (no whitespace
# before `<`, so ordinary comparisons such as `a < b` never match).
TYPE_ARG_CTX_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)<([A-Za-z0-9_,\s]*)$")
# Type-parameter declaration position: `fn id<` or `fn id<A, `.
TYPE_PARAM_DECL_CTX_RE = re.compile(
    r"\bfn\s+[A-Za-z_][A-Za-z0-9_]*\s*<([A-Za-z0-9_,\s]*)$")
# One complete explicit type argument (the core parser does not accept
# these at call sites yet; the LSP only reasons about them).
TYPE_ARGS_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*")
# Header of a generic named function, used to find type parameters that are
# in scope at the cursor. Matches across line breaks inside `<...>`.
FN_TYPE_PARAM_HEADER_RE = re.compile(
    r"\bfn\s+[A-Za-z_][A-Za-z0-9_]*\s*<([A-Za-z0-9_,\s]*)>")
# Tail of a line that is an incomplete member expression (`print p.x`).
LINE_CHAIN_TAIL_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*[A-Za-z0-9_]*)*"
    r"\s*\.\s*[A-Za-z0-9_]*$")
RECORD_DECL_RE = re.compile(
    r"\brecord\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}")
ENUM_DECL_RE = re.compile(
    r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}")
RECORD_FIELD_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_.]*)")

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
    names = getattr(node, "type_params", None) or []
    name = node.name + (("<" + ", ".join(names) + ">") if names else "")
    return signature_text(name, [_param_label(p) for p in node.params],
                          node.ret, node.effects)


def lambda_signature(node, prefix="fn") -> str:
    return signature_text(prefix, [_param_label(p) for p in node.params],
                          node.ret, node.effects)


def _type_param_names(node) -> list:
    """Declared generic type parameters of a snippet node, in order."""
    return [str(name) for name in (getattr(node, "type_params", None) or [])]


def _type_param_positions(node) -> list:
    """``[{"name": "T", "position": 0}, ...]`` for a generic function."""
    return [{"name": name, "position": i}
            for i, name in enumerate(_type_param_names(node))]


def _type_param_doc(node) -> str | None:
    positions = _type_param_positions(node)
    if not positions:
        return None
    return "Type parameters: " + ", ".join(
        "`%s` (position %d)" % (item["name"], item["position"])
        for item in positions)


def _fn_hover_text(node, intro=None, qualifier=None) -> str:
    """Markdown hover text for an ``fn`` declaration, including generics."""
    signature = fn_signature(node)
    if qualifier:
        signature = "%s.%s" % (qualifier, signature)
    label = "fn %s" % signature
    names = _type_param_names(node)
    if names:
        body = intro or "GAMA-L generic function `%s`." % node.name
        return "`%s`\n\n%s\n\n%s" % (label, body, _type_param_doc(node))
    if intro:
        return "`%s`\n\n%s" % (label, intro)
    return "`%s`\n\nGAMA-L function `%s`." % (label, node.name)


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


def _document_without_line(text: str, line: int) -> str:
    """Document text with ``line`` blanked, for parsing around a live edit.

    Feature requests routinely arrive while the current line is syntactically
    incomplete (``print id<``). Removing just that line lets the rest of the
    declarations parse so hover/signature/completion can still resolve known
    functions instead of falling back to builtins only.
    """
    rows = (text or "").split("\n")
    if 0 <= line < len(rows):
        rows = list(rows)
        rows[line] = ""
    return "\n".join(rows)


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


def _module_declarations(uri: str, path: str):
    """Exported ``fn``/``let``/``record``/``enum`` declarations of a module.

    Mirrors the core alias-import rules: the module (including plain imports
    it inlines) may only contain declarations, plain imports and ``use`` at
    the top level. Enums are accepted as an LSP navigation extension even
    though core alias imports still reject their evaluation. Malformed,
    missing or side-effect modules are treated as unavailable so completion
    and hover stay quiet instead of advertising members the runtime would
    reject. ``None`` means unavailable, while an empty list means nothing
    exportable.
    """
    target = _resolve_module(uri, path)
    root = _doc_root(uri)
    if target is None or root is None:
        return None
    try:
        flat_fn = getattr(G, "_flatten", None)
        if flat_fn is not None:
            stmts = flat_fn(target, root)
        else:  # pragma: no cover - older cores without namespace flattening
            stmts = G.parse(target.read_text(encoding="utf-8"))
        allowed = tuple(getattr(G, "_ALIAS_ALLOWED_TOPLEVEL",
                               (G.Fn, G.Let, G.Import, G.Use))) + (
                                   G.EnumDecl,)
        if not all(isinstance(st, allowed) for st in stmts):
            return None
    except Exception:
        return None
    return [st for st in stmts
            if isinstance(st, (G.Fn, G.Let, G.RecordDecl, G.EnumDecl))]


def _module_functions(uri: str, path: str):
    """Exported top-level ``fn`` declarations of an imported module, or None."""
    declarations = _module_declarations(uri, path)
    if declarations is None:
        return None
    return [node for node in declarations if isinstance(node, G.Fn)]


def _expr_type(expr):
    """Best-effort static type for a literal-ish constant expression."""
    if isinstance(expr, G.Literal):
        return G.tname(expr.value)
    if isinstance(expr, G.Lambda):
        return "Function"
    if isinstance(expr, G.ListExpr):
        kinds = {_expr_type(item) for item in expr.items}
        return "Table" if kinds and kinds <= {"Json"} else "List"
    if isinstance(expr, G.MapExpr):
        return "Json"
    return None


def _const_type(node):
    declared = getattr(node, "type_name", None)
    if declared:
        return declared
    try:
        return _expr_type(getattr(node, "expr", None))
    except Exception:
        return None


def _const_value(node, limit: int = 120):
    """Literal value text for hover, or None when it is not safe to show.

    Only parsed literals are rendered; arbitrary expressions are never
    evaluated at completion or hover time.
    """
    expr = getattr(node, "expr", None)
    if not isinstance(expr, G.Literal):
        return None
    value = expr.value
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, str):
        text = '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')
    else:
        decimal_type = getattr(G, "Decimal", ())
        if isinstance(value, int) or isinstance(value, decimal_type):
            text = str(value)
        else:
            return None
    if len(text) > limit:
        text = text[:limit - 1] + "\u2026"
    return text


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


# ------------------------------------------------------- records and enums
def _parse_or_none(text):
    """Parsed program, or ``None`` when the document does not parse."""
    try:
        return G.parse(text or "")
    except Exception:
        return None


def _record_decls(text):
    """Map record name -> ``RecordDecl`` for the document.

    Prefers the parsed AST; when parsing fails (common while the cursor line
    is still incomplete) a conservative regex fallback recovers declarations
    whose braces are closed. Missing or malformed declarations are simply
    absent, never fatal.
    """
    program = _parse_or_none(text)
    if program is not None:
        out = {}
        for node in _walk(program):
            if isinstance(node, G.RecordDecl) and node.name not in out:
                out[node.name] = node
        return out
    out = {}
    for match in RECORD_DECL_RE.finditer(text or ""):
        name, body = match.group(1), match.group(2)
        fields = [(fm.group(1), fm.group(2))
                  for fm in RECORD_FIELD_RE.finditer(body)]
        if name not in out:
            out[name] = G.RecordDecl(name, fields, 0, 0)
    return out


def _enum_decls(text):
    """Map enum name -> ``EnumDecl`` for the document (parsed or fallback)."""
    program = _parse_or_none(text)
    if program is not None:
        out = {}
        for node in _walk(program):
            if isinstance(node, G.EnumDecl) and node.name not in out:
                out[node.name] = node
        return out
    out = {}
    for match in ENUM_DECL_RE.finditer(text or ""):
        name = match.group(1)
        body = re.sub(r"#[^\n]*", "", match.group(2))
        variants = [(token, 0, 0) for token in re.split(r"[,\s]+", body)
                    if ALIAS_RE.match(token)]
        if name not in out:
            out[name] = G.EnumDecl(name, variants, 0, 0)
    return out


def _record_name_for(type_name, records, aliases):
    """Resolve a type annotation to a locally declared record name.

    Dotted types that start with an import alias belong to another module and
    are left unresolved instead of being mapped onto an unrelated local record
    with the same tail name.
    """
    if not type_name:
        return None
    if type_name in records:
        return type_name
    if "." in type_name:
        head, _sep, tail = type_name.partition(".")
        if head in aliases:
            return None
        if tail in records:
            return tail
    return None


def _expr_record_type(expr, records):
    """Record type produced by a literal or record-style constructor call."""
    if isinstance(expr, G.RecordLit):
        return expr.name
    if isinstance(expr, G.Call) and expr.name in records:
        return expr.name
    return None


def _regex_binding_type(text, name, records, aliases):
    """Best-effort binding type from raw text when the AST is unavailable."""
    escaped = re.escape(name)
    patterns = (
        r"\blet\s+%s\s*:\s*([A-Za-z_][A-Za-z0-9_.]*)" % escaped,
        r"\blet\s+%s\s*=\s*([A-Za-z_][A-Za-z0-9_.]*)\s*[\{\(]" % escaped,
        r"\b%s\s*:\s*([A-Za-z_][A-Za-z0-9_.]*)" % escaped,
    )
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match is None:
            continue
        candidate = match.group(1)
        if _record_name_for(candidate, records, aliases) is not None:
            return candidate
        head, _sep, tail = candidate.partition(".")
        if tail and head in aliases:
            return candidate
    return None


def _var_type_name(text, name, line, records, aliases):
    """Best-effort annotated/constructed type of a local variable.

    Bindings are collected document-wide (like the existing parameter helper);
    the nearest declaration at or before the cursor line wins, otherwise the
    earliest known binding is used. The nearest binding is authoritative even
    when its type is unknown, so a later ``let p = 5`` does not resurrect an
    earlier ``let p: Point``. Nothing is evaluated.
    """
    program = _parse_or_none(text)
    if program is None:
        return _regex_binding_type(text, name, records, aliases)
    candidates = []
    for node in _walk(program):
        if isinstance(node, G.Let) and node.name == name:
            type_name = getattr(node, "type_name", None)
            if not type_name:
                type_name = _expr_record_type(node.expr, records)
            candidates.append((int(node.line or 0), type_name))
        elif isinstance(node, G.Assign) and node.name == name:
            candidates.append(
                (int(node.line or 0), _expr_record_type(node.expr, records)))
        elif isinstance(node, (G.Fn, G.Lambda)):
            for param in node.params:
                if param.name == name:
                    candidates.append(
                        (int(node.line or 0), param.type_name))
    if not candidates:
        return None
    limit = max(1, int(line) + 1)
    before = [c for c in candidates if 0 < c[0] <= limit]
    chosen = max(before, key=lambda c: c[0]) if before else min(
        candidates, key=lambda c: c[0])
    return chosen[1]


def _chain_target_type(text, parts, records, enums, aliases, line):
    """Resolve a dotted prefix before the cursor to a record or enum type."""
    if not parts:
        return None
    base = parts[0]
    if len(parts) == 1:
        if base in enums:
            return ("enum", base)
        type_name = _var_type_name(text, base, line, records, aliases)
        record = _record_name_for(type_name, records, aliases)
        return ("record", record) if record else None
    type_name = _var_type_name(text, base, line, records, aliases)
    record = _record_name_for(type_name, records, aliases)
    for part in parts[1:]:
        if record is None:
            return None
        fields = dict(records[record].fields)
        if part not in fields:
            return None
        record = _record_name_for(fields[part], records, aliases)
    return ("record", record) if record else None


def _context_text(text, line, character):
    """Document with the incomplete member chain at the cursor repaired.

    Completion and hover commonly run on a line such as ``print p.``; the
    strict parser would reject it, hiding the declarations needed to resolve
    ``p``. Collapsing the trailing chain to its first identifier keeps the
    surrounding document parseable. Malformed documents still fall back to the
    regex scanners above.
    """
    rows = (text or "").split("\n")
    if line < 0 or line >= len(rows):
        return text or ""
    row = rows[line]
    index = utf16_to_index(row, max(0, character))
    prefix, suffix = row[:index], row[index:]
    match = LINE_CHAIN_TAIL_RE.search(prefix)
    if match:
        prefix = prefix[:match.start()] + match.group(1)
    if prefix and suffix and (prefix[-1].isalnum() or prefix[-1] == "_") \
            and (suffix[0].isalnum() or suffix[0] == "_"):
        prefix += " "
    rows[line] = prefix + suffix
    return "\n".join(rows)


def _record_signature_text(decl, qualifier=None):
    fields = ", ".join("%s: %s" % (name, type_name)
                       for name, type_name in decl.fields)
    if qualifier:
        head = "%s.%s" % (qualifier, decl.name)
        return head + (" { %s }" % fields if fields else " { }")
    head = "record %s" % decl.name
    return head + (" { %s }" % fields if fields else " { }")


def _record_hover_text(decl, qualifier=None):
    fields = ", ".join("`%s: %s`" % pair for pair in decl.fields)
    if fields:
        return ("`%s`\n\nGAMA-L record `%s` with fields %s."
                % (_record_signature_text(decl, qualifier), decl.name, fields))
    return ("`%s`\n\nGAMA-L record `%s` (no fields)."
            % (_record_signature_text(decl, qualifier), decl.name))


def _enum_signature_text(decl):
    variants = ", ".join(variant[0] for variant in decl.variants)
    head = "enum %s" % decl.name
    return head + (" { %s }" % variants if variants else " { }")


def _enum_hover_text(decl):
    variants = ", ".join("`%s`" % variant[0] for variant in decl.variants)
    if variants:
        return ("`%s`\n\nGAMA-L enum `%s` with variants %s."
                % (_enum_signature_text(decl), decl.name, variants))
    return ("`%s`\n\nGAMA-L enum `%s` (no variants)."
            % (_enum_signature_text(decl), decl.name))


def _structural_hover(text, name):
    """Hover for a record/enum declaration name, or ``None``."""
    records = _record_decls(text)
    if name in records:
        return _record_hover_text(records[name])
    enums = _enum_decls(text)
    if name in enums:
        return _enum_hover_text(enums[name])
    return None


def _dotted_member_at(text, line, character):
    """Return ``(chain_parts, member)`` when the cursor is on ``a.b[.c]``."""
    row = _line_text(text, line)
    if not row:
        return None
    pos = utf16_to_index(row, max(0, character))
    for match in WORD_RE.finditer(row):
        if match.start() <= pos <= match.end():
            before = row[:match.start()]
            chain = re.search(
                r"([A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*)"
                r"\s*\.\s*$", before)
            if chain is None:
                return None
            parts = [part.strip() for part in chain.group(1).split(".")
                     if part.strip()]
            return parts, match.group(0)
    return None


def _local_member_hover(text, line, character):
    """Hover for a record field or enum variant in a member expression."""
    found = _dotted_member_at(text, line, character)
    if found is None:
        return None
    parts, member = found
    context = _context_text(text, line, character)
    records = _record_decls(context)
    enums = _enum_decls(context)
    aliases = _alias_map(text)
    if len(parts) == 1:
        enum = enums.get(parts[0])
        if enum is not None:
            if member not in {variant[0] for variant in enum.variants}:
                return None
            return ("`%s.%s`\n\nGAMA-L enum variant `%s` of enum `%s`."
                    % (parts[0], member, member, parts[0]))
    type_name = _var_type_name(context, parts[0], line, records, aliases)
    record = _record_name_for(type_name, records, aliases)
    for part in parts[1:]:
        if record is None:
            return None
        fields = dict(records[record].fields)
        if part not in fields:
            return None
        record = _record_name_for(fields[part], records, aliases)
    if record is None:
        return None
    fields = dict(records[record].fields)
    if member not in fields:
        return None
    return ("`%s.%s`\n\nGAMA-L field `%s: %s` of record `%s`."
            % (".".join(parts), member, member, fields[member], record))


def _member_type_items(text, line, character, parts):
    """Completion items for fields/variants after a dotted prefix."""
    context = _context_text(text, line, character)
    records = _record_decls(context)
    enums = _enum_decls(context)
    aliases = _alias_map(text)
    target = _chain_target_type(context, parts, records, enums, aliases, line)
    if target is None:
        return []
    kind, name = target
    items = []
    seen = set()
    if kind == "enum":
        decl = enums.get(name)
        if decl is None:
            return []
        for variant_info in decl.variants:
            variant = variant_info[0]
            if variant in seen:
                continue
            seen.add(variant)
            items.append({
                "label": variant,
                "kind": KIND_ENUM_MEMBER,
                "detail": "%s.%s" % (name, variant),
                "documentation": "Variant `%s` of enum `%s`."
                                 % (variant, name),
            })
        return items
    decl = records.get(name)
    if decl is None:
        return []
    for field, type_name in decl.fields:
        if field in seen:
            continue
        seen.add(field)
        items.append({
            "label": field,
            "kind": KIND_FIELD,
            "detail": "field %s: %s" % (field, type_name),
            "documentation": "Field `%s` of record `%s`."
                             % (field, name),
        })
    return items


def _first_open_brace(text: str, start: int) -> int:
    """Index of the first ``{`` after ``start`` that is real code, or -1."""
    quote = None
    i = max(0, start)
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch == "#":
            newline = text.find("\n", i)
            i = len(text) if newline == -1 else newline
            continue
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "{":
            return i
        i += 1
    return -1


def _enclosing_type_params(text: str, line: int,
                           character: int | None = None) -> list:
    """Type parameters of the generic function whose body contains the cursor.

    This is a lexical scan (header + matching braces) so it still works while
    the cursor line is incomplete, which is the normal completion state.
    """
    rows = (text or "").split("\n")
    target = sum(len(row) + 1 for row in rows[:max(0, line)])
    if character is not None and 0 <= line < len(rows):
        target += utf16_to_index(rows[line], max(0, character))
    best = None
    for match in FN_TYPE_PARAM_HEADER_RE.finditer(text or ""):
        if match.start() > target:
            continue
        brace = _first_open_brace(text, match.end())
        if brace == -1 or brace > target:
            continue
        depth = 0
        quote = None
        i = brace
        end = None
        while i < len(text):
            ch = text[i]
            if quote is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch == "#":
                newline = text.find("\n", i)
                i = len(text) if newline == -1 else newline
                continue
            elif ch in ("'", '"'):
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None:
            end = len(text)
        if match.start() <= target <= end:
            names = [name.strip() for name in match.group(1).split(",")
                     if name.strip()]
            best = (match.start(), names)
    return best[1] if best else []


def _type_completion_items(node=None, in_scope=None):
    """Type completions for a generic declaration or explicit type argument.

    In-scope type parameters come first (these are the only user-defined type
    names valid at the call site), then the callee's declared parameters as
    context, then built-in type names. The core parser does not infer
    type-parameter names, so an *undeclared* ``fn f<`` can only offer the
    built-in names.
    """
    items = []
    seen = set()
    for name in in_scope or []:
        if name in seen:
            continue
        seen.add(name)
        items.append({
            "label": name,
            "kind": KIND_TYPE,
            "detail": "type parameter in scope",
            "documentation": "Type parameter `%s` from the enclosing "
                             "generic function." % name,
        })
    if node is not None:
        names = _type_param_names(node)
        for position, name in enumerate(names):
            if name in seen:
                continue
            seen.add(name)
            items.append({
                "label": name,
                "kind": KIND_TYPE,
                "detail": "type parameter of %s" % node.name,
                "documentation": "Generic type parameter `%s` (position %d) "
                                 "declared by `%s`."
                                 % (name, position, node.name),
            })
    for name in sorted(TYPE_NAMES):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_TYPE})
    return items


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

    # Member position: ``alias.<cursor>``. Offer the module's exported
    # functions, records and top-level `let` constants, mirroring the core
    # namespace. When the head is not an import alias, fall through to local
    # record/enum member completion below.
    mm = MEMBER_CTX_RE.search(prefix)
    if mm:
        alias = mm.group(1)
        imp = _alias_map(text).get(alias)
        if imp is not None:
            declarations = _module_declarations(uri, imp["path"])
            if declarations is None:
                return {"isIncomplete": False, "items": []}
            items = []
            seen_members = set()
            for node in sorted(declarations, key=lambda n: n.name):
                if node.name in seen_members:
                    continue
                seen_members.add(node.name)
                if isinstance(node, G.Fn):
                    items.append({
                        "label": node.name,
                        "kind": KIND_FUNCTION,
                        "detail": fn_signature(node),
                        "documentation": "From `%s` (imported as `%s`)."
                                         % (imp["path"], alias),
                    })
                elif isinstance(node, G.RecordDecl):
                    items.append({
                        "label": node.name,
                        "kind": KIND_CLASS,
                        "detail": "record %s" % node.name,
                        "documentation": "Record from `%s` (imported as "
                                         "`%s`)." % (imp["path"], alias),
                    })
                else:
                    type_name = _const_type(node)
                    detail = ("let %s: %s" % (node.name, type_name)
                              if type_name else "let %s" % node.name)
                    items.append({
                        "label": node.name,
                        "kind": KIND_CONSTANT,
                        "detail": detail,
                        "documentation": "Constant from `%s` (imported as "
                                         "`%s`)." % (imp["path"], alias),
                    })
            return {"isIncomplete": False, "items": items}

    # Record fields (``p.<cursor>``) and enum variants (``Color.<cursor>``).
    # The chain form also covers nested records such as ``line.a.``.
    chain = MEMBER_CHAIN_RE.search(prefix)
    if chain is not None:
        parts = [part.strip() for part in chain.group(1).split(".")
                 if part.strip()]
        return {"isIncomplete": False,
                "items": _member_type_items(text, line, character, parts)}

    # Generic type-parameter declaration: ``fn id<`` / ``fn id<A, ``.
    # Only built-in type names can be suggested because the names are chosen
    # by the author and cannot be inferred.
    if TYPE_PARAM_DECL_CTX_RE.search(prefix):
        return {"isIncomplete": False, "items": _type_completion_items()}

    # Explicit type arguments: ``id<`` / ``id<In``. This intentionally does
    # not fire for ``a < b`` (no whitespace before ``<``) and only advertises
    # types when ``id`` resolves to a generic ``fn`` declaration; otherwise
    # the ordinary completion list is returned unchanged.
    typed_args = TYPE_ARG_CTX_RE.search(prefix)
    if typed_args is not None:
        callee = typed_args.group(1)
        node = _decl_by_name_at(text, callee, line)
        if isinstance(node, G.Fn) and _type_param_names(node):
            return {"isIncomplete": False,
                    "items": _type_completion_items(
                        node, _enclosing_type_params(text, line, character))}

    items = []
    seen = set()
    # ``as`` is an import keyword lexed as an identifier by the core parser;
    # ``record`` is contextual and also lexed as an identifier.
    for kw in sorted(set(G.KEYWORDS) | {"as", "record"}):
        if kw in seen:
            continue
        seen.add(kw)
        items.append({"label": kw, "kind": KIND_KEYWORD})
    for name in sorted(TYPE_NAMES):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_TYPE})
    for name in sorted(_record_decls(text)):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_CLASS,
                      "detail": "record %s" % name,
                      "documentation": "GAMA-L record `%s`." % name})
    for name in sorted(_enum_decls(text)):
        if name in seen:
            continue
        seen.add(name)
        items.append({"label": name, "kind": KIND_ENUM,
                      "detail": "enum %s" % name,
                      "documentation": "GAMA-L enum `%s`." % name})
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


# ------------------------------------- record/enum navigation (definition+refs)
def _offset_at(text: str, line0: int, idx: int) -> int:
    rows = (text or "").split("\n")
    if line0 < 0 or line0 >= len(rows) or idx < 0:
        return -1
    return sum(len(row) + 1 for row in rows[:line0]) + idx


def _linecol_at(text: str, offset: int):
    if offset < 0:
        return -1, -1
    prefix = (text or "")[:offset]
    line0 = prefix.count("\n")
    idx = offset - (prefix.rfind("\n") + 1)
    return line0, idx


def _brace_body_range(text: str, start: int):
    """``(body_start, body_end)`` for the first brace block at/after start."""
    i = (text or "").find("{", max(0, start))
    if i < 0:
        return None
    depth = 0
    quote = None
    j = i
    n = len(text or "")
    while j < n:
        ch = text[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = None
            j += 1
            continue
        if ch == '"':
            quote = ch
            j += 1
            continue
        if ch == "#":
            nl = text.find("\n", j)
            j = n if nl < 0 else nl
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1, j
        j += 1
    return None


def _decl_body_range(text: str, node, keyword: str):
    line0 = max(0, int(getattr(node, "line", 1)) - 1)
    col0 = max(0, int(getattr(node, "col", 1)) - 1)
    start = _offset_at(text, line0, col0)
    if start < 0:
        return None
    key = (text or "").find(keyword, start)
    if key < 0:
        return None
    return _brace_body_range(text, key + len(keyword))


def _top_level_tokens(text: str, start: int, end: int):
    """Identifier tokens at bracket depth 0 (strings/comments ignored)."""
    out = []
    depth = 0
    quote = None
    i = start
    n = min(end, len(text or ""))
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == '"':
            quote = ch
            i += 1
            continue
        if ch == "#":
            nl = text.find("\n", i)
            i = n if nl < 0 or nl > n else nl
            continue
        if ch in "([{":
            depth += 1
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            if depth == 0:
                out.append((text[i:j], i))
            i = j
            continue
        i += 1
    return out


def _record_field_spans(text: str, decl):
    """Map field name -> source offset of its declaration (top-level only)."""
    body = _decl_body_range(text, decl, "record")
    if body is None:
        return {}
    out = {}
    start, end = body
    for word, off in _top_level_tokens(text, start, end):
        j = off + len(word)
        while j < end and text[j] in " \t":
            j += 1
        if j < end and text[j] == ":":
            out.setdefault(word, off)
    return out


def _enum_variant_spans(text: str, decl):
    """Ordered ``[(variant, offset)]`` for top-level enum variants."""
    body = _decl_body_range(text, decl, "enum")
    if body is None:
        return []
    start, end = body
    return _top_level_tokens(text, start, end)


def _enum_variant_owners(enums, variant: str):
    """Local enum names declaring ``variant``."""
    owners = []
    for owner, decl in enums.items():
        if any(entry[0] == variant for entry in decl.variants):
            owners.append(owner)
    return owners


def _adjacent_dot(text: str, offset: int, width: int) -> bool:
    """True when the token at ``offset`` is part of a dotted name."""
    n = len(text or "")
    i = offset + width
    while i < n and text[i] in " \t":
        i += 1
    if i < n and text[i] == ".":
        return True
    j = offset - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j >= 0 and text[j] == "."


def _bare_variant_offsets(text: str, variant: str):
    """Offsets of bare variant uses in match patterns and constructors."""
    program = _parse_or_none(text)
    if program is None:
        return []
    shadowed = set()
    for node in _walk(program):
        if isinstance(node, (G.Fn, G.Let, G.Assign, G.Param,
                             G.RecordDecl, G.EnumDecl)):
            name = getattr(node, "name", None)
            if name:
                shadowed.add(name)
    call_ok = variant not in shadowed and variant not in G.BUILTINS
    out = set()
    for node in _walk(program):
        if isinstance(node, G.Match):
            body = _brace_body_range(text, _node_offset(text, node))
            if body is not None:
                for word, off in _top_level_tokens(text, body[0], body[1]):
                    if word != variant:
                        continue
                    if _adjacent_dot(text, off, len(word)):
                        continue
                    out.add(off)
        elif isinstance(node, G.Call) and node.name == variant and call_ok:
            off = _node_offset(text, node)
            if off >= 0:
                out.add(off)
    return sorted(out)


def _bare_variant_at(text: str, line: int, character: int, enums):
    """Unambiguous bare enum variant under the cursor, or None."""
    span = _word_span_at(text, line, character)
    if span is None:
        return None
    idx, name = span
    owners = _enum_variant_owners(enums, name)
    if len(owners) != 1:
        return None
    if not any(_linecol_at(text, off) == (line, idx)
               for off in _bare_variant_offsets(text, name)):
        return None
    owner = owners[0]
    return {"kind": "variant", "name": name, "owner": owner,
            "decl": enums[owner]}


def _node_offset(text: str, node) -> int:
    line0 = max(0, int(getattr(node, "line", 1)) - 1)
    col0 = max(0, int(getattr(node, "col", 1)) - 1)
    return _offset_at(text, line0, col0)


def _record_lit_key_spans(text: str, node):
    """``[(key, offset)]`` for the top-level keys of a record literal."""
    body = _brace_body_range(text, _node_offset(text, node))
    if body is None:
        return []
    start, end = body
    out = []
    for word, off in _top_level_tokens(text, start, end):
        j = off + len(word)
        while j < end and text[j] in " \t":
            j += 1
        if j < end and text[j] == ":":
            out.append((word, off))
    return out


def _decl_name_span(text: str, node, keyword: str, name: str):
    """``(line, idx)`` of a record/enum declaration name, or None."""
    rows = (text or "").split("\n")
    line0 = max(0, int(getattr(node, "line", 1)) - 1)
    if line0 < len(rows):
        idx = _name_pos(rows[line0], max(0, int(getattr(node, "col", 1)) - 1),
                        name)
        if idx is not None:
            return line0, idx
    for line0 in range(len(rows)):
        idx = _name_pos(rows[line0], 0, name)
        if idx is not None:
            return line0, idx
    return None


def _word_span_at(text: str, line: int, character: int):
    row = _line_text(text, line)
    if not row:
        return None
    pos = utf16_to_index(row, max(0, character))
    for match in WORD_RE.finditer(row):
        if match.start() <= pos <= match.end():
            return match.start(), match.group(0)
    return None


def _masked_text(text: str) -> str:
    """Same-length text with string/comment contents blanked out."""
    chars = list(text or "")
    i = 0
    quote = None
    n = len(chars)
    while i < n:
        ch = chars[i]
        if quote:
            if ch == "\\":
                chars[i] = " "
                if i + 1 < n and chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if ch == quote:
                quote = None
            chars[i] = " "
            i += 1
            continue
        if ch == '"':
            quote = ch
            chars[i] = " "
        elif ch == "#":
            while i < n and chars[i] != "\n":
                chars[i] = " "
                i += 1
            continue
        i += 1
    return "".join(chars)


_DOTTED_CHAIN_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+)\b")


def _dotted_chains(text: str):
    """``[(parts, line0, idx_of_last_part)]`` for dotted expressions.

    Parse-free fallback used when a document contains syntax the core parser
    rejects (for example LSP-only ``alias.Enum.Variant`` match patterns).
    Operating on the masked text keeps offsets aligned with the document.
    """
    masked = _masked_text(text)
    out = []
    for match in _DOTTED_CHAIN_RE.finditer(masked):
        chain = match.group(1)
        parts = [part.strip() for part in chain.split(".") if part.strip()]
        if len(parts) < 2:
            continue
        words = list(WORD_RE.finditer(chain))
        if not words:
            continue
        off = match.start(1) + words[-1].start()
        line0, idx = _linecol_at(text, off)
        out.append((parts, line0, idx))
    return out


def _type_reference_spans(text: str, name: str):
    """All source offsets where a record/enum name is used as a type."""
    masked = _masked_text(text)
    out = []
    seen = set()
    pattern = re.compile(r"(?<![\w.])%s(?!\w)" % re.escape(name))
    for line0, row in enumerate(masked.split("\n")):
        for match in pattern.finditer(row):
            idx = match.start()
            before = row[:idx].rstrip()
            after = row[match.end():].lstrip()
            last = re.search(r"([A-Za-z_]\w*)$", before)
            word = last.group(1) if last else ""
            prev1 = before[-1:] if before else ""
            prev2 = before[-2:] if len(before) >= 2 else ""
            nxt = after[:1]
            used = False
            if word in ("record", "enum", "use"):
                used = True
            elif prev2 == "->" or prev1 == ":":
                used = True
            elif nxt in ("(", "{", "."):
                used = word not in ("fn", "let")
            if not used:
                continue
            key = (line0, idx)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _resolve_record_chain(text: str, parts, line: int, records, aliases):
    """Record owning the final chain segment (parts includes the member)."""
    if not parts or len(parts) < 2:
        return None
    type_name = _var_type_name(text, parts[0], line, records, aliases)
    record = _record_name_for(type_name, records, aliases)
    for part in parts[1:-1]:
        if record is None:
            return None
        fields = dict(records[record].fields)
        if part not in fields:
            return None
        record = _record_name_for(fields[part], records, aliases)
    return record


def _document_context(uri: str, records, aliases):
    """Type-resolution context for the document under the cursor."""
    return {"uri": uri, "records": records, "aliases": aliases,
            "path": None, "import": None}


def _module_context(file_uri: str, path):
    """Type-resolution context for an imported module, or ``None``.

    Uses the same confined declarations loader as the rest of cross-file
    navigation, so flattened plain imports contribute their records/enums
    while alias imports are resolved relative to the module file itself.
    """
    decls = _module_declarations(file_uri, path)
    target = _resolve_module(file_uri, path or "")
    if decls is None or target is None:
        return None
    records = {}
    enums = {}
    for node in decls:
        if isinstance(node, G.RecordDecl):
            records.setdefault(node.name, node)
        elif isinstance(node, G.EnumDecl):
            enums.setdefault(node.name, node)
    try:
        body = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        body = ""
    return {"uri": target.as_uri(), "records": records,
            "aliases": _alias_map(body), "path": target, "import": None,
            "enums": enums}


def _resolve_type_in_context(ctx, type_name):
    """Resolve a type annotation within a document/module context.

    Returns ``{"owner", "decl", "local", "path", "import"}``. ``local`` is
    relative to the original document (``path`` is ``None``); records reached
    through an alias carry the resolved ``path`` plus their ``import``.
    Aliased heads never fall back to an unrelated record with the same tail
    name.
    """
    if not type_name or ctx is None:
        return None
    records = ctx["records"]
    aliases = ctx["aliases"]
    if type_name in records:
        return {"owner": type_name, "decl": records[type_name],
                "local": ctx["path"] is None, "path": ctx["path"],
                "import": ctx["import"]}
    if "." not in type_name:
        return None
    head, _sep, tail = type_name.partition(".")
    if not tail:
        return None
    if head in aliases:
        imp = aliases[head]
        decl = _module_record(ctx["uri"], imp.get("path"), tail)
        target = _resolve_module(ctx["uri"], imp.get("path") or "")
        if decl is None or target is None:
            return None
        return {"owner": tail, "decl": decl, "local": False,
                "path": target, "import": imp}
    if "." in tail:
        return None
    if tail in records:
        return {"owner": tail, "decl": records[tail],
                "local": ctx["path"] is None, "path": ctx["path"],
                "import": ctx["import"]}
    return None


def _context_for_ref(ref, ctx):
    """Context in which ``ref``'s own field types must be resolved."""
    if ref is None or ctx is None:
        return None
    if ref.get("local"):
        return ctx
    imp = ref.get("import")
    if not imp:
        # Bare record declared in the current context's own module.
        return ctx
    return _module_context(ctx["uri"], imp.get("path"))


def _resolve_record_ref(uri: str, text: str, type_name, records, aliases):
    """Resolve a type annotation to a local or imported record declaration.

    Returns ``{"owner", "decl", "local"}`` for local records and adds
    ``"path"``/``"import"`` for records reached through an import alias.
    Aliased heads never fall back to an unrelated local record with the same
    tail name, and missing modules stay unresolved.
    """
    return _resolve_type_in_context(
        _document_context(uri, records, aliases), type_name)


def _resolve_record_chain_ref(uri: str, text: str, parts, line: int,
                              records, aliases):
    """Record owning the final chain segment, local or imported.

    ``parts`` includes the member under the cursor. Returns the same mapping
    as :func:`_resolve_record_ref`, or ``None`` when any step is unknown.
    While descending, field types are resolved in the context of the module
    that declares the current record, so a chain such as ``s.a.x`` works when
    ``s`` has an imported type and ``a`` is unqualified inside that module.
    """
    if not parts or len(parts) < 2:
        return None
    ctx = _document_context(uri, records, aliases)
    type_name = _var_type_name(text, parts[0], line, records, aliases)
    ref = _resolve_type_in_context(ctx, type_name)
    for part in parts[1:-1]:
        if ref is None:
            return None
        fields = dict(ref["decl"].fields)
        if part not in fields:
            return None
        ctx = _context_for_ref(ref, ctx)
        if ctx is None:
            return None
        ref = _resolve_type_in_context(ctx, fields[part])
    return ref


def _member_offset(text: str, node, part_index: int):
    line0 = max(0, int(getattr(node, "line", 1)) - 1)
    rows = (text or "").split("\n")
    if line0 >= len(rows):
        return None
    row = rows[line0]
    base = max(0, int(getattr(node, "col", 1)) - 1)
    offsets = [match.start() for match in WORD_RE.finditer(row, base)]
    if part_index < len(offsets):
        return offsets[part_index]
    return None


def _field_reference_spans(text: str, owner: str, field: str):
    records = _record_decls(text)
    if owner not in records:
        return []
    aliases = _alias_map(text)
    found = set()
    span = _record_field_spans(text, records[owner]).get(field)
    if span is not None:
        found.add(_linecol_at(text, span))
    program = _parse_or_none(text)
    if program is None:
        for parts, line0, idx in _dotted_chains(text):
            if not parts or parts[-1] != field:
                continue
            resolved = _resolve_record_chain(
                text, parts, line0, records, aliases)
            if resolved == owner:
                found.add((line0, idx))
        return sorted(found)
    for node in _walk(program):
        name = getattr(node, "name", None)
        if isinstance(node, (G.Var, G.Call)) and isinstance(name, str) \
                and "." in name:
            parts = [part for part in name.split(".") if part]
            resolved = _resolve_record_chain(
                text, parts, max(0, int(node.line) - 1), records, aliases)
            if resolved == owner and parts and parts[-1] == field:
                off = _member_offset(text, node, len(parts) - 1)
                if off is not None:
                    found.add((max(0, int(node.line) - 1), off))
        elif isinstance(node, G.RecordLit) and node.name == owner:
            for key, off in _record_lit_key_spans(text, node):
                if key == field:
                    found.add(_linecol_at(text, off))
    return sorted(found)


def _variant_reference_spans(text: str, owner: str, variant: str):
    masked = _masked_text(text)
    out = set()
    decls = _enum_decls(text)
    if owner in decls:
        for name, off in _enum_variant_spans(text, decls[owner]):
            if name == variant:
                out.add(_linecol_at(text, off))
    pattern = re.compile(
        r"\b%s\s*\.\s*%s\b" % (re.escape(owner), re.escape(variant)))
    for match in pattern.finditer(masked):
        sub = re.search(r"\b%s\b" % re.escape(variant), match.group(0))
        if sub is not None:
            out.add(_linecol_at(text, match.start() + sub.start()))
    # Bare variant uses are only safe to include when this variant has a
    # single local owner; otherwise the bare form is inherently ambiguous.
    if _enum_variant_owners(decls, variant) == [owner]:
        for off in _bare_variant_offsets(text, variant):
            out.add(_linecol_at(text, off))
    return sorted(out)


def _imported_field_spans(file_uri: str, body: str, target, owner: str,
                          field: str):
    """Offsets of ``var.field`` uses whose receiver has an imported type."""
    records = _record_decls(body)
    aliases = _alias_map(body)
    out = set()

    def consider(parts, line0, idx):
        if len(parts) < 2 or parts[-1] != field:
            return
        ref = _resolve_record_chain_ref(
            file_uri, body, parts, line0, records, aliases)
        if ref is None or ref.get("local"):
            return
        try:
            same_target = ref["path"].resolve() == target
        except (OSError, ValueError):
            same_target = False
        if not same_target or ref["owner"] != owner:
            return
        out.add((line0, idx))

    program = _parse_or_none(body)
    if program is None:
        for parts, line0, idx in _dotted_chains(body):
            consider(parts, line0, idx)
        return sorted(out)
    for node in _walk(program):
        name = getattr(node, "name", None)
        if not isinstance(node, (G.Var, G.Call)) \
                or not isinstance(name, str) or "." not in name:
            continue
        parts = [part for part in name.split(".") if part]
        if len(parts) < 2:
            continue
        off = _member_offset(body, node, len(parts) - 1)
        if off is not None:
            consider(parts, max(0, int(node.line) - 1), off)
    return sorted(out)


def _imported_variant_spans(file_uri: str, body: str, target, owner: str,
                            variant: str):
    """Offsets of ``alias.Owner.Variant`` uses in an importing document."""
    masked = _masked_text(body)
    out = set()
    for alias in _aliases_for_target(body, file_uri, target):
        pattern = re.compile(
            r"\b%s\s*\.\s*%s\s*\.\s*%s\b"
            % (re.escape(alias), re.escape(owner), re.escape(variant)))
        for match in pattern.finditer(masked):
            sub = re.search(r"\b%s\b" % re.escape(variant), match.group(0))
            if sub is not None:
                out.add(_linecol_at(body, match.start() + sub.start()))
    return sorted(out)


def _field_locations_across_files(uri: str, text: str, target, owner: str,
                                  field: str):
    """``[(uri, text, line0, idx)]`` for a record field across related files."""
    docs = _related_documents(uri, text)
    if target not in docs:
        try:
            body = target.read_text(encoding="utf-8")
            docs[target] = (body, target.as_uri())
        except (OSError, UnicodeError):
            return []
    out = []
    seen = set()
    for path, (body, file_uri) in docs.items():
        if path == target:
            spans = _field_reference_spans(body, owner, field)
        else:
            spans = _imported_field_spans(file_uri, body, target, owner, field)
        for line0, idx in spans:
            key = (file_uri, line0, idx)
            if key in seen:
                continue
            seen.add(key)
            out.append((file_uri, body, line0, idx))
    return out


def _variant_locations_across_files(uri: str, text: str, target, owner: str,
                                    variant: str):
    """``[(uri, text, line0, idx)]`` for an enum variant across files."""
    docs = _related_documents(uri, text)
    if target not in docs:
        try:
            body = target.read_text(encoding="utf-8")
            docs[target] = (body, target.as_uri())
        except (OSError, UnicodeError):
            return []
    out = []
    seen = set()
    for path, (body, file_uri) in docs.items():
        if path == target:
            spans = _variant_reference_spans(body, owner, variant)
        else:
            spans = _imported_variant_spans(
                file_uri, body, target, owner, variant)
        for line0, idx in spans:
            key = (file_uri, line0, idx)
            if key in seen:
                continue
            seen.add(key)
            out.append((file_uri, body, line0, idx))
    return out


def _symbol_at(uri: str, text: str, line: int, character: int):
    """Resolve the record/enum symbol under the cursor, or None."""
    aliases = _alias_map(text)
    found = _dotted_member_at(text, line, character)
    if found is not None:
        parts, member = found
        context = _context_text(text, line, character)
        records = _record_decls(context)
        enums = _enum_decls(context)
        if len(parts) == 1 and parts[0] in aliases:
            imp = aliases[parts[0]]
            node = _module_record(uri, imp.get("path"), member)
            if node is not None:
                target = _resolve_module(uri, imp.get("path") or "")
                return {"kind": "record_external", "name": member,
                        "decl": node, "path": target, "import": imp}
            enum = _module_enum(uri, imp.get("path"), member)
            if enum is not None:
                target = _resolve_module(uri, imp.get("path") or "")
                return {"kind": "enum_external", "name": member,
                        "decl": enum, "path": target, "import": imp}
            return None
        if len(parts) == 2 and parts[0] in aliases:
            imp = aliases[parts[0]]
            enum = _module_enum(uri, imp.get("path"), parts[1])
            target = _resolve_module(uri, imp.get("path") or "")
            if enum is not None and target is not None and \
                    member in {entry[0] for entry in enum.variants}:
                return {"kind": "variant_external", "name": member,
                        "owner": parts[1], "decl": enum,
                        "path": target, "import": imp}
            return None
        if len(parts) == 1 and parts[0] in enums:
            enum = enums[parts[0]]
            if member in {entry[0] for entry in enum.variants}:
                return {"kind": "variant", "name": member,
                        "owner": parts[0], "decl": enum}
            return None
        ref = _resolve_record_chain_ref(
            uri, text, parts + [member], line, records, aliases)
        if ref is None:
            ref = _resolve_record_chain_ref(
                uri, context, parts + [member], line, records, aliases)
        if ref is not None and member in dict(ref["decl"].fields):
            if ref.get("local"):
                return {"kind": "field", "name": member,
                        "owner": ref["owner"], "decl": ref["decl"]}
            return {"kind": "field_external", "name": member,
                    "owner": ref["owner"], "decl": ref["decl"],
                    "path": ref["path"], "import": ref["import"]}
        return None

    span = _word_span_at(text, line, character)
    if span is None:
        return None
    idx, name = span

    member = _namespaced_member(text, line, character)
    if member is not None:
        alias, member_name = member
        imp = aliases.get(alias)
        if imp is not None:
            node = _module_record(uri, imp.get("path"), member_name)
            if node is not None:
                target = _resolve_module(uri, imp.get("path") or "")
                return {"kind": "record_external", "name": member_name,
                        "decl": node, "path": target, "import": imp}
            enum = _module_enum(uri, imp.get("path"), member_name)
            if enum is not None:
                target = _resolve_module(uri, imp.get("path") or "")
                return {"kind": "enum_external", "name": member_name,
                        "decl": enum, "path": target, "import": imp}
            return None

    records = _record_decls(text)
    enums = _enum_decls(text)
    if name in records:
        return {"kind": "record", "name": name, "decl": records[name]}
    if name in enums:
        return {"kind": "enum", "name": name, "decl": enums[name]}

    for owner, decl in records.items():
        for field, off in _record_field_spans(text, decl).items():
            if _linecol_at(text, off) == (line, idx) and field == name:
                return {"kind": "field", "name": name,
                        "owner": owner, "decl": decl}
    for owner, decl in enums.items():
        for variant, off in _enum_variant_spans(text, decl):
            if _linecol_at(text, off) == (line, idx) and variant == name:
                return {"kind": "variant", "name": name,
                        "owner": owner, "decl": decl}

    bare = _bare_variant_at(text, line, character, enums)
    if bare is not None:
        return bare

    program = _parse_or_none(text)
    if program is not None:
        for node in _walk(program):
            if isinstance(node, G.RecordLit) and node.name in records:
                for key, off in _record_lit_key_spans(text, node):
                    if _linecol_at(text, off) == (line, idx) and key == name:
                        return {"kind": "field", "name": name,
                                "owner": node.name,
                                "decl": records[node.name]}
    return None


def _module_record(uri: str, path, name: str):
    if not path:
        return None
    for node in _module_declarations(uri, path) or []:
        if isinstance(node, G.RecordDecl) and node.name == name:
            return node
    return None


def _module_enum(uri: str, path, name: str):
    if not path:
        return None
    for node in _module_declarations(uri, path) or []:
        if isinstance(node, G.EnumDecl) and node.name == name:
            return node
    return None


_RELATED_MAX_FILES = 200


def _related_documents(uri: str, text: str):
    """Current document plus every file reachable through its imports.

    Returns ``{resolved_path: (text, uri)}``. Only files that resolve through
    the same confined module loader as the core are included, and the
    in-memory document text always wins over what is on disk.
    """
    docs = {}
    root = _doc_root(uri)
    if root is None:
        return docs
    current = uri_path(uri)
    if current:
        try:
            docs[Path(current).resolve()] = (text or "", uri)
        except (OSError, ValueError):
            pass
    queue = [(uri, text or "")]
    seen_uris = {uri}
    while queue and len(docs) < _RELATED_MAX_FILES:
        file_uri, body = queue.pop(0)
        for imp in _imports(body):
            target = _resolve_module(file_uri, imp.get("path") or "")
            if target is None:
                continue
            try:
                key = target.resolve()
                target_uri = key.as_uri()
            except (OSError, ValueError):
                continue
            if key in docs or target_uri in seen_uris:
                continue
            try:
                if key.stat().st_size > 4 * 1024 * 1024:
                    continue
                target_text = key.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            docs[key] = (target_text, target_uri)
            seen_uris.add(target_uri)
            queue.append((target_uri, target_text))
    # Direct siblings let navigation started on a declaration find importing
    # documents in the same document root. Deeper trees are not walked.
    try:
        siblings = sorted(root.glob("*.gm"))
    except (OSError, ValueError):
        siblings = []
    for candidate in siblings:
        if len(docs) >= _RELATED_MAX_FILES:
            break
        try:
            key = candidate.resolve()
            sibling_uri = key.as_uri()
        except (OSError, ValueError):
            continue
        if key in docs or sibling_uri in seen_uris:
            continue
        try:
            if not key.is_file() or key.stat().st_size > 4 * 1024 * 1024:
                continue
            body = key.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        docs[key] = (body, sibling_uri)
        seen_uris.add(sibling_uri)
    return docs


def _aliases_for_target(body: str, file_uri: str, target):
    """Aliases in ``body`` whose import resolves to ``target``."""
    out = []
    for imp in _imports(body):
        alias = imp.get("alias")
        if not alias:
            continue
        resolved = _resolve_module(file_uri, imp.get("path") or "")
        if resolved is None:
            continue
        try:
            if resolved.resolve() != target:
                continue
        except (OSError, ValueError):
            continue
        if alias not in out:
            out.append(alias)
    return out


def _qualified_symbol_spans(text: str, alias: str, name: str):
    """Source offsets of the ``name`` part of ``alias.name`` references."""
    masked = _masked_text(text)
    pattern = re.compile(
        r"\b%s\s*\.\s*%s\b" % (re.escape(alias), re.escape(name)))
    out = set()
    for match in pattern.finditer(masked):
        sub = re.search(r"\b%s\b" % re.escape(name), match.group(0))
        if sub is not None:
            out.add(_linecol_at(text, match.start() + sub.start()))
    return sorted(out)


def _type_locations_across_files(uri: str, text: str, target, name: str):
    """``[(uri, text, line0, idx)]`` for a record/enum across related files."""
    docs = _related_documents(uri, text)
    if target not in docs:
        try:
            body = target.read_text(encoding="utf-8")
            docs[target] = (body, target.as_uri())
        except (OSError, UnicodeError):
            return []
    out = []
    seen = set()
    for path, (body, file_uri) in docs.items():
        if path == target:
            spans = _type_reference_spans(body, name)
        else:
            spans = []
            for alias in _aliases_for_target(body, file_uri, target):
                spans.extend(_qualified_symbol_spans(body, alias, name))
        for line0, idx in spans:
            key = (file_uri, line0, idx)
            if key in seen:
                continue
            seen.add(key)
            out.append((file_uri, body, line0, idx))
    return out


def _declared_names_in_text(text: str):
    """Top-level declaration names in a module, best effort."""
    names = set(_record_decls(text)) | set(_enum_decls(text))
    for node in _declarations(text):
        if getattr(node, "name", None):
            names.add(node.name)
    return names


def _location(uri: str, text: str, line0: int, idx: int, width: int):
    rows = (text or "").split("\n")
    if line0 < 0 or line0 >= len(rows) or idx < 0:
        return None
    col = _utf16_pos(rows[line0], idx)
    return {"uri": uri, "range": {
        "start": {"line": line0, "character": col},
        "end": {"line": line0, "character": col + utf16_len(width * "x")},
    }}


def _structural_definition(uri: str, text: str, line: int, character: int):
    sym = _symbol_at(uri, text, line, character)
    if sym is None:
        return None
    kind = sym["kind"]
    if kind in ("record_external", "enum_external"):
        target = sym.get("path")
        if target is None:
            return None
        try:
            target_text = target.read_text(encoding="utf-8")
        except OSError:
            return None
        keyword = "record" if kind == "record_external" else "enum"
        span = _decl_name_span(target_text, sym["decl"], keyword,
                               sym["name"])
        if span is None:
            return None
        return _location(target.as_uri(), target_text, span[0], span[1],
                         len(sym["name"]))
    if kind == "record":
        span = _decl_name_span(text, sym["decl"], "record", sym["name"])
    elif kind == "enum":
        span = _decl_name_span(text, sym["decl"], "enum", sym["name"])
    elif kind == "field":
        off = _record_field_spans(text, sym["decl"]).get(sym["name"])
        span = _linecol_at(text, off) if off is not None else None
    elif kind == "variant":
        span = None
        for name, off in _enum_variant_spans(text, sym["decl"]):
            if name == sym["name"]:
                span = _linecol_at(text, off)
                break
    elif kind in ("field_external", "variant_external"):
        target = sym.get("path")
        if target is None:
            return None
        try:
            target_text = target.read_text(encoding="utf-8")
        except OSError:
            return None
        span = None
        if kind == "field_external":
            off = _record_field_spans(target_text, sym["decl"]).get(sym["name"])
            if off is not None:
                span = _linecol_at(target_text, off)
        else:
            for name, off in _enum_variant_spans(target_text, sym["decl"]):
                if name == sym["name"]:
                    span = _linecol_at(target_text, off)
                    break
        if span is None:
            return None
        return _location(target.as_uri(), target_text, span[0], span[1],
                         len(sym["name"]))
    else:
        return None
    if span is None:
        return None
    return _location(uri, text, span[0], span[1], len(sym["name"]))


def _structural_references(uri: str, text: str, line: int, character: int):
    sym = _symbol_at(uri, text, line, character)
    if sym is None:
        return None
    kind = sym["kind"]
    if kind in ("record", "enum", "record_external", "enum_external"):
        if kind in ("record_external", "enum_external"):
            target = sym.get("path")
            if target is None:
                return []
        else:
            current = uri_path(uri)
            if not current:
                return []
            target = Path(current).resolve()
        locations = _type_locations_across_files(
            uri, text, target, sym["name"])
        out = []
        for file_uri, body, line0, idx in locations:
            loc = _location(file_uri, body, line0, idx, len(sym["name"]))
            if loc is not None:
                out.append(loc)
        return out
    if kind in ("field", "field_external"):
        if kind == "field_external":
            target = sym.get("path")
            if target is None:
                return []
        else:
            current = uri_path(uri)
            if not current:
                spans = _field_reference_spans(text, sym["owner"], sym["name"])
                return [loc for loc in (
                    _location(uri, text, line0, idx, len(sym["name"]))
                    for line0, idx in spans) if loc is not None]
            target = Path(current).resolve()
        locations = _field_locations_across_files(
            uri, text, target, sym["owner"], sym["name"])
    elif kind in ("variant", "variant_external"):
        if kind == "variant_external":
            target = sym.get("path")
            if target is None:
                return []
        else:
            current = uri_path(uri)
            if not current:
                spans = _variant_reference_spans(
                    text, sym["owner"], sym["name"])
                return [loc for loc in (
                    _location(uri, text, line0, idx, len(sym["name"]))
                    for line0, idx in spans) if loc is not None]
            target = Path(current).resolve()
        locations = _variant_locations_across_files(
            uri, text, target, sym["owner"], sym["name"])
    else:
        return None
    out = []
    for file_uri, body, line0, idx in locations:
        loc = _location(file_uri, body, line0, idx, len(sym["name"]))
        if loc is not None:
            out.append(loc)
    return out


def _single_file_changes(uri: str, text: str, spans, new_name: str, width: int):
    edits = []
    for line0, idx in spans:
        loc = _location(uri, text, line0, idx, width)
        if loc is None:
            continue
        edits.append({"range": loc["range"], "newText": new_name})
    return {"changes": {uri: edits}} if edits else None


def _structural_rename(uri: str, text: str, line: int, character: int,
                       new_name: str):
    if not ALIAS_RE.match(new_name or "") or new_name in G.KEYWORDS:
        return None
    sym = _symbol_at(uri, text, line, character)
    if sym is None:
        return None
    kind = sym["kind"]
    records = _record_decls(text)
    enums = _enum_decls(text)
    if kind in ("record", "enum", "record_external", "enum_external"):
        if kind in ("record_external", "enum_external"):
            target = sym.get("path")
            if target is None:
                return None
            try:
                target_text = target.read_text(encoding="utf-8")
            except OSError:
                return None
            taken = _declared_names_in_text(target_text)
        else:
            current = uri_path(uri)
            if not current:
                return None
            target = Path(current).resolve()
            taken = set(records) | set(enums) | set(doc_names(text))
        if new_name in taken or new_name in TYPE_HOVERS \
                or new_name in G.BUILTINS:
            return None
        locations = _type_locations_across_files(
            uri, text, target, sym["name"])
        if not locations:
            return None
        changes = {}
        for file_uri, body, line0, idx in locations:
            loc = _location(file_uri, body, line0, idx, len(sym["name"]))
            if loc is None:
                continue
            changes.setdefault(file_uri, []).append(
                {"range": loc["range"], "newText": new_name})
        if not changes:
            return None
        return {"changes": changes}
    if kind in ("field", "field_external"):
        declared = {name for name, _type in sym["decl"].fields}
        if new_name in declared:
            return None
        if kind == "field_external":
            target = sym.get("path")
            if target is None:
                return None
        else:
            current = uri_path(uri)
            if not current:
                spans = _field_reference_spans(text, sym["owner"], sym["name"])
                return _single_file_changes(
                    uri, text, spans, new_name, len(sym["name"]))
            target = Path(current).resolve()
        locations = _field_locations_across_files(
            uri, text, target, sym["owner"], sym["name"])
    elif kind in ("variant", "variant_external"):
        declared = {entry[0] for entry in sym["decl"].variants}
        if new_name in declared:
            return None
        if kind == "variant_external":
            target = sym.get("path")
            if target is None:
                return None
        else:
            current = uri_path(uri)
            if not current:
                spans = _variant_reference_spans(
                    text, sym["owner"], sym["name"])
                return _single_file_changes(
                    uri, text, spans, new_name, len(sym["name"]))
            target = Path(current).resolve()
        locations = _variant_locations_across_files(
            uri, text, target, sym["owner"], sym["name"])
    else:
        return None
    if not locations:
        return None
    changes = {}
    for file_uri, body, line0, idx in locations:
        loc = _location(file_uri, body, line0, idx, len(sym["name"]))
        if loc is None:
            continue
        changes.setdefault(file_uri, []).append(
            {"range": loc["range"], "newText": new_name})
    if not changes:
        return None
    return {"changes": changes}


def prepare_rename(uri: str, text: str, line: int, character: int):
    """Range and placeholder for a safe record/enum symbol rename, or None."""
    sym = _symbol_at(uri, text, line, character)
    if sym is None:
        return None
    span = _word_span_at(text, line, character)
    if span is None:
        return None
    idx, name = span
    loc = _location(uri, text, line, idx, len(name))
    if loc is None:
        return None
    return {"range": loc["range"], "placeholder": sym["name"]}


def definition(uri: str, text: str, line: int, character: int):
    structural = _structural_definition(uri, text, line, character)
    if structural is not None:
        return structural
    if _dotted_member_at(text, line, character) is not None \
            or _namespaced_member(text, line, character) is not None:
        return None
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
    structural = _structural_references(uri, text, line, character)
    if structural is not None:
        return structural
    if _dotted_member_at(text, line, character) is not None \
            or _namespaced_member(text, line, character) is not None:
        return []
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
    structural = _structural_rename(uri, text, line, character, new_name)
    if structural is not None:
        return structural
    if _symbol_at(uri, text, line, character) is not None \
            or _dotted_member_at(text, line, character) is not None \
            or _namespaced_member(text, line, character) is not None:
        return None
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


def _lookup_with_line_fallback(text: str, name: str, lookup, line=None):
    """Run ``lookup`` on the document, tolerating one incomplete line.

    Live editing requests often arrive while the current or last line is
    syntactically incomplete. The whole-document parse then fails, so blank
    one likely-incomplete line at a time (cursor line first, then the last
    line, then the rest) until the rest of the declarations parse.
    """
    found = lookup(text, name)
    if found is not None:
        return found
    # The document parsed cleanly, so the symbol is genuinely absent; do not
    # re-parse once per line just to confirm a miss.
    if _parse_or_none(text) is not None:
        return None
    rows = (text or "").split("\n")
    candidates = []
    if line is not None:
        candidates.append(line)
    if rows:
        candidates.append(len(rows) - 1)
    candidates.extend(range(len(rows) - 1, -1, -1))
    seen = set()
    for candidate in candidates:
        if candidate in seen or not (0 <= candidate < len(rows)):
            continue
        seen.add(candidate)
        if len(seen) > 500:
            break
        found = lookup(_document_without_line(text, candidate), name)
        if found is not None:
            return found
    return None


def _decl_by_name_at(text: str, name: str, line: int | None = None):
    """Look up a declaration, tolerating an incomplete line being edited."""
    return _lookup_with_line_fallback(text, name, _decl_by_name, line)


def _param_by_name_at(text: str, name: str, line: int | None = None):
    return _lookup_with_line_fallback(text, name, _param_by_name, line)


def _decl_hover(text: str, name: str, line: int | None = None):
    node = _decl_by_name_at(text, name, line)
    if node is None:
        return None
    if isinstance(node, G.Fn):
        return _fn_hover_text(node)
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
    for node in _module_declarations(uri, imp["path"]) or []:
        if node.name != name:
            continue
        if isinstance(node, G.Fn):
            return _fn_hover_text(
                node, intro="GAMA-L function from `%s`." % imp["path"],
                qualifier=alias)
        if isinstance(node, G.RecordDecl):
            return ("`%s`\n\nGAMA-L record from `%s` (imported as `%s`)."
                    % (_record_signature_text(node, alias),
                       imp["path"], alias))
        type_name = _const_type(node)
        value = _const_value(node)
        facts = []
        if type_name:
            facts.append("type `%s`" % type_name)
        if value is not None:
            facts.append("value `%s`" % value)
        detail = ", ".join(facts) if facts else "constant"
        return ("`%s.%s`\n\nGAMA-L constant from `%s` (%s)."
                % (alias, name, imp["path"], detail))
    return None


def hover(uri: str, text: str, line: int, character: int):
    name = word_at(text, line, character)
    if not name:
        return None
    value = _member_hover(uri, text, line, character)
    if value is None:
        value = _local_member_hover(text, line, character)
    if value is None:
        value = _structural_hover(text, name)
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
        value = _decl_hover(text, name, line)
    if value is None:
        param = _param_by_name_at(text, name, line)
        if param is not None:
            value = "GAMA-L parameter `%s`: `%s`" % (
                name, param.type_name or "Any")
    if value is None and name in _alias_map(text):
        imp = _alias_map(text)[name]
        value = ("GAMA-L namespace alias `%s` for `%s`; call members as "
                 "`%s.fn(...)`." % (name, imp["path"], name))
    if value is None and name in G.KEYWORDS:
        value = "GAMA-L keyword `%s`" % name
    if value is None and name == "record":
        value = "GAMA-L keyword `record`"
    if value is None:
        return None
    return {"contents": {"kind": "markdown", "value": value}}


def _call_context(prefix: str):
    """Return ``(callee, argument_text, type_args)`` for the open call.

    ``type_args`` is a list of explicit type arguments when the prefix looks
    like ``callee<T, U>(``. The core parser does not accept that syntax yet,
    so callers must only trust ``type_args`` once the base callee resolves to
    a generic ``fn`` declaration.
    """
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
    type_args = None
    if j >= 0 and prefix[j] == ">":
        depth = 0
        k = j
        while k >= 0:
            if prefix[k] == ">":
                depth += 1
            elif prefix[k] == "<":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if k >= 0:
            body = prefix[k + 1:j].strip()
            if body and TYPE_ARGS_RE.fullmatch(body):
                type_args = [part.strip() for part in body.split(",")]
                j = k - 1
                while j >= 0 and prefix[j] in " \t":
                    j -= 1
    end = j
    while j >= 0 and (prefix[j].isalnum() or prefix[j] in "_."):
        j -= 1
    callee = prefix[j + 1:end + 1]
    if not callee:
        return None
    return callee, prefix[open_idx + 1:], type_args


def _node_signature(node, type_args=None):
    """``(label, parameter labels, type parameters, parameter types)``."""
    names = _type_param_names(node)
    if type_args and names:
        mapping = dict(zip(names, type_args))
        params = ["%s: %s" % (p.name, mapping.get(p.type_name, p.type_name or "Any"))
                  for p in node.params]
        ret = mapping.get(node.ret, node.ret or "Any")
        label = signature_text(
            "%s<%s>" % (node.name, ", ".join(type_args)), params, ret,
            node.effects)
        return (label, params, names,
                [mapping.get(p.type_name, p.type_name) for p in node.params])
    return (fn_signature(node), [_param_label(p) for p in node.params],
            names, [p.type_name for p in node.params])


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


def _signature_help(signature, params, type_params=None, param_types=None):
    type_params = list(type_params or [])
    parameter_items = []
    for i, label in enumerate(params):
        item = {"label": label}
        type_name = (param_types[i]
                     if param_types is not None and i < len(param_types)
                     else None)
        if type_name and type_name in type_params:
            item["documentation"] = "type parameter `%s`" % type_name
        parameter_items.append(item)
    signature_info = {
        "label": signature,
        "parameters": parameter_items,
    }
    if type_params:
        signature_info["documentation"] = {
            "kind": "markdown",
            "value": "Type parameters: " + ", ".join(
                "`%s` (position %d)" % (name, i)
                for i, name in enumerate(type_params)),
        }
    return {
        "signatures": [signature_info],
        "activeSignature": 0,
        "activeParameter": 0,
    }


def signature_help(uri: str, text: str, line: int, character: int):
    prefix = _line_prefix(text, line, character)
    ctx = _call_context(prefix)
    if ctx is None:
        return None
    callee, args, type_args = ctx
    active = _active_parameter(args)
    sig = None

    if "." in callee:
        alias, member = callee.split(".", 1)
        imp = _alias_map(text).get(alias)
        if imp is not None:
            for node in _module_functions(uri, imp["path"]) or []:
                if node.name == member:
                    sig = _node_signature(node, type_args)
                    break
    elif callee in SIGNATURES:
        label, params = builtin_signature(callee)
        sig = (label, params, [], [])
    else:
        node = _decl_by_name_at(text, callee, line)
        if isinstance(node, G.Fn):
            sig = _node_signature(node, type_args)
        elif isinstance(node, G.Let) and isinstance(node.expr, G.Lambda):
            sig = (lambda_signature(node.expr, "let " + node.name),
                   [_param_label(p) for p in node.expr.params], [], [])
        elif node is not None:
            sig = ("%s(...)" % callee, [], [], [])
        else:
            param = _param_by_name_at(text, callee, line)
            if param is not None and param.type_name == "Function":
                sig = ("%s(...)" % callee, [], [], [])

    if sig is None:
        return None
    result = _signature_help(sig[0], sig[1], sig[2], sig[3])
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


def _type_param_names_in(text: str) -> set:
    """All generic type-parameter names declared in the document."""
    names = set()
    for node in _declarations(text):
        if isinstance(node, G.Fn):
            names.update(_type_param_names(node))
    return names


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


def _token_type(tok, fns, type_params=None):
    if tok.kind == "KW":
        return T_KEYWORD
    if tok.kind == "IDENT":
        if tok.val in TYPE_NAMES or (type_params and tok.val in type_params):
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
    type_params = _type_param_names_in(text)
    data = []
    prev_line = 0
    prev_start = 0
    for tok in toks:
        if tok.kind in ("NL", "EOF"):
            continue
        ttype = _token_type(tok, fns, type_params)
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
                    "renameProvider": {"prepareProvider": True},
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
        elif method == "textDocument/prepareRename":
            td = _obj(params.get("textDocument"))
            uri = td.get("uri") or ""
            text = docs.get(uri, td.get("text") or "")
            pos = _obj(params.get("position"))
            line = int(pos.get("line") or 0)
            ch = int(pos.get("character") or 0)
            send({"jsonrpc": "2.0", "id": mid,
                  "result": prepare_rename(uri, text, line, ch)})
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
