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
import sqlite3
import sys
import urllib.request
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

# ------------------------------------------------------------------ lexer
KEYWORDS = {"let", "fn", "if", "else", "return", "true", "false", "null",
            "prove", "verify", "print", "effects", "filter", "select", "sum",
            "limit", "count", "map", "in", "import"}

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
class Let: name: str; type_name: str | None; expr: Any; line: int; col: int
@dataclass
class Assign: name: str; expr: Any; line: int; col: int
@dataclass
class Param: name: str; type_name: str
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
        if x.kind == "KW" and x.val == "let":
            self.take(); name = self.expect("IDENT").val; tn = None
            if self.at("OP", ":"): self.take(); tn = self.expect("IDENT").val
            self.expect("OP", "="); self.skip_nl(); return Let(name, tn, self.expr(), x.line, x.col)
        if x.kind == "KW" and x.val == "fn": return self.fn()
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
        if x.kind == "IDENT" and self.peek(1).kind == "OP" and self.peek(1).val == ":=":
            name = self.take().val; self.take(); return Assign(name, self.expr(), x.line, x.col)
        return ExprStmt(self.expr(), x.line, x.col)
    def fn(self):
        x = self.take(); name = self.expect("IDENT").val; self.expect("OP", "("); self.skip_nl()
        params = []
        if not self.at("OP", ")"):
            while True:
                pn = self.expect("IDENT").val; self.expect("OP", ":"); pt = self.expect("IDENT").val
                params.append(Param(pn, pt)); self.skip_nl()
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
        if v and all(isinstance(x, dict) for x in v): return "Table"
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
}

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
    def expr(self, e, row=False) -> str:
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
            if row: return "Any"
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
                return rt
            f = self.funcs.get(e.name)
            if not f: raise GamaTypeError(f"unknown function {e.name}", e.line, e.col)
            if len(f.params) != len(e.args): raise GamaTypeError(f"{e.name} expects {len(f.params)} args", e.line, e.col)
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
                if stage == "filter":
                    self.expr(args[0], row=True); t = "Table"
                elif stage == "select":
                    t = "Table"
                elif stage == "sum":
                    if args: pass
                    t = "Decimal"
                elif stage == "limit":
                    lt = self.expr(args[0], row)
                    if lt != "Int": raise GamaTypeError("limit expects Int", e.line, e.col)
                    t = "Table"
                elif stage == "map":
                    t = "List"
                elif stage == "count":
                    t = "Int"
            return t
        return "Any"

def _flatten(path: pathlib.Path, root: pathlib.Path | None = None,
             seen: set | None = None, loaded: set | None = None) -> list:
    path = path.resolve(); root = (root or path.parent).resolve()
    if seen is None: seen = set()
    if loaded is None: loaded = set()
    if os.path.commonpath([str(path), str(root)]) != str(root):
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
            out.extend(_flatten(path.parent / s.path, root, seen, loaded))
        else:
            out.append(s)
    seen.discard(path); loaded.add(path)
    return out

def load_file(path: str):
    p = pathlib.Path(path).resolve()
    return _flatten(p, p.parent)

def check(src: str):
    ast = parse(src)
    Checker().program(ast)
    return ast

def check_file(path: str):
    ast = load_file(path); Checker().program(ast); return ast

def run_file(path: str, interactive=False):
    ast = check_file(path)
    return Interpreter(interactive=interactive).run(ast)

def fmt_source(src: str) -> str:
    """Deterministic whitespace formatter (full AST formatting is later)."""
    lines = [ln.rstrip() for ln in src.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]: lines.pop(0)
    while lines and not lines[-1]: lines.pop()
    return "\n".join(lines) + "\n"

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
                    with urllib.request.urlopen(req, timeout=60) as r:
                        d = json.loads(r.read(1 << 20).decode())
                    self.last_ask = {"model": model, "prompt_hash": hashlib.sha256(p.encode()).hexdigest()}
                    return d["choices"][0]["message"]["content"]
                except Exception as ex:
                    raise GamaRuntimeError("ask failed: %s" % ex, e.line, e.col) from None
            if e.name == "remember": self.memory[str(self.expr(e.args[0], env, row))] = self.expr(e.args[1], env, row); return True
            if e.name == "recall": return self.memory.get(str(self.expr(e.args[0], env, row)))
            if e.name == "wait_user":
                if not self.interactive:
                    raise GamaRuntimeError("HITL required but session is non-interactive", e.line, e.col)
                return input(f"[GAMA-L] {self.expr(e.args[0], env, row)} ").strip().lower() in ("yes", "y", "da")
            if e.name == "len": return len(self.expr(e.args[0], env, row))
            if e.name == "text": return str(self.expr(e.args[0], env, row))
            if e.name in ("sql_write", "mail"): raise GamaRuntimeError(f"{e.name}: connector not configured", e.line, e.col)
            if e.name == "number": return Decimal(str(self.expr(e.args[0], env, row)))
            if e.name in self.funcs:
                fn, closure = self.funcs[e.name]
                local = Env(closure)
                for p, a in zip(fn.params, e.args): local.set(p.name, self.expr(a, env, row))
                try:
                    for s in fn.body: self.stmt(s, local)
                except ReturnSignal as r: return r.value
                return None
            raise GamaRuntimeError(f"unknown function {e.name}", e.line, e.col)
        if isinstance(e, Pipe):
            value = self.expr(e.base, env, row)
            for stage, args in e.stages:
                if stage == "filter": value = [r for r in value if self.truth(self.expr(args[0], env, r))]
                elif stage == "select": value = [{f: r.get(f) for f in args} for r in value]
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
                elif stage == "map": value = [r.get(args[0]) for r in value]
            return value
        raise GamaRuntimeError("bad expression")

def run(src: str, interactive=False):
    ast = check(src)
    return Interpreter(interactive=interactive).run(ast)

# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="gamal2")
    ap.add_argument("cmd", choices=["run", "check", "fmt", "test"], nargs="?", default="run")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--interactive", action="store_true")
    a = ap.parse_args(argv)
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
            src = pathlib.Path(a.file).read_text(encoding="utf-8"); parse(src)
            sys.stdout.write(fmt_source(src)); return 0
        it = run_file(a.file, interactive=a.interactive)
        for line in it.out: print(line)
        return 0
    except (GamaError, OSError, ReturnSignal, TypeError, IndexError, ZeroDivisionError, ValueError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr); return 1

if __name__ == "__main__":
    raise SystemExit(main())
