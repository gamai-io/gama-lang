#!/usr/bin/env python3
"""GAMA-L 2.0 — standalone stack-bytecode IR prototype.

This module compiles the ``gamal2`` AST into a small stack machine and runs it
without instantiating ``gamal2.Interpreter``.  It exists to prove that the 2.0
core can target a bytecode backend and to provide a place to grow a future VM.
It is intentionally a *subset*: anything outside the documented surface is
rejected with :class:`IRUnsupported` at compile time (or at the first dynamic
use), never silently given a different meaning.

Supported statements
--------------------
``let`` / ``:=`` (assign), ``print``, ``if``/``else`` (with ordinary blocks),
``while``, ``break``, ``continue``, ``fn`` (named function definitions),
``return`` (inside functions only), and expression statements.

Supported expressions
---------------------
Literals: ``Int``, ``Decimal``, ``Text``, ``Bool``, ``null``.
Variables, unary ``!``/``-``; binary ``+ - * / %``, comparisons
``== != < <= > >=`` and short-circuit ``&&``/``||``; calls to *named,
user-defined* functions (including recursion and default parameters).

Runtime model
-------------
The VM is a stack machine with absolute jump targets, lexical frames
(:class:`Frame`) matching ``gamal2.Env`` semantics, and a call stack realised by
recursion (one ``_exec`` per call).  ``let`` defines in the current frame,
``:=`` updates the nearest binding and otherwise creates a global, ``if``
branches get their own frame, and ``while`` bodies share the enclosing frame
exactly like the reference interpreter.  Rendering of printed values and
decimal division/modulo follow the reference implementation byte-for-byte.

Limitations (raise ``IRUnsupported`` / documented runtime errors)
------------------------------------------------------------------
* ``for``, ``match``, ``prove``/``verify`` (and dotted proof names), ``import``.
* List and map literals, indexing, dotted names / field access (``a.b``) and
  dotted calls / namespaces (``mod.fn``), pipes and ``try``.
* All builtins (``sql``, ``ask``, ``len``, ``text``, ``number``, ``round``,
  ``upper``, ``Ok``/``Err``, result/text/collection helpers, ``mail``,
  ``remember``/``recall``, ``wait_user``, ...).
* Lambdas, first-class function values and expression callees (``f()`` where
  ``f`` is a variable).  Calling such a value fails with a clear runtime error.
* ``else if`` chains: the reference interpreter itself raises
  ``TypeError: 'If' object is not iterable`` for them, so the IR rejects them
  explicitly rather than inventing different behaviour.
* ``break``/``continue`` outside a loop and ``return`` outside a function are
  rejected at compile time.
* The safety limits ``GAMA_L_MAX_STEPS`` and ``GAMA_L_LOOP_LIMIT`` are **not**
  enforced by the IR (the optional ``GAMA_L_MAX_CALL_DEPTH`` guard is).  The
  IR is a prototype, not a hardened runtime.
* Errors produced while running supported code raise :class:`IRRuntimeError`
  with messages aligned to the reference runtime, but they are distinct types.

The public API is :func:`compile_program`, :func:`run_program`, the
:class:`Program` (with :meth:`Program.disassemble`) and :class:`RunResult`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import gamal2 as g2

__all__ = [
    "IRError",
    "IRUnsupported",
    "IRRuntimeError",
    "Instr",
    "Code",
    "ParamProto",
    "FunctionProto",
    "Program",
    "RunResult",
    "compile_program",
    "run_program",
]


# ------------------------------------------------------------------ errors
class IRError(Exception):
    """Base class for all IR errors."""


class IRUnsupported(IRError):
    """Raised when the AST contains a construct outside the documented subset."""


class IRRuntimeError(IRError):
    """Raised when supported code fails at run time (bad call, unknown name...)."""


# ------------------------------------------------------------------ IR data
@dataclass
class Instr:
    """One stack-machine instruction.  ``arg`` is opcode specific."""

    op: str
    arg: Any = None

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return self.op if self.arg is None else f"{self.op} {self.arg!r}"


@dataclass
class Code:
    instrs: list[Instr] = field(default_factory=list)


@dataclass
class ParamProto:
    name: str
    default: Code | None = None


@dataclass
class FunctionProto:
    name: str
    params: list[ParamProto]
    body: Code


@dataclass
class Program:
    main: Code

    def disassemble(self) -> str:
        """Return a readable listing of the main instruction stream."""
        lines = []
        for i, ins in enumerate(self.main.instrs):
            lines.append(f"{i:04d}  {ins}")
        return "\n".join(lines)


@dataclass
class RunResult:
    out: list[str]
    globals: dict


# ------------------------------------------------------------------ frames
class Frame:
    """Lexical environment matching ``gamal2.Env`` (get/set/assign)."""

    __slots__ = ("vars", "parent")

    def __init__(self, parent: "Frame | None" = None):
        self.vars: dict[str, Any] = {}
        self.parent = parent

    def get(self, name: str):
        frame = self
        while frame is not None:
            if name in frame.vars:
                return frame.vars[name]
            frame = frame.parent
        raise IRRuntimeError(f"unknown variable {name}")

    def define(self, name: str, value: Any) -> None:
        self.vars[name] = value

    def assign(self, name: str, value: Any) -> None:
        frame = self
        while frame is not None:
            if name in frame.vars:
                frame.vars[name] = value
                return
            if frame.parent is None:
                frame.vars[name] = value
                return
            frame = frame.parent

    def outermost(self) -> "Frame":
        frame = self
        while frame.parent is not None:
            frame = frame.parent
        return frame


# ------------------------------------------------------------------ helpers
def _truth(value: Any) -> bool:
    return bool(value)


def _render(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _binop(op: str, left: Any, right: Any):
    try:
        if op == "/":
            return Decimal(left) / Decimal(right)
        if op == "%":
            return Decimal(left) % Decimal(right)
        return {
            "+": lambda: left + right,
            "-": lambda: left - right,
            "*": lambda: left * right,
            "==": lambda: left == right,
            "!=": lambda: left != right,
            "<": lambda: left < right,
            "<=": lambda: left <= right,
            ">": lambda: left > right,
            ">=": lambda: left >= right,
        }[op]()
    except (ZeroDivisionError, ArithmeticError, TypeError, ValueError) as exc:
        raise IRRuntimeError(f"{op}: {exc}") from None


# Builtins implemented by the reference interpreter.  The IR supports none of
# them; naming the full set lets us fail at compile time with a clear message
# instead of a confusing runtime "unknown function".
_UNSUPPORTED_BUILTINS = {
    "sql", "ask", "remember", "recall", "wait_user", "len", "text", "number",
    "sql_write", "mail", "round", "Ok", "Err", "unwrap", "unwrap_or", "is_ok",
    "is_err", "split", "join", "contains", "trim", "upper", "lower", "replace",
    "starts_with", "ends_with", "keys", "values", "has",
}


def _unsupported(node: Any, why: str | None = None) -> None:
    line = getattr(node, "line", 0)
    col = getattr(node, "col", 0)
    name = type(node).__name__
    msg = f"IR does not support {name}"
    if why:
        msg += f": {why}"
    raise IRUnsupported(f"{msg} (line {line}, col {col})")


# ------------------------------------------------------------------ compiler
class _Compiler:
    """Compiles one instruction stream (main program, a function body or a
    default-argument expression).  Frames created by ``if`` branches are
    tracked so ``break``/``continue`` can unwind them precisely."""

    def __init__(self, in_function: bool = False):
        self.code = Code()
        self.frame_depth = 0
        self.loops: list[dict] = []
        self.in_function = in_function

    # -- emission helpers ------------------------------------------------
    def emit(self, op: str, arg: Any = None) -> int:
        self.code.instrs.append(Instr(op, arg))
        return len(self.code.instrs) - 1

    def here(self) -> int:
        return len(self.code.instrs)

    def patch(self, index: int, target: int) -> None:
        self.code.instrs[index].arg = target

    # -- program / statements -------------------------------------------
    def compile_statements(self, stmts: list) -> None:
        for stmt in stmts:
            self.compile_statement(stmt)

    def compile_statement(self, stmt: Any) -> None:
        if isinstance(stmt, g2.Let):
            self.compile_expr(stmt.expr)
            self.emit("LET", stmt.name)
            return
        if isinstance(stmt, g2.Assign):
            self.compile_expr(stmt.expr)
            self.emit("ASSIGN", stmt.name)
            return
        if isinstance(stmt, g2.Use):
            self.emit("NOP")
            return
        if isinstance(stmt, g2.Print):
            self.compile_expr(stmt.expr)
            self.emit("PRINT")
            return
        if isinstance(stmt, g2.ExprStmt):
            self.compile_expr(stmt.expr)
            self.emit("POP")
            return
        if isinstance(stmt, g2.If):
            self.compile_if(stmt)
            return
        if isinstance(stmt, g2.While):
            self.compile_while(stmt)
            return
        if isinstance(stmt, g2.Break):
            self.compile_break(stmt)
            return
        if isinstance(stmt, g2.Continue):
            self.compile_continue(stmt)
            return
        if isinstance(stmt, g2.Fn):
            self.emit("DEF", self.compile_function(stmt))
            return
        if isinstance(stmt, g2.Return):
            if not self.in_function:
                _unsupported(stmt, "return outside a function")
            self.compile_expr(stmt.expr)
            self.emit("RETURN")
            return
        _unsupported(stmt)

    def compile_if(self, stmt: g2.If) -> None:
        if isinstance(stmt.other, g2.If):
            _unsupported(stmt.other, "else-if chains are rejected by the reference interpreter")
        self.compile_expr(stmt.cond)
        jump_false = self.emit("JUMP_IF_FALSE", None)

        self.emit("NEW_FRAME")
        self.frame_depth += 1
        self.compile_statements(stmt.then)
        self.frame_depth -= 1
        self.emit("POP_FRAME")
        jump_end = self.emit("JUMP", None)

        self.patch(jump_false, self.here())
        if stmt.other:
            self.emit("NEW_FRAME")
            self.frame_depth += 1
            self.compile_statements(stmt.other)
            self.frame_depth -= 1
            self.emit("POP_FRAME")
        self.patch(jump_end, self.here())

    def compile_while(self, stmt: g2.While) -> None:
        start = self.here()
        self.compile_expr(stmt.cond)
        jump_false = self.emit("JUMP_IF_FALSE", None)

        ctx = {"base_depth": self.frame_depth, "continue": start, "breaks": []}
        self.loops.append(ctx)
        self.compile_statements(stmt.body)
        self.loops.pop()
        self.emit("JUMP", start)

        end = self.here()
        self.patch(jump_false, end)
        for index in ctx["breaks"]:
            self.patch(index, end)

    def compile_break(self, stmt: g2.Break) -> None:
        if not self.loops:
            _unsupported(stmt, "break outside a loop")
        ctx = self.loops[-1]
        unwind = self.frame_depth - ctx["base_depth"]
        if unwind:
            self.emit("UNWIND", unwind)
        index = self.emit("JUMP", None)
        ctx["breaks"].append(index)

    def compile_continue(self, stmt: g2.Continue) -> None:
        if not self.loops:
            _unsupported(stmt, "continue outside a loop")
        ctx = self.loops[-1]
        unwind = self.frame_depth - ctx["base_depth"]
        if unwind:
            self.emit("UNWIND", unwind)
        self.emit("JUMP", ctx["continue"])

    def compile_function(self, stmt: g2.Fn) -> FunctionProto:
        body = _Compiler(in_function=True)
        body.compile_statements(stmt.body)
        body.emit("CONST", None)
        body.emit("RETURN")

        params: list[ParamProto] = []
        for param in stmt.params:
            default: Code | None = None
            if param.default is not None:
                default_compiler = _Compiler()
                default_compiler.compile_expr(param.default)
                default_compiler.emit("RETURN")
                default = default_compiler.code
            params.append(ParamProto(param.name, default))
        return FunctionProto(stmt.name, params, body.code)

    # -- expressions -----------------------------------------------------
    def compile_expr(self, expr: Any) -> None:
        if isinstance(expr, g2.Literal):
            self.emit("CONST", expr.value)
            return
        if isinstance(expr, g2.Var):
            if "." in expr.name:
                _unsupported(expr, "dotted names / field access")
            self.emit("LOAD", expr.name)
            return
        if isinstance(expr, g2.Unary):
            if expr.op not in ("!", "-"):
                _unsupported(expr, f"unary {expr.op!r}")
            self.compile_expr(expr.right)
            self.emit("UNARY", expr.op)
            return
        if isinstance(expr, g2.Binary):
            if expr.op in ("&&", "||"):
                self.compile_logical(expr)
            else:
                self.compile_expr(expr.left)
                self.compile_expr(expr.right)
                self.emit("BINOP", expr.op)
            return
        if isinstance(expr, g2.Call):
            if expr.name in _UNSUPPORTED_BUILTINS:
                _unsupported(expr, f"builtin {expr.name!r} is not supported")
            if "." in expr.name:
                _unsupported(expr, "dotted calls / namespaces")
            self.emit("CHECK_CALL", (expr.name, len(expr.args)))
            for arg in expr.args:
                self.compile_expr(arg)
            self.emit("CALL", len(expr.args))
            return
        _unsupported(expr)

    def compile_logical(self, expr: g2.Binary) -> None:
        # Match `truth(l) and truth(r)` / `truth(l) or truth(r)`: the result is
        # always a Bool and the right operand is only evaluated when needed.
        short_jump = "JUMP_IF_FALSE" if expr.op == "&&" else "JUMP_IF_TRUE"
        self.compile_expr(expr.left)
        self.emit("TRUTH")
        self.emit("DUP")
        jump = self.emit(short_jump, None)
        self.emit("POP")
        self.compile_expr(expr.right)
        self.emit("TRUTH")
        self.patch(jump, self.here())


# ------------------------------------------------------------------ runtime
class _CallTarget:
    __slots__ = ("proto", "closure")

    def __init__(self, proto: FunctionProto, closure: Frame):
        self.proto = proto
        self.closure = closure


class _VM:
    def __init__(self, initial: Any = None):
        if initial is None:
            self.root = Frame()
        elif isinstance(initial, Frame):
            self.root = initial
        elif isinstance(initial, dict):
            self.root = Frame()
            self.root.vars.update(initial)
        else:
            raise TypeError("env must be a dict, a gama_ir.Frame or None")
        self.functions: dict[str, tuple[FunctionProto, Frame]] = {}
        self.out: list[str] = []
        self.call_depth = 0

    # -- call handling ---------------------------------------------------
    def _invoke(self, proto: FunctionProto, closure: Frame, args: list) -> Any:
        self.call_depth += 1
        try:
            limit = self._call_depth_limit()
            if self.call_depth > limit:
                raise IRRuntimeError(
                    f"call depth limit exceeded: {self.call_depth} > GAMA_L_MAX_CALL_DEPTH={limit}")
            local = Frame(parent=closure)
            for i, param in enumerate(proto.params):
                if i < len(args):
                    local.define(param.name, args[i])
                else:
                    local.define(param.name, self._exec(param.default, closure))
            return self._exec(proto.body, local)
        finally:
            self.call_depth -= 1

    @staticmethod
    def _call_depth_limit() -> int:
        raw = os.environ.get("GAMA_L_MAX_CALL_DEPTH")
        if raw is None or not raw.strip():
            return 1000
        try:
            return int(raw)
        except ValueError:
            return 1000

    # -- interpreter loop ------------------------------------------------
    def _exec(self, code: Code, frame: Frame) -> Any:
        instrs = code.instrs
        ip = 0
        stack: list[Any] = []
        frames: list[Frame] = [frame]
        while ip < len(instrs):
            ins = instrs[ip]
            ip += 1
            op = ins.op
            arg = ins.arg

            if op == "CONST":
                stack.append(arg)
            elif op == "LOAD":
                stack.append(frames[-1].get(arg))
            elif op == "LET":
                frames[-1].define(arg, stack.pop())
            elif op == "ASSIGN":
                frames[-1].assign(arg, stack.pop())
            elif op == "UNARY":
                value = stack.pop()
                stack.append((not _truth(value)) if arg == "!" else -value)
            elif op == "BINOP":
                right = stack.pop()
                left = stack.pop()
                stack.append(_binop(arg, left, right))
            elif op == "TRUTH":
                stack.append(_truth(stack.pop()))
            elif op == "DUP":
                stack.append(stack[-1])
            elif op == "POP":
                stack.pop()
            elif op == "PRINT":
                self.out.append(_render(stack.pop()))
            elif op == "JUMP":
                ip = arg
            elif op == "JUMP_IF_FALSE":
                if not _truth(stack.pop()):
                    ip = arg
            elif op == "JUMP_IF_TRUE":
                if _truth(stack.pop()):
                    ip = arg
            elif op == "NEW_FRAME":
                frames.append(Frame(parent=frames[-1]))
            elif op == "POP_FRAME":
                if len(frames) > 1:
                    frames.pop()
            elif op == "UNWIND":
                for _ in range(arg or 0):
                    if len(frames) > 1:
                        frames.pop()
            elif op == "CHECK_CALL":
                name, argc = arg
                entry = self.functions.get(name)
                if entry is None:
                    raise IRRuntimeError(f"unknown function {name}")
                proto, closure = entry
                required = sum(1 for p in proto.params if p.default is None)
                if not (required <= argc <= len(proto.params)):
                    raise IRRuntimeError(
                        f"{name} expects {required}..{len(proto.params)} args, got {argc}")
                stack.append(_CallTarget(proto, closure))
            elif op == "CALL":
                argc = arg
                args = stack[-argc:] if argc else []
                if argc:
                    del stack[-argc:]
                target = stack.pop()
                stack.append(self._invoke(target.proto, target.closure, args))
            elif op == "DEF":
                self.functions[arg.name] = (arg, frames[-1])
            elif op == "RETURN":
                return stack.pop() if stack else None
            elif op == "NOP":
                pass
            else:  # pragma: no cover - internal invariant
                raise IRRuntimeError(f"bad opcode {op!r}")
        return None


# ------------------------------------------------------------------ public API
def compile_program(ast: Any) -> Program:
    """Compile a parsed (optionally checked) ``gamal2`` AST into a :class:`Program`.

    ``ast`` is the list of statement nodes returned by ``gamal2.parse`` or
    ``gamal2.check``.  Passing a :class:`Program` returns it unchanged; passing
    a source string is a convenience that parses and checks it first.
    """
    if isinstance(ast, Program):
        return ast
    if isinstance(ast, str):
        ast = g2.check(ast)
    if not isinstance(ast, (list, tuple)):
        raise TypeError("compile_program expects a statement list, Program or source string")
    compiler = _Compiler()
    compiler.compile_statements(list(ast))
    return Program(compiler.code)


def run_program(ast: Any, env: Any = None) -> RunResult:
    """Compile (if needed) and run a program, returning printed output + globals.

    ``env`` seeds the global frame: a ``dict`` of initial bindings, a
    :class:`Frame`, or ``None``.
    """
    program = ast if isinstance(ast, Program) else compile_program(ast)
    vm = _VM(env)
    vm._exec(program.main, vm.root)
    return RunResult(list(vm.out), dict(vm.root.outermost().vars))
