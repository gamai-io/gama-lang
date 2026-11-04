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
``let`` / ``:=`` (assign), ``print``, ``if``/``else`` (including ``else if``
chains), ``while``, ``for`` (list/text iteration), ``break``, ``continue``,
``match`` on ``Ok``/``Err``, ``fn`` (named function definitions), ``return``
(inside functions only), and expression statements.

Supported expressions
---------------------
Literals: ``Int``, ``Decimal``, ``Text``, ``Bool``, ``null``, list literals
``[a, b]`` and map literals ``{k: v}``.
Variables, unary ``!``/``-``; binary ``+ - * / %``, comparisons
``== != < <= > >=`` and short-circuit ``&&``/``||``; indexing ``base[i]``
(list/text, with the reference interpreter's ``int`` coercion and Python-style
negative indices); calls to *named, user-defined* functions (including
recursion and default parameters); and the core builtins ``len``, ``text``,
``number``, ``round`` and ``Ok``/``Err``.

Runtime model
-------------
The VM is a stack machine with absolute jump targets, lexical frames
(:class:`Frame`) matching ``gamal2.Env`` semantics, and a call stack realised by
recursion (one ``_exec`` per call).  ``let`` defines in the current frame,
``:=`` updates the nearest binding and otherwise creates a global, ``if``
branches get their own frame, and ``while`` bodies share the enclosing frame
exactly like the reference interpreter.  ``for`` creates a fresh frame per item
(so the loop variable does not leak) and ``match`` keeps its subject in a
private scope frame, which lets ``break``/``continue`` unwind it cleanly.
Rendering of printed values and decimal division/modulo follow the reference
implementation byte-for-byte.

Limitations (raise ``IRUnsupported`` / documented runtime errors)
------------------------------------------------------------------
* ``prove``/``verify`` (and dotted proof names) and ``import``.
* Dotted names / field access (``a.b``), dotted calls / namespaces
  (``mod.fn``), pipes and ``try``.
* Builtins other than ``len``, ``text``, ``number``, ``round``, ``Ok``/``Err``
  (``sql``, ``ask``, ``upper``, result/text/collection helpers, ``mail``,
  ``remember``/``recall``, ``wait_user``, ...).
* ``match`` is supported only for ``Ok``/``Err`` results; there is no
  structural pattern matching beyond those arms.
* Map literals are JSON values: field access (``m.k``) stays unsupported in the
  IR, and ``m[0]`` uses the reference ``int`` coercion, so text-keyed maps are
  not indexable in either runtime.
* Lambdas, first-class function values and expression callees (``f()`` where
  ``f`` is a variable).  Calling such a value fails with a clear runtime error.
* ``break``/``continue`` outside a loop and ``return`` outside a function are
  rejected at compile time.
* The safety limits ``GAMA_L_MAX_STEPS`` and ``GAMA_L_LOOP_LIMIT`` are **not**
  enforced by the IR (the optional ``GAMA_L_MAX_CALL_DEPTH`` guard is).  The
  IR is a prototype, not a hardened runtime.
* Errors produced while running supported code raise :class:`IRRuntimeError`
  (a subclass of :class:`gamal2.GamaError`) with messages aligned to the
  reference runtime; they remain catchable as :class:`IRError` too.

The public API is :func:`compile_program`, :func:`run_program`, the
:class:`Program` (with :meth:`Program.disassemble`) and :class:`RunResult`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
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


class IRRuntimeError(IRError, g2.GamaError):
    """Raised when supported code fails at run time (bad call, unknown name...).

    Also a :class:`gamal2.GamaError`, so callers that catch the language error
    family (rather than the IR-specific ``IRError``) still see IR failures.
    """

    def __init__(self, msg: str, line: int = 0, col: int = 0):
        IRError.__init__(self, msg)
        self.msg, self.line, self.col = msg, line, col

    def __str__(self) -> str:
        return f"{self.msg} (line {self.line}, col {self.col})"


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


# Core builtins the IR implements with reference semantics.
_SUPPORTED_BUILTINS = {"len", "text", "number", "round", "Ok", "Err"}

