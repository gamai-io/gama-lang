#!/usr/bin/env python3
"""Canonical GAMA-L 2 formatter (new module, independent of gamal2.fmt_source).

Guarantees:
  * CRLF/CR -> LF, trailing whitespace removed, at most one blank line in a row.
  * Block indentation by ``{}`` with brace/comment/string awareness
    (braces inside strings or ``#`` comments never affect depth).
  * ``}`` and a leading ``else`` are outdented.
  * Whitespace normalization around operators only in code, never inside
    strings or comments, and never touching unary ``-``.
  * Line order and comment text are preserved; no token is added or removed.

Safety policy (documented trade-off):
  If a line cannot be normalized without a risk of changing semantics, it is
  left as close to the original as possible instead of being "prettified":
  lines that are part of a multi-line string literal are emitted verbatim.
"""
from __future__ import annotations

import re

__all__ = ["format_source"]

# Operators whose surrounding whitespace is normalized (outside strings/comments).
NORMALIZE = {
    "=", ":=", "->", "==", "!=", "<=", ">=", "&&", "||",
    "+", "-", "*", "/", "%", "<", ">",
}
# Operators that are unary in GAMA-L; they must not get a space after them.
UNARY = {"-", "!"}
# Token values that put a following "-" / "!" into a unary position.
OPEN_TOKENS = {
    "(", "[", "{", ",", ":", ";", "|", "=", ":=", "->", "==", "!=", "<=",
    ">=", "&&", "||", "+", "-", "*", "/", "%", "<", ">",
}
UNARY_CONTEXT_WORDS = {
    "let", "fn", "if", "else", "return", "print", "prove", "verify", "import",
    "effects", "filter", "select", "sum", "map", "count", "limit", "in",
}

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    | (?P<comment>\#[^\n]*)
    | (?P<word>[A-Za-z_][A-Za-z0-9_.]*|\d+(?:\.\d+)?)
    | (?P<op>->|:=|==|!=|<=|>=|&&|\|\||.)
    """,
    re.VERBOSE,
)


def _scan_string_state(line: str, quote: str | None) -> str | None:
    """Return the open quote (or None) after scanning ``line``.

    Tracks escapes; ``#`` outside a string starts a comment and stops the scan.
    """
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            break
        i += 1
    return quote


def _tokenize(line: str) -> list[tuple[str, str]]:
    """Split a single line into (kind, text) tokens; strings stay verbatim."""
    toks: list[tuple[str, str]] = []
    pos = 0
    while pos < len(line):
        m = _TOKEN_RE.match(line, pos)
        if m is None:  # pragma: no cover - regex has a catch-all
            toks.append(("op", line[pos]))
            pos += 1
            continue
        kind = m.lastgroup or "op"
        toks.append((kind, m.group()))
        pos = m.end()
    return toks


def _is_unary_context(prev: tuple[str, str] | None) -> bool:
    if prev is None:
        return True
    kind, tok = prev
    if kind == "op":
        return tok in OPEN_TOKENS
    if kind == "word":
        return tok in UNARY_CONTEXT_WORDS
    return False


def _render(toks: list[tuple[str, str]]) -> tuple[str, int, bool]:
    """Render tokens to text; return (text, brace_delta, has_content)."""
    pieces: list[str] = []
    prev: tuple[str, str] | None = None
    pending_space = False
    delta = 0
    for kind, tok in toks:
        if kind == "ws":
            pending_space = True
            continue
        if kind == "comment":
            if pieces and pieces[-1] != " ":
                pieces.append(" ")
            pieces.append(tok)
            pending_space = False
            continue
        if kind == "op":
            if tok == "{":
                delta += 1
            elif tok == "}":
                delta -= 1
        binary = (
            kind == "op"
            and tok in NORMALIZE
            and not (tok in UNARY and _is_unary_context(prev))
        )
        if binary:
            while pieces and pieces[-1] == " ":
                pieces.pop()
            if pieces:
                pieces.append(" ")
            pieces.append(tok)
            pieces.append(" ")
        else:
            if pending_space and pieces and pieces[-1] != " ":
                pieces.append(" ")
            pieces.append(tok)
        prev = (kind, tok)
        pending_space = False
    text = "".join(pieces).strip()
    return text, delta, bool(text)


def _leading_outdent(toks: list[tuple[str, str]]) -> int:
    for kind, tok in toks:
        if kind == "ws":
            continue
        if kind == "op" and tok == "}":
            return 1
        if kind == "word" and tok == "else":
            return 1
        return 0
    return 0


def format_source(src: str) -> str:
    """Format GAMA-L source canonically. Idempotent by construction."""
    text = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)

    out: list[str] = []
    depth = 0
    open_quote: str | None = None

    for line in lines:
        if open_quote is not None:
            # Continuation of a multi-line string: byte-for-byte verbatim.
            out.append(line)
            open_quote = _scan_string_state(line, open_quote)
            continue

        trailing_state = _scan_string_state(line, None)
        if trailing_state is not None:
            # Line opens a multi-line string: cannot reformat safely.
            out.append(line)
            open_quote = trailing_state
            continue

        stripped = line.rstrip()
        toks = _tokenize(stripped)
        rendered, delta, has_content = _render(toks)
        if not has_content:
            if out and out[-1] != "":
                out.append("")
            continue
        lead = _leading_outdent(toks)
        out.append("  " * max(0, depth - lead) + rendered)
        depth = max(0, depth + delta)

    return "\n".join(out) + "\n"
