#!/usr/bin/env python3
"""GAMA-L 2.0 core — real parser, checker and interpreter (subset).

This is the first executable 2.0 core, not a transpiler. It implements:
literals, let/assign, functions, if/else, pipes, ask/sql/remember/recall,
wait_user, prove/verify, static type checks and effect checks.

It intentionally does not implement modules, imports, web connectors or a
package manager yet; see SPEC_2.0.md for the full target.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

# ------------------------------------------------------------------ errors
class GamaError(Exception):
    def __init__(self, msg: str, line: int = 0, col: int = 0):
        super().__init__(msg)
        self.msg, self.line, self.col = msg, line, col
        # Set for errors that originate in an imported file so tooling can map
        # them back to the import statement instead of the importing file.
        self.src: str | None = None
    def __str__(self):
        return f"{self.msg} (line {self.line}, col {self.col})"

class GamaTypeError(GamaError): pass
class GamaEffectError(GamaError): pass
class GamaRuntimeError(GamaError): pass

# ------------------------------------------------------------------ call depth
# Each GAMA-L call consumes several CPython frames (invoke -> stmt -> expr ->
# invoke ...), so a depth limit equal to the raw recursion limit is unsafe:
# ``down(400)`` with the old default of 1000 hit RecursionError before the
# guard. The default below is intentionally conservative and the effective cap
# is additionally clamped against the live interpreter budget.
DEFAULT_CALL_DEPTH = 200
_CALL_DEPTH_SAFETY_MARGIN = 100
_CALL_FRAMES_PER_CALL = 4


def _call_depth_cap() -> int:
    """Largest GAMA-L call depth that fits the current Python stack budget."""
    return max(1, (sys.getrecursionlimit() - _CALL_DEPTH_SAFETY_MARGIN)
               // _CALL_FRAMES_PER_CALL)

class Denied:
    """Explicit HITL refusal value; falsy, renders as Denied."""
    def __bool__(self): return False
    def __str__(self): return "Denied"
    __repr__ = __str__

class ResultVal:
    """Runtime Result value: Ok(value) or Err(error)."""
    def __init__(self, ok: bool, value): self.ok, self.value = ok, value
    def __bool__(self): return self.ok
    def __str__(self): return ("Ok(" if self.ok else "Err(") + str(self.value) + ")"
    __repr__ = __str__

class RecordVal:
    """Runtime record (struct) value: a nominal type name plus ordered fields.

    Field order follows the declaration order, which makes printing
    deterministic. Equality is nominal: type name and fields must match.
    """
    def __init__(self, type_name: str, fields: dict):
        self.type_name, self.fields = type_name, dict(fields)
    def __eq__(self, other):
        return (isinstance(other, RecordVal)
                and self.type_name == other.type_name
                and self.fields == other.fields)
    def __ne__(self, other):
        return not self.__eq__(other)
    def __str__(self):
        body = ", ".join(f"{k}: {_record_value_str(v)}"
                         for k, v in self.fields.items())
        return f"{self.type_name} {{ {body} }}"
    __repr__ = __str__

def _record_value_str(v) -> str:
    """Deterministic rendering of one record field value."""
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)

_NO_PAYLOAD = object()

class EnumVal:
    """Runtime enum value: Enum.Variant or Enum.Variant(payload, ...).

    ``payloads is None`` marks a nullary variant; otherwise it is a tuple with
    one entry per declared payload (so a single ``null`` payload is still a
    real payload value). Equality compares enum, variant and the payload tuple;
    printing is deterministic and renders each payload like a record field
    value, e.g. ``Pair.Two(1, "a")``.
    """
    __slots__ = ("enum", "variant", "payloads")
    def __init__(self, enum: str, variant: str, payload=_NO_PAYLOAD, payloads=None):
        self.enum, self.variant = enum, variant
        if payloads is not None:
            self.payloads = tuple(payloads)
        elif payload is _NO_PAYLOAD:
            self.payloads = None
        else:
            self.payloads = (payload,)
    @property
    def payload(self):
        """Backward-compatible single-payload view.

        Returns ``_NO_PAYLOAD`` for nullary variants, the sole value for a
        one-payload variant, and the payload tuple for multi-payload variants.
        """
        if self.payloads is None:
            return _NO_PAYLOAD
        if len(self.payloads) == 1:
            return self.payloads[0]
        return self.payloads
    def __eq__(self, other):
        if isinstance(other, EnumVal):
            if self.enum != other.enum or self.variant != other.variant:
                return False
            if self.payloads is None or other.payloads is None:
                return self.payloads is other.payloads
            return self.payloads == other.payloads
        return NotImplemented
    def __hash__(self): return hash((self.enum, self.variant))
    def __str__(self):
        if self.payloads is None:
            return f"{self.enum}.{self.variant}"
        rendered = ", ".join(_record_value_str(p) for p in self.payloads)
        return f"{self.enum}.{self.variant}({rendered})"
    __repr__ = __str__
class FunctionVal:
    """Runtime callable value: a lambda bound to its defining environment."""
    def __init__(self, params, ret, effects, body, closure, name="<lambda>"):
        self.params, self.ret, self.effects = params, ret, effects
        self.body, self.closure, self.name = body, closure, name
    def __str__(self): return f"<fn {self.name}>"
    __repr__ = __str__

class TableType(str):
    """String-compatible 'Table' type carrying an optional column set."""
    fields: set[str] | None
    def __new__(cls, fields=None):
        o = str.__new__(cls, "Table"); o.fields = set(fields) if fields else None; return o

# ------------------------------------------------------------------ lexer
KEYWORDS = {"let", "fn", "if", "else", "return", "true", "false", "null",
            "prove", "verify", "print", "effects", "filter", "select", "sum",
            "limit", "count", "map", "in", "import", "make", "use", "wait",
            "for", "while", "break", "continue", "match", "enum"}

@dataclass
class Tok:
    kind: str
    val: Any
    line: int
    col: int

def lex(src: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0; line = 1; col = 1; n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\r":
            i += 1; col += 1; continue
        if c == "\n":
            out.append(Tok("NL", "\n", line, col)); i += 1; line += 1; col = 1; continue
        if c == "#":
            while i < n and src[i] != "\n": i += 1
            continue
        if c in "\"'":
            q = c; i += 1; col += 1; start = line, col; buf = []
            while i < n and src[i] != q:
                if src[i] == "\\" and i + 1 < n:
                    esc = src[i+1]
                    buf.append({"n":"\n","t":"\t","\\":"\\",'"':'"',"'":"'"}.get(esc, esc))
                    i += 2; col += 2
                elif src[i] == "\n":
                    # Multiline string: newline advances the line and resets col.
                    buf.append("\n"); i += 1; line += 1; col = 1
                else:
                    buf.append(src[i]); i += 1; col += 1
            if i >= n: raise GamaError("unterminated string", *start)
            i += 1; col += 1
            out.append(Tok("STRING", "".join(buf), start[0], start[1])); continue
        if c.isdigit():
            start = line, col; j = i
            while i < n and (src[i].isdigit() or src[i] == "."): i += 1
            txt = src[j:i]
            try:
                val = Decimal(txt) if "." in txt else int(txt)
            except (ValueError, OverflowError, InvalidOperation):
                raise GamaError(
                    "number literal is too large or malformed",
                    start[0], start[1]) from None
            out.append(Tok("NUMBER", val, start[0], start[1])); col += len(txt); continue
        if c.isalpha() or c == "_":
            start = line, col; j = i
            while i < n and (src[i].isalnum() or src[i] in "_."): i += 1
            txt = src[j:i]
            out.append(Tok("KW" if txt in KEYWORDS else "IDENT", txt, start[0], start[1]))
            col += len(txt); continue
        two = src[i:i+2]
        if two in ("==","!=","<=",">=","&&","||",":=","->","=>"):
            out.append(Tok("OP", two, line, col)); i += 2; col += 2; continue
        if c in "+-*/%<>!|(){}[],:;=?":
            out.append(Tok("OP", c, line, col)); i += 1; col += 1; continue
        raise GamaError(f"unexpected character {c!r}", line, col)
    out.append(Tok("EOF", None, line, col))
    return out

# ------------------------------------------------------------------ AST
@dataclass
class Literal: value: Any; line: int; col: int
@dataclass
class Var: name: str; line: int; col: int
@dataclass
class Attr: base: Any; name: str; line: int; col: int
@dataclass
class ListExpr: items: list; line: int; col: int
@dataclass
class MapExpr: items: dict; line: int; col: int
@dataclass
class Unary: op: str; right: Any; line: int; col: int
@dataclass
class Binary: left: Any; op: str; right: Any; line: int; col: int
@dataclass
class Call: name: str; args: list; line: int; col: int
@dataclass
class CallExpr: callee: Any; args: list; line: int; col: int
@dataclass
class Pipe: base: Any; stages: list; line: int; col: int
@dataclass
class Import: path: str; line: int; col: int; alias: str | None = None
@dataclass
class Use: name: str; line: int; col: int
@dataclass
class For: name: str; iterable: Any; body: list; line: int; col: int
@dataclass
class While: cond: Any; body: list; line: int; col: int
@dataclass
class Break: line: int; col: int
@dataclass
class Continue: line: int; col: int
@dataclass
class Index: base: Any; index: Any; line: int; col: int
@dataclass
class Let: name: str; type_name: str | None; expr: Any; line: int; col: int; exported: bool = False
@dataclass
class Assign: name: str; expr: Any; line: int; col: int
@dataclass
class Param: name: str; type_name: str; default: Any = None
@dataclass
class Fn: name: str; params: list; ret: str | None; effects: set | None; body: list; line: int; col: int; exported: bool = False
@dataclass
class Lambda: params: list; ret: str | None; effects: set | None; body: list; line: int; col: int
@dataclass
class Match: expr: Any; arms: list; line: int; col: int
@dataclass
class EnumDecl: name: str; variants: list; line: int; col: int; exported: bool = False
@dataclass
class If: cond: Any; then: list; other: list | None; line: int; col: int
@dataclass
class Return: expr: Any; line: int; col: int
@dataclass
class Prove: name: str; expr: Any; line: int; col: int
@dataclass
class Verify: expr: Any; line: int; col: int
@dataclass
class Try: expr: Any; line: int; col: int
@dataclass
class RecordDecl: name: str; fields: list; line: int; col: int; exported: bool = False
@dataclass
class RecordLit: name: str; fields: list; line: int; col: int

@dataclass
class Print: expr: Any; line: int; col: int
@dataclass
class ExprStmt: expr: Any; line: int; col: int

# Statements that end with a closing brace; a following statement may start
# on the same line without an explicit separator.
_BLOCK_STMT = (If, While, For, Match, Fn, RecordDecl, EnumDecl)

# ------------------------------------------------------------------ parser
class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks; self.i = 0
        # Pre-scan record type names so `Point { x: 1 }` can be parsed as a
        # named record literal without making a bare `name {` ambiguous with
        # an if/while/for block header. Only locally declared records qualify.
        self.record_names: set[str] = set()
        for j in range(len(toks) - 2):
            if (toks[j].kind == "IDENT" and toks[j].val == "record"
                    and toks[j + 1].kind == "IDENT"
                    and toks[j + 2].kind == "OP" and toks[j + 2].val == "{"):
                self.record_names.add(toks[j + 1].val)
    def peek(self, k=0): return self.t[min(self.i+k, len(self.t)-1)]
    def at(self, kind, val=None):
        x = self.peek()
        return x.kind == kind and (val is None or x.val == val)
    def take(self): x = self.peek(); self.i += 1; return x
    def expect(self, kind, val=None):
        x = self.peek()
        if x.kind != kind or (val is not None and x.val != val):
            raise GamaError(f"expected {val or kind}, got {x.val!r}", x.line, x.col)
        return self.take()
    def skip_seps(self):
        while self.at("NL") or self.at("OP", ";"): self.take()
    def skip_nl(self):
        while self.at("NL"): self.take()
    def program(self):
        out = []; self.skip_seps()
        while not self.at("EOF"):
            out.append(self.statement(top=True))
            if not (self.at("NL") or self.at("OP", ";") or self.at("EOF")
                    or isinstance(out[-1], _BLOCK_STMT)):
                x = self.peek(); raise GamaError("expected end of statement", x.line, x.col)
            self.skip_seps()
        self._reject_duplicate_exports(out)
        return out
    def _reject_duplicate_exports(self, stmts):
        seen = {}
        for s in stmts:
            if not getattr(s, "exported", False):
                continue
            name = getattr(s, "name", None)
            if name is None:
                continue
            if name in seen:
                raise GamaError("duplicate export %r" % name, s.line, s.col)
            seen[name] = s
    def block(self):
        self.expect("OP", "{"); self.skip_seps(); out = []
        while not self.at("OP", "}"):
            if self.at("EOF"):
                x = self.peek(); raise GamaError("unterminated block", x.line, x.col)
            out.append(self.statement(top=False))
            if not (self.at("NL") or self.at("OP", ";") or self.at("OP", "}")
                    or isinstance(out[-1], _BLOCK_STMT)):
                x = self.peek(); raise GamaError("expected end of statement", x.line, x.col)
            self.skip_seps()
        self.expect("OP", "}"); return out
    def statement(self, top=False):
        x = self.peek()
        if x.kind == "IDENT" and x.val == "export":
            nxt = self.peek(1)
            is_decl = (
                (nxt.kind == "KW" and nxt.val in ("fn", "make", "let", "enum"))
                or (nxt.kind == "IDENT" and nxt.val == "record"
                    and self.peek(2).kind == "IDENT")
            )
            if not is_decl:
                raise GamaError(
                    "export must be followed by fn, let, record or enum",
                    x.line, x.col)
            if not top:
                raise GamaError("export is only allowed at top level",
                                x.line, x.col)
            self.take()
            inner = self.statement(top=True)
            if isinstance(inner, EnumDecl):
                raise GamaError("export enum is not supported", x.line, x.col)
            return replace(inner, exported=True)
        if (x.kind == "IDENT" and x.val == "record"
                and self.peek(1).kind == "IDENT"
                and self.peek(2).kind == "OP" and self.peek(2).val == "{"):
            return self.record_decl()
        if x.kind == "KW" and x.val == "import":
            self.take(); path = self.expect("STRING").val
            alias = None
            if self.at("IDENT", "as"):
                self.take()
                alias = self.expect("IDENT").val
            return Import(path, x.line, x.col, alias)
        if x.kind == "KW" and x.val == "use":
            self.take(); return Use(self.expect("IDENT").val, x.line, x.col)
        if x.kind == "KW" and x.val == "for":
            self.take(); name = self.expect("IDENT").val
            self.expect("KW", "in"); iterable = self.expr(); self.skip_nl()
            return For(name, iterable, self.block(), x.line, x.col)
        if x.kind == "KW" and x.val == "while":
            self.take(); cond = self.expr(); self.skip_nl()
            return While(cond, self.block(), x.line, x.col)
        if x.kind == "KW" and x.val == "break":
            self.take(); return Break(x.line, x.col)
        if x.kind == "KW" and x.val == "continue":
            self.take(); return Continue(x.line, x.col)
        if x.kind == "KW" and x.val == "enum":
            return self.enum_decl()
        if x.kind == "KW" and x.val == "let":
            self.take(); name = self.expect("IDENT").val; tn = None
            if self.at("OP", ":"): self.take(); tn = self.expect("IDENT").val
            self.expect("OP", "="); self.skip_nl(); return Let(name, tn, self.expr(), x.line, x.col)
        if x.kind == "KW" and x.val in ("fn", "make"): return self.fn()
        if x.kind == "KW" and x.val == "match": return self.match_stmt()
        if x.kind == "KW" and x.val == "if": return self.if_stmt()
        if x.kind == "KW" and x.val == "return":
            self.take(); return Return(self.expr(), x.line, x.col)
        if x.kind == "KW" and x.val == "prove":
            self.take(); name = self.expect("IDENT").val; self.skip_nl()
            if self.at("OP", "="):
                self.take(); self.skip_nl(); expr = self.expr()
            elif self.at("OP", "{"):
                expr = self.primary()
            else:
                raise GamaError("prove expects '=' or '{'", x.line, x.col)
            return Prove(name, expr, x.line, x.col)
        if x.kind == "KW" and x.val == "verify":
            self.take(); return Verify(self.expr(), x.line, x.col)
        if x.kind == "KW" and x.val == "print":
            self.take(); return Print(self.expr(), x.line, x.col)
        if x.kind == "IDENT" and x.val == "remember" and self.peek(1).kind == "IDENT":
            self.take(); key = self.take().val; self.expect("OP", "=")
            return ExprStmt(Call("remember", [Literal(key, x.line, x.col), self.expr()], x.line, x.col), x.line, x.col)
        if x.kind == "IDENT" and self.peek(1).kind == "OP" and self.peek(1).val == ":=":
            name = self.take().val; self.take(); return Assign(name, self.expr(), x.line, x.col)
        return ExprStmt(self.expr(), x.line, x.col)
    def record_decl(self):
        x = self.take()  # `record`
        name = self.expect("IDENT").val
        self.skip_nl()
        self.expect("OP", "{")
        self.skip_seps()
        fields = []
        while not self.at("OP", "}"):
            if self.at("EOF"):
                e = self.peek(); raise GamaError("unterminated record declaration", e.line, e.col)
            fn = self.expect("IDENT").val
            self.expect("OP", ":")
            ft = self.expect("IDENT").val
            fields.append((fn, ft))
            self.skip_seps()
            if self.at("OP", ","):
                self.take(); self.skip_seps()
        self.expect("OP", "}")
        return RecordDecl(name, fields, x.line, x.col)
    def fn(self):
        x = self.take(); name = self.expect("IDENT").val; self.expect("OP", "("); self.skip_nl()
        params = []
        if not self.at("OP", ")"):
            while True:
                pn = self.expect("IDENT").val; self.expect("OP", ":"); pt = self.expect("IDENT").val
                default = None
                if self.at("OP", "="):
                    self.take(); self.skip_nl(); default = self.expr()
                params.append(Param(pn, pt, default)); self.skip_nl()
                if not self.at("OP", ","): break
                self.take(); self.skip_nl()
        self.expect("OP", ")"); ret = None
        if self.at("OP", "->"): self.take(); self.skip_nl(); ret = self.expect("IDENT").val
        effects = None
        if self.at("KW", "effects"):
            self.take(); self.expect("OP", "{"); self.skip_nl(); effects = set()
            while not self.at("OP", "}"):
                effects.add(self.expect("IDENT").val); self.skip_nl()
                if self.at("OP", ","): self.take(); self.skip_nl()
            self.expect("OP", "}")
        return Fn(name, params, ret, effects, self.block(), x.line, x.col)
    def lambda_expr(self):
        x = self.take(); self.expect("OP", "("); self.skip_nl()
        params = []
        if not self.at("OP", ")"):
            while True:
                pn = self.expect("IDENT").val; self.expect("OP", ":"); pt = self.expect("IDENT").val
                default = None
                if self.at("OP", "="):
                    self.take(); self.skip_nl(); default = self.expr()
                params.append(Param(pn, pt, default)); self.skip_nl()
                if not self.at("OP", ","): break
                self.take(); self.skip_nl()
        self.expect("OP", ")"); ret = None
        if self.at("OP", "->"): self.take(); self.skip_nl(); ret = self.expect("IDENT").val
        effects = None
        if self.at("KW", "effects"):
            self.take(); self.expect("OP", "{"); self.skip_nl(); effects = set()
            while not self.at("OP", "}"):
                effects.add(self.expect("IDENT").val); self.skip_nl()
                if self.at("OP", ","): self.take(); self.skip_nl()
            self.expect("OP", "}")
        return Lambda(params, ret, effects, self.block(), x.line, x.col)
    def if_stmt(self):
        x = self.take(); cond = self.expr(); self.skip_nl(); then = self.block(); other = None
        save = self.i; self.skip_seps()
        if self.at("KW", "else"):
            self.take(); self.skip_nl()
            other = self.if_stmt() if self.at("KW", "if") else self.block()
        else:
            self.i = save
        return If(cond, then, other, x.line, x.col)
    def enum_decl(self):
        x = self.take()  # enum
        name = self.expect("IDENT").val
        if "." in name:
            raise GamaError("enum name must be a simple identifier", x.line, x.col)
        self.expect("OP", "{"); self.skip_seps()
        variants = []
        while not self.at("OP", "}"):
            v = self.expect("IDENT")
            if "." in v.val:
                raise GamaError("enum variant must be a simple identifier", v.line, v.col)
            payload = None
            if self.at("OP", "("):
                self.take(); self.skip_nl()
                payload_types = []
                while not self.at("OP", ")"):
                    pt = self.expect("IDENT")
                    if "." in pt.val:
                        raise GamaError(
                            "enum payload type must be a simple identifier",
                            pt.line, pt.col)
                    payload_types.append(pt.val)
                    self.skip_nl()
                    if self.at("OP", ","):
                        self.take(); self.skip_nl()
                    elif not self.at("OP", ")"):
                        y = self.peek()
                        raise GamaError(
                            "expected ',' or ')' in enum payload", y.line, y.col)
                self.expect("OP", ")")
                if len(payload_types) == 1:
                    payload = payload_types[0]
                elif payload_types:
                    payload = tuple(payload_types)
            variants.append((v.val, payload, v.line, v.col))
            if self.at("OP", ","):
                self.take(); self.skip_seps()
            elif self.at("NL"):
                self.skip_seps()
            elif not self.at("OP", "}"):
                y = self.peek()
                raise GamaError("expected ',' or '}' in enum", y.line, y.col)
        self.expect("OP", "}")
        return EnumDecl(name, variants, x.line, x.col)
    def match_stmt(self):
        x = self.take(); subject = self.expr(); self.skip_nl(); self.expect("OP", "{")
        arms = []
        while not self.at("OP", "}"):
            self.skip_seps()
            if self.at("OP", "}"): break
            arm = self.take()
            if arm.kind != "IDENT":
                raise GamaError("match arm must be Ok(x), Err(e), Variant(x), Variant, Enum.Variant or _", arm.line, arm.col)
            kind = arm.val
            bindings = None
            if kind == "_":
                pass
            else:
                if self.at("OP", "("):
                    self.take(); self.skip_nl()
                    names = []
                    while not self.at("OP", ")"):
                        names.append(self.expect("IDENT").val)
                        self.skip_nl()
                        if self.at("OP", ","):
                            self.take(); self.skip_nl()
                        elif not self.at("OP", ")"):
                            y = self.peek()
                            raise GamaError(
                                "expected ',' or ')' in match pattern",
                                y.line, y.col)
                    self.expect("OP", ")")
                    if len(names) == 1:
                        bindings = names[0]
                    elif names:
                        bindings = names
                elif kind in ("Ok", "Err"):
                    raise GamaError(
                        f"{kind} match patterns require a binding: {kind}(x)",
                        arm.line, arm.col)
                elif "." in kind and kind.count(".") != 1:
                    raise GamaError("enum match pattern must be Enum.Variant", arm.line, arm.col)
            if self.at("OP", "=>"):
                self.take(); self.skip_nl()
            self.skip_nl(); body = self.block(); arms.append((kind, bindings, body))
            if self.at("OP", ","): self.take()
            self.skip_seps()
        self.expect("OP", "}")
        return Match(subject, arms, x.line, x.col)
    def expr(self): return self.pipe()
    def pipe(self):
        left = self.binary(0); stages = []
        while True:
            save = self.i; self.skip_nl()
            if not self.at("OP", "|"):
                self.i = save; break
            x = self.take(); self.skip_nl()
            st = self.take()
            if st.kind not in ("IDENT", "KW"): raise GamaError("pipe stage expected", st.line, st.col)
            stage = st.val; args = []
            if stage in ("filter", "limit"):
                self.skip_nl(); args = [self.binary(0)]
            elif stage == "select":
                self.skip_nl()
                while True:
                    args.append(self.expect("IDENT").val)
                    if not self.at("OP", ","): break
                    self.take(); self.skip_nl()
            elif stage in ("sum", "map"):
                self.skip_nl(); args = [self.expect("IDENT").val]
            elif stage == "count":
                pass
            else:
                raise GamaError(f"unknown pipe stage {stage!r}", x.line, x.col)
            stages.append((stage, args))
        return Pipe(left, stages, left.line, left.col) if stages else left
    PREC = {"||":1, "&&":2, "==":3, "!=":3, "<":4, "<=":4, ">":4, ">=":4,
            "+":5, "-":5, "*":6, "/":6, "%":6}
    def binary(self, minp):
        left = self.unary()
        while self.at("OP") and self.peek().val in self.PREC and self.PREC[self.peek().val] >= minp:
            op = self.take(); p = self.PREC[op.val]
            # A long expression may continue on the next line after the
            # operator.  Nothing is skipped before the operator, so statement
            # boundaries (and therefore existing programs) are unaffected.
            self.skip_nl()
            right = self.binary(p + 1)
            left = Binary(left, op.val, right, op.line, op.col)
        return left
    def unary(self):
        if self.at("OP") and self.peek().val in ("!", "-"):
            x = self.take(); return Unary(x.val, self.unary(), x.line, x.col)
        return self.postfix()
    def postfix(self):
        e = self.primary()
        while True:
            if self.at("OP", "("):
                x = self.take(); self.skip_nl(); args = []
                if not self.at("OP", ")"):
                    while True:
                        args.append(self.expr()); self.skip_nl()
                        if not self.at("OP", ","): break
                        self.take(); self.skip_nl()
                self.expect("OP", ")"); e = CallExpr(e, args, x.line, x.col)
            elif self.at("OP", "["):
                x = self.take(); idx = self.expr(); self.expect("OP", "]")
                e = Index(e, idx, x.line, x.col)
            elif self.at("OP", "?"):
                x = self.take(); e = Try(e, x.line, x.col)
            else:
                break
        return e
    def primary(self):
        x = self.peek()
        if x.kind == "KW" and x.val == "fn" and self.peek(1).kind == "OP" and self.peek(1).val == "(":
            return self.lambda_expr()
        if x.kind == "NUMBER": self.take(); return Literal(x.val, x.line, x.col)
        if x.kind == "STRING": self.take(); return Literal(x.val, x.line, x.col)
        if x.kind == "KW" and x.val in ("true", "false", "null"):
            self.take(); return Literal({"true":True,"false":False,"null":None}[x.val], x.line, x.col)
        if x.kind == "KW" and x.val == "wait":
            self.take(); self.expect("IDENT", "user")
            args = [self.expr()] if not (self.at("NL") or self.at("OP", ";") or self.at("EOF")) else []
            return Call("wait_user", args, x.line, x.col)
        if x.kind == "IDENT" and x.val == "ask" and self.peek(1).kind != "OP":
            self.take(); arg = self.expr(); args = [arg]
            if self.at("IDENT", "with"):
                self.take(); args.append(self.expr())
            return Call("ask", args, x.line, x.col)
        if x.kind == "IDENT" and x.val == "recall" and self.peek(1).kind == "IDENT":
            self.take(); name = self.take().val
            return Call("recall", [Literal(name, x.line, x.col)], x.line, x.col)
        if x.kind == "IDENT":
            self.take(); name = x.val
            if self.at("OP", "("):
                self.take(); self.skip_nl(); args = []
                if not self.at("OP", ")"):
                    while True:
                        args.append(self.expr()); self.skip_nl()
                        if not self.at("OP", ","): break
                        self.take(); self.skip_nl()
                self.expect("OP", ")"); return Call(name, args, x.line, x.col)
            if name in self.record_names and self.at("OP", "{"):
                # Named record literal: Point { x: 3, y: 4 }
                self.take(); self.skip_nl(); pairs = []
                if not self.at("OP", "}"):
                    while True:
                        k = self.take()
                        if k.kind not in ("IDENT", "STRING", "KW"):
                            raise GamaError("record field name expected", k.line, k.col)
                        self.expect("OP", ":"); self.skip_nl()
                        pairs.append((str(k.val), self.expr())); self.skip_nl()
                        if not self.at("OP", ","): break
                        self.take(); self.skip_nl()
                self.expect("OP", "}")
                return RecordLit(name, pairs, x.line, x.col)
            return Var(name, x.line, x.col)
        if x.kind == "OP" and x.val == "(":
            self.take(); self.skip_nl(); e = self.expr(); self.skip_nl(); self.expect("OP", ")"); return e
        if x.kind == "OP" and x.val == "[":
            self.take(); self.skip_nl(); items = []
            if not self.at("OP", "]"):
                while True:
                    items.append(self.expr()); self.skip_nl()
                    if not self.at("OP", ","): break
                    self.take(); self.skip_nl()
            self.expect("OP", "]"); return ListExpr(items, x.line, x.col)
        if x.kind == "OP" and x.val == "{":
            self.take(); self.skip_nl(); items = {}
            if not self.at("OP", "}"):
                while True:
                    k = self.take()
                    if k.kind not in ("IDENT", "STRING", "KW"): raise GamaError("map key expected", k.line, k.col)
                    self.expect("OP", ":"); self.skip_nl(); items[str(k.val)] = self.expr(); self.skip_nl()
                    if not self.at("OP", ","): break
                    self.take(); self.skip_nl()
            self.expect("OP", "}"); return MapExpr(items, x.line, x.col)
        raise GamaError(f"unexpected token {x.val!r}", x.line, x.col)

def parse(src: str):
    return Parser(lex(src)).program()

# ------------------------------------------------------------------ types / checker
NUM = {"Int", "Decimal"}
def tname(v) -> str:
    if isinstance(v, RecordVal): return v.type_name
    if isinstance(v, ResultVal): return "Result"
    if isinstance(v, EnumVal): return v.enum
    if isinstance(v, FunctionVal): return "Function"
    if v is None: return "Null"
    if isinstance(v, bool): return "Bool"
    if isinstance(v, int): return "Int"
    if isinstance(v, Decimal): return "Decimal"
    if isinstance(v, str): return "Text"
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            fields = set(v[0].keys())
            return TableType(fields) if all(set(x.keys()) == fields for x in v) else TableType()
        return "List"
    if isinstance(v, dict): return "Json"
    return "Any"

class _AmbiguousVariantError(Exception):
    """A bare variant name matches variants of more than one enum."""
    def __init__(self, name: str):
        super().__init__(name); self.name = name

def find_enum_variant(enums, name):
    """Resolve an enum variant reference against ``enums``.

    ``enums`` maps ``Enum -> {Variant: payload_spec}`` where ``payload_spec``
    is ``None`` (nullary), one type name, or a tuple of type names. Accepts
    ``Enum.Variant`` and a bare ``Variant`` that must be unique across all
    declared enums. Returns ``(enum, variant, payload_type)`` or ``None`` when
    the reference is not a declared variant. Raises ``_AmbiguousVariantError``
    for a bare name defined by several enums.
    """
    if "." in name:
        base, _, variant = name.partition(".")
        pm = enums.get(base)
        if pm is not None and variant in pm:
            return base, variant, pm[variant]
        return None
    hits = [(base, name, pm[name]) for base, pm in enums.items() if name in pm]
    if not hits:
        return None
    if len(hits) > 1:
        raise _AmbiguousVariantError(name)
    return hits[0]

def _payload_type_list(payload_type):
    """Normalise a declared payload spec to a tuple of type names.

    ``None`` means a nullary variant, a plain string is one payload, and a
    tuple is already the multi-payload form.  Kept separate so single-payload
    declarations remain plain strings for backward compatibility.
    """
    if payload_type is None:
        return ()
    if isinstance(payload_type, tuple):
        return payload_type
    return (payload_type,)

def _binding_list(bindings):
    """Normalise a match arm binding spec to a list of names.

    ``None`` means no binding, a plain string is one binding (kept as a string
    for IR-subset compatibility), and a list/tuple is already multi-binding.
    """
    if bindings is None:
        return []
    if isinstance(bindings, str):
        return [bindings]
    return list(bindings)

BUILTINS = {
    "sql": ({"sql.read"}, "Table"),
    "ask": ({"ask"}, "Text"),
    "remember": ({"memory.write"}, "Bool"),
    "recall": ({"memory.read"}, "Any"),
    "wait_user": ({"hitl"}, "Bool"),
    "len": (set(), "Int"),
    "text": (set(), "Text"),
    "number": (set(), "Decimal"),
    "sql_write": ({"sql.write"}, "Bool"),
    "mail": ({"mail"}, "Bool"),
    "round": (set(), "Decimal"),
    "Ok": (set(), "Result"),
    "Err": (set(), "Result"),
    # Result helpers
    "unwrap": (set(), "Any"),
    "unwrap_or": (set(), "Any"),
    "is_ok": (set(), "Bool"),
    "is_err": (set(), "Bool"),
    # Text helpers
    "split": (set(), "List"),
    "join": (set(), "Text"),
    "contains": (set(), "Bool"),
    "trim": (set(), "Text"),
    "upper": (set(), "Text"),
    "lower": (set(), "Text"),
    "replace": (set(), "Text"),
    "starts_with": (set(), "Bool"),
    "ends_with": (set(), "Bool"),
    # Collection helpers
    "keys": (set(), "List"),
    "values": (set(), "List"),
    "has": (set(), "Bool"),
    # Numeric helpers
    "abs": (set(), "Any"),
    "min": (set(), "Any"),
    "max": (set(), "Any"),
    "floor": (set(), "Int"),
    "ceil": (set(), "Int"),
    # Text/List helpers
    "reverse": (set(), "Any"),
    "slice": (set(), "Any"),
    "index_of": (set(), "Int"),
    "sort": (set(), "List"),
    # Conversion helpers
    "to_int": (set(), "Int"),
}

# Static signatures for the pure core builtins: (min_args, max_args, wants).
# `wants` is indexed by argument position; the last entry repeats. A `None`
# slot means "any type"; the type "Any" always skips the static check.
_CORE_SIGS: dict[str, tuple[int, int, list]] = {
    "unwrap": (1, 1, [{"Result"}]),
    "unwrap_or": (2, 2, [{"Result"}, None]),
    "is_ok": (1, 1, [{"Result"}]),
    "is_err": (1, 1, [{"Result"}]),
    "split": (2, 2, [{"Text"}, {"Text"}]),
    "join": (2, 2, [{"List", "Table"}, {"Text"}]),
    "contains": (2, 2, [{"Text", "List", "Table"}, None]),
    "trim": (1, 1, [{"Text"}]),
    "upper": (1, 1, [{"Text"}]),
    "lower": (1, 1, [{"Text"}]),
    "replace": (3, 3, [{"Text"}, {"Text"}, {"Text"}]),
    "starts_with": (2, 2, [{"Text"}, {"Text"}]),
    "ends_with": (2, 2, [{"Text"}, {"Text"}]),
    "keys": (1, 1, [{"Json"}]),
    "values": (1, 1, [{"Json"}]),
    "has": (2, 2, [{"Json"}, {"Text"}]),
    # Numeric helpers (min/max accept one or more numbers)
    "abs": (1, 1, [{"Int", "Decimal"}]),
    "min": (1, 64, [{"Int", "Decimal"}]),
    "max": (1, 64, [{"Int", "Decimal"}]),
    "floor": (1, 1, [{"Int", "Decimal"}]),
    "ceil": (1, 1, [{"Int", "Decimal"}]),
    # Text/List helpers
    "reverse": (1, 1, [{"Text", "List"}]),
    "slice": (3, 3, [{"Text", "List"}, {"Int"}, {"Int"}]),
    "index_of": (2, 2, [{"Text", "List"}, None]),
    "sort": (1, 1, [{"List"}]),
    # Conversion helpers
    "to_int": (1, 1, [{"Int", "Decimal", "Text"}]),
}

# Builtins whose runtime handlers read positional arguments directly need a
# static arity guard so a bad call is a ``GamaTypeError`` instead of a bare
# ``IndexError``.  ``len`` additionally has a known argument domain.
_SIMPLE_ARITY: dict[str, tuple[int, int]] = {
    "len": (1, 1),
    "text": (1, 1),
    "number": (1, 1),
    "round": (1, 2),
}
_LEN_TYPES = {"Text", "List", "Table", "Json"}

def check_simple_sig(name: str, types: list, call: Call) -> None:
    """Validate arity (and, for ``len``, the argument domain) of a builtin."""
    mn, mx = _SIMPLE_ARITY[name]
    if not (mn <= len(types) <= mx):
        raise GamaTypeError(
            f"{name} expects {mn}..{mx} args, got {len(types)}", call.line, call.col)
    if name == "len":
        t = types[0]
        if t != "Any" and t not in _LEN_TYPES:
            raise GamaTypeError(
                f"len expects Text/List/Table/Json, got {t}", call.line, call.col)

def _dyn_numeric(types: list) -> str:
    """Numeric builtins preserve Decimal when present, otherwise Int."""
    if any(t == "Any" for t in types):
        return "Any"
    return "Decimal" if "Decimal" in types else "Int"

def _dyn_first(types: list) -> str:
    """Builtins that preserve the Text/List type of their first argument."""
    t = types[0] if types else "Any"
    return t if t in ("Text", "List") else "Any"

_DYN_RET = {
    "abs": _dyn_numeric,
    "min": _dyn_numeric,
    "max": _dyn_numeric,
    "reverse": _dyn_first,
    "slice": _dyn_first,
    "index_of": lambda types: "Int",
    "sort": lambda types: "List",
    "to_int": lambda types: "Int",
}

def check_core_sig(name: str, types: list, call: Call) -> None:
    """Validate arity and argument types for a pure core builtin."""
    mn, mx, wants = _CORE_SIGS[name]
    if not (mn <= len(types) <= mx):
        raise GamaTypeError(
            f"{name} expects {mn}..{mx} args, got {len(types)}", call.line, call.col)
    for i, t in enumerate(types):
        want = wants[min(i, len(wants) - 1)]
        if want is None or t == "Any":
            continue
        if t not in want:
            raise GamaTypeError(
                f"{name}: arg {i + 1} is {t}, expected {'/'.join(sorted(want))}",
                call.line, call.col)

def _need_args(name: str, args: list, n: int, line: int = 0, col: int = 0) -> None:
    if len(args) != n:
        raise GamaRuntimeError(
            f"{name} expects {n} args, got {len(args)}", line, col)

def _as_text(v, name: str, line: int = 0, col: int = 0) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, Decimal):
        return format(v, "f")
    raise GamaRuntimeError(f"{name} expects Text, got {tname(v)}", line, col)

def _as_number(v, name: str, line: int = 0, col: int = 0):
    """Return a GAMA numeric value (Int or Decimal), rejecting Bool.

    Non-finite decimals (NaN/Infinity) are rejected here as a defensive
    backstop so numeric builtins can never leak ``decimal.InvalidOperation``.
    """
    if isinstance(v, bool) or not isinstance(v, (int, Decimal)):
        raise GamaRuntimeError(
            f"{name} expects a number, got {tname(v)}", line, col)
    if isinstance(v, Decimal) and not v.is_finite():
        raise GamaRuntimeError(
            f"{name}: non-finite number {v} is not supported", line, col)
    return v

def _join_item(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, Decimal):
        return format(v, "f")
    if v is None:
        return ""
    return str(v)

def eval_core_builtin(name: str, args: list, line: int = 0, col: int = 0):
    """Evaluate a pure core builtin on already-evaluated arguments."""
    if name == "unwrap":
        _need_args(name, args, 1, line, col)
        r = args[0]
        if not isinstance(r, ResultVal):
            raise GamaRuntimeError("unwrap expects Result", line, col)
        if not r.ok:
            raise GamaRuntimeError(f"unwrap: {r}", line, col)
        return r.value
    if name == "unwrap_or":
        _need_args(name, args, 2, line, col)
        r = args[0]
        if not isinstance(r, ResultVal):
            raise GamaRuntimeError("unwrap_or expects Result", line, col)
        return r.value if r.ok else args[1]
    if name == "is_ok":
        _need_args(name, args, 1, line, col)
        r = args[0]
        if not isinstance(r, ResultVal):
            raise GamaRuntimeError("is_ok expects Result", line, col)
        return r.ok
    if name == "is_err":
        _need_args(name, args, 1, line, col)
        r = args[0]
        if not isinstance(r, ResultVal):
            raise GamaRuntimeError("is_err expects Result", line, col)
        return not r.ok
    if name == "split":
        _need_args(name, args, 2, line, col)
        s = _as_text(args[0], name, line, col)
        sep = _as_text(args[1], name, line, col)
        if sep == "":
            raise GamaRuntimeError("split: separator must not be empty", line, col)
        return s.split(sep)
    if name == "join":
        _need_args(name, args, 2, line, col)
        seq = args[0]
        if not isinstance(seq, (list, tuple)):
            raise GamaRuntimeError("join expects List", line, col)
        sep = _as_text(args[1], name, line, col)
        return sep.join(_join_item(x) for x in seq)
    if name == "contains":
        _need_args(name, args, 2, line, col)
        hay, needle = args
        if isinstance(hay, str):
            return _as_text(needle, name, line, col) in hay
        if isinstance(hay, (list, tuple)):
            return needle in hay
        raise GamaRuntimeError("contains expects Text or List", line, col)
    if name == "trim":
        _need_args(name, args, 1, line, col)
        return _as_text(args[0], name, line, col).strip()
    if name == "upper":
        _need_args(name, args, 1, line, col)
        return _as_text(args[0], name, line, col).upper()
    if name == "lower":
        _need_args(name, args, 1, line, col)
        return _as_text(args[0], name, line, col).lower()
    if name == "replace":
        _need_args(name, args, 3, line, col)
        s = _as_text(args[0], name, line, col)
        old = _as_text(args[1], name, line, col)
        new = _as_text(args[2], name, line, col)
        return s.replace(old, new)
    if name == "starts_with":
        _need_args(name, args, 2, line, col)
        s = _as_text(args[0], name, line, col)
        return s.startswith(_as_text(args[1], name, line, col))
    if name == "ends_with":
        _need_args(name, args, 2, line, col)
        s = _as_text(args[0], name, line, col)
        return s.endswith(_as_text(args[1], name, line, col))
    if name == "keys":
        _need_args(name, args, 1, line, col)
        m = args[0]
        if not isinstance(m, dict):
            raise GamaRuntimeError("keys expects Json", line, col)
        return list(m.keys())
    if name == "values":
        _need_args(name, args, 1, line, col)
        m = args[0]
        if not isinstance(m, dict):
            raise GamaRuntimeError("values expects Json", line, col)
        return list(m.values())
    if name == "has":
        _need_args(name, args, 2, line, col)
        m, key = args
        if not isinstance(m, dict):
            raise GamaRuntimeError("has expects Json", line, col)
        return _as_text(key, name, line, col) in m
    if name == "abs":
        _need_args(name, args, 1, line, col)
        v = _as_number(args[0], name, line, col)
        return -v if v < 0 else v
    if name in ("min", "max"):
        if not args:
            raise GamaRuntimeError(f"{name} expects at least 1 arg", line, col)
        vals = [_as_number(a, name, line, col) for a in args]
        return min(vals) if name == "min" else max(vals)
    if name in ("floor", "ceil"):
        _need_args(name, args, 1, line, col)
        v = _as_number(args[0], name, line, col)
        if isinstance(v, int):
            return v
        return math.floor(v) if name == "floor" else math.ceil(v)
    if name == "reverse":
        _need_args(name, args, 1, line, col)
        v = args[0]
        if isinstance(v, str):
            return v[::-1]
        if isinstance(v, list):
            return list(reversed(v))
        raise GamaRuntimeError(
            f"reverse expects Text or List, got {tname(v)}", line, col)
    if name == "slice":
        _need_args(name, args, 3, line, col)
        seq, start, end = args
        if not isinstance(seq, (str, list)):
            raise GamaRuntimeError(
                f"slice expects Text or List, got {tname(seq)}", line, col)
        for label, v in (("start", start), ("end", end)):
            if isinstance(v, bool) or not isinstance(v, int):
                raise GamaRuntimeError(
                    f"slice {label} must be Int, got {tname(v)}", line, col)
        n = len(seq)
        if start < 0 or end < 0:
            raise GamaRuntimeError("slice bounds must be >= 0", line, col)
        if start > end:
            raise GamaRuntimeError("slice start must be <= end", line, col)
        if end > n:
            raise GamaRuntimeError(
                f"slice end {end} out of range for length {n}", line, col)
        return seq[start:end]
    if name == "index_of":
        _need_args(name, args, 2, line, col)
        hay, needle = args
        if isinstance(hay, str):
            if not isinstance(needle, str):
                raise GamaRuntimeError(
                    f"index_of on Text needs Text, got {tname(needle)}", line, col)
            return hay.find(needle)
        if isinstance(hay, list):
            for i, item in enumerate(hay):
                if item == needle:
                    return i
            return -1
        raise GamaRuntimeError(
            f"index_of expects Text or List, got {tname(hay)}", line, col)
    if name == "sort":
        _need_args(name, args, 1, line, col)
        seq = args[0]
        if not isinstance(seq, list):
            raise GamaRuntimeError(
                f"sort expects List, got {tname(seq)}", line, col)
        if not seq:
            return []
        kinds = {tname(x) for x in seq}
        if kinds <= {"Int", "Decimal"} or kinds == {"Text"}:
            try:
                return sorted(seq)
            except (InvalidOperation, TypeError, ValueError) as ex:
                raise GamaRuntimeError(
                    f"sort: cannot order values ({ex})", line, col) from None
        raise GamaRuntimeError(
            "sort expects a list of all numbers or all Text", line, col)
    if name == "to_int":
        _need_args(name, args, 1, line, col)
        v = args[0]
        if isinstance(v, bool):
            raise GamaRuntimeError("to_int expects a number or Text", line, col)
        if isinstance(v, int):
            return v
        if isinstance(v, Decimal):
            if not v.is_finite() or v != v.to_integral_value():
                raise GamaRuntimeError(
                    f"to_int: {v} is not an integral finite Decimal", line, col)
            try:
                return int(v)
            except (ValueError, OverflowError):
                raise GamaRuntimeError(
                    "to_int: integer value is too large", line, col) from None
        if isinstance(v, str):
            s = v.strip()
            if not re.fullmatch(r"[+-]?\d+", s):
                raise GamaRuntimeError(
                    f"to_int: cannot parse {v!r} as an integer", line, col)
            try:
                return int(s)
            except (ValueError, OverflowError):
                raise GamaRuntimeError(
                    "to_int: integer text is too large (Python digit limit)",
                    line, col) from None
        raise GamaRuntimeError(
            f"to_int expects Int, integral Decimal, or Text, got {tname(v)}",
            line, col)
    raise GamaRuntimeError(f"unknown builtin {name}", line, col)

def sqlite_schema() -> dict[str, set[str]] | None:
    """All user tables and columns in GAMA_L_DB (read-only), or None."""
    db = os.environ.get("GAMA_L_DB", "")
    if not db or not pathlib.Path(db).exists(): return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        out = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            out[name] = {r[1] for r in con.execute(f'PRAGMA table_info("{name}")')}
        con.close(); return out or None
    except Exception:
        return None

def sql_fields(query: str) -> set[str] | None:
    schema = sqlite_schema()
    if not schema: return None
    m = re.search(r"\bfrom\s+[\"`\[]?([A-Za-z_]\w*)", query, re.I)
    if m and m.group(1) in schema:
        return schema[m.group(1)]
    return set().union(*schema.values())

_SQL_WRITE_RE = re.compile(r"^\s*(insert|update|delete)\b", re.I)

def sql_write(query: str, line: int = 0, col: int = 0) -> bool:
    """Execute exactly one INSERT/UPDATE/DELETE against GAMA_L_DB.

    The language has no bind arguments yet, so the statement is sent as-is
    (parameterless); callers must quote literals themselves. Writes are
    disabled unless GAMA_L_ALLOW_WRITE=1. DDL and stacked statements are
    rejected before touching the database; this is a guard, not a full SQL
    parser.
    """
    dbp = os.environ.get("GAMA_L_DB", "").strip()
    if not dbp:
        raise GamaRuntimeError("sql_write: GAMA_L_DB is not set", line, col)
    dbpath = pathlib.Path(dbp).resolve()
    if not dbpath.is_file():
        raise GamaRuntimeError("sql_write: database does not exist: %s" % dbpath, line, col)
    if os.environ.get("GAMA_L_ALLOW_WRITE") != "1":
        raise GamaRuntimeError(
            "sql_write: writes disabled (set GAMA_L_ALLOW_WRITE=1)", line, col)
    q = query.strip()
    if q.endswith(";"):
        q = q[:-1].rstrip()
    if ";" in q:
        raise GamaRuntimeError("sql_write: exactly one statement allowed", line, col)
    if not _SQL_WRITE_RE.match(q):
        raise GamaRuntimeError(
            "sql_write: only INSERT/UPDATE/DELETE allowed", line, col)
    try:
        con = sqlite3.connect(f"file:{dbpath}?mode=rw", uri=True)
        try:
            con.execute(q)
            con.commit()
        finally:
            con.close()
    except sqlite3.Error as ex:
        raise GamaRuntimeError("sql_write failed: %s" % ex, line, col) from None
    return True

class Checker:
    def __init__(self):
        self.scope: list[dict[str, str]] = [{}]
        self.funcs: dict[str, Fn] = {}
        self.records: dict[str, RecordDecl] = {}
        self.enums: dict[str, set[str]] = {}
        self.effects: set[str] = set()
        self.ret_type: str | None = None
        self.loop_depth = 0
    def push(self): self.scope.append({})
    def pop(self): self.scope.pop()
    def get(self, n):
        for s in reversed(self.scope):
            if n in s: return s[n]
        return None
    def set(self, n, t): self.scope[-1][n] = t
    def program(self, stmts):
        for s in stmts:
            if isinstance(s, RecordDecl):
                if s.name in self.records:
                    e = GamaTypeError(f"duplicate record {s.name}", s.line, s.col)
                    e.src = getattr(s, "_src", None)
                    raise e
                if s.name in BUILTINS:
                    e = GamaTypeError(
                        f"record {s.name} collides with an existing name",
                        s.line, s.col)
                    e.src = getattr(s, "_src", None)
                    raise e
                seen = set()
                for fn, _ft in s.fields:
                    if fn in seen:
                        e = GamaTypeError(
                            f"record {s.name}: duplicate field {fn}", s.line, s.col)
                        e.src = getattr(s, "_src", None)
                        raise e
                    seen.add(fn)
                self.records[s.name] = s
        for s in stmts:
            if isinstance(s, Fn):
                if s.name in self.funcs:
                    e = GamaTypeError("duplicate function", s.line, s.col)
                    e.src = getattr(s, "_src", None)
                    raise e
                if s.name in self.records:
                    e = GamaTypeError(
                        f"function {s.name} collides with record {s.name}",
                        s.line, s.col)
                    e.src = getattr(s, "_src", None)
                    raise e
                self.funcs[s.name] = s
            elif isinstance(s, EnumDecl):
                if s.name in self.enums:
                    e = GamaTypeError(f"duplicate enum {s.name}", s.line, s.col)
                    e.src = getattr(s, "_src", None)
                    raise e
                seen = set()
                for vn, _vp, vl, vc in s.variants:
                    if vn in seen:
                        e = GamaTypeError(
                            f"duplicate enum variant {s.name}.{vn}", vl, vc)
                        e.src = getattr(s, "_src", None)
                        raise e
                    seen.add(vn)
                self.enums[s.name] = {vn: vp for vn, vp, _vl, _vc in s.variants}
        for s in stmts:
            if isinstance(s, EnumDecl):
                continue
            try:
                self.stmt(s, None)
            except GamaError as e:
                if e.src is None:
                    e.src = getattr(s, "_src", None)
                raise
    def stmt(self, s, allowed):
        if isinstance(s, Import):
            raise GamaError("import requires check_file/run_file", s.line, s.col)
        if isinstance(s, EnumDecl):
            raise GamaError("enum declarations must be top-level", s.line, s.col)
        if isinstance(s, Use):
            return
        if isinstance(s, RecordDecl):
            return
        if isinstance(s, For):
            t = self.expr(s.iterable)
            if t not in ("List", "Table", "Text", "Any"):
                raise GamaTypeError("for needs List/Table/Text", s.line, s.col)
            self.push(); self.set(s.name, "Any"); self.loop_depth += 1
            [self.stmt(x, allowed) for x in s.body]
            self.loop_depth -= 1; self.pop()
            return
        if isinstance(s, While):
            ct = self.expr(s.cond)
            if ct != "Bool": raise GamaTypeError("while condition must be Bool", s.line, s.col)
            self.loop_depth += 1; [self.stmt(x, allowed) for x in s.body]; self.loop_depth -= 1
            return
        if isinstance(s, Match):
            t = self.expr(s.expr)
            if t not in ("Result", "Any") and t not in self.enums:
                raise GamaTypeError("match expects Result or enum", s.line, s.col)
            for kind, bindings, body in s.arms:
                names = _binding_list(bindings)
                payload_types = ()
                if kind == "_":
                    if names:
                        raise GamaTypeError(
                            "match: _ pattern cannot bind a payload",
                            s.line, s.col)
                elif kind in ("Ok", "Err"):
                    if t not in ("Result", "Any"):
                        raise GamaTypeError(
                            f"match: {kind} pattern does not match subject type {t}",
                            s.line, s.col)
                    if len(names) != 1:
                        raise GamaTypeError(
                            f"match: {kind} pattern binds exactly one value: "
                            f"{kind}(x)", s.line, s.col)
                    payload_types = ("Any",)
                else:
                    try:
                        resolved = find_enum_variant(self.enums, kind)
                    except _AmbiguousVariantError:
                        raise GamaTypeError(
                            f"ambiguous enum variant {kind}; write Enum.{kind}",
                            s.line, s.col) from None
                    if resolved is None:
                        raise GamaTypeError(
                            f"unknown enum variant {kind}", s.line, s.col)
                    base, variant, payload_type = resolved
                    if t not in ("Any", base):
                        raise GamaTypeError(
                            f"match: {kind} pattern does not match subject type {t}",
                            s.line, s.col)
                    payload_types = _payload_type_list(payload_type)
                    if not payload_types and names:
                        raise GamaTypeError(
                            f"enum variant {base}.{variant} is nullary and cannot bind a payload",
                            s.line, s.col)
                    if payload_types and not names:
                        raise GamaTypeError(
                            f"enum variant {base}.{variant} requires a payload pattern "
                            f"{variant}(x)", s.line, s.col)
                    if payload_types and len(names) != len(payload_types):
                        raise GamaTypeError(
                            f"enum variant {base}.{variant} expects "
                            f"{len(payload_types)} payload binding"
                            f"{'s' if len(payload_types) != 1 else ''}, "
                            f"got {len(names)}", s.line, s.col)
                self.push()
                for name, ptype in zip(names, payload_types):
                    self.set(name, ptype or "Any")
                [self.stmt(x, allowed) for x in body]
                self.pop()
            return
        if isinstance(s, (Break, Continue)):
            if self.loop_depth <= 0: raise GamaTypeError("break/continue outside loop", s.line, s.col)
            return
        if isinstance(s, Let):
            # Bind the name before checking a lambda body so recursive
            # lambdas can refer to themselves.
            if isinstance(s.expr, Lambda):
                self.set(s.name, s.type_name or "Function")
                t = self.expr(s.expr)
                if s.type_name and not self.compat(t, s.type_name):
                    raise GamaTypeError(f"{s.name}: {t} != {s.type_name}", s.line, s.col)
                self.set(s.name, s.type_name or t)
            else:
                t = self.expr(s.expr)
                if s.type_name and not self.compat(t, s.type_name): raise GamaTypeError(f"{s.name}: {t} != {s.type_name}", s.line, s.col)
                self.set(s.name, s.type_name or t)
        elif isinstance(s, Assign):
            t = self.expr(s.expr)
            cur = self.get(s.name)
            if cur is None and "." in s.name:
                self.check_field_assign(s, t)
            elif cur is None: self.set(s.name, t)
            elif t != "Any" and not self.compat(t, cur): raise GamaTypeError(f"{s.name}: {t} != {cur}", s.line, s.col)
        elif isinstance(s, Fn):
            self.fn(s)
        elif isinstance(s, If):
            ct = self.expr(s.cond)
            if ct != "Bool": raise GamaTypeError(f"if condition must be Bool, got {ct}", s.line, s.col)
            self.push(); [self.stmt(x, allowed) for x in s.then]; self.pop()
            if s.other:
                self.push()
                # ``else if`` stores a nested If node rather than a block;
                # both branches (and its nested else) must be checked.
                if isinstance(s.other, If):
                    self.stmt(s.other, allowed)
                else:
                    [self.stmt(x, allowed) for x in s.other]
                self.pop()
        elif isinstance(s, Return):
            if self.ret_type is None: raise GamaTypeError("return outside function", s.line, s.col)
            rt = self.expr(s.expr)
            if rt != "Any" and not self.compat(rt, self.ret_type): raise GamaTypeError(f"return {rt} != {self.ret_type}", s.line, s.col)
        elif isinstance(s, Prove):
            self.expr(s.expr); self.effects.add("proof.write")
            self.set(s.name, "Json")
        elif isinstance(s, Verify): self.expr(s.expr); self.effects.add("proof.read")
        elif isinstance(s, Print): self.expr(s.expr); self.effects.add("io.print")
        elif isinstance(s, ExprStmt): self.expr(s.expr)
    def fn(self, f: Fn):
        self.push()
        for p in f.params: self.set(p.name, p.type_name)
        old = self.effects; self.effects = set()
        old_ret = self.ret_type; self.ret_type = f.ret or "Any"
        for s in f.body: self.stmt(s, f.effects or set())
        used = set(self.effects); self.effects = old; self.ret_type = old_ret
        allowed = f.effects or set()
        bad = used - allowed
        if bad: raise GamaEffectError(f"function {f.name} uses undeclared effects: {','.join(sorted(bad))}", f.line, f.col)
        self.pop()
    def compat(self, got, want):
        return want in ("Any", got) or (want in NUM and got in NUM) or (want == "List" and got in ("List", "Table"))
    def check_field_assign(self, s, value_type):
        """Validate a dotted assignment target ``base.field[.field...]``.

        The longest dotted prefix that names a variable is the receiver; the
        remaining segments must be record fields, the last one being the
        assigned field. The checker never records a shadow variable for the
        dotted target, so later reads still resolve through the base record.
        """
        parts = s.name.split(".")
        base_n = None
        for i in range(len(parts) - 1, 0, -1):
            if self.get(".".join(parts[:i])) is not None:
                base_n = i
                break
        if base_n is None:
            raise GamaTypeError(
                f"unknown variable {parts[0]} in assignment target {s.name}",
                s.line, s.col)
        t = self.get(".".join(parts[:base_n]))
        for part in parts[base_n:-1]:
            t = self.record_field_type(t, part, s)
        ftype = self.record_field_type(t, parts[-1], s)
        if value_type != "Any" and not self.compat(value_type, ftype):
            raise GamaTypeError(f"{s.name}: {value_type} != {ftype}",
                                s.line, s.col)
    def record_field_type(self, t, field, s):
        if t not in self.records:
            raise GamaTypeError(
                f"cannot assign field {field!r} on non-record type {t}",
                s.line, s.col)
        fmap = dict(self.records[t].fields)
        if field not in fmap:
            raise GamaTypeError(f"unknown field {field!r} on {t}",
                                s.line, s.col)
        return fmap[field]
    def expr(self, e, row=None) -> str:
        if isinstance(e, Literal): return tname(e.value)
        if isinstance(e, Lambda):
            self.push()
            for p in e.params: self.set(p.name, p.type_name)
            old = self.effects; self.effects = set()
            old_ret = self.ret_type; self.ret_type = e.ret or "Any"
            for s in e.body: self.stmt(s, e.effects or set())
            used = set(self.effects); self.effects = old; self.ret_type = old_ret
            allowed = e.effects or set()
            self.pop()
            bad = used - allowed
            if bad:
                raise GamaEffectError(
                    "lambda uses undeclared effects: " + ",".join(sorted(bad)), e.line, e.col)
            return "Function"
        if isinstance(e, ListExpr):
            types = {self.expr(x, row) for x in e.items}
            return "Table" if types and types <= {"Json"} else "List"
        if isinstance(e, MapExpr): [self.expr(x, row) for x in e.items.values()]; return "Json"
        if isinstance(e, Index):
            bt = self.expr(e.base, row); it = self.expr(e.index, row)
            if bt not in ("List", "Table", "Text", "Any"):
                raise GamaTypeError("index needs List/Table/Text", e.line, e.col)
            if it != "Int": raise GamaTypeError("index must be Int", e.line, e.col)
            return "Text" if bt == "Text" else ("Json" if bt == "Table" else "Any")
        if isinstance(e, Try):
            t = self.expr(e.expr, row)
            if t not in ("Result", "Any"):
                raise GamaTypeError("? expects Result", e.line, e.col)
            if self.ret_type is None:
                raise GamaTypeError("? outside function", e.line, e.col)
            if self.ret_type not in ("Result", "Any"):
                raise GamaTypeError("? requires function returning Result", e.line, e.col)
            return "Any"
        if isinstance(e, RecordLit):
            rec = self.records.get(e.name)
            if rec is None:
                raise GamaTypeError(f"unknown record {e.name}", e.line, e.col)
            decl = dict(rec.fields)
            seen = set()
            for k, val in e.fields:
                if k in seen:
                    raise GamaTypeError(
                        f"{e.name}: duplicate field {k}", e.line, e.col)
                seen.add(k)
                if k not in decl:
                    raise GamaTypeError(
                        f"{e.name}: unknown field {k}", e.line, e.col)
                at = self.expr(val, row)
                if not self.compat(at, decl[k]):
                    raise GamaTypeError(
                        f"{e.name}.{k}: {at} != {decl[k]}", e.line, e.col)
            missing = [fn for fn, _ft in rec.fields if fn not in seen]
            if missing:
                raise GamaTypeError(
                    f"{e.name}: missing field{'s' if len(missing) > 1 else ''} "
                    + ", ".join(missing), e.line, e.col)
            return e.name
        if isinstance(e, Attr):
            t = self.expr(e.base)
            if t not in ("Json", "Any", "Table"): raise GamaTypeError("field access needs Json/Table", e.line, e.col)
            return "Any"
        if isinstance(e, Var):
            t = self.get(e.name)
            if t is not None: return t
            # A named function used as a value has type Function.
            if e.name in self.funcs: return "Function"
            if "." in e.name:
                base, _, variant = e.name.partition(".")
                if base in self.enums:
                    if variant not in self.enums[base]:
                        raise GamaTypeError(
                            f"unknown enum variant {e.name}", e.line, e.col)
                    if self.enums[base][variant] is not None:
                        raise GamaTypeError(
                            f"enum variant {e.name} requires a payload argument; "
                            f"write {variant}(...)", e.line, e.col)
                    return base
            if "." in e.name:
                parts = e.name.split(".")
                for i in range(len(parts) - 1, 0, -1):
                    base = self.get(".".join(parts[:i]))
                    if base is None:
                        continue
                    t = base
                    for part in parts[i:]:
                        if t in self.records:
                            fmap = dict(self.records[t].fields)
                            if part not in fmap:
                                raise GamaTypeError(
                                    f"unknown field {part!r} on {t}",
                                    e.line, e.col)
                            t = fmap[part]
                        else:
                            t = "Any"
                    return t
            if row is not None:
                if e.name in row: return "Any"
                raise GamaTypeError(f"unknown column {e.name!r}", e.line, e.col)
            raise GamaTypeError(f"unknown variable {e.name}", e.line, e.col)
        if isinstance(e, Unary):
            t = self.expr(e.right, row)
            if e.op == "!" and t != "Bool": raise GamaTypeError("! expects Bool", e.line, e.col)
            if e.op == "-" and t not in NUM: raise GamaTypeError("- expects number", e.line, e.col)
            return "Bool" if e.op == "!" else t
        if isinstance(e, Binary):
            a = self.expr(e.left, row); b = self.expr(e.right, row)
            if e.op in ("+", "-", "*", "/", "%"):
                if e.op == "+" and a == b == "Text": return "Text"
                if "Any" in (a, b): return "Any"
                if a not in NUM or b not in NUM: raise GamaTypeError(f"{e.op} expects numbers", e.line, e.col)
                return "Decimal" if "Decimal" in (a, b) or e.op == "/" else "Int"
            if e.op in ("<", "<=", ">", ">="):
                if "Any" in (a, b): return "Bool"
                if not ((a in NUM and b in NUM) or (a == b == "Text")):
                    raise GamaTypeError(f"{e.op} expects two numbers or two texts", e.line, e.col)
                return "Bool"
            if e.op in ("&&", "||"):
                if a != "Bool" or b != "Bool": raise GamaTypeError(f"{e.op} expects Bool", e.line, e.col)
                return "Bool"
            if "Any" in (a, b): return "Bool"
            if a != b and not (a in NUM and b in NUM): raise GamaTypeError("== expects matching types", e.line, e.col)
            return "Bool"
        if isinstance(e, Call):
            if e.name in self.records:
                rec = self.records[e.name]
                if len(e.args) != len(rec.fields):
                    raise GamaTypeError(
                        f"{e.name} expects {len(rec.fields)} fields, got "
                        f"{len(e.args)}", e.line, e.col)
                for (fn, ft), a in zip(rec.fields, e.args):
                    at = self.expr(a, row)
                    if not self.compat(at, ft):
                        raise GamaTypeError(
                            f"{e.name}.{fn}: {at} != {ft}", e.line, e.col)
                return e.name
            if e.name in BUILTINS:
                eff, rt = BUILTINS[e.name]
                self.effects |= eff
                types = [self.expr(a, row) for a in e.args]
                if e.name in _CORE_SIGS:
                    check_core_sig(e.name, types, e)
                if e.name in _SIMPLE_ARITY:
                    check_simple_sig(e.name, types, e)
                if e.name in _DYN_RET:
                    return _DYN_RET[e.name](types)
                if e.name == "sql":
                    q = e.args[0].value if e.args and isinstance(e.args[0], Literal) else ""
                    fields = sql_fields(q)
                    if fields is not None: return TableType(fields)
                    if not os.environ.get("GAMA_L_DB"):
                        return TableType(DEMO_ROWS[0].keys())
                    return TableType(None)
                return rt
            f = self.funcs.get(e.name)
            if not f:
                vt = self.get(e.name)
                if vt == "Function":
                    for a in e.args: self.expr(a)
                    return "Any"
                if vt is not None:
                    raise GamaTypeError(f"{e.name} is not callable ({vt})", e.line, e.col)
                try:
                    ctor = find_enum_variant(self.enums, e.name)
                except _AmbiguousVariantError:
                    raise GamaTypeError(
                        f"ambiguous enum constructor {e.name}; write Enum.{e.name}",
                        e.line, e.col) from None
                if ctor is not None:
                    base, variant, payload_t = ctor
                    if payload_t is None:
                        raise GamaTypeError(
                            f"enum variant {base}.{variant} is nullary; write "
                            f"{base}.{variant} without parentheses", e.line, e.col)
                    payload_types = _payload_type_list(payload_t)
                    if len(e.args) != len(payload_types):
                        raise GamaTypeError(
                            f"enum variant {base}.{variant} expects "
                            f"{len(payload_types)} payload argument"
                            f"{'s' if len(payload_types) != 1 else ''}, "
                            f"got {len(e.args)}", e.line, e.col)
                    for idx, (arg, ptype) in enumerate(
                            zip(e.args, payload_types)):
                        at = self.expr(arg)
                        if not self.compat(at, ptype):
                            where = ("" if len(payload_types) == 1
                                     else f" {idx + 1}")
                            raise GamaTypeError(
                                f"{base}.{variant}: payload{where} {at} != {ptype}",
                                e.line, e.col)
                    return base
                raise GamaTypeError(f"unknown function {e.name}", e.line, e.col)
            required = sum(1 for p in f.params if p.default is None)
            if not (required <= len(e.args) <= len(f.params)):
                raise GamaTypeError(f"{e.name} expects {required}..{len(f.params)} args", e.line, e.col)
            for p, a in zip(f.params, e.args):
                at = self.expr(a)
                if not self.compat(at, p.type_name): raise GamaTypeError(f"{e.name}: {p.name} {at} != {p.type_name}", e.line, e.col)
            self.effects |= (f.effects or set())
            return f.ret or "Any"
        if isinstance(e, CallExpr):
            ct = self.expr(e.callee)
            if ct not in ("Function", "Any"):
                raise GamaTypeError("expression is not callable", e.line, e.col)
            callee = e.callee
            if isinstance(callee, Lambda):
                required = sum(1 for p in callee.params if p.default is None)
                if not (required <= len(e.args) <= len(callee.params)):
                    raise GamaTypeError(
                        f"lambda expects {required}..{len(callee.params)} args, got {len(e.args)}",
                        e.line, e.col)
                for p, a in zip(callee.params, e.args):
                    at = self.expr(a)
                    if not self.compat(at, p.type_name):
                        raise GamaTypeError(f"lambda: {p.name} {at} != {p.type_name}", e.line, e.col)
                return callee.ret or "Any"
            if isinstance(callee, Var) and callee.name in self.funcs:
                f = self.funcs[callee.name]
                required = sum(1 for p in f.params if p.default is None)
                if not (required <= len(e.args) <= len(f.params)):
                    raise GamaTypeError(
                        f"{callee.name} expects {required}..{len(f.params)} args, got {len(e.args)}",
                        e.line, e.col)
                for p, a in zip(f.params, e.args):
                    at = self.expr(a)
                    if not self.compat(at, p.type_name):
                        raise GamaTypeError(f"{callee.name}: {p.name} {at} != {p.type_name}", e.line, e.col)
                return f.ret or "Any"
            for a in e.args: self.expr(a)
            return "Any"
        if isinstance(e, Pipe):
            t = self.expr(e.base, row)
            if t not in ("List", "Table", "Any"):
                raise GamaTypeError("pipe needs List/Table", e.line, e.col)
            for stage, args in e.stages:
                if t not in ("List", "Table", "Any"):
                    raise GamaTypeError(f"pipe stage {stage!r} needs List/Table", e.line, e.col)
                fields = t.fields if isinstance(t, TableType) else (None if t == "Any" else set())
                if stage == "filter":
                    self.expr(args[0], row=fields); t = TableType(fields)
                elif stage == "select":
                    if fields is not None:
                        for f in args:
                            if f not in fields: raise GamaTypeError(f"unknown column {f!r}", e.line, e.col)
                    t = TableType(args)
                elif stage == "sum":
                    if fields is not None and args and args[0] not in fields:
                        raise GamaTypeError(f"unknown column {args[0]!r}", e.line, e.col)
                    t = "Decimal"
                elif stage == "limit":
                    lt = self.expr(args[0], row)
                    if lt != "Int": raise GamaTypeError("limit expects Int", e.line, e.col)
                    t = TableType(fields)
                elif stage == "map":
                    if fields is not None and args and args[0] not in fields:
                        raise GamaTypeError(f"unknown column {args[0]!r}", e.line, e.col)
                    t = "List"
                elif stage == "count":
                    t = "Int"
            return t
        return "Any"

def _project_root(entry: pathlib.Path) -> pathlib.Path:
    for p in [entry.parent, *entry.parents]:
        if (p / "gamal.toml").exists():
            return p
    return entry.parent

_ALIAS_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Aliased modules expose `fn` definitions and top-level `let` constants.
# Plain imports (flattened inside the module) and `use` are allowed as
# declaration/no-op statements. Everything else at the top level is a side
# effect (print/prove/verify/loops/assignments/expression statements) and is
# rejected up front instead of being silently dropped by the bounded
# namespace MVP. Top-level `let` values are evaluated at import time and
# exposed as `alias.name`; module functions keep seeing them through the same
# renamed binding.
_ALIAS_ALLOWED_TOPLEVEL = (Fn, Let, Import, Use, RecordDecl)
_STMT_KIND = {Let: "let", Assign: "assignment", Print: "print",
              Prove: "prove", Verify: "verify",
              ExprStmt: "expression statement"}

def _alias_toplevel_error(stmt, alias: str) -> GamaError:
    kind = _STMT_KIND.get(type(stmt), type(stmt).__name__.lower())
    return GamaError(
        "aliased import %r: unsupported top-level statement %r "
        "(only fn definitions, top-level let constants, plain imports, "
        "and use are allowed)"
        % (alias, kind),
        getattr(stmt, "line", 0), getattr(stmt, "col", 0))

def _check_alias_toplevel(stmts, alias: str) -> None:
    for st in stmts:
        if not isinstance(st, _ALIAS_ALLOWED_TOPLEVEL):
            raise _alias_toplevel_error(st, alias)

def _rename_refs(node, mapping):
    """Rename module-local function uses (calls and value refs) in the AST.

    Only names present in ``mapping`` are rewritten; every other node is kept.
    This also renames the ``Fn`` declaration itself when the whole node is
    passed, so sibling and recursive calls keep pointing at the namespaced
    function.
    """
    if isinstance(node, list):
        return [_rename_refs(x, mapping) for x in node]
    if isinstance(node, tuple):
        return tuple(_rename_refs(x, mapping) for x in node)
    if isinstance(node, dict):
        return {k: _rename_refs(v, mapping) for k, v in node.items()}
    if isinstance(node, Call):
        return replace(node, name=mapping.get(node.name, node.name),
                       args=[_rename_refs(a, mapping) for a in node.args])
    if isinstance(node, Var):
        return replace(node, name=mapping.get(node.name, node.name))
    if isinstance(node, RecordDecl):
        return replace(
            node, name=mapping.get(node.name, node.name),
            fields=[(fn, mapping.get(ft, ft)) for fn, ft in node.fields])
    if isinstance(node, RecordLit):
        return replace(
            node, name=mapping.get(node.name, node.name),
            fields=[(k, _rename_refs(v, mapping)) for k, v in node.fields])
    if isinstance(node, Let):
        return replace(
            node, name=mapping.get(node.name, node.name),
            type_name=(mapping.get(node.type_name, node.type_name)
                       if node.type_name else node.type_name),
            expr=_rename_refs(node.expr, mapping))
    if isinstance(node, Assign):
        return replace(node, name=mapping.get(node.name, node.name),
                       expr=_rename_refs(node.expr, mapping))
    if isinstance(node, Param):
        return replace(
            node, type_name=mapping.get(node.type_name, node.type_name),
            default=(_rename_refs(node.default, mapping)
                     if node.default is not None else None))
    if isinstance(node, Fn):
        return replace(
            node, name=mapping.get(node.name, node.name),
            ret=(mapping.get(node.ret, node.ret) if node.ret else node.ret),
            params=[_rename_refs(p, mapping) for p in node.params],
            body=_rename_refs(node.body, mapping))
    if isinstance(node, Lambda):
        return replace(
            node,
            ret=(mapping.get(node.ret, node.ret) if node.ret else node.ret),
            params=[_rename_refs(p, mapping) for p in node.params],
            body=_rename_refs(node.body, mapping))
    if is_dataclass(node):
        changes = {}
        for f in fields(node):
            v = getattr(node, f.name)
            if isinstance(v, (list, tuple, dict, Call, Var)) or is_dataclass(v):
                nv = _rename_refs(v, mapping)
                if nv is not v:
                    changes[f.name] = nv
        if changes:
            return replace(node, **changes)
    return node

def _mark_src(node, path) -> None:
    """Record the file a top-level statement came from (tooling provenance)."""
    try:
        node._src = str(path)
    except (AttributeError, TypeError):
        pass


def _flatten(path: pathlib.Path, root: pathlib.Path | None = None,
             seen: set | None = None, loaded: set | None = None,
             _top: bool = True) -> list:
    path = path.resolve(); root = (root or _project_root(path)).resolve()
    if seen is None: seen = set()
    if loaded is None: loaded = set()
    try:
        inside = os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        inside = False
    if not inside:
        raise GamaError("import outside project root: %s" % path)
    if path in seen:
        raise GamaError("import cycle: %s" % path)
    if path in loaded:
        return []
    if len(seen) > 64:
        raise GamaError("import depth limit exceeded")
    seen.add(path)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as ex:
        raise GamaError("cannot import %s: %s" % (path, ex)) from None
    try:
        ast = parse(source)
    except GamaError as ex:
        if not _top and ex.src is None:
            ex.src = str(path)
        raise
    # Top-level names declared in this file. Aliases must not shadow them and
    # generated `alias.fn` names must not clash with them.
    declared = set()
    for s in ast:
        if isinstance(s, (Fn, Let, Assign, Use, RecordDecl)):
            declared.add(s.name)
    out = []
    aliases = set()
    for s in ast:
        if isinstance(s, Import):
            candidates = [path.parent / s.path,
                          root / ".gamal" / "modules" / s.path,
                          root / "gama_modules" / s.path]
            target = next((c for c in candidates if c.exists()), None)
            if target is None:
                raise GamaError("cannot import %s" % s.path, s.line, s.col)
            if s.alias is None:
                sub = _flatten(target, root, seen, loaded, _top=False)
                for st in sub:
                    out.append(st)
                    if isinstance(st, Fn):
                        declared.add(st.name)
                continue
            # Namespaced import: expose fn definitions and let constants.
            if not _ALIAS_IDENT.match(s.alias):
                raise GamaError("invalid import alias %r" % s.alias, s.line, s.col)
            if s.alias in aliases:
                raise GamaError("duplicate import alias %r" % s.alias, s.line, s.col)
            if s.alias in declared:
                raise GamaError(
                    "import alias %r collides with existing top-level name" % s.alias,
                    s.line, s.col)
            aliases.add(s.alias)
            # Expand the module with a fresh `loaded` set: the same module may
            # be imported under two different aliases. `seen` is shared so
            # cycles are still detected.
            sub = _flatten(target, root, seen, set(), _top=False)
            try:
                _check_alias_toplevel(sub, s.alias)
            except GamaError as ex:
                if ex.src is None:
                    ex.src = str(target)
                raise
            # Explicit exports filter the alias surface. Modules without any
            # `export` keep the legacy behaviour: every top-level fn/let/record
            # is visible through the alias (backward compatibility).
            exported = {st.name for st in sub
                        if getattr(st, "exported", False)
                        and isinstance(st, (Fn, Let, RecordDecl))}
            if exported:
                names = {}
                for st in sub:
                    if (isinstance(st, (Fn, Let, RecordDecl))
                            and st.name in exported):
                        if st.name in names:
                            raise GamaError(
                                "duplicate export %r in imported module %r"
                                % (st.name, s.path), st.line, st.col)
                        names[st.name] = st
            sub_decls = [st for st in sub if isinstance(st, (Fn, Let, RecordDecl))]
            mapping = {}
            for st in sub_decls:
                if exported and st.name not in exported:
                    # Private declaration: keep it callable inside the module
                    # but unreachable as `alias.name`. A separate character
                    # (`:`) cannot be spelled in a source identifier, so this
                    # internal name cannot collide with user code.
                    mapping[st.name] = "%s::private::%s" % (s.alias, st.name)
                else:
                    mapping[st.name] = s.alias + "." + st.name
            for st in sub_decls:
                new_name = mapping[st.name]
                public = not exported or st.name in exported
                if public:
                    if new_name in declared:
                        raise GamaError(
                            "import alias %r produces duplicate name %r"
                            % (s.alias, new_name), s.line, s.col)
                    declared.add(new_name)
                renamed = replace(_rename_refs(st, mapping), name=new_name)
                _mark_src(renamed, getattr(st, "_src", target))
                out.append(renamed)
        else:
            if not _top:
                _mark_src(s, path)
            out.append(s)
    seen.discard(path); loaded.add(path)
    return out

def load_file(path: str):
    p = pathlib.Path(path).resolve()
    return _flatten(p, _project_root(p))

def check(src: str):
    ast = parse(src)
    Checker().program(ast)
    return ast

def check_file(path: str):
    ast = load_file(path); Checker().program(ast); return ast

def run_file(path: str, interactive=False):
    ast = check_file(path)
    return Interpreter(interactive=interactive).run(ast)

def _brace_delta(line: str) -> int:
    """Net braces on a line, ignoring strings and comments."""
    d = 0; i = 0; q = None; n = len(line)
    while i < n:
        c = line[i]
        if q:
            if c == "\\": i += 2; continue
            if c == q: q = None
        elif c in "\"'": q = c
        elif c == "#": break
        elif c == "{": d += 1
        elif c == "}": d -= 1
        i += 1
    return d

def fmt_source(src: str, width: int = 88) -> str:
    """Canonical formatter (falls back to indentation-only if module absent)."""
    try:
        from gama_fmt import format_source
        return format_source(src, width=width)
    except ImportError:
        pass
    lines = [ln.rstrip() for ln in src.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]: lines.pop(0)
    while lines and not lines[-1]: lines.pop()
    out = []; depth = 0
    for raw in lines:
        text = raw.strip()
        if not text:
            out.append(""); continue
        starts = text[0] if text else ""
        lead = 1 if (starts == "}" or text.startswith("else")) else 0
        out.append("  " * max(0, depth - lead) + text)
        depth = max(0, depth + _brace_delta(text))
    return "\n".join(out) + "\n"

# ------------------------------------------------------------------ evaluator
class ReturnSignal(Exception):
    def __init__(self, value): self.value = value

class PropagateSignal(Exception):
    def __init__(self, value): self.value = value

class BreakSignal(Exception): pass
class ContinueSignal(Exception): pass

class Env:
    def __init__(self, parent=None): self.v = {}; self.parent = parent
    def get(self, n):
        if n in self.v: return self.v[n]
        if self.parent: return self.parent.get(n)
        raise GamaRuntimeError(f"unknown variable {n}")
    def set(self, n, v): self.v[n] = v
    def assign(self, n, v):
        if n in self.v: self.v[n] = v; return
        if self.parent: self.parent.assign(n, v); return
        self.v[n] = v

DEMO_ROWS = [
    {"konto": "2111176", "naziv": "ALFA HADŽIĆI", "mjesto": "Hadžići", "neto": Decimal("100.00")},
    {"konto": "2112000", "naziv": "PRIMJER DOO", "mjesto": "Sarajevo", "neto": Decimal("250.00")},
]

def _proof_raw(name, value):
    """Canonical JSON used for proof hashing and idempotency comparison."""
    return json.dumps({"name": name, "value": value}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"), default=str)

def _proof_env(name):
    """Read an intent/lease env var; empty or whitespace means unset."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None

