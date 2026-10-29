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
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# ------------------------------------------------------------------ errors
class GamaError(Exception):
    def __init__(self, msg: str, line: int = 0, col: int = 0):
        super().__init__(msg)
        self.msg, self.line, self.col = msg, line, col
    def __str__(self):
        return f"{self.msg} (line {self.line}, col {self.col})"

class GamaTypeError(GamaError): pass
class GamaEffectError(GamaError): pass
class GamaRuntimeError(GamaError): pass

class Denied:
    """Explicit HITL refusal value; falsy, renders as Denied."""
    def __bool__(self): return False
    def __str__(self): return "Denied"
    __repr__ = __str__

class TableType(str):
    """String-compatible 'Table' type carrying an optional column set."""
    fields: set[str] | None
    def __new__(cls, fields=None):
        o = str.__new__(cls, "Table"); o.fields = set(fields) if fields else None; return o

# ------------------------------------------------------------------ lexer
KEYWORDS = {"let", "fn", "if", "else", "return", "true", "false", "null",
            "prove", "verify", "print", "effects", "filter", "select", "sum",
            "limit", "count", "map", "in", "import", "make", "use", "wait"}

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
            val = Decimal(txt) if "." in txt else int(txt)
            out.append(Tok("NUMBER", val, start[0], start[1])); col += len(txt); continue
        if c.isalpha() or c == "_":
            start = line, col; j = i
            while i < n and (src[i].isalnum() or src[i] in "_."): i += 1
            txt = src[j:i]
            out.append(Tok("KW" if txt in KEYWORDS else "IDENT", txt, start[0], start[1]))
            col += len(txt); continue
        two = src[i:i+2]
        if two in ("==","!=","<=",">=","&&","||",":=","->"):
            out.append(Tok("OP", two, line, col)); i += 2; col += 2; continue
        if c in "+-*/%<>!|(){}[],:;=":
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
class Pipe: base: Any; stages: list; line: int; col: int
@dataclass
class Import: path: str; line: int; col: int
@dataclass
class Use: name: str; line: int; col: int
@dataclass
class Let: name: str; type_name: str | None; expr: Any; line: int; col: int
@dataclass
class Assign: name: str; expr: Any; line: int; col: int
@dataclass
class Param: name: str; type_name: str; default: Any = None
@dataclass
class Fn: name: str; params: list; ret: str | None; effects: set | None; body: list; line: int; col: int
@dataclass
class If: cond: Any; then: list; other: list | None; line: int; col: int
@dataclass
class Return: expr: Any; line: int; col: int
@dataclass
class Prove: name: str; expr: Any; line: int; col: int
@dataclass
class Verify: expr: Any; line: int; col: int
@dataclass
class Print: expr: Any; line: int; col: int
@dataclass
class ExprStmt: expr: Any; line: int; col: int