# Builtins implemented by the reference interpreter but outside the IR subset.
# Naming them lets us fail at compile time with a clear message instead of a
# confusing runtime "unknown function".
_UNSUPPORTED_BUILTINS = {
    "sql", "ask", "remember", "recall", "wait_user",
    "sql_write", "mail", "unwrap", "unwrap_or", "is_ok",
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


class _ForState:
    """Cursor for one active ``for`` loop (kept on the VM value stack)."""

    __slots__ = ("items", "index")

    def __init__(self, items: list):
        self.items = items
        self.index = 0


def _result_matches(kind: str, value: Any) -> bool:
    if not isinstance(value, g2.ResultVal):
        return False
    if kind == "Ok":
        return value.ok
    if kind == "Err":
        return not value.ok
    return False


def _builtin(name: str, args: list, line: int, col: int) -> Any:
    """Evaluate a supported builtin on already-evaluated arguments.

    Messages mirror ``gamal2.Interpreter.expr`` so IR/interpreted failures are
    directly comparable; all failures are :class:`IRRuntimeError` (a
    ``GamaError``), never raw Python exceptions.
    """
    if name in ("Ok", "Err"):
        if len(args) != 1:
            raise IRRuntimeError(f"{name} expects 1 arg", line, col)
        return g2.ResultVal(name == "Ok", args[0])
    if name == "len":
        if len(args) != 1:
            raise IRRuntimeError(f"len expects 1 args, got {len(args)}", line, col)
        value = args[0]
        if not isinstance(value, (str, list, tuple, dict)):
            raise IRRuntimeError(
                f"len expects Text, List or Json, got {g2.tname(value)}", line, col)
        return len(value)
    if name == "text":
        if len(args) != 1:
            raise IRRuntimeError(f"text expects 1 args, got {len(args)}", line, col)
        return str(args[0])
    if name == "number":
        if len(args) != 1:
            raise IRRuntimeError(f"number expects 1 args, got {len(args)}", line, col)
        raw = args[0]
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            raise IRRuntimeError(
                f"number: cannot convert {raw!r} to Decimal", line, col) from None
    if name == "round":
        if not (1 <= len(args) <= 2):
            raise IRRuntimeError(
                f"round expects 1..2 args, got {len(args)}", line, col)
        raw = args[0]
        try:
            digits = int(args[1]) if len(args) > 1 else 0
        except (InvalidOperation, ArithmeticError, ValueError, TypeError):
            # ``int(Decimal('NaN'))`` raises ``InvalidOperation`` (an
            # ``ArithmeticError``); the IR reports a language error instead of
            # leaking a Python exception.
            raise IRRuntimeError("round: digits must be Int", line, col) from None
        try:
            value = Decimal(str(raw))
            return value.quantize(Decimal(1).scaleb(-digits))
        except (InvalidOperation, ValueError, TypeError):
            raise IRRuntimeError(
                f"round: cannot round {raw!r} to Decimal", line, col) from None
    raise IRRuntimeError(f"unknown builtin {name}", line, col)  # pragma: no cover


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
        self.match_seq = 0

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
        if isinstance(stmt, g2.For):
            self.compile_for(stmt)
            return
        if isinstance(stmt, g2.Match):
            self.compile_match(stmt)
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
            # ``else if`` is represented as a nested ``If`` in ``If.other``.
            # The reference interpreter evaluates it inside the else branch's
            # own frame, so compile the nested ``If`` under this frame too and
            # let its own branches add their frames recursively.
            self.emit("NEW_FRAME")
            self.frame_depth += 1
            if isinstance(stmt.other, g2.If):
                self.compile_if(stmt.other)
            else:
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

    def compile_for(self, stmt: g2.For) -> None:
        # Reference semantics: evaluate the iterable once, then run the body in
        # a fresh child frame per item.  The sequence cursor lives on the value
        # stack so ``continue`` can resume it without copying Python iterators.
        self.compile_expr(stmt.iterable)
        self.emit("ITER_PREP", (stmt.line, stmt.col))
        base_depth = self.frame_depth
        start = self.here()
        next_jump = self.emit("FOR_NEXT", None)

        self.emit("NEW_FRAME")
        self.frame_depth += 1
        self.emit("LET", stmt.name)

        ctx = {
            "base_depth": base_depth,
            "continue": start,
            "breaks": [],
            # ``break`` must also discard the sequence cursor left on the stack.
            "break_cleanup": ["POP"],
        }
        self.loops.append(ctx)
        self.compile_statements(stmt.body)
        self.loops.pop()

        self.frame_depth -= 1
        self.emit("POP_FRAME")
        self.emit("JUMP", start)

        end = self.here()
        self.patch(next_jump, end)
        for index in ctx["breaks"]:
            self.patch(index, end)

    def compile_match(self, stmt: g2.Match) -> None:
        # Keep the subject in a private scope frame (not on the value stack) so
        # ``break``/``continue`` inside an arm can unwind it with frames alone.
        self.compile_expr(stmt.expr)
        self.emit("MATCH_PREP", (stmt.line, stmt.col))
        match_base = self.frame_depth
        self.emit("NEW_FRAME")
        self.frame_depth = match_base + 1
        hidden = f"$match#{self.match_seq}"
        self.match_seq += 1
        self.emit("LET", hidden)

        end_jumps: list[int] = []
        for kind, var, body in stmt.arms:
            next_arm = None
            if kind != "_":
                self.emit("LOAD", hidden)
                next_arm = self.emit("MATCH_JUMP_UNLESS", [kind, None])

            self.emit("NEW_FRAME")
            self.frame_depth = match_base + 2
            if var:
                self.emit("LOAD", hidden)
                self.emit("GET_RESULT_VALUE")
                self.emit("LET", var)
            self.compile_statements(body)

            self.emit("POP_FRAME")  # arm frame
            self.emit("POP_FRAME")  # match scope
            end_jumps.append(self.emit("JUMP", None))
            if next_arm is not None:
                self.code.instrs[next_arm].arg[1] = self.here()
            # Fall-through for the next arm still runs inside the match scope.
            self.frame_depth = match_base + 1

        self.emit("POP_FRAME")
        self.frame_depth = match_base
        self.emit("MATCH_FAIL", (stmt.line, stmt.col))

        end = self.here()
        for index in end_jumps:
            self.patch(index, end)

    def compile_break(self, stmt: g2.Break) -> None:
        if not self.loops:
            _unsupported(stmt, "break outside a loop")
        ctx = self.loops[-1]
        unwind = self.frame_depth - ctx["base_depth"]
        if unwind:
            self.emit("UNWIND", unwind)
        for op in ctx.get("break_cleanup", ()):
            self.emit(op)
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
        if isinstance(expr, g2.ListExpr):
            for item in expr.items:
                self.compile_expr(item)
            self.emit("BUILD_LIST", len(expr.items))
            return
        if isinstance(expr, g2.MapExpr):
            # ``MapExpr.items`` is an insertion-ordered dict of str -> expr; the
            # keys are static, so only the values are evaluated.
            for value in expr.items.values():
                self.compile_expr(value)
            self.emit("BUILD_MAP", tuple(expr.items.keys()))
            return
        if isinstance(expr, g2.Index):
            self.compile_expr(expr.base)
            self.compile_expr(expr.index)
            self.emit("INDEX", (expr.line, expr.col))
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
            if expr.name in _SUPPORTED_BUILTINS:
                for arg in expr.args:
                    self.compile_expr(arg)
                self.emit(
                    "BUILTIN",
                    (expr.name, len(expr.args), expr.line, expr.col),
                )
                return
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
            elif op == "BUILD_LIST":
                count = arg or 0
                items = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                stack.append(list(items))
            elif op == "BUILD_MAP":
                keys = arg or ()
                count = len(keys)
                values = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                stack.append(dict(zip(keys, values)))
            elif op == "INDEX":
                line, col = arg
                index = stack.pop()
                base = stack.pop()
                try:
                    stack.append(base[int(index)])
                except (IndexError, KeyError, TypeError, ValueError):
                    raise IRRuntimeError(
                        "index out of range or invalid", line, col) from None
            elif op == "ITER_PREP":
                line, col = arg
                seq = stack.pop()
                if isinstance(seq, str):
                    seq = list(seq)
                if not isinstance(seq, (list, tuple)):
                    raise IRRuntimeError("for needs list/text", line, col)
                stack.append(_ForState(list(seq)))
            elif op == "FOR_NEXT":
                state = stack[-1]
                if state.index >= len(state.items):
                    stack.pop()
                    ip = arg
                else:
                    stack.append(state.items[state.index])
                    state.index += 1
            elif op == "BUILTIN":
                name, argc, line, col = arg
                args = stack[-argc:] if argc else []
                if argc:
                    del stack[-argc:]
                stack.append(_builtin(name, args, line, col))
            elif op == "MATCH_PREP":
                line, col = arg
                if not isinstance(stack[-1], g2.ResultVal):
                    raise IRRuntimeError("match expects Result", line, col)
            elif op == "GET_RESULT_VALUE":
                stack.append(stack.pop().value)
            elif op == "MATCH_JUMP_UNLESS":
                kind, target = arg
                value = stack.pop()
                if not _result_matches(kind, value):
                    ip = target
            elif op == "MATCH_FAIL":
                line, col = arg
                raise IRRuntimeError("match: no arm matched", line, col)
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