def _proof_binding(intent, lease):
    """Canonical intent/lease suffix for proof hashing and signing.

    Unset (None, None) returns an empty suffix so legacy receipts keep the
    exact legacy payload ``prev + raw`` and their old hashes/signatures.
    Set values are appended deterministically so the chain hash binds the
    receipt to its intent and lease as well.
    """
    if intent is None and lease is None:
        return ""
    parts = []
    if intent is not None:
        parts.append("intent=" + json.dumps(
            intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if lease is not None:
        parts.append("lease=" + json.dumps(
            lease, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return "\n" + "\n".join(parts)

def _proof_payload(prev, raw, intent=None, lease=None):
    """Single canonical payload for both the hash chain and the HMAC signature."""
    return str(prev) + raw + _proof_binding(intent, lease)

def _ledger_scan(data):
    """Parse proof-ledger JSONL text and validate it as one hash chain.

    Returns ``(records, problems)``. ``records`` holds every valid JSON object
    in file order. ``problems`` is a deduplicated, stable list of integrity
    codes:

    - ``malformed_line``: a non-empty line is not a JSON object
    - ``missing_sha``: a record has no usable ``sha256``
    - ``broken_chain``: a ``prev`` is neither ``GENESIS`` on the first record
      nor the immediately preceding record's hash (gap, reorder or tamper)
    - ``forked_chain``: two records share a predecessor (the ledger branched)
    """
    records = []
    problems = []
    for line in (data or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            problems.append("malformed_line")
            continue
        if not isinstance(rec, dict):
            problems.append("malformed_line")
            continue
        records.append(rec)
    seen_sha = set()
    prev_uses = {}
    for i, rec in enumerate(records):
        sha = str(rec.get("sha256", "") or "")
        prev = rec.get("prev")
        if not sha:
            problems.append("missing_sha")
        expected = "GENESIS" if i == 0 else str(records[i - 1].get("sha256", "") or "")
        if prev != expected:
            # Reusing an already-seen predecessor means the file branched;
            # anything else is a gap, reorder or tamper.
            if i > 0 and prev in seen_sha:
                problems.append("forked_chain")
            else:
                problems.append("broken_chain")
        if isinstance(prev, str):
            prev_uses[prev] = prev_uses.get(prev, 0) + 1
        if sha:
            seen_sha.add(sha)
    if any(n > 1 for n in prev_uses.values()):
        problems.append("forked_chain")
    out = []
    for code in problems:
        if code not in out:
            out.append(code)
    return records, out

class Interpreter:
    def __init__(self, interactive=False):
        self.env = Env(); self.funcs = {}; self.records = {}; self.enums = {}; self.out = []; self.memory = {}
        self.proofs = {}; self.proof_chain = []; self.prev_proof = "GENESIS"; self.interactive = interactive
        self._ledger_records = []; self._ledger_problems = []
        self.last_ask = None
        self.call_depth = 0
        self.out_chars = 0
        self.steps = 0
        self.expr_steps = 0
        self.proof_ledger_path = (os.environ.get("GAMA_L_PROOF_LEDGER") or "").strip() or None
        if self.proof_ledger_path:
            self._load_proof_ledger()
    def _load_proof_ledger(self):
        """Load persisted proof records so chains and idempotency survive a run.

        Reads under a shared POSIX ``flock`` so it never observes a line that
        another process is still writing. The whole chain is validated; a fork
        or gap is recorded (``verify`` reports it, ``prove`` refuses to extend
        it) rather than silently accepted.
        """
        path = self.proof_ledger_path
        records, problems, data = [], [], None
        try:
            import fcntl as _fcntl
        except ImportError:
            _fcntl = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                locked = False
                if _fcntl is not None:
                    try:
                        _fcntl.flock(fh.fileno(), _fcntl.LOCK_SH)
                        locked = True
                    except OSError:
                        locked = False
                try:
                    data = fh.read()
                finally:
                    if locked:
                        try:
                            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                        except OSError:
                            pass
        except FileNotFoundError:
            data = None
        if data is not None:
            records, problems = _ledger_scan(data)
        self._install_ledger_state(records)
        self._ledger_problems = problems
    def _install_ledger_state(self, records):
        """Make ``records`` (in file order) the authoritative in-memory view."""
        self._ledger_records = list(records)
        chain = []
        proofs = {}
        for rec in records:
            sha = str(rec.get("sha256", "") or "")
            if sha:
                chain.append(sha)
            name = rec.get("name")
            if isinstance(name, str) and name not in proofs:
                proofs[name] = rec
        self.proof_chain = chain
        self.proofs = proofs
        self.prev_proof = chain[-1] if chain else "GENESIS"
        self._ledger_problems = []
    def _ledger_prove(self, name, val, raw, intent, lease):
        """Serialize load + tail-read + hash + append under one exclusive lock.

        The lock spans re-reading the ledger, duplicate/binding checks, hash and
        HMAC computation, and the append. Concurrent processes therefore cannot
        both extend the same predecessor: each sees the other's already-committed
        record and chains after it. A forked or malformed ledger is refused
        instead of being extended.

        Without ``fcntl`` (non-POSIX) the re-read still removes the common
        read-then-write race, but cross-process atomicity becomes best-effort.
        """
        path = self.proof_ledger_path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            import fcntl as _fcntl
        except ImportError:
            _fcntl = None
        with open(path, "a+", encoding="utf-8") as fh:
            locked = False
            if _fcntl is not None:
                try:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)
                    locked = True
                except OSError:
                    locked = False
            try:
                fh.seek(0)
                data = fh.read()
                records, problems = _ledger_scan(data)
                if problems:
                    raise GamaRuntimeError(
                        "prove: proof ledger is not a single valid chain ("
                        + ", ".join(problems) + f"): {path}")
                for old in records:
                    if old.get("name") != name:
                        continue
                    self._check_proof_binding(name, old, intent, lease)
                    if (old.get("value") == val
                            or _proof_raw(old.get("name"), old.get("value")) == raw):
                        self._install_ledger_state(records)
                        return old
                    raise GamaRuntimeError(
                        f"prove: duplicate key {name!r} with different value")
                prev = str(records[-1].get("sha256")) if records else "GENESIS"
                payload = _proof_payload(prev, raw, intent, lease)
                h = hashlib.sha256(payload.encode()).hexdigest()
                rec = {"name": name, "value": val, "sha256": h, "prev": prev,
                       "id": h[:16], "ts": __import__("time").time()}
                if intent is not None:
                    rec["intent"] = intent
                if lease is not None:
                    rec["lease"] = lease
                key = os.environ.get("GAMA_L_PROOF_KEY", "").encode()
                if key:
                    rec["sig"] = hmac.new(
                        key, payload.encode(), hashlib.sha256).hexdigest()
                if data and not data.endswith("\n"):
                    fh.write("\n")
                fh.write(json.dumps(rec, ensure_ascii=False,
                                    separators=(",", ":"), default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                self._install_ledger_state(records + [rec])
                return rec
            finally:
                if locked:
                    try:
                        _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                    except OSError:
                        pass
    @staticmethod
    def _env_int(name, default, strict=False, line=0, col=0):
        """Parse an integer env limit.

        Blank or malformed values fall back to ``default`` by default.  With
        ``strict=True`` they raise a clear ``GamaRuntimeError`` instead, which
        callers such as the while-loop limit use so a typo cannot silently
        disable (or crash) a guard.
        """
        raw = os.environ.get(name)
        if raw is None:
            # Unset always means "use the default"; strict only governs values
            # that are present but not a valid integer.
            return default
        if not raw.strip():
            if strict:
                raise GamaRuntimeError(
                    f"{name} must be an integer, got {raw!r}", line, col)
            return default
        try:
            return int(raw)
        except ValueError:
            if strict:
                raise GamaRuntimeError(
                    f"{name} must be an integer, got {raw!r}",
                    line, col) from None
            return default
    @staticmethod
    def _env_optional_int(name):
        """Return int from env, or None when unset/blank/malformed/negative."""
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return None
        try:
            val = int(raw)
        except ValueError:
            return None
        return val if val >= 0 else None
    def _step_limit(self):
        return self._env_optional_int("GAMA_L_MAX_STEPS")
    def _tick(self, line=0, col=0):
        """Count one executed statement or loop iteration and enforce the optional cap."""
        self.steps += 1
        limit = self._step_limit()
        if limit is not None and self.steps > limit:
            raise GamaRuntimeError(
                f"step limit exceeded: {self.steps} > GAMA_L_MAX_STEPS={limit}",
                line, col)
    def _expr_step_limit(self):
        return self._env_optional_int("GAMA_L_MAX_EXPR_STEPS")
    def _expr_tick(self, line=0, col=0):
        """Count one Interpreter.expr evaluation (nested evaluations count separately)."""
        self.expr_steps += 1
        limit = self._expr_step_limit()
        if limit is not None and self.expr_steps > limit:
            raise GamaRuntimeError(
                f"expression step limit exceeded: {self.expr_steps} > GAMA_L_MAX_EXPR_STEPS={limit}",
                line, col)
    def _emit(self, text):
        """Append one rendered output line, enforcing the output char cap."""
        text = str(text)
        limit = self._env_int("GAMA_L_MAX_OUTPUT_CHARS", 1000000)
        total = self.out_chars + len(text)
        if total > limit:
            raise GamaRuntimeError(
                f"output limit exceeded: {total} chars > GAMA_L_MAX_OUTPUT_CHARS={limit}")
        self.out.append(text)
        self.out_chars = total
    def run(self, stmts):
        # Register top-level enums up front so functions declared before an
        # enum can still reference its variants when they are invoked.
        for s in stmts:
            if isinstance(s, EnumDecl) and s.name not in self.enums:
                self.enums[s.name] = {v[0]: v[1] for v in s.variants}
        try:
            # Record declarations are nominal and may be referenced before the
            # declaration line; register them up front like the checker does.
            for s in stmts:
                if isinstance(s, RecordDecl):
                    self.records[s.name] = s
            for s in stmts: self.stmt(s, self.env)
        except PropagateSignal:
            raise GamaRuntimeError("? outside function") from None
        return self
    def stmt(self, s, env):
        self._tick(s.line, s.col)
        if isinstance(s, Let): env.set(s.name, self.expr(s.expr, env))
        elif isinstance(s, Assign):
            value = self.expr(s.expr, env)
            if "." in s.name and not self.env_has(env, s.name):
                self.assign_field(s.name, value, env, s.line, s.col)
            else:
                env.assign(s.name, value)
        elif isinstance(s, Use): pass
        elif isinstance(s, RecordDecl): self.records[s.name] = s
        elif isinstance(s, EnumDecl):
            self.enums.setdefault(s.name, {v[0]: v[1] for v in s.variants})
        elif isinstance(s, For):
            seq = self.expr(s.iterable, env)
            if isinstance(seq, str): seq = list(seq)
            if not isinstance(seq, (list, tuple)): raise GamaRuntimeError("for needs list/text", s.line, s.col)
            for item in seq:
                self._tick(s.line, s.col)
                local = Env(env); local.set(s.name, item)
                try:
                    for x in s.body: self.stmt(x, local)
                except ContinueSignal: continue
                except BreakSignal: break
        elif isinstance(s, While):
            n = 0
            while self.truth(self.expr(s.cond, env)):
                n += 1
                self._tick(s.line, s.col)
                loop_limit = self._env_int(
                    "GAMA_L_LOOP_LIMIT", 1000000, strict=True,
                    line=s.line, col=s.col)
                if n > loop_limit:
                    raise GamaRuntimeError(
                        f"loop limit exceeded: {n} > GAMA_L_LOOP_LIMIT={loop_limit}",
                        s.line, s.col)
                try:
                    for x in s.body: self.stmt(x, env)
                except ContinueSignal: continue
                except BreakSignal: break
        elif isinstance(s, Match):
            rv = self.expr(s.expr, env)
            if not isinstance(rv, (ResultVal, EnumVal)):
                # Keep the historical message for non-Result subjects; the
                # IR-subset parity tests compare this text byte-for-byte.
                raise GamaRuntimeError(
                    "match expects Result", s.line, s.col)
            for kind, bindings, body in s.arms:
                if kind == "_":
                    hit = True
                elif kind in ("Ok", "Err"):
                    hit = (isinstance(rv, ResultVal)
                           and (kind == "Ok") == rv.ok)
                elif isinstance(rv, EnumVal):
                    try:
                        resolved = find_enum_variant(self.enums, kind)
                    except _AmbiguousVariantError:
                        resolved = None
                    if resolved is None:
                        hit = False
                    else:
                        base, variant, _payload_t = resolved
                        hit = rv.enum == base and rv.variant == variant
                else:
                    hit = False
                if hit:
                    local = Env(env)
                    names = _binding_list(bindings)
                    if names and isinstance(rv, ResultVal):
                        if len(names) != 1:
                            raise GamaRuntimeError(
                                f"match: {kind} pattern binds exactly one value",
                                s.line, s.col)
                        local.set(names[0], rv.value)
                    elif names and isinstance(rv, EnumVal):
                        if rv.payloads is None:
                            raise GamaRuntimeError(
                                f"enum variant {rv.enum}.{rv.variant} is "
                                f"nullary and cannot bind a payload",
                                s.line, s.col)
                        if len(names) != len(rv.payloads):
                            raise GamaRuntimeError(
                                f"enum variant {rv.enum}.{rv.variant} expects "
                                f"{len(rv.payloads)} payload binding"
                                f"{'s' if len(rv.payloads) != 1 else ''}, "
                                f"got {len(names)}", s.line, s.col)
                        for name, value in zip(names, rv.payloads):
                            local.set(name, value)
                    for x in body: self.stmt(x, local)
                    return
            raise GamaRuntimeError("match: no arm matched", s.line, s.col)
        elif isinstance(s, Break): raise BreakSignal()
        elif isinstance(s, Continue): raise ContinueSignal()
        elif isinstance(s, Fn): self.funcs[s.name] = (s, env)
        elif isinstance(s, If):
            if self.truth(self.expr(s.cond, env)):
                blk = Env(env)
                for x in s.then: self.stmt(x, blk)
            elif s.other:
                blk = Env(env)
                # ``else if`` is a nested If node; run it in this scope.
                if isinstance(s.other, If):
                    self.stmt(s.other, blk)
                else:
                    for x in s.other: self.stmt(x, blk)
        elif isinstance(s, Return): raise ReturnSignal(self.expr(s.expr, env))
        elif isinstance(s, Prove):
            val = self.expr(s.expr, env)
            rec = self.prove(s.name, val)
            env.set(s.name, rec); self.proofs[s.name] = rec
        elif isinstance(s, Verify):
            _v = self.verify(self.expr(s.expr, env))
            env.set("_verify", _v); self._emit(self.render(_v))
        elif isinstance(s, Print): self._emit(self.render(self.expr(s.expr, env)))
        elif isinstance(s, ExprStmt): self.expr(s.expr, env)
    def truth(self, v): return bool(v)
    def render(self, v):
        try:
            if isinstance(v, Decimal): return format(v, "f")
            if isinstance(v, (dict, list)):
                return json.dumps(v, ensure_ascii=False, default=str)
            if isinstance(v, RecordVal): return str(v)
            if isinstance(v, ResultVal): return str(v)
            return str(v)
        except (ValueError, OverflowError) as ex:
            # CPython's int->str digit limit raises ValueError; never leak it.
            raise GamaRuntimeError(
                f"cannot render value to text: {ex}") from None
    @staticmethod
    def _check_proof_binding(name, rec, intent, lease):
        """Enforce ``(intent, lease, key)`` uniqueness for one proof key.

        A proof key is bound to the intent/lease recorded when it was first
        created (or loaded from the ledger). Re-proving the same key under a
        different binding is an error, never a silent idempotent hit, so a
        receipt cannot be replayed under another intent or lease.
        """
        if intent is not None:
            old_intent = rec.get("intent")
            if old_intent != intent:
                raise GamaRuntimeError(
                    f"prove: proof {name!r} is bound to intent {old_intent!r}, "
                    f"not {intent!r}")
        if lease is not None:
            old_lease = rec.get("lease")
            if old_lease != lease:
                raise GamaRuntimeError(
                    f"prove: proof {name!r} is bound to lease {old_lease!r}, "
                    f"not {lease!r}")
    def prove(self, name, val):
        raw = _proof_raw(name, val)
        intent = _proof_env("GAMA_L_PROOF_INTENT")
        lease = _proof_env("GAMA_L_PROOF_LEASE")
        if self.proof_ledger_path:
            # The ledger is authoritative: load, duplicate check, tail read and
            # append all happen under one exclusive lock, so concurrent runs
            # cannot both extend the same predecessor.
            return self._ledger_prove(name, val, raw, intent, lease)
        if name in self.proofs:
            old = self.proofs[name]
            if old.get("value") == val or _proof_raw(old.get("name"), old.get("value")) == raw:
                self._check_proof_binding(name, old, intent, lease)
                return old
            raise GamaRuntimeError(f"prove: duplicate key {name!r} with different value")
        payload = _proof_payload(self.prev_proof, raw, intent, lease)
        h = hashlib.sha256(payload.encode()).hexdigest()
        rec = {"name": name, "value": val, "sha256": h, "prev": self.prev_proof,
               "id": h[:16], "ts": __import__("time").time()}
        if intent is not None:
            rec["intent"] = intent
        if lease is not None:
            rec["lease"] = lease
        key = os.environ.get("GAMA_L_PROOF_KEY", "").encode()
        if key:
            rec["sig"] = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        self.prev_proof = h
        self.proof_chain.append(h)
        self.proofs[name] = rec
        return rec
    def verify(self, rec):
        if not isinstance(rec, dict) or "sha256" not in rec: return {"ok": False, "errors": ["not_a_proof"]}
        raw = _proof_raw(rec.get("name"), rec.get("value"))
        rec_intent = rec.get("intent")
        rec_lease = rec.get("lease")
        prev = rec.get("prev", "GENESIS")
        payload = _proof_payload(prev, raw, rec_intent, rec_lease)
        exp = hashlib.sha256(payload.encode()).hexdigest()
        errs = []
        if not hmac.compare_digest(exp, str(rec.get("sha256"))): errs.append("hash_mismatch")
        if rec.get("prev") not in ("GENESIS",) and rec.get("prev") not in self.proof_chain:
            errs.append("untrusted_chain")
        # A malformed or forked ledger is never silently usable: any verify in
        # a process that loaded it reports the integrity failure.
        for code in self._ledger_problems:
            if code not in errs:
                errs.append(code)
        key = os.environ.get("GAMA_L_PROOF_KEY", "").encode()
        sig = rec.get("sig")
        if sig:
            if not key:
                errs.append("missing_key")
            else:
                want = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(want, str(sig)): errs.append("bad_signature")
        elif os.environ.get("GAMA_L_REQUIRE_SIG") == "1":
            errs.append("missing_signature")
        env_intent = _proof_env("GAMA_L_PROOF_INTENT")
        env_lease = _proof_env("GAMA_L_PROOF_LEASE")
        if env_intent is not None:
            if rec_intent is None:
                errs.append("intent_missing")
            elif rec_intent != env_intent:
                errs.append("intent_mismatch")
        if env_lease is not None:
            if rec_lease is None:
                errs.append("lease_missing")
            elif rec_lease != env_lease:
                errs.append("lease_mismatch")
        if os.environ.get("GAMA_L_REQUIRE_INTENT_LEASE") == "1":
            if env_intent is None:
                errs.append("intent_not_configured")
            if env_lease is None:
                errs.append("lease_not_configured")
        return {"ok": not errs, "expected": exp, "errors": errs}
    def invoke(self, fv, args, env, row, line, col):
        configured = self._env_int("GAMA_L_MAX_CALL_DEPTH", DEFAULT_CALL_DEPTH)
        # Even an explicit high value must not exceed what CPython can hold.
        depth_limit = min(configured, _call_depth_cap())
        self.call_depth += 1
        try:
            if self.call_depth > depth_limit:
                raise GamaRuntimeError(
                    f"call depth limit exceeded: {self.call_depth} > "
                    f"GAMA_L_MAX_CALL_DEPTH={configured} "
                    f"(effective cap {depth_limit})",
                    line, col)
            required = sum(1 for p in fv.params if p.default is None)
            if not (required <= len(args) <= len(fv.params)):
                raise GamaRuntimeError(
                    f"{fv.name} expects {required}..{len(fv.params)} args, got {len(args)}",
                    line, col)
            local = Env(fv.closure)
            for i, p in enumerate(fv.params):
                if i < len(args):
                    value = self.expr(args[i], env, row)
                else:
                    value = self.expr(p.default, fv.closure)
                local.set(p.name, value)
            try:
                for s in fv.body: self.stmt(s, local)
            except ReturnSignal as r: return r.value
            except PropagateSignal as p: return ResultVal(False, p.value)
            return None
        except RecursionError:
            # Backstop: if the CPython stack is exhausted before the depth
            # guard (e.g. a raised recursionlimit with very frame-heavy
            # expressions), surface a GAMA error, never RecursionError.
            raise GamaRuntimeError(
                "call depth exceeded the Python recursion limit", line, col) from None
        finally:
            self.call_depth -= 1
    @staticmethod
    def env_has(env, name):
        try:
            env.get(name)
            return True
        except GamaRuntimeError:
            return False
    def assign_field(self, name, value, env, line, col):
        """Mutate a record field in place for a dotted assignment target.

        The longest dotted prefix bound in ``env`` is the receiver; remaining
        segments descend through nested records. Only ``RecordVal`` receivers
        are supported: JSON/dict field assignment is a static type error.
        """
        parts = name.split(".")
        base_n = None
        for i in range(len(parts) - 1, 0, -1):
            if self.env_has(env, ".".join(parts[:i])):
                base_n = i
                break
        if base_n is None:
            raise GamaRuntimeError(
                f"unknown variable {parts[0]} in assignment target {name}",
                line, col)
        obj = env.get(".".join(parts[:base_n]))
        for part in parts[base_n:-1]:
            if not isinstance(obj, RecordVal):
                raise GamaRuntimeError(
                    f"cannot assign field {part!r} on non-record value",
                    line, col)
            if part not in obj.fields:
                raise GamaRuntimeError(f"unknown field {name}", line, col)
            obj = obj.fields[part]
        if not isinstance(obj, RecordVal):
            raise GamaRuntimeError(
                f"cannot assign field {parts[-1]!r} on non-record value",
                line, col)
        if parts[-1] not in obj.fields:
            raise GamaRuntimeError(f"unknown field {name}", line, col)
        obj.fields[parts[-1]] = value
    def expr(self, e, env, row=None):
        """Evaluate one expression node and count it as one expression step.

        Nested evaluations (operands, arguments, list/map items, pipe stages,
        function arguments/defaults, etc.) recurse through this method and are
        therefore counted individually.  The cap is disabled by default and is
        independent of GAMA_L_MAX_STEPS.
        """
        self._expr_tick(getattr(e, "line", 0), getattr(e, "col", 0))
        if isinstance(e, Literal): return e.value
        if isinstance(e, RecordLit):
            rec = self.records.get(e.name)
            if rec is None:
                raise GamaRuntimeError(f"unknown record {e.name}", e.line, e.col)
            decl = dict(rec.fields)
            values = {}
            for k, val in e.fields:
                if k in values:
                    raise GamaRuntimeError(
                        f"{e.name}: duplicate field {k}", e.line, e.col)
                if k not in decl:
                    raise GamaRuntimeError(
                        f"{e.name}: unknown field {k}", e.line, e.col)
                values[k] = self.expr(val, env, row)
            missing = [fn for fn, _ft in rec.fields if fn not in values]
            if missing:
                raise GamaRuntimeError(
                    f"{e.name}: missing field{'s' if len(missing) > 1 else ''} "
                    + ", ".join(missing), e.line, e.col)
            return RecordVal(e.name, {fn: values[fn] for fn, _ft in rec.fields})
        if isinstance(e, Lambda):
            return FunctionVal(e.params, e.ret, e.effects, e.body, env)
        if isinstance(e, ListExpr): return [self.expr(x, env, row) for x in e.items]
        if isinstance(e, MapExpr): return {k: self.expr(v, env, row) for k, v in e.items.items()}
        if isinstance(e, Index):
            base = self.expr(e.base, env, row); idx = self.expr(e.index, env, row)
            try:
                return base[int(idx)]
            except (IndexError, KeyError, TypeError, ValueError):
                raise GamaRuntimeError("index out of range or invalid", e.line, e.col) from None
        if isinstance(e, Try):
            v = self.expr(e.expr, env, row)
            if not isinstance(v, ResultVal):
                raise GamaRuntimeError("? expects Result", e.line, e.col)
            if v.ok:
                return v.value
            raise PropagateSignal(v.value)
        if isinstance(e, Attr):
            base = self.expr(e.base, env, row)
            if not isinstance(base, dict): raise GamaRuntimeError("field access needs Json/Table", e.line, e.col)
            return base.get(e.name)
        if isinstance(e, Var):
            if row is not None and e.name in row: return row[e.name]
            try:
                return env.get(e.name)
            except GamaRuntimeError:
                if e.name in self.funcs:
                    fn, closure = self.funcs[e.name]
                    return FunctionVal(fn.params, fn.ret, fn.effects, fn.body, closure, fn.name)
                if "." in e.name:
                    base, _, variant = e.name.partition(".")
                    if base in self.enums:
                        if variant not in self.enums[base]:
                            raise GamaRuntimeError(
                                f"unknown enum variant {e.name}", e.line, e.col)
                        if self.enums[base][variant] is not None:
                            raise GamaRuntimeError(
                                f"enum variant {e.name} requires a payload "
                                f"argument; write {variant}(...)", e.line, e.col)
                        return EnumVal(base, variant)
                if "." not in e.name: raise
                parts = e.name.split(".")
                base_n = None
                for i in range(len(parts) - 1, 0, -1):
                    try:
                        env.get(".".join(parts[:i])); base_n = i; break
                    except GamaRuntimeError:
                        pass
                if base_n is None: raise
                obj = env.get(".".join(parts[:base_n]))
                for part in parts[base_n:]:
                    if isinstance(obj, RecordVal):
                        if part not in obj.fields:
                            raise GamaRuntimeError(
                                f"unknown field {e.name}", e.line, e.col)
                        obj = obj.fields[part]
                        continue
                    if isinstance(obj, dict) and part not in obj and isinstance(obj.get("value"), dict):
                        obj = obj["value"]
                    if not isinstance(obj, dict) or part not in obj:
                        raise GamaRuntimeError(f"unknown field {e.name}", e.line, e.col)
                    obj = obj[part]
                return obj
        if isinstance(e, Unary):
            v = self.expr(e.right, env, row)
            return (not self.truth(v)) if e.op == "!" else -v
        if isinstance(e, Binary):
            if e.op == "&&": return self.truth(self.expr(e.left, env, row)) and self.truth(self.expr(e.right, env, row))
            if e.op == "||": return self.truth(self.expr(e.left, env, row)) or self.truth(self.expr(e.right, env, row))
            a = self.expr(e.left, env, row); b = self.expr(e.right, env, row)
            try:
                if e.op == "/":
                    return Decimal(a) / Decimal(b)
                if e.op == "%":
                    return Decimal(a) % Decimal(b)
                return {"+":lambda:a+b,"-":lambda:a-b,"*":lambda:a*b,
                        "==":lambda:a==b,"!=":lambda:a!=b,"<":lambda:a<b,"<=":lambda:a<=b,
                        ">":lambda:a>b,">=":lambda:a>=b}[e.op]()
            except (ZeroDivisionError, ArithmeticError, TypeError, ValueError) as ex:
                raise GamaRuntimeError(f"{e.op}: {ex}", e.line, e.col) from None
        if isinstance(e, Call):
            if e.name in self.records:
                rec = self.records[e.name]
                if len(e.args) != len(rec.fields):
                    raise GamaRuntimeError(
                        f"{e.name} expects {len(rec.fields)} fields, got "
                        f"{len(e.args)}", e.line, e.col)
                vals = [self.expr(a, env, row) for a in e.args]
                return RecordVal(e.name, {fn: v for (fn, _ft), v in
                                          zip(rec.fields, vals)})
            if e.name in ("Ok", "Err"):
                if len(e.args) != 1: raise GamaRuntimeError(f"{e.name} expects 1 arg", e.line, e.col)
                return ResultVal(e.name == "Ok", self.expr(e.args[0], env, row))
            if e.name == "sql":
                q = str(self.expr(e.args[0], env, row)) if e.args else ""
                dbp = os.environ.get("GAMA_L_DB", "").strip()
                if not dbp:
                    return [dict(r) for r in DEMO_ROWS]
                if not q.lstrip().lower().startswith(("select", "with")):
                    raise GamaRuntimeError("GAMA_L_DB only allows SELECT/WITH", e.line, e.col)
                try:
                    con = sqlite3.connect("file:%s?mode=ro" % pathlib.Path(dbp).resolve(), uri=True)
                    con.execute("PRAGMA query_only = ON")
                    try:
                        cur = con.execute(q)
                        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchmany(10000)]
                    finally:
                        con.close()
                    return rows
                except sqlite3.Error as ex:
                    raise GamaRuntimeError("sql failed: %s" % ex, e.line, e.col) from None
            if e.name == "ask":
                p = str(self.expr(e.args[0], env, row)) if e.args else ""
                if len(e.args) > 1:
                    ctx = self.expr(e.args[1], env, row)
                    p += "\n\nContext:\n" + (ctx if isinstance(ctx, str) else json.dumps(ctx, default=str, ensure_ascii=False))
                if os.environ.get("GAMA_L_AI_URL"):
                    try:
                        from gama_ai import AIError, ask as ai_ask
                        return ai_ask(p)
                    except AIError as ex:
                        raise GamaRuntimeError("ask: %s" % ex, e.line, e.col) from None
                url = os.environ.get("GAMA_L_ASK_URL", "").strip()
                model = os.environ.get("GAMA_L_ASK_MODEL", "deepseek-chat")
                if url and not url.startswith("https://") and os.environ.get("GAMA_L_ASK_ALLOW_HTTP") != "1":
                    raise GamaRuntimeError("ask: endpoint must be https://", e.line, e.col)
                if not url:
                    self.last_ask = {"model": "mock", "prompt_hash": hashlib.sha256(p.encode()).hexdigest()}
                    return "Unavailable"
                body = json.dumps({"model": model, "messages": [{"role": "user", "content": p}]}).encode()
                headers = {"Content-Type": "application/json"}
                key = os.environ.get("GAMA_L_ASK_KEY", "")
                if key: headers["Authorization"] = "Bearer " + key
                try:
                    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body, headers=headers)
                    with _NO_REDIRECT_OPENER.open(req, timeout=60) as r:
                        d = json.loads(r.read(1 << 20).decode())
                    self.last_ask = {"model": model, "prompt_hash": hashlib.sha256(p.encode()).hexdigest()}
                    return d["choices"][0]["message"]["content"]
                except Exception as ex:
                    raise GamaRuntimeError("ask failed: %s" % ex, e.line, e.col) from None
            if e.name == "remember": self.memory[str(self.expr(e.args[0], env, row))] = self.expr(e.args[1], env, row); return True
            if e.name == "recall": return self.memory.get(str(self.expr(e.args[0], env, row)))
            if e.name == "wait_user":
                if not self.interactive:
                    return Denied()
                return input(f"[GAMA-L] {self.expr(e.args[0], env, row)} ").strip().lower() in ("yes", "y", "da")
            if e.name in _CORE_SIGS:
                return eval_core_builtin(
                    e.name, [self.expr(a, env, row) for a in e.args], e.line, e.col)
            if e.name == "len":
                _need_args("len", e.args, 1, e.line, e.col)
                value = self.expr(e.args[0], env, row)
                if not isinstance(value, (str, list, tuple, dict)):
                    raise GamaRuntimeError(
                        f"len expects Text, List or Json, got {tname(value)}",
                        e.line, e.col)
                return len(value)
            if e.name == "text":
                _need_args("text", e.args, 1, e.line, e.col)
                return str(self.expr(e.args[0], env, row))
            if e.name == "sql_write":
                q = str(self.expr(e.args[0], env, row)) if e.args else ""
                return sql_write(q, e.line, e.col)
            if e.name == "mail":
                if len(e.args) != 3:
                    raise GamaRuntimeError("mail expects to, subject, body", e.line, e.col)
                vals = [self.expr(a, env, row) for a in e.args]
                try:
                    from gama_mail import MailError, send_mail
                    return send_mail(str(vals[0]), str(vals[1]), str(vals[2]), e.line, e.col)
                except MailError as ex:
                    raise GamaRuntimeError(str(ex), e.line, e.col) from None
            if e.name == "number":
                _need_args("number", e.args, 1, e.line, e.col)
                raw = self.expr(e.args[0], env, row)
                try:
                    value = Decimal(str(raw))
                except (InvalidOperation, ValueError, TypeError):
                    raise GamaRuntimeError(
                        f"number: cannot convert {raw!r} to Decimal",
                        e.line, e.col) from None
                if not value.is_finite():
                    raise GamaRuntimeError(
                        f"number: non-finite value {raw!r} is not supported",
                        e.line, e.col)
                return value
            if e.name == "round":
                if not (1 <= len(e.args) <= 2):
                    raise GamaRuntimeError(
                        f"round expects 1..2 args, got {len(e.args)}",
                        e.line, e.col)
                raw = self.expr(e.args[0], env, row)
                try:
                    digits = int(self.expr(e.args[1], env, row)) if len(e.args) > 1 else 0
                except (ValueError, TypeError):
                    raise GamaRuntimeError(
                        "round: digits must be Int", e.line, e.col) from None
                try:
                    value = Decimal(str(raw))
                    return value.quantize(Decimal(1).scaleb(-digits))
                except (InvalidOperation, ValueError, TypeError):
                    raise GamaRuntimeError(
                        f"round: cannot round {raw!r} to Decimal",
                        e.line, e.col) from None
            if e.name in self.funcs:
                fn, closure = self.funcs[e.name]
                return self.invoke(
                    FunctionVal(fn.params, fn.ret, fn.effects, fn.body, closure, fn.name),
                    e.args, env, row, e.line, e.col)
            try:
                fnval = env.get(e.name)
            except GamaRuntimeError:
                fnval = None
            if fnval is None:
                try:
                    ctor = find_enum_variant(self.enums, e.name)
                except _AmbiguousVariantError:
                    raise GamaRuntimeError(
                        f"ambiguous enum constructor {e.name}; write Enum.{e.name}",
                        e.line, e.col) from None
                if ctor is not None:
                    base, variant, payload_t = ctor
                    if payload_t is None:
                        raise GamaRuntimeError(
                            f"enum variant {base}.{variant} is nullary; write "
                            f"{base}.{variant} without parentheses", e.line, e.col)
                    payload_types = _payload_type_list(payload_t)
                    if len(e.args) != len(payload_types):
                        raise GamaRuntimeError(
                            f"enum variant {base}.{variant} expects "
                            f"{len(payload_types)} payload argument"
                            f"{'s' if len(payload_types) != 1 else ''}, "
                            f"got {len(e.args)}", e.line, e.col)
                    values = tuple(self.expr(a, env, row) for a in e.args)
                    return EnumVal(base, variant, payloads=values)
            if isinstance(fnval, FunctionVal):
                return self.invoke(fnval, e.args, env, row, e.line, e.col)
            if fnval is not None:
                raise GamaRuntimeError(
                    f"{e.name} is not callable ({tname(fnval)})", e.line, e.col)
            raise GamaRuntimeError(f"unknown function {e.name}", e.line, e.col)
        if isinstance(e, CallExpr):
            fnval = self.expr(e.callee, env, row)
            if not isinstance(fnval, FunctionVal):
                raise GamaRuntimeError(
                    f"expression is not callable ({tname(fnval)})", e.line, e.col)
            return self.invoke(fnval, e.args, env, row, e.line, e.col)
        if isinstance(e, Pipe):
            value = self.expr(e.base, env, row)
            for stage, args in e.stages:
                if stage == "filter": value = [r for r in value if self.truth(self.expr(args[0], env, r))]
                elif stage == "select": value = [{f: r[f] for f in args} for r in value]
                elif stage == "sum":
                    try:
                        value = sum((Decimal(str(r[args[0]])) for r in value), Decimal(0))
                    except (KeyError, TypeError, ValueError) as ex:
                        raise GamaRuntimeError("sum: %s" % ex, e.line, e.col) from None
                elif stage == "limit":
                    n = int(self.expr(args[0], env, row))
                    if n < 0: raise GamaRuntimeError("limit must be >= 0", e.line, e.col)
                    value = value[:n]
                elif stage == "count": value = len(value)
                elif stage == "map":
                    try: value = [r[args[0]] for r in value]
                    except KeyError as ex: raise GamaRuntimeError("map: unknown column %s" % ex, e.line, e.col) from None
            return value
        raise GamaRuntimeError("bad expression")

def run(src: str, interactive=False):
    ast = check(src)
    return Interpreter(interactive=interactive).run(ast)

# ------------------------------------------------------------------ package manager
def _read_toml(path: pathlib.Path) -> dict:
    """Small TOML subset: [section], key = "value", inline tables."""
    out: dict = {}; sec = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line: continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1].strip(); out.setdefault(sec, {}); continue
        if "=" not in line: continue
        k, v = line.split("=", 1); k = k.strip(); v = v.strip()
        if v.startswith("{") and v.endswith("}"):
            d = {}
            for part in v[1:-1].split(","):
                if "=" in part:
                    a, b = part.split("=", 1); d[a.strip()] = b.strip().strip('"\'')
            out.setdefault(sec, {})[k] = d
        else:
            out.setdefault(sec, {})[k] = v.strip('"\'')
    return out

def _safe_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name and not name.startswith(".")

def _inside(root: pathlib.Path, path: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve()); return True
    except ValueError:
        return False

def _copy_tree(src: pathlib.Path, dst: pathlib.Path) -> None:
    for p in src.rglob("*"):
        rel = p.relative_to(src).parts
        if ".git" in rel or ".gamal" in rel:
            continue
        if p.is_symlink():
            raise ValueError(f"symlink not allowed in dependency: {p}")
    if dst.exists():
        if dst.is_symlink(): dst.unlink()
        else: shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True,
                    ignore=shutil.ignore_patterns(".git", ".gamal"))

def _sanitize_source(source: str) -> str:
    try:
        u = urllib.parse.urlsplit(source)
        if u.scheme and u.netloc and ("@" in u.netloc):
            host = u.netloc.rsplit("@", 1)[1]
            return urllib.parse.urlunsplit((u.scheme, host, u.path, u.query, u.fragment))
    except Exception:
        pass
    return source

def _git(args: list[str], root: pathlib.Path) -> tuple[int, str, str]:
    """Run git without a shell. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as ex:
        return 1, "", str(ex)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def _git_source(spec: dict, root: pathlib.Path) -> str:
    """Local git paths resolve relative to the project root; URLs stay as-is."""
    git = spec.get("git", "")
    if "://" in git or git.startswith("git@") or pathlib.Path(git).is_absolute():
        return git
    return str((root / git).resolve())

def _pkg_install_one_git(name: str, spec: dict, root: pathlib.Path,
                         moddir: pathlib.Path, cache_root: pathlib.Path,
                         update: bool = False,
                         expected: str | None = None) -> tuple[dict | None, str]:
    source = _git_source(spec, root)
    cache = cache_root / name
    if not cache.exists():
        cache_root.mkdir(parents=True, exist_ok=True)
        rc, _, err = _git(["clone", "--quiet", "--", source, str(cache)], root)
        if rc != 0:
            return None, f"git clone failed for {name} ({source}): {err or 'exit %d' % rc}"
    elif update and not spec.get("rev"):
        _git(["-C", str(cache), "fetch", "--quiet", "--all", "--tags"], root)
        _git(["-C", str(cache), "pull", "--quiet", "--ff-only"], root)
    rev = spec.get("rev")
    if rev:
        rev = str(rev)
        if rev.startswith("-") or not all(c.isalnum() or c in "._/@^~{}" for c in rev):
            return None, f"invalid git rev for {name}: {rev!r}"
        # Refresh refs first; tolerate a failed fetch (rev may already be cached).
        _git(["-C", str(cache), "fetch", "--quiet", "--all", "--tags"], root)
        rc, _, err = _git(["-C", str(cache), "checkout", "--quiet", rev, "--"], root)
        if rc != 0:
            return None, f"git checkout {rev} failed for {name}: {err or 'exit %d' % rc}"
    rc, head, err = _git(["-C", str(cache), "rev-parse", "HEAD"], root)
    if rc != 0 or not head:
        return None, f"git rev-parse HEAD failed for {name}: {err or 'exit %d' % rc}"
    if expected and not update and expected != head:
        return None, f"lock mismatch for {name}: {expected[:12]} -> {head[:12]}; run: gamal2.py pkg update"
    dst = moddir / name
    try:
        _copy_tree(cache, dst)
    except Exception as ex:
        return None, f"copy failed for {name}: {ex}"
    return {"source": _sanitize_source(spec.get("git", source)), "commit": head, "installed": str(dst)}, ""

def _pkg_install(root: pathlib.Path, update: bool = False) -> int:
    cfg = root / "gamal.toml"
    if not cfg.exists():
        print("no gamal.toml (run: gamal2.py pkg init)"); return 1
    data = _read_toml(cfg)
    deps = data.get("dependencies") or {}
    moddir = root / ".gamal" / "modules"; moddir.mkdir(parents=True, exist_ok=True)
    cache_root = root / ".gamal" / "cache"
    lock_path = root / ".gamal" / "lock.json"
    old_lock = {}
    if lock_path.exists():
        try: old_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception: old_lock = {}
    lock: dict = {}
    for name, spec in deps.items():
        if not _safe_name(name):
            print(f"invalid dependency name: {name!r}"); return 1
        if isinstance(spec, dict) and spec.get("git"):
            entry, err = _pkg_install_one_git(
                name, spec, root, moddir, cache_root, update,
                (old_lock.get(name) or {}).get("commit"))
            if entry is None:
                print(err); return 1
            old = old_lock.get(name) or {}
            if not update and old.get("commit") and old.get("commit") != entry["commit"]:
                print(f"lock mismatch for {name}: {old['commit'][:12]} -> {entry['commit'][:12]}; run: gamal2.py pkg update")
                return 1
            lock[name] = entry
            print(f"installed {name} -> {entry['installed']} ({entry['commit'][:12]})")
            continue
        src_value = spec.get("path", "") if isinstance(spec, dict) else spec
        if not src_value:
            print(f"missing dependency {name}: no path or git source"); return 1
        src = pathlib.Path(src_value)
        if not src.is_absolute(): src = (root / src).resolve()
        else: src = src.resolve()
        if not _inside(root, src) or src == root.resolve():
            print(f"unsafe path dependency {name}: must be inside project root"); return 1
        if not src.exists():
            print(f"missing dependency {name}: {src}"); return 1
        dst = moddir / name
        try:
            _copy_tree(src, dst)
        except Exception as ex:
            print(f"copy failed for {name}: {ex}"); return 1
        lock[name] = {"path": str(src), "installed": str(dst)}
        print(f"installed {name} -> {dst}")
    lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return 0

def _pkg_init(root: pathlib.Path) -> int:
    cfg = root / "gamal.toml"
    if cfg.exists(): print("gamal.toml already exists"); return 0
    cfg.write_text('[package]\nname = "app"\nversion = "0.1.0"\n\n[dependencies]\n', encoding="utf-8")
    print("created gamal.toml"); return 0

def pkg_main(argv: list[str]) -> int:
    action = argv[0] if argv else "list"
    root = pathlib.Path(argv[1]).resolve() if len(argv) > 1 else pathlib.Path.cwd()
    if action == "init": return _pkg_init(root)
    if action in ("search", "resolve", "add"):
        import gama_registry as R
        if len(argv) < 2:
            print("usage: gamal2.py pkg %s <registry.json> [name] [version]" % action); return 2
        try:
            idx = R.load_registry(argv[1])
            if action == "search":
                q = argv[2] if len(argv) > 2 else ""
                for row in R.search(idx, q):
                    print("%s %s %s" % (row["name"], row.get("version") or "-", row.get("git") or "-"))
                return 0
            if len(argv) < 3:
                print("usage: gamal2.py pkg %s <registry.json> <name> [version]" % action); return 2
            entry = R.resolve(idx, argv[2], argv[3] if len(argv) > 3 else None)
            if action == "resolve":
                print(json.dumps(entry, indent=2)); return 0
            R.add_dependency(pathlib.Path.cwd(), argv[2], entry)
            print("added %s -> gamal.toml" % argv[2]); return 0
        except R.RegistryError as ex:
            print("registry error: %s" % ex); return 1
    if action == "install": return _pkg_install(root)
    if action == "update": return _pkg_install(root, update=True)
    if action == "list":
        cfg = root / "gamal.toml"
        if not cfg.exists(): print("no gamal.toml"); return 1
        data = _read_toml(cfg)
        for name, spec in (data.get("dependencies") or {}).items():
            print("%s %s" % (name, spec))
        return 0
    print("usage: gamal2.py pkg init|install|update|list|search|resolve|add ..."); return 2

# ------------------------------------------------------------------ IR CLI
def ir_main(argv):
    """Opt-in entry point for the standalone bytecode IR prototype.

    ``gamal2 ir run FILE.gm`` compiles the checked AST with :mod:`gama_ir` and
    executes it on the IR VM; ``gamal2 ir compile FILE.gm`` (alias ``disasm``)
    prints the listing.  The default ``run`` backend is untouched.
    """
    # ``gama_ir`` imports ``gamal2`` for its AST classes.  When this file is
    # executed as a script the module lives under ``__main__``, so alias it
    # first: otherwise ``gama_ir`` would load a second copy and ``isinstance``
    # checks against its AST nodes would fail.
    if __name__ != "gamal2" and "gamal2" not in sys.modules:
        sys.modules["gamal2"] = sys.modules[__name__]
    import gama_ir

    ap = argparse.ArgumentParser(prog="gamal2 ir")
    ap.add_argument("action", choices=["run", "compile", "disasm"])
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true",
                    help="compile/disasm: emit the listing as JSON")
    a = ap.parse_args(argv)
    try:
        ast = check_file(a.file)
        program = gama_ir.compile_program(ast)
        if a.action in ("compile", "disasm"):
            if a.json:
                listing = [
                    {"index": i, "op": ins.op,
                     "arg": None if ins.arg is None else repr(ins.arg)}
                    for i, ins in enumerate(program.main.instrs)
                ]
                print(json.dumps({"file": a.file, "instructions": listing}))
            else:
                print(program.disassemble())
            return 0
        result = gama_ir.run_program(program)
        for line in result.out:
            print(line)
        return 0
    except gama_ir.IRUnsupported as e:
        print(f"IRUnsupported: {e}", file=sys.stderr)
        return 1
    except gama_ir.IRRuntimeError as e:
        print(f"IRRuntimeError: {e}", file=sys.stderr)
        return 1
    except gama_ir.IRError as e:
        print(f"IRError: {e}", file=sys.stderr)
        return 1
    except (GamaError, OSError, TypeError, IndexError, ZeroDivisionError,
            ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1


# ------------------------------------------------------------------ CLI
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "doctor":
        import gama_doctor
        return gama_doctor.main(argv[1:])
    if argv and argv[0] == "lsp":
        import gama_lsp
        return gama_lsp.main() or 0
    if argv and argv[0] == "pkg":
        return pkg_main(argv[1:])
    if argv and argv[0] == "ir":
        return ir_main(argv[1:])
    ap = argparse.ArgumentParser(prog="gamal2")
    ap.add_argument("cmd", choices=["run", "check", "fmt", "test", "lsp", "doctor"], nargs="?", default="run")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="fmt: report if the file is unformatted, write nothing (rc 1 if so)")
    ap.add_argument("--write", action="store_true",
                    help="fmt: rewrite the file in place with the formatted source")
    ap.add_argument("--width", type=int, default=88,
                    help="fmt: soft column limit for wrapping long expressions (default 88)")
    a = ap.parse_intermixed_args(argv)
    if a.cmd != "fmt" and (a.check or a.write):
        print("error: --check/--write are only valid with the 'fmt' command", file=sys.stderr)
        return 2
    if a.cmd == "test":
        import unittest
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent / "tests"))
        suite = unittest.defaultTestLoader.discover(str(__import__("pathlib").Path(__file__).parent / "tests"))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if not a.file:
        print("usage: gamal2.py run FILE.gm"); return 2
    try:
        if a.cmd == "check":
            check_file(a.file); print("OK"); return 0
        if a.cmd == "fmt":
            if a.check and a.write:
                print("error: --check and --write cannot be used together", file=sys.stderr)
                return 2
            path = pathlib.Path(a.file)
            src = path.read_text(encoding="utf-8"); parse(src)
            formatted = fmt_source(src, width=a.width)
            if a.check:
                if formatted == src:
                    return 0
                print(f"needs formatting: {a.file}")
                return 1
            if a.write:
                path.write_text(formatted, encoding="utf-8")
                print(f"{a.file}: formatted")
                return 0
            sys.stdout.write(formatted); return 0
        it = run_file(a.file, interactive=a.interactive)
        for line in it.out: print(line)
        return 0
    except (GamaError, OSError, ReturnSignal, TypeError, IndexError, ZeroDivisionError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr); return 1

if __name__ == "__main__":
    raise SystemExit(main())