# ------------------------------------------------------------------ parser
class Parser:
    def __init__(self, toks: list[Tok]):
        self.t = toks; self.i = 0
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
            out.append(self.statement())
            if not (self.at("NL") or self.at("OP", ";") or self.at("EOF")):
                x = self.peek(); raise GamaError("expected end of statement", x.line, x.col)
            self.skip_seps()
        return out
    def block(self):
        self.expect("OP", "{"); self.skip_seps(); out = []
        while not self.at("OP", "}"):
            if self.at("EOF"):
                x = self.peek(); raise GamaError("unterminated block", x.line, x.col)
            out.append(self.statement())
            if not (self.at("NL") or self.at("OP", ";") or self.at("OP", "}")):
                x = self.peek(); raise GamaError("expected end of statement", x.line, x.col)
            self.skip_seps()
        self.expect("OP", "}"); return out
    def statement(self):
        x = self.peek()
        if x.kind == "KW" and x.val == "import":
            self.take(); path = self.expect("STRING").val
            return Import(path, x.line, x.col)
        if x.kind == "KW" and x.val == "use":
            self.take(); return Use(self.expect("IDENT").val, x.line, x.col)
        if x.kind == "KW" and x.val == "let":
            self.take(); name = self.expect("IDENT").val; tn = None
            if self.at("OP", ":"): self.take(); tn = self.expect("IDENT").val
            self.expect("OP", "="); self.skip_nl(); return Let(name, tn, self.expr(), x.line, x.col)
        if x.kind == "KW" and x.val in ("fn", "make"): return self.fn()
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
    def if_stmt(self):
        x = self.take(); cond = self.expr(); self.skip_nl(); then = self.block(); other = None
        save = self.i; self.skip_seps()
        if self.at("KW", "else"):
            self.take(); self.skip_nl()
            other = self.if_stmt() if self.at("KW", "if") else self.block()
        else:
            self.i = save
        return If(cond, then, other, x.line, x.col)
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
            op = self.take(); p = self.PREC[op.val]; right = self.binary(p + 1)
            left = Binary(left, op.val, right, op.line, op.col)
        return left
    def unary(self):
        if self.at("OP") and self.peek().val in ("!", "-"):
            x = self.take(); return Unary(x.val, self.unary(), x.line, x.col)
        return self.primary()
    def primary(self):
        x = self.peek()
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
}

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
        self.effects: set[str] = set()
        self.ret_type: str | None = None
    def push(self): self.scope.append({})
    def pop(self): self.scope.pop()
    def get(self, n):
        for s in reversed(self.scope):
            if n in s: return s[n]
        return None
    def set(self, n, t): self.scope[-1][n] = t
    def program(self, stmts):
        for s in stmts:
            if isinstance(s, Fn):
                if s.name in self.funcs: raise GamaTypeError("duplicate function", s.line, s.col)
                self.funcs[s.name] = s
        for s in stmts: self.stmt(s, None)
    def stmt(self, s, allowed):
        if isinstance(s, Import):
            raise GamaError("import requires check_file/run_file", s.line, s.col)
        if isinstance(s, Use):
            return
        if isinstance(s, Let):
            t = self.expr(s.expr)
            if s.type_name and not self.compat(t, s.type_name): raise GamaTypeError(f"{s.name}: {t} != {s.type_name}", s.line, s.col)
            self.set(s.name, s.type_name or t)
        elif isinstance(s, Assign):
            t = self.expr(s.expr)
            cur = self.get(s.name)
            if cur is None: self.set(s.name, t)
            elif not self.compat(t, cur): raise GamaTypeError(f"{s.name}: {t} != {cur}", s.line, s.col)
        elif isinstance(s, Fn):
            self.fn(s)
        elif isinstance(s, If):
            ct = self.expr(s.cond)
            if ct != "Bool": raise GamaTypeError(f"if condition must be Bool, got {ct}", s.line, s.col)
            self.push(); [self.stmt(x, allowed) for x in s.then]; self.pop()
            if s.other:
                self.push(); [self.stmt(x, allowed) for x in s.other]; self.pop()
        elif isinstance(s, Return):
            if self.ret_type is None: raise GamaTypeError("return outside function", s.line, s.col)
            rt = self.expr(s.expr)
            if not self.compat(rt, self.ret_type): raise GamaTypeError(f"return {rt} != {self.ret_type}", s.line, s.col)
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
    def expr(self, e, row=None) -> str:
        if isinstance(e, Literal): return tname(e.value)
        if isinstance(e, ListExpr):
            types = {self.expr(x, row) for x in e.items}
            return "Table" if types and types <= {"Json"} else "List"
        if isinstance(e, MapExpr): [self.expr(x, row) for x in e.items.values()]; return "Json"
        if isinstance(e, Attr):
            t = self.expr(e.base)
            if t not in ("Json", "Any", "Table"): raise GamaTypeError("field access needs Json/Table", e.line, e.col)
            return "Any"
        if isinstance(e, Var):
            t = self.get(e.name)
            if t is not None: return t
            if "." in e.name:
                parts = e.name.split(".")
                for i in range(len(parts) - 1, 0, -1):
                    if self.get(".".join(parts[:i])) is not None:
                        return "Any"
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
            if e.name in BUILTINS:
                eff, rt = BUILTINS[e.name]
                self.effects |= eff
                for a in e.args: self.expr(a, row)
                if e.name == "sql":
                    q = e.args[0].value if e.args and isinstance(e.args[0], Literal) else ""
                    fields = sql_fields(q)
                    if fields is not None: return TableType(fields)
                    if not os.environ.get("GAMA_L_DB"):
                        return TableType(DEMO_ROWS[0].keys())
                    return TableType(None)
                return rt
            f = self.funcs.get(e.name)
            if not f: raise GamaTypeError(f"unknown function {e.name}", e.line, e.col)
            required = sum(1 for p in f.params if p.default is None)
            if not (required <= len(e.args) <= len(f.params)):
                raise GamaTypeError(f"{e.name} expects {required}..{len(f.params)} args", e.line, e.col)
            for p, a in zip(f.params, e.args):
                at = self.expr(a)
                if not self.compat(at, p.type_name): raise GamaTypeError(f"{e.name}: {p.name} {at} != {p.type_name}", e.line, e.col)
            self.effects |= (f.effects or set())
            return f.ret or "Any"
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

