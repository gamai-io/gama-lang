#!/usr/bin/env python3
"""Canonical GAMA-L 2 formatter.

This module now contains two formatters:

* **AST formatter** (default): parses the program with the real ``gamal2``
  lexer/parser and pretty-prints the AST.  It preserves comment text, keeps
  string literals byte-for-byte (including multi-line literals), keeps optional
  semicolons and the ``prove name { ... }`` / ``prove name = ...`` spelling, and
  is idempotent.  Two whitespace-accepted layouts are preserved where the
  parser discards the information: ``} else {`` on one line versus ``}`` and
  ``else {`` on separate lines.
* **Legacy token formatter** (fallback only): the previous line-oriented,
  token-based formatter.  It is used when the source cannot be lexed/parsed
  (``gamal2`` already parses before calling ``fmt``), so behaviour on invalid
  snippets stays close to the old implementation.

Guarantees of the AST formatter:
  * ``parse(format_source(x))`` is AST-equivalent to ``parse(x)`` for valid
    programs (line/column metadata excluded).
  * ``format_source(format_source(x)) == format_source(x)``.
  * CRLF/CR -> LF, trailing whitespace removed, at most one blank line in a row.
  * Block indentation is two spaces; ``}`` and ``else`` line up with the opener.
  * A function body written entirely on one source line stays on one line
    (historical LSP behaviour); every other block is expanded to one statement
    per line.  ``else`` keeps its same-line/next-line relationship to ``}``.
  * Comments are re-attached to the statement they precede (leading), kept
    inline when they were trailing on the same rendered line, and any comment
    that cannot be attached is emitted at the same block indentation instead of
    being dropped.
  * String literal contents are never rewritten: the raw source slice from the
    opening quote to the closing quote is emitted verbatim, so escapes and
    multi-line bodies survive exactly.  A block that opens on the same source
    line as a multi-line string opener keeps that first statement on the
    header line, so the continuation line cannot desynchronise block depth.
  * Record and enum declarations are canonicalised to one line when they fit
    and carry no interior comments (``record Name { field: Type, ... }`` /
    ``enum Name { Variant, ... }``); named record literals
    (``Point { x: 1, y: 2 }``) keep field order.

Line wrapping (added in this revision):
  * Expressions longer than ``width`` (default 88) are wrapped deterministically
    with two-space continuation indentation: list/map/record literals, record
    copy/`with` updates and call argument lists go one entry per line with the
    closing delimiter lined up
    with the opening expression, binary chains break after the operator, and
    pipes put each ``| stage`` on its own line.  Short constructs are emitted
    exactly as before (one line).
  * The same width applies to declarations and structural headers:
    ``record``/``enum`` declarations put one field/variant per line with the
    closing ``}`` at the declaration indent; ``fn``/``make`` headers put one
    parameter (and a long default expression) per line, keep
    ``) -> Ret effects {...}`` together and break a long ``effects`` set one
    name per line; ``if``/``while``/``for``/``match`` headers wrap their
    expression; ``prove name = expr`` / ``prove name { ... }`` wraps ``expr``.
  * No token is added or removed by wrapping (no trailing commas, no extra
    parentheses), so the semantic token stream is unchanged.  Binary wrapping
    relies on the parser accepting a newline after a binary operator, and
    header wrapping on the parser accepting newlines inside parameter and
    declaration braces.
  * Comments inside a wrapped literal/call/declaration stay on the entry they
    annotate (``1, # one``).  Comments between binary operands stay after the
    operator line they followed, comments inside a pipe stage stay on that
    stage, and comments on their own line are emitted just before the next
    entry at the continuation indent.  A short construct whose only reason to
    expand is an interior comment is expanded (``[1, # one`` and
    ``x | # c`` no longer force a safe-position move).
  * Comments on the closing delimiter line stay with the statement (trailing
    comment), as before.

Known limitations (documented, not hidden):
  * A comment with no legal line break at its position still moves to the
    nearest safe attachment point: after ``|`` and before a stage keyword
    (moves to the base line), before a binary operator (moves to the preceding
    operator/operand line), or between ``let x =`` and its expression (moves to
    the end of that statement or the next statement).  Comments are never
    dropped or duplicated.
  * Comments inside a ``fn`` ``effects`` block or an inline lambda body use the
    safe-position fallback; the construct itself still wraps.
  * Inline lambdas (``fn(...) { ... }`` used as an expression) are not
    width-wrapped; their comments use the safe-position fallback.
  * Indivisible tokens (a single identifier or string longer than ``width``)
    still overflow.
  * Map keys are re-rendered from their decoded string value: a quoted
    identifier key (``"total"``) becomes a bare key (``total``).  The map entry
    and lookup semantics are identical.
  * Duplicate map keys are already collapsed by the parser itself.
  * ``effects`` is stored as a ``set`` by the parser, so effect names are
    emitted in sorted order (the only deterministic order available).
  * Surface sugar that the parser lowers to a builtin call is emitted as a call:
    ``remember k = v`` -> ``remember("k", v)``, ``recall k`` -> ``recall("k")``,
    ``wait user x`` -> ``wait_user(x)``.  The AST is unchanged.
  * ``make name(...)`` is preserved when detected; otherwise ``fn`` is emitted.
"""
from __future__ import annotations

import re

try:  # normal package-relative import (tests put v2/ on sys.path)
    import gamal2 as _G
except Exception:  # pragma: no cover - only when imported oddly
    _G = None

__all__ = ["format_source"]

# ---------------------------------------------------------------------------
# Legacy token formatter (kept as an explicit fallback)
# ---------------------------------------------------------------------------

# Operators whose surrounding whitespace is normalized (outside strings/comments).
NORMALIZE = {
    "=", ":=", "->", "==", "!=", "<=", ">=", "&&", "||",
    "+", "-", "*", "/", "%", "<", ">",
}
UNARY = {"-", "!"}
POSTFIX = {"?"}
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


def _scan_braces(line: str, quote: str | None) -> tuple[int, str | None]:
    """Return ``(brace_delta, end_quote)`` for code outside strings/comments.

    Continues an already-open ``quote`` (from a multi-line string) so the rest
    of a line that closes a literal still contributes its ``{``/``}`` to the
    block depth.  This is what the old line-oriented formatter skipped when it
    emitted a multi-line string opener verbatim.
    """
    delta = 0
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            i += 1
            continue
        if c == "#":
            break
        if c == "{":
            delta += 1
        elif c == "}":
            delta -= 1
        i += 1
    return delta, quote


def _tokenize(line: str) -> list[tuple[str, str]]:
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
        if kind == "op" and tok in POSTFIX:
            while pieces and pieces[-1] == " ":
                pieces.pop()
            pieces.append(tok)
            prev = (kind, tok)
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


def _legacy_format_source(src: str) -> str:
    """Previous token/indentation formatter.  Fallback for invalid sources."""
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
            out.append(line)
            delta, open_quote = _scan_braces(line, open_quote)
            depth = max(0, depth + delta)
            continue

        trailing_state = _scan_string_state(line, None)
        if trailing_state is not None:
            # A multi-line string opener is emitted verbatim, but braces in the
            # code before the opening quote still belong to the block depth.
            out.append(line)
            delta, open_quote = _scan_braces(line, None)
            depth = max(0, depth + delta)
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


# ---------------------------------------------------------------------------
# AST formatter helpers
# ---------------------------------------------------------------------------

_UNIT = "  "

DEFAULT_WIDTH = 88