def _flatten(path: pathlib.Path, root: pathlib.Path | None = None,
             seen: set | None = None, loaded: set | None = None) -> list:
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
        ast = parse(path.read_text(encoding="utf-8"))
    except OSError as ex:
        raise GamaError("cannot import %s: %s" % (path, ex)) from None
    out = []
    for s in ast:
        if isinstance(s, Import):
            candidates = [path.parent / s.path,
                          root / ".gamal" / "modules" / s.path,
                          root / "gama_modules" / s.path]
            target = next((c for c in candidates if c.exists()), None)
            if target is None:
                raise GamaError("cannot import %s" % s.path, s.line, s.col)
            out.extend(_flatten(target, root, seen, loaded))
        else:
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

def fmt_source(src: str) -> str:
    """Canonical formatter (falls back to indentation-only if module absent)."""
    try:
        from gama_fmt import format_source
        return format_source(src)
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

class Interpreter:
    def __init__(self, interactive=False):
        self.env = Env(); self.funcs = {}; self.out = []; self.memory = {}
        self.proofs = {}; self.proof_chain = []; self.prev_proof = "GENESIS"; self.interactive = interactive
        self.last_ask = None
    def run(self, stmts):
        for s in stmts: self.stmt(s, self.env)
        return self
    def stmt(self, s, env):
        if isinstance(s, Let): env.set(s.name, self.expr(s.expr, env))
        elif isinstance(s, Assign): env.assign(s.name, self.expr(s.expr, env))
        elif isinstance(s, Use): pass
        elif isinstance(s, Fn): self.funcs[s.name] = (s, env)
        elif isinstance(s, If):
            if self.truth(self.expr(s.cond, env)):
                blk = Env(env)
                for x in s.then: self.stmt(x, blk)
            elif s.other:
                blk = Env(env)
                for x in s.other: self.stmt(x, blk)
        elif isinstance(s, Return): raise ReturnSignal(self.expr(s.expr, env))
        elif isinstance(s, Prove):
            val = self.expr(s.expr, env)
            rec = self.prove(s.name, val)
            env.set(s.name, rec); self.proofs[s.name] = rec
        elif isinstance(s, Verify):
            _v = self.verify(self.expr(s.expr, env))
            env.set("_verify", _v); self.out.append(self.render(_v))
        elif isinstance(s, Print): self.out.append(self.render(self.expr(s.expr, env)))
        elif isinstance(s, ExprStmt): self.expr(s.expr, env)
    def truth(self, v): return bool(v)
    def render(self, v):
        if isinstance(v, Decimal): return format(v, "f")
        if isinstance(v, (dict, list)): return json.dumps(v, ensure_ascii=False, default=str)
        return str(v)
    def prove(self, name, val):
        if name in self.proofs:
            old = self.proofs[name]
            if old.get("value") == val:
                return old
            raise GamaRuntimeError(f"prove: duplicate key {name!r} with different value")
        core = {"name": name, "value": val}
        raw = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
        h = hashlib.sha256((self.prev_proof + raw).encode()).hexdigest()
        rec = {"name": name, "value": val, "sha256": h, "prev": self.prev_proof,
               "id": h[:16], "ts": __import__("time").time()}
        key = os.environ.get("GAMA_L_PROOF_KEY", "").encode()
        if key:
            rec["sig"] = hmac.new(key, (self.prev_proof + raw).encode(), hashlib.sha256).hexdigest()
        self.prev_proof = h
        self.proof_chain.append(h)
        return rec
    def verify(self, rec):
        if not isinstance(rec, dict) or "sha256" not in rec: return {"ok": False, "errors": ["not_a_proof"]}
        raw = json.dumps({"name": rec.get("name"), "value": rec.get("value")},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
        exp = hashlib.sha256((rec.get("prev", "GENESIS") + raw).encode()).hexdigest()
        errs = []
        if not hmac.compare_digest(exp, str(rec.get("sha256"))): errs.append("hash_mismatch")
        if rec.get("prev") not in ("GENESIS",) and rec.get("prev") not in self.proof_chain:
            errs.append("untrusted_chain")
        key = os.environ.get("GAMA_L_PROOF_KEY", "").encode()
        sig = rec.get("sig")
        if sig:
            if not key:
                errs.append("missing_key")
            else:
                want = hmac.new(key, (str(rec.get("prev", "GENESIS")) + raw).encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(want, str(sig)): errs.append("bad_signature")
        elif os.environ.get("GAMA_L_REQUIRE_SIG") == "1":
            errs.append("missing_signature")
        return {"ok": not errs, "expected": exp, "errors": errs}
    def expr(self, e, env, row=None):
        if isinstance(e, Literal): return e.value
        if isinstance(e, ListExpr): return [self.expr(x, env, row) for x in e.items]
        if isinstance(e, MapExpr): return {k: self.expr(v, env, row) for k, v in e.items.items()}
        if isinstance(e, Attr):
            base = self.expr(e.base, env, row)
            if not isinstance(base, dict): raise GamaRuntimeError("field access needs Json/Table", e.line, e.col)
            return base.get(e.name)
        if isinstance(e, Var):
            if row is not None and e.name in row: return row[e.name]
            try:
                return env.get(e.name)
            except GamaRuntimeError:
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
            if e.name == "len": return len(self.expr(e.args[0], env, row))
            if e.name == "text": return str(self.expr(e.args[0], env, row))
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
            if e.name == "number": return Decimal(str(self.expr(e.args[0], env, row)))
            if e.name == "round":
                value = Decimal(str(self.expr(e.args[0], env, row)))
                digits = int(self.expr(e.args[1], env, row)) if len(e.args) > 1 else 0
                return value.quantize(Decimal(1).scaleb(-digits))
            if e.name in self.funcs:
                fn, closure = self.funcs[e.name]
                local = Env(closure)
                for i, p in enumerate(fn.params):
                    value = self.expr(e.args[i], env, row) if i < len(e.args) else self.expr(p.default, closure)
                    local.set(p.name, value)
                try:
                    for s in fn.body: self.stmt(s, local)
                except ReturnSignal as r: return r.value
                return None
            raise GamaRuntimeError(f"unknown function {e.name}", e.line, e.col)
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
    ap = argparse.ArgumentParser(prog="gamal2")
    ap.add_argument("cmd", choices=["run", "check", "fmt", "test", "lsp", "doctor"], nargs="?", default="run")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="fmt: report if the file is unformatted, write nothing (rc 1 if so)")
    ap.add_argument("--write", action="store_true",
                    help="fmt: rewrite the file in place with the formatted source")
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
            formatted = fmt_source(src)
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