_PREC = {"||": 1, "&&": 2, "==": 3, "!=": 3,
         "<": 4, "<=": 4, ">": 4, ">=": 4,
         "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}
_PIPE_PREC = 0
_UNARY_PREC = 7
_POSTFIX_PREC = 8


class _Comment:
    __slots__ = ("line", "col", "text", "own_line")

    def __init__(self, line: int, col: int, text: str, own_line: bool):
        self.line = line
        self.col = col
        self.text = text
        self.own_line = own_line

    def __repr__(self):  # pragma: no cover - debugging aid
        return "_Comment(%d, %d, %r, own=%s)" % (
            self.line, self.col, self.text, self.own_line)


class _SpanParser(_G.Parser if _G is not None else object):
    """Parser subclass that records exact token spans per statement."""

    def __init__(self, toks):
        super().__init__(toks)
        self.spans = {}

    def statement(self, top=False):
        start = self.i
        node = super().statement(top=top)
        self.spans[id(node)] = (start, self.i)
        return node


def _bare_key_ok(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", key))


def _quote(text: str) -> str:
    buf = ['"']
    for ch in text:
        if ch == "\\":
            buf.append("\\\\")
        elif ch == '"':
            buf.append('\\"')
        elif ch == "\n":
            buf.append("\\n")
        elif ch == "\t":
            buf.append("\\t")
        elif ch == "\r":
            buf.append("\\r")
        else:
            buf.append(ch)
    buf.append('"')
    return "".join(buf)


class _AstFormatter:
    def __init__(self, src: str, width: int = DEFAULT_WIDTH):
        self.src = src
        self.width = max(20, int(width))
        self.lines = src.split("\n")
        offsets = [0]
        for line in self.lines[:-1]:
            offsets.append(offsets[-1] + len(line) + 1)
        self.offsets = offsets
        self.toks = list(_G.lex(src))
        self.parser = _SpanParser(self.toks)
        self.ast = self.parser.program()
        self.spans = self.parser.spans
        self.sig = [t for t in self.toks if t.kind not in ("NL", "EOF")]
        self.sig_lines = [t.line for t in self.sig]
        self.tok_at = {(t.line, t.col): t for t in self.sig}
        self.comments = self._collect_comments()
        self.used: set[int] = set()
        self.out: list[str] = []
        self.str_end: dict[tuple[int, int], int] = {}
        for t in self.sig:
            if t.kind == "STRING":
                raw, end_line = self._raw_string(t.line, t.col)
                if raw is not None:
                    self.str_end[(t.line, t.col)] = end_line

    # ------------------------------------------------------------- scanning
    def _collect_comments(self):
        comments: list[_Comment] = []
        line_has_code: dict[int, bool] = {}
        i, line, col, n = 0, 1, 1, len(self.src)
        quote = None
        while i < n:
            c = self.src[i]
            if c == "\n":
                line += 1
                col = 1
                i += 1
                continue
            if quote is not None:
                if c == "\\" and i + 1 < n:
                    i += 2
                    col += 2
                    continue
                if c == quote:
                    quote = None
                    line_has_code[line] = True
                i += 1
                col += 1
                continue
            if c in "\"'":
                quote = c
                line_has_code[line] = True
                i += 1
                col += 1
                continue
            if c == "#":
                j = self.src.find("\n", i)
                if j < 0:
                    j = n
                text = self.src[i:j].rstrip()
                comments.append(_Comment(line, col, text,
                                         not line_has_code.get(line, False)))
                col += j - i
                i = j
                continue
            if not c.isspace():
                line_has_code[line] = True
            i += 1
            col += 1
        return comments

    def _raw_string(self, line: int, col: int):
        """Return (raw literal including quotes, end line) or (None, line).

        ``gamal2`` records the position of a string token *after* the opening
        quote (its column points at the first content character), so the quote
        is at ``col - 2`` in 0-based text offsets.  Synthetic literals created by
        the parser (``remember``/``recall`` keys) point at a keyword and simply
        fall back to re-quoting their decoded value.
        """
        if line - 1 >= len(self.offsets):
            return None, line
        pos = self.offsets[line - 1] + (col - 2)
        if pos < 0 or pos >= len(self.src) or self.src[pos] not in "\"'":
            return None, line
        q = self.src[pos]
        end_line = line
        i = pos + 1
        while i < len(self.src):
            c = self.src[i]
            if c == "\\" and i + 1 < len(self.src):
                i += 2
                continue
            if c == "\n":
                end_line += 1
                i += 1
                continue
            if c == q:
                i += 1
                return self.src[pos:i], end_line
            i += 1
        return self.src[pos:i], end_line

    def _span(self, stmt):
        return self.spans.get(id(stmt))

    def _stmt_end_line(self, stmt) -> int:
        span = self._span(stmt)
        if not span:
            return getattr(stmt, "line", 1)
        start, end = span
        if end <= start:
            return getattr(stmt, "line", 1)
        last = self.toks[end - 1]
        return self.str_end.get((last.line, last.col), last.line)

    def _has_semicolon(self, stmt) -> bool:
        span = self._span(stmt)
        if not span:
            return False
        _start, end = span
        return (end < len(self.toks) and self.toks[end].kind == "OP"
                and self.toks[end].val == ";")

    def _pairs(self, start_line: int, end_line: int):
        """Top-level ``{...}`` pairs fully inside [start_line, end_line)."""
        pairs = []
        depth = 0
        cur = None
        for t in self.sig:
            if t.line < start_line:
                continue
            if t.line >= end_line:
                break
            if t.kind == "OP" and t.val == "{":
                if depth == 0:
                    cur = t
                depth += 1
            elif t.kind == "OP" and t.val == "}":
                if depth > 0:
                    depth -= 1
                if depth == 0 and cur is not None:
                    pairs.append((cur, t))
                    cur = None
        return pairs

    def _child_pairs(self, outer):
        """Direct ``{...}`` child pairs of a top-level pair, regardless of lines.

        ``_pairs`` works on line ranges, so it cannot see arms that share a
        line with the match opener (or the whole match written on one line).
        Walking tokens between the outer braces at depth 1 keeps the match
        renderer correct for compact sources without changing line-based
        block discovery elsewhere.
        """
        start = end = None
        for i, t in enumerate(self.sig):
            if t is outer[0]:
                start = i
            elif t is outer[1]:
                end = i
                break
        if start is None or end is None:
            return []
        pairs = []
        depth = 0
        cur = None
        for t in self.sig[start + 1:end]:
            if t.kind == "OP" and t.val == "{":
                if depth == 0:
                    cur = t
                depth += 1
            elif t.kind == "OP" and t.val == "}":
                if depth > 0:
                    depth -= 1
                if depth == 0 and cur is not None:
                    pairs.append((cur, t))
                    cur = None
        return pairs

    @staticmethod
    def _pick_body_pair(pairs, stmts, fallback_last=True):
        if not pairs:
            return None
        if stmts:
            first = min(s.line for s in stmts)
            last = max(s.line for s in stmts)
            found = [p for p in pairs
                     if p[0].line < first and p[1].line >= last]
            if found:
                return min(found, key=lambda p: p[1].line - p[0].line)
        return pairs[-1] if fallback_last else pairs[0]

    def _find_token(self, kind, val, start_line, end_line):
        for t in self.sig:
            if t.line < start_line:
                continue
            if t.line >= end_line:
                return None
            if t.kind == kind and t.val == val:
                return t
        return None

    # ------------------------------------------------------------- emitting
    def _emit(self, text: str, src_lines=(), attach: bool = True,
              suffix: str = ""):
        # A wrapped expression (or a multi-line string) arrives as a single
        # string containing newlines; keep one element per physical line so
        # comment attachment and blank-line handling stay line-accurate.
        parts = text.split("\n")
        for part in parts[:-1]:
            self.out.append(part)
        self.out.append(parts[-1] + suffix)
        if not attach:
            return
        for idx, c in enumerate(self.comments):
            if idx in self.used or c.own_line:
                continue
            if c.line in src_lines:
                self.used.add(idx)
                self.out[-1] += " " + c.text
                break

    def _maybe_blank(self):
        if self.out and self.out[-1] != "":
            self.out.append("")

    def _flush_before(self, prev_end: int, limit: int, indent: int,
                      low: int) -> int:
        text = _UNIT * indent
        for idx, c in enumerate(self.comments):
            if idx in self.used:
                continue
            if not (low < c.line < limit):
                continue
            if self.out and c.line - prev_end >= 2:
                self._maybe_blank()
            self._emit(text + c.text, (), attach=False)
            self.used.add(idx)
            prev_end = max(prev_end, c.line)
        return prev_end

    # ------------------------------------------------------------- wrapping
    @staticmethod
    def _pos_lt(a, b) -> bool:
        return a[0] < b[0] or (a[0] == b[0] and a[1] < b[1])

    def _span_for_container(self, node):
        G = _G
        if isinstance(node, G.ListExpr):
            return self._delim_span("[", node.line, node.col)
        if isinstance(node, (G.MapExpr, G.RecordLit)):
            return self._delim_span("{", node.line, node.col)
        if isinstance(node, G.RecordUpdate):
            # ``node.line/col`` points at the ``with`` token, so the first
            # following ``{`` is the update's own brace (not a base literal).
            return self._delim_span("{", node.line, node.col)
        if isinstance(node, (G.Call, G.CallExpr)):
            return self._delim_span("(", node.line, node.col)
        return None

    def _delim_span(self, open_val, line, col):
        """Return (open_line, open_col, close_line, close_col) of a delimiter.

        The token at ``(line, col)`` may be a container's opening bracket, a
        call name or a record name; the first ``open_val`` token at or after
        that position on the same line is the opener, then brackets are
        balanced (all three kinds) to find the matching close.
        """
        open_idx = None
        for i, t in enumerate(self.sig):
            if t.line < line:
                continue
            if t.line > line:
                break
            if t.col >= col and t.kind == "OP" and t.val == open_val:
                open_idx = i
                break
        if open_idx is None:
            return None
        depth = 0
        for t in self.sig[open_idx:]:
            if t.kind != "OP":
                continue
            if t.val in "([{":
                depth += 1
            elif t.val in ")]}":
                depth -= 1
                if depth == 0:
                    ot = self.sig[open_idx]
                    return (ot.line, ot.col, t.line, t.col)
        return None

    def _expr_start_pos(self, node):
        G = _G
        if isinstance(node, G.Binary):
            return self._expr_start_pos(node.left)
        if isinstance(node, G.Unary):
            return (node.line, node.col)
        if isinstance(node, G.Attr):
            return self._expr_start_pos(node.base)
        if isinstance(node, G.RecordUpdate):
            return self._expr_start_pos(node.base)
        if isinstance(node, G.Index):
            return self._expr_start_pos(node.base)
        if isinstance(node, G.Try):
            return self._expr_start_pos(node.expr)
        if isinstance(node, G.CallExpr):
            return self._expr_start_pos(node.callee)
        if isinstance(node, G.Pipe):
            return self._expr_start_pos(node.base)
        return (getattr(node, "line", 0), getattr(node, "col", 0))

    def _expr_end_pos(self, node):
        G = _G
        if isinstance(node, G.Literal) and isinstance(node.value, str):
            raw, end_line = self._raw_string(node.line, node.col)
            if raw is not None and raw[:1] in "\"'":
                start = self.offsets[node.line - 1] + (node.col - 2)
                end_off = start + len(raw)
                end_col = end_off - self.offsets[end_line - 1] + 1
                return (end_line, end_col)
            return (node.line, node.col + len(str(node.value)))
        if isinstance(node, G.Literal):
            return (node.line, node.col + len(self._literal(node)))
        if isinstance(node, G.Var):
            return (node.line, node.col + len(node.name))
        if isinstance(node, G.Attr):
            return (node.line, node.col + len(node.name))
        if isinstance(node, G.Unary):
            return self._expr_end_pos(node.right)
        if isinstance(node, G.Binary):
            return self._expr_end_pos(node.right)
        if isinstance(node, G.Index):
            span = self._delim_span("[", node.line, node.col)
            if span:
                return (span[2], span[3])
            return (node.line, node.col + 1)
        if isinstance(node, G.Try):
            return (node.line, node.col + 1)
        span = self._span_for_container(node)
        if span is not None:
            return (span[2], span[3])
        if isinstance(node, G.Lambda):
            if node.body:
                return (self._stmt_end_line(node.body[-1]), 10 ** 6)
            return (node.line, 10 ** 6)
        if isinstance(node, G.Pipe):
            try:
                end, _stages = self._pipe_positions(node)
                return end
            except ValueError:
                return (node.line, 10 ** 6)
        return (getattr(node, "line", 0), 10 ** 6)

    def _interior_comments(self, span):
        oline, ocol, cline, ccol = span
        open_pos, close_pos = (oline, ocol), (cline, ccol)
        out = []
        for idx, c in enumerate(self.comments):
            if idx in self.used:
                continue
            pos = (c.line, c.col)
            if self._pos_lt(open_pos, pos) and self._pos_lt(pos, close_pos):
                out.append((idx, c))
        return out

    def _flush_comment_range(self, lo, hi, indent, result, inline_lines,
                             attach_line=None, attach_src_line=None):
        """Move unused comments in (lo, hi) into ``result`` in source order.

        A single-line comment on ``attach_src_line`` is appended to the
        already-emitted ``attach_line`` (its attachment point); anything else
        becomes its own line at ``indent``.
        """
        for idx, c in enumerate(self.comments):
            if idx in self.used:
                continue
            pos = (c.line, c.col)
            if not self._pos_lt(lo, pos):
                continue
            if not self._pos_lt(pos, hi):
                break
            if (not c.own_line and attach_line is not None
                    and attach_src_line == c.line
                    and attach_line not in inline_lines):
                result[attach_line] += " " + c.text
                inline_lines.add(attach_line)
            else:
                result.append(" " * indent + c.text)
            self.used.add(idx)

    def _fits(self, text: str, base_col: int) -> bool:
        return "\n" not in text and base_col + len(text) <= self.width

    def _binary_parts(self, node):
        """Flatten a left-associative same-precedence chain.

        Returns ``(parts, prec)`` where each part is
        ``(operator, right_operand, operator_node)`` and the first part has
        ``operator is None``.  Flattening keeps the original token order.
        """
        G = _G
        p = _PREC[node.op]
        out = []

        def walk(n):
            if isinstance(n, G.Binary) and _PREC[n.op] == p:
                walk(n.left)
                out.append((n.op, n.right, n))
            else:
                out.append((None, n, None))

        walk(node)
        return out, p

    def _binary_has_comment(self, node) -> bool:
        parts, _p = self._binary_parts(node)
        first_pos = self._expr_start_pos(parts[0][1])
        last_pos = self._expr_end_pos(parts[-1][1])
        for idx, c in enumerate(self.comments):
            if idx in self.used:
                continue
            pos = (c.line, c.col)
            if not (self._pos_lt(first_pos, pos) and self._pos_lt(pos, last_pos)):
                continue
            if c.own_line:
                return True
        for op, right, opnode in parts[1:]:
            if opnode is None:
                continue
            rs = self._expr_start_pos(right)
            op_pos = (opnode.line, opnode.col)
            for idx, c in enumerate(self.comments):
                if idx in self.used:
                    continue
                pos = (c.line, c.col)
                if not self._pos_lt(op_pos, pos):
                    continue
                if (c.line == rs[0] and self._pos_lt(pos, rs)) or \
                        c.line < rs[0]:
                    return True
        return False

    def _expr_has_comment(self, node) -> bool:
        """True if an expansion can keep an interior comment in place.

        A container (list/map/record/call) owns every comment between its
        delimiters, a binary owns comments around its operators, and a pipe
        owns comments between its stages.  Binary children are checked
        recursively so a comment inside a nested container still forces the
        enclosing binary to expand (the container is then expanded by
        ``_wrap``), instead of being moved to a safe position.
        """
        G = _G
        span = self._span_for_container(node)
        if span is not None and self._interior_comments(span):
            return True
        if isinstance(node, G.Binary):
            return (self._binary_has_comment(node)
                    or self._expr_has_comment(node.left)
                    or self._expr_has_comment(node.right))
        if isinstance(node, G.Pipe):
            return self._pipe_has_comment(node)
        return False

    def _force_expand(self, node) -> bool:
        return self._expr_has_comment(node)

    def _wrap(self, node, close_indent: int, base_col: int,
              minp: int = 0) -> str:
        """Render ``node``, wrapping when it exceeds the configured width.

        The first returned line has no indentation (the caller positions it);
        continuation lines already carry absolute two-space indentation.
        """
        G = _G
        text = self._expr(node, minp)
        if self._fits(text, base_col) and not self._force_expand(node):
            return text
        if isinstance(node, (G.ListExpr, G.MapExpr, G.RecordLit,
                             G.RecordUpdate, G.Call, G.CallExpr)):
            return self._wrap_container(node, close_indent)
        if isinstance(node, G.Binary):
            return self._wrap_binary(node, close_indent, base_col, minp)
        if isinstance(node, G.Pipe):
            return self._wrap_pipe(node, close_indent, base_col, minp)
        return text

    def _wrap_container(self, node, close_indent) -> str:
        G = _G
        inner = close_indent + 2

        def lit_renderer(item):
            return lambda ii, bc: self._wrap(item, ii, bc, 0)

        def map_renderer(key, val):
            k = key if _bare_key_ok(key) else _quote(key)
            return lambda ii, bc: (
                k + ": " + self._wrap(val, ii, ii + len(k) + 2, 0))

        def update_renderer(key, val):
            k = key if _bare_key_ok(key) else _quote(key)
            return lambda ii, bc: (
                k + " = " + self._wrap(val, ii, ii + len(k) + 3, 0))

        if isinstance(node, G.ListExpr):
            open_text, close_text = "[", "]"
            entries = [(self._expr_start_pos(it), self._expr_end_pos(it),
                        lit_renderer(it)) for it in node.items]
        elif isinstance(node, G.MapExpr):
            open_text, close_text = "{", "}"
            entries = [(self._expr_start_pos(v), self._expr_end_pos(v),
                        map_renderer(k, v)) for k, v in node.items.items()]
        elif isinstance(node, G.RecordLit):
            open_text, close_text = node.name + " {", "}"
            entries = [(self._expr_start_pos(v), self._expr_end_pos(v),
                        map_renderer(k, v)) for k, v in node.fields]
        elif isinstance(node, G.RecordUpdate):
            open_text = self._expr(node.base, _POSTFIX_PREC) + " with {"
            close_text = "}"
            entries = [(self._expr_start_pos(v), self._expr_end_pos(v),
                        update_renderer(k, v)) for k, v in node.fields]
        elif isinstance(node, G.Call):
            open_text, close_text = node.name + "(", ")"
            entries = [(self._expr_start_pos(a), self._expr_end_pos(a),
                        lit_renderer(a)) for a in node.args]
        else:  # CallExpr
            open_text = self._expr(node.callee, _POSTFIX_PREC) + "("
            close_text = ")"
            entries = [(self._expr_start_pos(a), self._expr_end_pos(a),
                        lit_renderer(a)) for a in node.args]

        result = [open_text]
        inline_lines: set[int] = set()
        span = self._span_for_container(node)
        if span is None:
            for i, (_s, _e, render) in enumerate(entries):
                tl = render(inner, inner).split("\n")
                tl[0] = " " * inner + tl[0]
                if i < len(entries) - 1:
                    tl[-1] += ","
                result.extend(tl)
            result.append(" " * close_indent + close_text)
            return "\n".join(result)

        oline, ocol, cline, ccol = span
        open_pos, close_pos = (oline, ocol), (cline, ccol)
        cursor = open_pos
        prev_line = 0
        prev_src = oline
        for i, (start, end, render) in enumerate(entries):
            self._flush_comment_range(
                cursor, start, inner, result, inline_lines,
                attach_line=prev_line, attach_src_line=prev_src)
            tl = render(inner, inner).split("\n")
            tl[0] = " " * inner + tl[0]
            if i < len(entries) - 1:
                tl[-1] += ","
            result.extend(tl)
            last_line = len(result) - 1
            self._flush_comment_range(
                start, end, inner, result, inline_lines,
                attach_line=last_line, attach_src_line=end[0])
            if self._pos_lt(cursor, end):
                cursor = end
            prev_line = last_line
            prev_src = end[0]
        self._flush_comment_range(
            cursor, close_pos, inner, result, inline_lines,
            attach_line=prev_line, attach_src_line=prev_src)
        result.append(" " * close_indent + close_text)
        return "\n".join(result)

    def _wrap_binary(self, node, close_indent, base_col, minp) -> str:
        parts, p = self._binary_parts(node)
        cont = close_indent + 2
        lines = [self._wrap(parts[0][1], close_indent, base_col, p)]
        inline_lines: set[int] = set()
        for op, right, opnode in parts[1:]:
            lines[-1] += " " + op
            rs = self._expr_start_pos(right)
            if opnode is not None:
                self._flush_comment_range(
                    (opnode.line, opnode.col), rs, cont, lines, inline_lines,
                    attach_line=len(lines) - 1, attach_src_line=opnode.line)
            rl = self._wrap(right, cont, cont, p + 1).split("\n")
            rl[0] = " " * cont + rl[0]
            lines.extend(rl)
        return "\n".join(lines)

    def _sig_index_after(self, pos) -> int:
        for i, t in enumerate(self.sig):
            if self._pos_lt(pos, (t.line, t.col)):
                return i
        return len(self.sig)

    def _pipe_positions(self, node):
        """Return ``(end_pos, stages)`` with token positions for a pipe.

        Each stage entry is ``(stage, args, stage_token, end_pos)``.  Inner
        stages of a parenthesised nested pipe are included first, so the tail
        of the list always matches ``node.stages``.
        """
        if isinstance(node.base, _G.Pipe):
            cursor, out = self._pipe_positions(node.base)
        else:
            cursor = self._expr_end_pos(node.base)
            out = []
        for stage, args in node.stages:
            idx = self._sig_index_after(cursor)
            if (idx >= len(self.sig)
                    or self.sig[idx].kind != "OP"
                    or self.sig[idx].val != "|"):
                raise ValueError("pipe token not found")
            idx += 1
            if idx >= len(self.sig):
                raise ValueError("pipe stage token not found")
            stage_tok = self.sig[idx]
            if stage_tok.val != stage:
                raise ValueError("pipe stage mismatch")
            scan = idx
            if stage in ("filter", "limit"):
                end = self._expr_end_pos(args[0])
            elif stage in ("select", "sum", "map"):
                names = list(args)
                for name in names:
                    scan += 1
                    while (scan < len(self.sig)
                           and self.sig[scan].val != name):
                        scan += 1
                    if scan >= len(self.sig):
                        raise ValueError("pipe argument token not found")
                    tok = self.sig[scan]
                    end = (tok.line, tok.col + len(tok.val))
            else:  # count
                end = (stage_tok.line,
                       stage_tok.col + len(stage_tok.val))
            out.append((stage, args, stage_tok, end))
            cursor = end
        return cursor, out

    def _pipe_has_comment(self, node) -> bool:
        try:
            end, _stages = self._pipe_positions(node)
        except ValueError:
            return False
        start = self._expr_start_pos(node.base)
        for idx, c in enumerate(self.comments):
            if idx in self.used:
                continue
            pos = (c.line, c.col)
            if self._pos_lt(start, pos) and self._pos_lt(pos, end):
                return True
        return False

    def _wrap_pipe(self, node, close_indent, base_col, minp,
                   _paren: bool = False) -> str:
        if _PIPE_PREC < minp:
            if _paren:
                return self._expr(node, minp)
            body = self._wrap_pipe(node, close_indent, base_col + 1, 0,
                                   _paren=True)
            lines = body.split("\n")
            lines[0] = "(" + lines[0]
            lines[-1] += ")"
            return "\n".join(lines)
        cont = close_indent + 2
        text = self._wrap(node.base, close_indent, base_col, 1)
        lines = text.split("\n")
        inline_lines: set[int] = set()
        base_end = self._expr_end_pos(node.base)
        try:
            _end, positions = self._pipe_positions(node)
        except ValueError:
            positions = None
        if positions is not None:
            positions = positions[-len(node.stages):] if node.stages else []
        cursor = base_end
        prev_line = len(lines) - 1
        prev_src = cursor[0]
        for i, (stage, args) in enumerate(node.stages):
            stage_tok = stage_end = None
            if positions is not None and i < len(positions):
                _s, _a, stage_tok, stage_end = positions[i]
            stage_pos = ((stage_tok.line, stage_tok.col)
                         if stage_tok is not None else None)
            if stage_pos is not None:
                self._flush_comment_range(
                    cursor, stage_pos, cont, lines, inline_lines,
                    attach_line=prev_line, attach_src_line=prev_src)
            if stage in ("filter", "limit"):
                arg_base = cont + len("| " + stage + " ")
                part = stage + " " + self._wrap(args[0], cont,
                                                arg_base, 0)
            elif stage == "select":
                part = "select " + ", ".join(args)
            elif stage in ("sum", "map"):
                part = stage + " " + args[0]
            else:
                part = stage
            block = " " * cont + "| " + part
            lines.extend(block.split("\n"))
            if stage_pos is not None and stage_end is not None:
                self._flush_comment_range(
                    stage_pos, stage_end, cont, lines, inline_lines,
                    attach_line=len(lines) - 1,
                    attach_src_line=stage_end[0])
                cursor = stage_end
                prev_src = stage_end[0]
            prev_line = len(lines) - 1
        return "\n".join(lines)

    def _emit_expr_stmt(self, ind, prefix, stmt, indent, semi):
        node = stmt.expr
        base_col = len(ind) + len(prefix)
        text = self._wrap(node, len(ind), base_col)
        end_line = self._stmt_end_line(stmt)
        self._emit(ind + prefix + text, [stmt.line, end_line], suffix=semi)

    # --------------------------------------------------------------- output
    def format(self) -> str:
        self.render_block(self.ast, 0, 0, len(self.lines) + 1)
        while self.out and self.out[-1] == "":
            self.out.pop()
        if not self.out:
            return "\n"
        return "\n".join(self.out) + "\n"

    def render_block(self, stmts, indent: int, lo: int, hi: int):
        prev_end = lo
        for stmt in stmts:
            prev_end = self._flush_before(prev_end, stmt.line, indent, lo)
            if self.out and stmt.line - prev_end >= 2:
                self._maybe_blank()
            self.render_stmt(stmt, indent)
            prev_end = max(prev_end, self._stmt_end_line(stmt))
        self._flush_before(prev_end, hi, indent, lo)

    # ------------------------------------------------------------ statements
    def render_stmt(self, s, indent: int):
        ind = _UNIT * indent
        semi = ";" if self._has_semicolon(s) else ""
        G = _G

        if isinstance(s, G.Import):
            alias = (" as " + s.alias) if s.alias else ""
            self._emit(ind + "import " + self._import_path_text(s) + alias,
                       [s.line], suffix=semi)
            return
        if isinstance(s, G.Use):
            self._emit(ind + "use " + s.name, [s.line], suffix=semi)
            return
        if isinstance(s, G.Break):
            self._emit(ind + "break", [s.line], suffix=semi)
            return
        if isinstance(s, G.Continue):
            self._emit(ind + "continue", [s.line], suffix=semi)
            return
        if isinstance(s, G.Let):
            tn = (": " + s.type_name) if s.type_name else ""
            prefix = "export " if getattr(s, "exported", False) else ""
            self._emit_expr_stmt(
                ind, prefix + "let " + s.name + tn + " = ", s, indent, semi)
            return
        if isinstance(s, G.Assign):
            self._emit_expr_stmt(ind, s.name + " := ", s, indent, semi)
            return
        if isinstance(s, G.Return):
            self._emit_expr_stmt(ind, "return ", s, indent, semi)
            return
        if isinstance(s, G.Print):
            self._emit_expr_stmt(ind, "print ", s, indent, semi)
            return
        if isinstance(s, G.Verify):
            self._emit_expr_stmt(ind, "verify ", s, indent, semi)
            return
        if isinstance(s, G.Prove):
            self._render_prove(s, indent, semi)
            return
        if isinstance(s, G.RecordDecl):
            self._render_record(s, indent, semi)
            return
        if isinstance(s, G.EnumDecl):
            self._render_enum(s, indent, semi)
            return
        if isinstance(s, G.Fn):
            self._render_fn_stmt(s, indent, semi)
            return
        if isinstance(s, G.If):
            self._render_if(s, indent, self._next_line(s),
                            prefix="", extra_lines=(), semi=semi)
            return
        if isinstance(s, G.While):
            self._render_loop(s, indent, semi, "while")
            return
        if isinstance(s, G.For):
            self._render_loop(s, indent, semi, "for")
            return
        if isinstance(s, G.Match):
            self._render_match(s, indent, semi)
            return
        if isinstance(s, G.ExprStmt):
            self._emit_expr_stmt(ind, "", s, indent, semi)
            return
        raise _G.GamaError("unsupported statement " + type(s).__name__,
                           getattr(s, "line", 0), getattr(s, "col", 0))

    def _next_line(self, stmt) -> int:
        """Line after this statement's syntactic span (best effort)."""
        return self._stmt_end_line(stmt) + 1

    def _fn_keyword(self, s):
        tok = self.tok_at.get((s.line, s.col))
        if tok is not None and tok.kind == "KW" and tok.val == "make":
            return "make"
        return "fn"

    def _params_text(self, params) -> str:
        parts = []
        for p in params:
            part = p.name + ": " + p.type_name
            if p.default is not None:
                part += " = " + self._expr(p.default)
            parts.append(part)
        return ", ".join(parts)

    def _effects_text(self, effects) -> str:
        if effects is None:
            return ""
        if not effects:
            return " effects {}"
        return " effects {" + ", ".join(sorted(effects)) + "}"

    def _fn_header(self, s, keyword="fn", named=True) -> str:
        name = (" " + s.name) if named else ""
        ret = (" -> " + s.ret) if s.ret else ""
        prefix = "export " if getattr(s, "exported", False) else ""
        return prefix + keyword + name + "(" + self._params_text(s.params) + ")" + \
            ret + self._effects_text(s.effects)

    def _param_positions(self, s):
        """Token spans ``(start, end)`` for each fn parameter, in order."""
        span = self._span(s)
        if not span or not s.params:
            return []
        toks = self.toks[span[0]:span[1]]
        i = 0
        while i < len(toks) and not (toks[i].kind == "OP"
                                     and toks[i].val == "("):
            i += 1
        i += 1
        out = []
        for p in s.params:
            while i < len(toks) and toks[i].kind == "NL":
                i += 1
            while i < len(toks) and not (toks[i].kind == "IDENT"
                                         and toks[i].val == p.name):
                if toks[i].kind == "OP" and toks[i].val == ")":
                    return out
                i += 1
            if i >= len(toks):
                return out
            start = (toks[i].line, toks[i].col)
            i += 1
            while i < len(toks) and toks[i].kind == "NL":
                i += 1
            if not (i < len(toks) and toks[i].kind == "OP"
                    and toks[i].val == ":"):
                return out
            i += 1
            while i < len(toks) and toks[i].kind == "NL":
                i += 1
            if not (i < len(toks) and toks[i].kind == "IDENT"):
                return out
            type_tok = toks[i]
            i += 1
            end = (type_tok.line, type_tok.col + len(type_tok.val))
            if p.default is not None:
                end = self._expr_end_pos(p.default)
                while (i < len(toks)
                       and not self._pos_lt(
                           end, (toks[i].line, toks[i].col))):
                    i += 1
            out.append((start, end))
        return out

    def _fn_header_text(self, s, indent: int, keyword="fn",
                        named=True) -> str:
        """Render a fn header, breaking the parameter list when too long.

        ``indent`` is the statement indentation level; continuation lines carry
        absolute indentation.  The closing ``) -> Ret effects {...}`` stays on
        one line because the parser requires ``->``/``effects`` directly after
        ``)``.  Default values are wrapped with the normal expression wrapper.
        """
        ind = _UNIT * indent
        single = self._fn_header(s, keyword, named)
        if self._fits(single + " {", len(ind)):
            return single
        name = (" " + s.name) if named else ""
        ret = (" -> " + s.ret) if s.ret else ""
        prefix = "export " if getattr(s, "exported", False) else ""
        inner = len(ind) + 2
        effects = s.effects
        if not s.params:
            first = prefix + keyword + name + "()" + ret
            if not effects:
                return single
            names = sorted(effects)
            one = " effects {" + ", ".join(names) + "}"
            if self._fits(first + one + " {", len(ind)) or len(names) == 1:
                return first + one
            lines = [first + " effects {"]
            for i, nm in enumerate(names):
                part = " " * inner + nm
                if i < len(names) - 1:
                    part += ","
                lines.append(part)
            lines.append(" " * len(ind) + "}")
            return "\n".join(lines)
        lines = [prefix + keyword + name + "("]
        texts = []
        for p in s.params:
            text = p.name + ": " + p.type_name
            if p.default is not None:
                text += " = " + self._wrap(
                    p.default, inner, inner + len(text) + 3)
            parts = text.split("\n")
            parts[0] = " " * inner + parts[0]
            texts.append(parts)
        positions = self._param_positions(s)
        span = self._delim_span("(", s.line, s.col)
        if span is not None and len(positions) == len(s.params):
            cursor = (span[0], span[1])
            close_pos = (span[2], span[3])
            inline_lines: set[int] = set()
            prev_line = 0
            prev_src = s.line
            for i, parts in enumerate(texts):
                start, end = positions[i]
                self._flush_comment_range(
                    cursor, start, inner, lines, inline_lines,
                    attach_line=prev_line, attach_src_line=prev_src)
                lines.extend(parts)
                if i < len(texts) - 1:
                    lines[-1] += ","
                self._flush_comment_range(
                    start, end, inner, lines, inline_lines,
                    attach_line=len(lines) - 1,
                    attach_src_line=end[0])
                cursor = end
                prev_line = len(lines) - 1
                prev_src = end[0]
            self._flush_comment_range(
                cursor, close_pos, inner, lines, inline_lines,
                attach_line=prev_line, attach_src_line=prev_src)
        else:
            for i, parts in enumerate(texts):
                lines.extend(parts)
                if i < len(texts) - 1:
                    lines[-1] += ","
        close = " " * len(ind) + ")" + ret
        if effects is None:
            lines.append(close)
        elif not effects:
            lines.append(close + " effects {}")
        else:
            names = sorted(effects)
            one = " effects {" + ", ".join(names) + "}"
            if self._fits(close + one + " {", len(ind)):
                lines.append(close + one)
            else:
                lines.append(close + " effects {")
                for i, nm in enumerate(names):
                    part = " " * inner + nm
                    if i < len(names) - 1:
                        part += ","
                    lines.append(part)
                lines.append(" " * len(ind) + "}")
        return "\n".join(lines)

    def _header_expr(self, prefix: str, node, indent: int,
                     minp: int = 0) -> str:
        """Render ``prefix + expression`` for a control-flow header.

        The trailing ``{`` is accounted for in the width check, so a header is
        only kept on one line when the opener fits too.  ``{`` is appended by
        the caller to the last line.
        """
        ind = _UNIT * indent
        base_col = len(ind) + len(prefix)
        text = self._expr(node, minp)
        if self._fits(text + " {", base_col) and not self._force_expand(node):
            return prefix + text
        return prefix + self._wrap(node, len(ind), base_col + 2, minp)

    @staticmethod
    def _record_text(s) -> str:
        prefix = "export " if getattr(s, "exported", False) else ""
        if not s.fields:
            return prefix + "record " + s.name + " { }"
        fields = [fn + ": " + ft for fn, ft in s.fields]
        return prefix + "record " + s.name + " { " + ", ".join(fields) + " }"

    @staticmethod
    def _enum_variant_text(v) -> str:
        if isinstance(v, tuple):
            name = v[0]
            payload = v[1] if len(v) > 1 else None
        else:
            name, payload = v, None
        if isinstance(payload, tuple):
            return name + "(" + ", ".join(payload) + ")"
        return name + ("(" + payload + ")" if payload else "")

    @classmethod
    def _enum_text(cls, s) -> str:
        parts = [cls._enum_variant_text(v) for v in s.variants]
        if not parts:
            return "enum " + s.name + " { }"
        return "enum " + s.name + " { " + ", ".join(parts) + " }"

    def _record_field_positions(self, s):
        """Token spans ``(start, end)`` for each record field, in order."""
        span = self._span(s)
        if not span:
            return []
        toks = self.toks[span[0]:span[1]]
        i = 0
        while i < len(toks) and not (toks[i].kind == "OP"
                                     and toks[i].val == "{"):
            i += 1
        i += 1
        out = []
        while i < len(toks):
            t = toks[i]
            if t.kind == "OP" and t.val == "}":
                break
            if t.kind == "NL" or (t.kind == "OP" and t.val in ",;"):
                i += 1
                continue
            if t.kind == "IDENT":
                j = i + 1
                while j < len(toks) and toks[j].kind == "NL":
                    j += 1
                if (j < len(toks) and toks[j].kind == "OP"
                        and toks[j].val == ":"):
                    k = j + 1
                    while k < len(toks) and toks[k].kind == "NL":
                        k += 1
                    if k < len(toks) and toks[k].kind == "IDENT":
                        ft = toks[k]
                        out.append(((t.line, t.col),
                                    (ft.line,
                                     ft.col + len(ft.val))))
                        i = k + 1
                        continue
            i += 1
        return out

    def _enum_variant_positions(self, s):
        """Token spans ``(start, end)`` for each enum variant, in order."""
        span = self._span(s)
        if not span:
            return []
        toks = self.toks[span[0]:span[1]]
        i = 0
        while i < len(toks) and not (toks[i].kind == "OP"
                                     and toks[i].val == "{"):
            i += 1
        i += 1
        out = []
        while i < len(toks):
            t = toks[i]
            if t.kind == "OP" and t.val == "}":
                break
            if t.kind == "NL" or (t.kind == "OP" and t.val in ",;"):
                i += 1
                continue
            if t.kind != "IDENT":
                i += 1
                continue
            start = (t.line, t.col)
            end = (t.line, t.col + len(t.val))
            i += 1
            j = i
            while j < len(toks) and toks[j].kind == "NL":
                j += 1
            if j < len(toks) and toks[j].kind == "OP" and toks[j].val == "(":
                depth = 0
                while j < len(toks):
                    u = toks[j]
                    if u.kind == "OP" and u.val == "(":
                        depth += 1
                    elif u.kind == "OP" and u.val == ")":
                        depth -= 1
                        if depth == 0:
                            end = (u.line, u.col + 1)
                            j += 1
                            break
                    j += 1
                i = j
            out.append((start, end))
        return out

    def _decl_has_comment(self, s) -> bool:
        span = self._delim_span("{", s.line, s.col)
        return bool(span and self._interior_comments(span))

    def _render_record(self, s, indent: int, semi: str):
        ind = _UNIT * indent
        text = self._record_text(s)
        has_comment = self._decl_has_comment(s)
        if (not has_comment and not s.fields) or (
                not has_comment and self._fits(text, len(ind))):
            self._emit(ind + text, [s.line], suffix=semi)
            return
        prefix = "export " if getattr(s, "exported", False) else ""
        inner = len(ind) + 2
        lines = [prefix + "record " + s.name + " {"]
        self._render_decl_body(
            s, s.fields, self._record_field_positions(s),
            [fn + ": " + ft for fn, ft in s.fields],
            lines, indent, inner, semi, close_text="}")

    def _render_enum(self, s, indent: int, semi: str):
        ind = _UNIT * indent
        text = self._enum_text(s)
        has_comment = self._decl_has_comment(s)
        if (not has_comment and not s.variants) or (
                not has_comment and self._fits(text, len(ind))):
            self._emit(ind + text, [s.line], suffix=semi)
            return
        inner = len(ind) + 2
        lines = ["enum " + s.name + " {"]
        self._render_decl_body(
            s, s.variants, self._enum_variant_positions(s),
            [self._enum_variant_text(v) for v in s.variants],
            lines, indent, inner, semi, close_text="}")

    def _separator_text(self, after_pos, before_pos) -> str:
        """Original separator token between two declaration entries.

        Record and enum entries may be separated by ``,``, ``;`` or just a
        newline; the wrapped form reuses the original separator so no token is
        added or removed.
        """
        comma = semi = False
        for t in self.sig:
            tpos = (t.line, t.col)
            if self._pos_lt(tpos, after_pos):
                continue
            if not self._pos_lt(tpos, before_pos):
                break
            if t.kind == "OP" and t.val == ",":
                comma = True
            elif t.kind == "OP" and t.val == ";":
                semi = True
        if comma:
            return ","
        if semi:
            return ";"
        return ""

    def _render_decl_body(self, s, entries, positions, texts, lines,
                          indent, inner, semi, close_text):
        """Vertical declaration body with comments kept on their entry."""
        ind = _UNIT * indent
        span = self._delim_span("{", s.line, s.col)
        if span is None or len(positions) != len(entries):
            for i, text in enumerate(texts):
                line = " " * inner + text
                if i < len(entries) - 1:
                    line += ","
                lines.append(line)
            lines.append(" " * len(ind) + close_text)
            self._emit("\n".join(lines), [s.line], suffix=semi)
            return
        seps = [
            self._separator_text(positions[i][1], positions[i + 1][0])
            for i in range(len(positions) - 1)
        ]
        cursor = (span[0], span[1])
        close_pos = (span[2], span[3])
        inline_lines: set[int] = set()
        prev_line = 0
        prev_src = s.line
        for i, (start, end) in enumerate(positions):
            self._flush_comment_range(
                cursor, start, inner, lines, inline_lines,
                attach_line=prev_line, attach_src_line=prev_src)
            line = " " * inner + texts[i]
            if i < len(seps):
                line += seps[i]
            lines.append(line)
            self._flush_comment_range(
                start, end, inner, lines, inline_lines,
                attach_line=len(lines) - 1,
                attach_src_line=end[0])
            cursor = end
            prev_line = len(lines) - 1
            prev_src = end[0]
        self._flush_comment_range(
            cursor, close_pos, inner, lines, inline_lines,
            attach_line=prev_line, attach_src_line=prev_src)
        lines.append(" " * len(ind) + close_text + semi)
        end_line = self._stmt_end_line(s)
        self._flush_comment_range(
            close_pos, (end_line + 1, 0), inner, lines, inline_lines,
            attach_line=len(lines) - 1, attach_src_line=end_line)
        self._emit("\n".join(lines), attach=False)

    def _multiline_lead(self, stmts, brace_line: int):
        """Inline text for a first statement that shares the opener line.

        Only a statement whose rendered form contains a real newline (i.e. a
        raw multi-line string) qualifies.  Keeping it on the ``{`` line is what
        prevents the literal's continuation line from being mistaken for the
        end of the block by any line-oriented depth counter.
        """
        if not stmts or stmts[0].line != brace_line:
            return None
        try:
            text = self._inline_stmt(stmts[0])
        except _G.GamaError:
            return None
        return text if "\n" in text else None

    def _render_fn_stmt(self, s, indent: int, semi: str):
        ind = _UNIT * indent
        nxt = self._next_line(s)
        pairs = self._pairs(s.line, nxt)
        body = self._pick_body_pair(pairs, s.body)
        header = self._fn_header_text(
            s, indent, self._fn_keyword(s)) + " {"
        header_end = body[0].line + 1 if body else s.line + 1
        if body and s.body and body[0].line == body[1].line:
            # A function body written entirely on one source line stays on one
            # line (historical LSP behaviour); other blocks are expanded.
            inner = self._inline_block(s.body)
            self._emit(ind + header + " " + inner + " }",
                       range(s.line, body[1].line + 1), suffix=semi)
            return
        lead = self._multiline_lead(s.body, body[0].line) if body else None
        if lead is not None:
            first = s.body[0]
            lead_semi = ";" if self._has_semicolon(first) else ""
            first_end = self._stmt_end_line(first)
            self._emit(ind + header + " " + lead + lead_semi,
                       range(s.line, first_end + 1))
            self.render_block(s.body[1:], indent + 1,
                              first_end, body[1].line)
        else:
            self._emit(ind + header, range(s.line, header_end))
            if body:
                self.render_block(s.body, indent + 1,
                                  body[0].line, body[1].line)
        self._emit(ind + "}", [body[1].line] if body else [s.line],
                   suffix=semi)

    def _render_loop(self, s, indent: int, semi: str, kind: str):
        ind = _UNIT * indent
        nxt = self._next_line(s)
        pairs = self._pairs(s.line, nxt)
        body = self._pick_body_pair(pairs, s.body)
        if kind == "while":
            header = self._header_expr("while ", s.cond, indent)
        else:
            header = self._header_expr(
                "for " + s.name + " in ", s.iterable, indent)
        header += " {"
        header_end = body[0].line + 1 if body else s.line + 1
        lead = self._multiline_lead(s.body, body[0].line) if body else None
        if lead is not None:
            first = s.body[0]
            lead_semi = ";" if self._has_semicolon(first) else ""
            first_end = self._stmt_end_line(first)
            self._emit(ind + header + " " + lead + lead_semi,
                       range(s.line, first_end + 1))
            self.render_block(s.body[1:], indent + 1,
                              first_end, body[1].line)
        else:
            self._emit(ind + header, range(s.line, header_end))
            if body:
                self.render_block(s.body, indent + 1,
                                  body[0].line, body[1].line)
        self._emit(ind + "}", [body[1].line] if body else [s.line],
                   suffix=semi)

    def _render_if(self, s, indent: int, nxt: int, prefix: str,
                   extra_lines, semi: str):
        ind = _UNIT * indent
        pairs = self._pairs(s.line, nxt)
        then_pair = self._pick_body_pair(pairs, s.then)
        if then_pair is None and pairs:
            then_pair = pairs[0]
        header = self._header_expr(
            prefix + "if ", s.cond, indent) + " {"
        head_lines = list(range(s.line, (then_pair[0].line + 1)
                                if then_pair else s.line + 1))
        head_lines.extend(extra_lines)
        lead = self._multiline_lead(s.then, then_pair[0].line) \
            if then_pair else None
        if lead is not None:
            first = s.then[0]
            lead_semi = ";" if self._has_semicolon(first) else ""
            first_end = self._stmt_end_line(first)
            lead_lines = list(range(s.line, first_end + 1)) + list(extra_lines)
            self._emit(ind + header + " " + lead + lead_semi,
                       lead_lines)
            self.render_block(s.then[1:], indent + 1,
                              first_end, then_pair[1].line)
        else:
            self._emit(ind + header, head_lines)
            if then_pair:
                self.render_block(s.then, indent + 1,
                                  then_pair[0].line, then_pair[1].line)
        close_line = then_pair[1].line if then_pair else s.line
        other = s.other
        if other is None:
            self._emit(ind + "}", [close_line], suffix=semi)
            return

        else_tok = self._find_token("KW", "else", close_line, nxt)
        same_line = else_tok is not None and else_tok.line == close_line
        if isinstance(other, _G.If):
            if same_line:
                self._render_if(other, indent, nxt, prefix="} else ",
                                extra_lines=[close_line], semi=semi)
            else:
                self._emit(ind + "}", [close_line])
                else_line = else_tok.line if else_tok else close_line + 1
                self._flush_before(close_line, else_line, indent, close_line)
                self._render_if(other, indent, nxt, prefix="else ",
                                extra_lines=[], semi=semi)
            return

        else_pair = self._pick_body_pair(pairs, other)
        if else_pair is None and pairs:
            after = [p for p in pairs if p[0].line >= close_line]
            else_pair = after[-1] if after else None
        if same_line:
            lines = [close_line]
            if else_pair:
                lines.append(else_pair[0].line)
            self._emit(ind + "} else {", lines)
        else:
            self._emit(ind + "}", [close_line])
            else_line = else_tok.line if else_tok else close_line + 1
            self._flush_before(close_line, else_line, indent, close_line)
            self._emit(ind + "else {",
                       [else_pair[0].line] if else_pair else [close_line + 1])
        if else_pair:
            self.render_block(other, indent + 1,
                              else_pair[0].line, else_pair[1].line)
        self._emit(ind + "}", [else_pair[1].line] if else_pair else [close_line],
                   suffix=semi)

    def _render_match(self, s, indent: int, semi: str):
        ind = _UNIT * indent
        nxt = self._next_line(s)
        pairs = self._pairs(s.line, nxt)
        outer = pairs[-1] if pairs else None
        header = self._header_expr("match ", s.expr, indent) + " {"
        header_end = outer[0].line + 1 if outer else s.line + 1
        self._emit(ind + header, range(s.line, header_end))
        if outer is not None:
            arm_pairs = self._child_pairs(outer)
            prev_end = outer[0].line
            for i, (name, bindings, body) in enumerate(s.arms):
                pair = arm_pairs[i] if i < len(arm_pairs) else None
                limit = pair[0].line if pair else outer[1].line
                prev_end = self._flush_before(prev_end, limit, indent + 1,
                                              outer[0].line)
                if name == "_" or not bindings:
                    label = name
                elif isinstance(bindings, str):
                    label = name + "(" + bindings + ")"
                else:
                    label = name + "(" + ", ".join(bindings) + ")"
                label += " {"
                self._emit(_UNIT * (indent + 1) + label,
                           [pair[0].line] if pair else [s.line])
                if pair:
                    self.render_block(body, indent + 2,
                                      pair[0].line, pair[1].line)
                self._emit(_UNIT * (indent + 1) + "}",
                           [pair[1].line] if pair else [s.line])
                if pair:
                    prev_end = max(prev_end, pair[1].line)
            self._flush_before(prev_end, outer[1].line, indent + 1,
                               outer[0].line)
        self._emit(ind + "}",
                   [outer[1].line] if outer else [s.line], suffix=semi)

    def _prove_uses_eq(self, s) -> bool:
        span = self._span(s)
        if not span:
            return False
        start, end = span
        idx = start + 2  # skip ``prove`` and the name
        while idx < end and self.toks[idx].kind == "NL":
            idx += 1
        return (idx < end and self.toks[idx].kind == "OP"
                and self.toks[idx].val == "=")

    def _prove_text(self, s) -> str:
        name = "prove " + s.name
        expr = self._expr(s.expr)
        return name + (" = " if self._prove_uses_eq(s) else " ") + expr

    def _render_prove(self, s, indent: int, semi: str):
        ind = _UNIT * indent
        prefix = "prove " + s.name + (" = " if self._prove_uses_eq(s) else " ")
        text = self._wrap(s.expr, len(ind), len(ind) + len(prefix))
        end_line = self._stmt_end_line(s)
        self._emit(ind + prefix + text, [s.line, end_line], suffix=semi)

    def _import_path_text(self, s) -> str:
        span = self._span(s)
        if span:
            start, end = span
            for t in self.toks[start:end]:
                if t.kind == "STRING":
                    raw, _end = self._raw_string(t.line, t.col)
                    if raw is not None and raw[:1] in "\"'":
                        return raw
        return '"' + s.path.replace('"', '\\"') + '"'

    # ------------------------------------------------------------ expressions
    def _expr(self, node, minp: int = 0) -> str:
        G = _G
        if isinstance(node, G.Literal):
            return self._literal(node)
        if isinstance(node, G.Var):
            return node.name
        if isinstance(node, G.Attr):
            return self._expr(node.base, _POSTFIX_PREC) + "." + node.name
        if isinstance(node, G.RecordLit):
            if not node.fields:
                return node.name + " { }"
            parts = []
            for key, val in node.fields:
                k = key if _bare_key_ok(key) else _quote(key)
                parts.append(k + ": " + self._expr(val))
            return node.name + " { " + ", ".join(parts) + " }"
        if isinstance(node, G.RecordUpdate):
            base = self._expr(node.base, _POSTFIX_PREC)
            inner = ", ".join(
                (k if _bare_key_ok(k) else _quote(k)) + " = " + self._expr(v)
                for k, v in node.fields)
            text = base + (" with { }" if not node.fields
                           else " with { " + inner + " }")
            return "(" + text + ")" if _POSTFIX_PREC < minp else text
        if isinstance(node, G.ListExpr):
            return "[" + ", ".join(self._expr(x) for x in node.items) + "]"
        if isinstance(node, G.MapExpr):
            if not node.items:
                return "{}"
            parts = []
            for key, val in node.items.items():
                k = key if _bare_key_ok(key) else _quote(key)
                parts.append(k + ": " + self._expr(val))
            return "{" + ", ".join(parts) + "}"
        if isinstance(node, G.Unary):
            inner = self._expr(node.right, _UNARY_PREC)
            text = node.op + inner
            return "(" + text + ")" if _UNARY_PREC < minp else text
        if isinstance(node, G.Binary):
            prec = _PREC[node.op]
            text = (self._expr(node.left, prec) + " " + node.op + " " +
                    self._expr(node.right, prec + 1))
            return "(" + text + ")" if prec < minp else text
        if isinstance(node, G.Call):
            return node.name + "(" + \
                ", ".join(self._expr(a) for a in node.args) + ")"
        if isinstance(node, G.CallExpr):
            callee = self._expr(node.callee, _POSTFIX_PREC)
            return callee + "(" + \
                ", ".join(self._expr(a) for a in node.args) + ")"
        if isinstance(node, G.Index):
            base = self._expr(node.base, _POSTFIX_PREC)
            return base + "[" + self._expr(node.index) + "]"
        if isinstance(node, G.Try):
            inner = self._expr(node.expr, _POSTFIX_PREC)
            text = inner + "?"
            return "(" + text + ")" if _POSTFIX_PREC < minp else text
        if isinstance(node, G.Pipe):
            text = self._expr(node.base, 1)
            for stage, args in node.stages:
                if stage in ("filter", "limit"):
                    text += " | " + stage + " " + self._expr(args[0])
                elif stage == "select":
                    text += " | select " + ", ".join(args)
                elif stage in ("sum", "map"):
                    text += " | " + stage + " " + args[0]
                else:  # count
                    text += " | " + stage
            return "(" + text + ")" if _PIPE_PREC < minp else text
        if isinstance(node, G.Lambda):
            return self._inline_lambda(node)
        raise _G.GamaError("unsupported expression " + type(node).__name__,
                           getattr(node, "line", 0), getattr(node, "col", 0))

    def _literal(self, node) -> str:
        val = node.value
        if val is None:
            return "null"
        if val is True:
            return "true"
        if val is False:
            return "false"
        if isinstance(val, str):
            raw, _end = self._raw_string(node.line, node.col)
            if raw is not None and raw[:1] in "\"'":
                return raw
            return _quote(val)
        # int / Decimal
        return str(val)

    def _inline_lambda(self, node) -> str:
        ret = (" -> " + node.ret) if node.ret else ""
        header = "fn(" + self._params_text(node.params) + ")" + ret + \
            self._effects_text(node.effects) + " { "
        body = self._inline_block(node.body)
        return header + body + " }"

    # ------------------------------------------------------ inline statements
    def _inline_block(self, stmts) -> str:
        parts = []
        for stmt in stmts:
            text = self._inline_stmt(stmt)
            if self._has_semicolon(stmt):
                text += ";"
            parts.append(text)
        return " ".join(parts)

    def _inline_stmt(self, s) -> str:
        G = _G
        if isinstance(s, G.Let):
            tn = (": " + s.type_name) if s.type_name else ""
            prefix = "export " if getattr(s, "exported", False) else ""
            return prefix + "let " + s.name + tn + " = " + self._expr(s.expr)
        if isinstance(s, G.Assign):
            return s.name + " := " + self._expr(s.expr)
        if isinstance(s, G.Return):
            return "return " + self._expr(s.expr)
        if isinstance(s, G.Print):
            return "print " + self._expr(s.expr)
        if isinstance(s, G.Verify):
            return "verify " + self._expr(s.expr)
        if isinstance(s, G.Prove):
            return self._prove_text(s)
        if isinstance(s, G.ExprStmt):
            return self._expr(s.expr)
        if isinstance(s, G.Break):
            return "break"
        if isinstance(s, G.Continue):
            return "continue"
        if isinstance(s, G.Use):
            return "use " + s.name
        if isinstance(s, G.Import):
            alias = (" as " + s.alias) if s.alias else ""
            return "import " + self._import_path_text(s) + alias
        if isinstance(s, G.RecordDecl):
            return self._record_text(s)
        if isinstance(s, G.EnumDecl):
            return self._enum_text(s)
        if isinstance(s, G.If):
            then = self._inline_block(s.then)
            text = "if " + self._expr(s.cond) + " { " + then + " }"
            if s.other is None:
                return text
            if isinstance(s.other, G.If):
                return text + " else " + self._inline_stmt(s.other)
            other = self._inline_block(s.other)
            return text + " else { " + other + " }"
        if isinstance(s, G.While):
            body = self._inline_block(s.body)
            return "while " + self._expr(s.cond) + " { " + body + " }"
        if isinstance(s, G.For):
            body = self._inline_block(s.body)
            return ("for " + s.name + " in " + self._expr(s.iterable) +
                    " { " + body + " }")
        if isinstance(s, G.Match):
            arms = []
            for name, var, body in s.arms:
                label = name if name == "_" or var is None else name + "(" + var + ")"
                inner = self._inline_block(body)
                arms.append(label + " { " + inner + " }")
            return "match " + self._expr(s.expr) + " { " + " ".join(arms) + " }"
        if isinstance(s, G.Fn):
            body = self._inline_block(s.body)
            return self._fn_header(s, self._fn_keyword(s)) + " { " + body + " }"
        raise _G.GamaError("unsupported inline statement " +
                           type(s).__name__,
                           getattr(s, "line", 0), getattr(s, "col", 0))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_source(src: str, width: int = DEFAULT_WIDTH) -> str:
    """Format GAMA-L source canonically.

    The AST formatter is the default; invalid sources fall back to the legacy
    token formatter so ``fmt`` never crashes on a snippet that does not parse.
    ``width`` is the soft column limit for wrapping long expressions
    (default 88); short constructs are never wrapped.
    """
    text = src.replace("\r\n", "\n").replace("\r", "\n")
    if _G is None:
        return _legacy_format_source(text)
    try:
        return _AstFormatter(text, width=width).format()
    except _G.GamaError:
        return _legacy_format_source(text)
