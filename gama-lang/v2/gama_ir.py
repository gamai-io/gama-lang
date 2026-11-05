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
``match`` on ``Ok``/``Err`` results and enum variants (nullary, one-payload and
multi-payload patterns), ``record`` and ``enum`` declarations (including payload
variants), ``fn`` (named function definitions), ``return`` (inside functions
only), and expression statements.

Supported expressions
---------------------
Literals: ``Int``, ``Decimal``, ``Text``, ``Bool``, ``null``, list literals
``[a, b]`` and map literals ``{k: v}``.
Record construction (positional ``Point(1, 2)`` and named
``Point { x: 1, y: 2 }``), record copy/``with`` updates
(``p with { x = 3 }``, shallow, producing a new value of the same nominal
type), field access ``p.x`` (walking record fields and map keys), enum
variants ``Color.Red`` and payload constructors ``Some(5)`` /
``Option.Some(5)`` (bare when unambiguous), unary ``!``/``-``; binary ``+ - * / %``,
comparisons ``== != < <= > >=`` and short-circuit ``&&``/``||``; indexing
``base[i]`` (list/text, with the reference interpreter's ``int`` coercion and
Python-style negative indices); calls to *named, user-defined* functions
(including recursion and default parameters); the Result propagation operator
``expr?`` (inside a function whose declared return type is ``Result`` or
``Any``: ``Ok`` yields its payload, ``Err`` returns that ``Err`` from the
enclosing function); and the core builtins ``len``, ``text``, ``number``,
``round`` and ``Ok``/``Err``.

Records and enums follow the reference runtime exactly: record values are
nominal (type name plus declaration-ordered fields, value-based equality),
enum values are ``Enum.Variant`` (nullary) or ``Enum.Variant(payload, ...)``,
and both reuse the reference value classes for rendering.  Declarations are registered before the program runs
(so functions may refer to records/enums declared later) and nested record
declarations take effect when execution reaches them, mirroring
``gamal2.Interpreter``.

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
* ``prove``/``verify`` (and dotted proof names).  Plain ``import "lib.gm"`` is
  supported only through :func:`compile_file` (and the ``gamal2 ir run|compile
  FILE`` CLI), where the core loader flattens imported statements before
  compiling; compiling a bare source string has no filesystem context to
  resolve imports.  Aliased imports flatten too and are supported: the loader
  exposes module declarations as dotted names (``math.add``, ``math.pi``,
  ``geo.Point``) and this backend resolves them against the same function,
  global and nominal-type tables it uses for local names.
* In-place record field assignment (``p.x := 3``) is interpreter-only; the
  compiler rejects it with :class:`IRUnsupported` instead of silently creating
  a shadow variable. Record copy/``with`` updates *are* supported: an
  ``expr with { field = value, ... }`` compiles to a shallow copy of the
  receiver with the named fields replaced (declaration order preserved), and
  the original value is left untouched.
* Enum payload variants are supported for declarations, constructors and
  ``match`` bindings, including aliased imports: the core loader namespaces
  declarations and references (``geo.Color`` / ``geo.Color.Red`` /
  ``geo.Option.Some``) and this backend resolves the longest dotted enum
  prefix. Constructors are ``Some(5)`` (bare when unique), ``Option.Some(5)``
  and namespaced forms; match arms bind every payload (``Some(v)``,
  ``Pair(a, b)``, ``geo.Option.Some(v, s)``). A payload variant used as a bare
  value and a nullary variant called with ``()`` raise the reference runtime's
  messages. Named record construction with a dotted type name
  (``geo.Point { ... }``) is not accepted by the parser; positional
  construction ``geo.Point(1, 2)`` works.
* Pipes and first-class function values (including reading a namespaced
  function as a value) are unsupported.
* ``?`` is accepted only inside a named function whose declared return type is
  ``Result`` or ``Any``. Using it at the top level, in a function returning a
  concrete non-``Result`` type, or inside a default-parameter expression is
  rejected at compile time with :class:`IRUnsupported` (the checker raises the
  matching ``GamaTypeError`` first when compiling checked source). If an
  unchecked AST applies ``?`` to a non-``Result`` value, the VM raises
  ``IRRuntimeError("? expects Result")`` on first use.
* Builtins other than ``len``, ``text``, ``number``, ``round``, ``Ok``/``Err``
  (``sql``, ``ask``, ``upper``, result/text/collection helpers, ``mail``,
  ``remember``/``recall``, ``wait_user``, ...).
* ``match`` is supported for ``Ok``/``Err`` results and enum variants
  (nullary, one-payload and multi-payload, bare or qualified).  There is no
  structural pattern matching beyond those arms (records cannot be
  destructured).
* Map literals are JSON values: dotted field access (``m.k``) follows the
  reference fallback (it also works on maps), and ``m[0]`` uses the reference
  ``int`` coercion, so text-keyed maps are not indexable in either runtime.
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
    # Nominal type tables captured from top-level declarations.  Maps a record
    # name to its ordered field names and an enum name to its variant set; the
    # VM seeds its registries from these before running ``main``.
    records: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # ``enum -> (frozenset of variant names | variant -> payload type tuple)``.
    # A payload-free enum keeps the historical ``frozenset`` shape; an enum
    # with at least one payload stores ``{variant: (type, ...)}`` (nullary
    # variants map to ``()``).  Helpers below normalise both forms.
    enums: dict[str, Any] = field(default_factory=dict)

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


_MISSING = object()


class _AmbiguousVariant(Exception):
    """A bare variant name is declared by more than one enum."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _payload_types(payload: Any) -> tuple[str, ...]:
    """Normalise a declared payload spec to a tuple of type names.

    ``None`` is a nullary variant, a plain string is one payload, and a tuple
    is already the multi-payload form (mirrors ``gamal2._payload_type_list``).
    """
    if payload is None:
        return ()
    if isinstance(payload, tuple):
        return payload
    return (payload,)


def _enum_spec(variants: list) -> Any:
    """Build the ``Program.enums`` entry for one ``EnumDecl``.

    Payload-free enums keep the historical ``frozenset`` so existing code and
    tests that compare variants as a set keep working.  As soon as one variant
    carries a payload, the entry becomes ``{variant: (type, ...)}`` so arity (and
    the nullary/one/multi distinction) survives into the VM.
    """
    specs: dict[str, tuple[str, ...]] = {}
    has_payload = False
    for entry in variants:
        name = entry[0]
        payload = entry[1] if len(entry) > 1 else None
        types = _payload_types(payload)
        specs[name] = types
        if types:
            has_payload = True
    return specs if has_payload else frozenset(specs)


def _variant_payloads(spec: Any, variant: str) -> Any:
    """Payload tuple for ``variant`` in one enum spec, or ``_MISSING``.

    A nullary variant returns ``()`` (an empty tuple); an unknown variant
    returns the sentinel so callers can tell "absent" from "no payload".
    """
    if isinstance(spec, dict):
        return spec[variant] if variant in spec else _MISSING
    return () if variant in spec else _MISSING


def _resolve_enum_variant(enums: dict, name: str):
    """Resolve ``Enum.Variant`` / a bare ``Variant`` against ``enums``.

    Accepts qualified names with a namespaced enum (``geo.Color.Red``) and a
    bare variant that must be unique across all enums.  Returns
    ``(enum, variant, payload_types)`` or ``None``; raises
    :class:`_AmbiguousVariant` when a bare name belongs to several enums.
    """
    if "." in name:
        base, _, variant = name.rpartition(".")
        spec = enums.get(base)
        if spec is None:
            return None
        payloads = _variant_payloads(spec, variant)
        if payloads is _MISSING:
            return None
        return base, variant, payloads
    hits = []
    for base, spec in enums.items():
        payloads = _variant_payloads(spec, name)
        if payloads is not _MISSING:
            hits.append((base, name, payloads))
    if not hits:
        return None
    if len(hits) > 1:
        raise _AmbiguousVariant(name)
    return hits[0]


def _binding_names(bindings: Any) -> list[str]:
    """Normalise a match arm binding spec to a list of names.

    ``None`` means no binding, a string is one binding (the parser preserves
    that form), and a list/tuple is already multi-binding.
    """
    if bindings is None:
        return []
    if isinstance(bindings, str):
        return [bindings]
    return list(bindings)


def _match_kind(kind: str, value: Any, enums: dict) -> bool:
    """Whether ``value`` matches one ``match`` arm label.

    Mirrors ``gamal2.Interpreter.stmt``: ``Ok``/``Err`` match results and
    enum labels resolve through the same enum table as constructors, so bare
    variants, qualified variants and namespaced enums all behave alike.  An
    ambiguous bare variant simply does not match (the checker rejects it
    first); anything else (including a record) never matches an arm.
    """
    if kind in ("Ok", "Err"):
        return _result_matches(kind, value)
    if isinstance(value, g2.EnumVal):
        try:
            resolved = _resolve_enum_variant(enums, kind)
        except _AmbiguousVariant:
            return False
        if resolved is None:
            return False
        base, variant, _payloads = resolved
        return value.enum == base and value.variant == variant
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

    def __init__(self, in_function: bool = False, records=None, enums=None,
                 ret_type: str | None = None):
        self.code = Code()
        self.frame_depth = 0
        self.loops: list[dict] = []
        self.in_function = in_function
        # Declared return type of the enclosing function (``None`` at top level
        # and in default-argument expressions).  ``?`` needs ``Result``/``Any``.
        self.ret_type = ret_type
        self.match_seq = 0
        # Nominal type tables used only to decide, at compile time, whether a
        # ``Call`` is a positional record constructor.  Dotted names and
        # ``RecordLit`` are resolved dynamically at run time.
        self.records: dict[str, tuple[str, ...]] = records if records is not None else {}
        self.enums: dict[str, Any] = enums if enums is not None else {}

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
            if "." in stmt.name:
                # Field assignment mutates a record in place; the IR value
                # model has no record mutation, so reject it instead of
                # silently creating a shadow variable.
                _unsupported(stmt, "record field assignment")
            self.compile_expr(stmt.expr)
            self.emit("ASSIGN", stmt.name)
            return
        if isinstance(stmt, g2.Use):
            self.emit("NOP")
            return
        if isinstance(stmt, g2.RecordDecl):
            if getattr(stmt, "methods", None):
                # The IR value model has no method table or bound calls; reject
                # explicitly instead of compiling a record that silently loses
                # its methods (and would fail later on the call instead).
                _unsupported(stmt, "record methods")
            fields = tuple(fn for fn, _ft in stmt.fields)
            self.records[stmt.name] = fields
            self.emit("DEF_RECORD", (stmt.name, fields))
            return
        if isinstance(stmt, g2.EnumDecl):
            spec = _enum_spec(stmt.variants)
            self.enums[stmt.name] = spec
            self.emit("DEF_ENUM", (stmt.name, spec))
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
        if isinstance(stmt, g2.Import):
            # A source string has no file to resolve against; ``compile_file``
            # lets the core loader flatten plain imports before we get here.
            _unsupported(
                stmt,
                "imports are resolved only by compile_file() / 'ir run FILE'; "
                "a bare source string has no filesystem context",
            )
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
        for kind, bindings, body in stmt.arms:
            names = _binding_names(bindings)
            next_arm = None
            if kind != "_":
                self.emit("LOAD", hidden)
                next_arm = self.emit("MATCH_JUMP_UNLESS", [kind, None])

            self.emit("NEW_FRAME")
            self.frame_depth = match_base + 2
            if names:
                self.emit("LOAD", hidden)
                if kind in ("Ok", "Err"):
                    self.emit("MATCH_BIND_RESULT",
                              (kind, names, stmt.line, stmt.col))
                elif kind != "_":
                    self.emit("MATCH_BIND_ENUM",
                              (names, stmt.line, stmt.col))
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
        # Function bodies get their own nominal tables (copied) so a record
        # declared inside one body does not leak into later top-level code.
        body = _Compiler(
            in_function=True,
            records=dict(self.records),
            enums=dict(self.enums),
            ret_type=(stmt.ret or "Any"),
        )
        body.compile_statements(stmt.body)
        body.emit("CONST", None)
        body.emit("RETURN")

        params: list[ParamProto] = []
        for param in stmt.params:
            default: Code | None = None
            if param.default is not None:
                default_compiler = _Compiler(
                    records=dict(self.records), enums=dict(self.enums))
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
                # Resolved dynamically like the reference runtime: exact name,
                # then enum variant, then longest-bound-prefix field walk.
                self.emit("LOAD_NAME", (expr.name, expr.line, expr.col))
            else:
                self.emit("LOAD", expr.name)
            return
        if isinstance(expr, g2.RecordLit):
            # Evaluate field expressions in source order, then validate/order
            # them against the declaration at run time.
            names = tuple(str(k) for k, _v in expr.fields)
            for _k, value in expr.fields:
                self.compile_expr(value)
            self.emit("BUILD_RECORD", (expr.name, names, expr.line, expr.col))
            return
        if isinstance(expr, g2.RecordUpdate):
            # Copy-with evaluates the receiver first, then each replacement in
            # source order, exactly like the reference interpreter.  The VM
            # builds a fresh record of the receiver's nominal type, preserving
            # declaration order and leaving the receiver (and its aliases)
            # untouched (shallow copies share unchanged nested values).
            names = tuple(str(k) for k, _v in expr.fields)
            self.compile_expr(expr.base)
            for _k, value in expr.fields:
                self.compile_expr(value)
            self.emit("COPY_RECORD_WITH", (names, expr.line, expr.col))
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
        if isinstance(expr, g2.Try):
            # ``expr?`` is only meaningful inside a function that can return a
            # ``Result``.  Mirror ``gamal2.Checker.expr`` so an unchecked AST is
            # rejected with the same wording instead of silently diverging.
            if not self.in_function:
                raise IRUnsupported(
                    f"IR does not support Try: ? outside function "
                    f"(line {expr.line}, col {expr.col})")
            if self.ret_type not in ("Result", "Any"):
                raise IRUnsupported(
                    f"IR does not support Try: ? requires function returning "
                    f"Result (line {expr.line}, col {expr.col})")
            self.compile_expr(expr.expr)
            self.emit("TRY", (expr.line, expr.col))
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
            if expr.name in self.records:
                # Positional record constructor.  The reference runtime checks
                # arity *before* evaluating arguments, so emit the guard first.
                self.emit("CHECK_RECORD", (expr.name, len(expr.args), expr.line, expr.col))
                for arg in expr.args:
                    self.compile_expr(arg)
                self.emit("BUILD_RECORD_POS", (expr.name, len(expr.args), expr.line, expr.col))
                return
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
            # Dotted calls (``math.add``) resolve through the same function
            # table as plain names: the core loader stores aliased module
            # functions under their dotted name, so CHECK_CALL / CALL need no
            # special casing.  Positional record constructors were handled
            # above.  A name that is missing at run time reports the same
            # "unknown function" error as the reference interpreter.
            self.emit(
                "CHECK_CALL", (expr.name, len(expr.args), expr.line, expr.col))
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


class _EnumCtorTarget:
    """A resolved enum constructor awaiting its already-evaluated arguments."""

    __slots__ = ("enum", "variant")

    def __init__(self, enum: str, variant: str):
        self.enum = enum
        self.variant = variant


class _VM:
    def __init__(self, initial: Any = None, records=None, enums=None):
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
        # Nominal type registries.  Copies so a reused ``Program`` is unaffected
        # by declarations that activate only at run time.
        self.records: dict[str, tuple[str, ...]] = dict(records or {})
        self.enums: dict[str, Any] = dict(enums or {})

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

    # -- dotted names / records / enums ----------------------------------
    def _enum_value(self, name: str, line: int, col: int):
        """Resolve ``Enum.Variant`` where ``Enum`` may itself be namespaced.

        The longest dot-separated prefix that names a declared enum wins, so
        ``geo.Color.Red`` resolves when the enum table stores ``geo.Color``.
        A payload variant used as a bare value is rejected with the reference
        runtime's message instead of producing a payload-less value.  Returns
        ``None`` when no prefix names an enum (field access et al.).
        """
        parts = name.split(".")
        for i in range(len(parts) - 1, 0, -1):
            enum_name = ".".join(parts[:i])
            if enum_name not in self.enums:
                continue
            variant = ".".join(parts[i:])
            payloads = _variant_payloads(self.enums[enum_name], variant)
            if payloads is _MISSING:
                raise IRRuntimeError(f"unknown enum variant {name}", line, col)
            if payloads:
                raise IRRuntimeError(
                    f"enum variant {enum_name}.{variant} requires a payload "
                    f"argument; write {enum_name}.{variant}(...)", line, col)
            return g2.EnumVal(enum_name, variant)
        return None

    def _load_name(self, frame: Frame, arg) -> Any:
        """Resolve a dotted name exactly like ``gamal2.Interpreter.expr``.

        Order: an exact binding, then an enum variant (``Enum.Variant``), then
        the longest bound prefix walked through record fields / map keys.
        """
        name, line, col = arg
        try:
            return frame.get(name)
        except IRRuntimeError:
            pass
        variant_value = self._enum_value(name, line, col)
        if variant_value is not None:
            return variant_value
        parts = name.split(".")
        base_n = None
        for i in range(len(parts) - 1, 0, -1):
            try:
                frame.get(".".join(parts[:i]))
                base_n = i
                break
            except IRRuntimeError:
                pass
        if base_n is None:
            raise IRRuntimeError(f"unknown variable {name}")
        obj = frame.get(".".join(parts[:base_n]))
        for part in parts[base_n:]:
            if isinstance(obj, g2.RecordVal):
                if part not in obj.fields:
                    raise IRRuntimeError(f"unknown field {name}", line, col)
                obj = obj.fields[part]
                continue
            if (isinstance(obj, dict) and part not in obj
                    and isinstance(obj.get("value"), dict)):
                obj = obj["value"]
            if not isinstance(obj, dict) or part not in obj:
                raise IRRuntimeError(f"unknown field {name}", line, col)
            obj = obj[part]
        return obj

    def _define_record(self, name: str, fields: tuple[str, ...]) -> None:
        self.records[name] = tuple(fields)

    def _define_enum(self, name: str, spec) -> None:
        self.enums[name] = spec

    def _check_record(self, arg) -> None:
        name, argc, line, col = arg
        decl = self.records.get(name)
        if decl is None:
            raise IRRuntimeError(f"unknown function {name}", line, col)
        if argc != len(decl):
            raise IRRuntimeError(
                f"{name} expects {len(decl)} fields, got {argc}", line, col)

    def _make_record(self, name: str, decl: tuple[str, ...],
                     pairs, line: int, col: int) -> Any:
        """Validate named fields and reorder them to declaration order."""
        values: dict[str, Any] = {}
        for key, value in pairs:
            if key in values:
                raise IRRuntimeError(f"{name}: duplicate field {key}", line, col)
            if key not in decl:
                raise IRRuntimeError(f"{name}: unknown field {key}", line, col)
            values[key] = value
        missing = [fn for fn in decl if fn not in values]
        if missing:
            raise IRRuntimeError(
                f"{name}: missing field{'s' if len(missing) > 1 else ''} "
                + ", ".join(missing), line, col)
        return g2.RecordVal(name, {fn: values[fn] for fn in decl})

    def _copy_record_with(self, base: Any, field_names: tuple[str, ...],
                          values: list, line: int, col: int) -> Any:
        """Apply ``base with { field = value, ... }`` like the interpreter.

        The result is a new :class:`gamal2.RecordVal` of the receiver's nominal
        type, with fields in declaration order.  The copy is shallow: the
        top-level field mapping is new, but unchanged nested record/container
        values are shared with the receiver.  Unknown or duplicate update
        fields are reported here (the checker normally rejects them first) so
        an unchecked AST cannot silently drop or double-apply an update.
        """
        if not isinstance(base, g2.RecordVal):
            raise IRRuntimeError("with expects a record value", line, col)
        decl = self.records.get(base.type_name)
        if decl is None:
            raise IRRuntimeError(f"unknown record {base.type_name}", line, col)
        updated = dict(base.fields)
        seen: set[str] = set()
        for key, value in zip(field_names, values):
            if key in seen:
                raise IRRuntimeError(
                    f"{base.type_name}: duplicate field {key} in with", line, col)
            seen.add(key)
            if key not in decl:
                raise IRRuntimeError(
                    f"{base.type_name}: unknown field {key} in with", line, col)
            updated[key] = value
        return g2.RecordVal(
            base.type_name, {fn: updated[fn] for fn in decl})

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
            elif op == "LOAD_NAME":
                stack.append(self._load_name(frames[-1], arg))
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
            elif op == "BUILD_RECORD":
                name, field_names, line, col = arg
                count = len(field_names)
                values = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                decl = self.records.get(name)
                if decl is None:
                    raise IRRuntimeError(f"unknown record {name}", line, col)
                stack.append(self._make_record(
                    name, decl, zip(field_names, values), line, col))
            elif op == "BUILD_RECORD_POS":
                name, argc, line, col = arg
                values = stack[-argc:] if argc else []
                if argc:
                    del stack[-argc:]
                decl = self.records.get(name)
                if decl is None:
                    raise IRRuntimeError(f"unknown function {name}", line, col)
                if argc != len(decl):
                    raise IRRuntimeError(
                        f"{name} expects {len(decl)} fields, got {argc}", line, col)
                stack.append(g2.RecordVal(name, dict(zip(decl, values))))
            elif op == "COPY_RECORD_WITH":
                field_names, line, col = arg
                count = len(field_names)
                values = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                base = stack.pop()
                stack.append(self._copy_record_with(
                    base, field_names, values, line, col))
            elif op == "INDEX":
                line, col = arg
                index = stack.pop()
                base = stack.pop()
                try:
                    stack.append(base[int(index)])
                except (IndexError, KeyError, TypeError, ValueError):
                    raise IRRuntimeError(
                        "index out of range or invalid", line, col) from None
            elif op == "TRY":
                # Result propagation: ``Ok`` yields its payload; ``Err`` returns
                # that Err from the enclosing function.  Because every function
                # body is one ``_exec`` call, a plain return unwinds any active
                # if/loop/match frames exactly like ``PropagateSignal`` does in
                # the reference interpreter, without raising a Python exception.
                line, col = arg
                value = stack.pop()
                if not isinstance(value, g2.ResultVal):
                    raise IRRuntimeError("? expects Result", line, col)
                if value.ok:
                    stack.append(value.value)
                else:
                    return g2.ResultVal(False, value.value)
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
                if not isinstance(stack[-1], (g2.ResultVal, g2.EnumVal)):
                    raise IRRuntimeError("match expects Result", line, col)
            elif op == "GET_RESULT_VALUE":
                stack.append(stack.pop().value)
            elif op == "MATCH_BIND_RESULT":
                kind, names, line, col = arg
                value = stack.pop()
                if len(names) != 1:
                    raise IRRuntimeError(
                        f"match: {kind} pattern binds exactly one value",
                        line, col)
                frames[-1].define(names[0], value.value)
            elif op == "MATCH_BIND_ENUM":
                names, line, col = arg
                value = stack.pop()
                if value.payloads is None:
                    raise IRRuntimeError(
                        f"enum variant {value.enum}.{value.variant} is "
                        f"nullary and cannot bind a payload", line, col)
                if len(names) != len(value.payloads):
                    raise IRRuntimeError(
                        f"enum variant {value.enum}.{value.variant} expects "
                        f"{len(value.payloads)} payload binding"
                        f"{'s' if len(value.payloads) != 1 else ''}, "
                        f"got {len(names)}", line, col)
                for name, item in zip(names, value.payloads):
                    frames[-1].define(name, item)
            elif op == "MATCH_JUMP_UNLESS":
                kind, target = arg
                value = stack.pop()
                if not _match_kind(kind, value, self.enums):
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
                name, argc, line, col = arg
                entry = self.functions.get(name)
                if entry is not None:
                    proto, closure = entry
                    required = sum(1 for p in proto.params if p.default is None)
                    if not (required <= argc <= len(proto.params)):
                        raise IRRuntimeError(
                            f"{name} expects {required}..{len(proto.params)} args, got {argc}",
                            line, col)
                    stack.append(_CallTarget(proto, closure))
                else:
                    # Functions first, then enum constructors: the reference
                    # interpreter resolves ``Call`` through ``self.funcs`` before
                    # ``find_enum_variant``, so a user function shadows a variant
                    # with the same name.  Arity/kind failures happen here,
                    # before arguments are evaluated, exactly like the
                    # interpreter (which checks before ``self.expr``).
                    try:
                        ctor = _resolve_enum_variant(self.enums, name)
                    except _AmbiguousVariant:
                        raise IRRuntimeError(
                            f"ambiguous enum constructor {name}; write "
                            f"Enum.{name}", line, col) from None
                    if ctor is None:
                        raise IRRuntimeError(
                            f"unknown function {name}", line, col)
                    base, variant, payloads = ctor
                    if not payloads:
                        raise IRRuntimeError(
                            f"enum variant {base}.{variant} is nullary; write "
                            f"{base}.{variant} without parentheses", line, col)
                    if argc != len(payloads):
                        raise IRRuntimeError(
                            f"enum variant {base}.{variant} expects "
                            f"{len(payloads)} payload argument"
                            f"{'s' if len(payloads) != 1 else ''}, "
                            f"got {argc}", line, col)
                    stack.append(_EnumCtorTarget(base, variant))
            elif op == "CALL":
                argc = arg
                args = stack[-argc:] if argc else []
                if argc:
                    del stack[-argc:]
                target = stack.pop()
                if isinstance(target, _EnumCtorTarget):
                    stack.append(g2.EnumVal(
                        target.enum, target.variant, payloads=tuple(args)))
                else:
                    stack.append(self._invoke(
                        target.proto, target.closure, args))
            elif op == "DEF":
                self.functions[arg.name] = (arg, frames[-1])
            elif op == "DEF_RECORD":
                name, fields = arg
                self._define_record(name, fields)
            elif op == "DEF_ENUM":
                name, variants = arg
                self._define_enum(name, variants)
            elif op == "CHECK_RECORD":
                self._check_record(arg)
            elif op == "RETURN":
                return stack.pop() if stack else None
            elif op == "NOP":
                pass
            else:  # pragma: no cover - internal invariant
                raise IRRuntimeError(f"bad opcode {op!r}")
        return None


# ------------------------------------------------------------------ public API
def _top_level_decls(stmts: list) -> tuple[dict, dict]:
    """Collect top-level record/enum declarations before compiling.

    Mirrors ``gamal2.Interpreter.run``: these are registered up front, so a
    function declared before a ``record``/``enum`` can still refer to it.
    Nested declarations are *not* included; they activate via ``DEF_RECORD`` /
    ``DEF_ENUM`` when execution reaches them.
    """
    records: dict[str, tuple[str, ...]] = {}
    enums: dict[str, Any] = {}
    for stmt in stmts:
        if isinstance(stmt, g2.RecordDecl):
            records[stmt.name] = tuple(fn for fn, _ft in stmt.fields)
        elif isinstance(stmt, g2.EnumDecl):
            enums[stmt.name] = _enum_spec(stmt.variants)
    return records, enums


def compile_program(ast: Any) -> Program:
    """Compile a parsed (optionally checked) ``gamal2`` AST into a :class:`Program`.

    ``ast`` is the list of statement nodes returned by ``gamal2.parse`` or
    ``gamal2.check``.  Passing a :class:`Program` returns it unchanged; passing
    a source string is a convenience that parses and checks it first.

    Imports cannot be resolved from a source string; use :func:`compile_file`
    for a ``.gm`` file (plain imports are flattened by the core loader).
    """
    if isinstance(ast, Program):
        return ast
    if isinstance(ast, str):
        ast = g2.check(ast)
    if not isinstance(ast, (list, tuple)):
        raise TypeError("compile_program expects a statement list, Program or source string")
    stmts = list(ast)
    top_records, top_enums = _top_level_decls(stmts)
    compiler = _Compiler(records=dict(top_records), enums=dict(top_enums))
    compiler.compile_statements(stmts)
    return Program(compiler.code, top_records, top_enums)


def compile_file(path: Any) -> Program:
    """Compile a ``.gm`` file, resolving imports first via the core loader.

    ``gamal2.check_file`` is the same front end ``gamal2 run`` uses: it applies
    ``_flatten`` (plain imports are expanded in place), the project-root import
    bound, cycle detection and the import depth limit, then type-checks the
    flattened program.  Nothing about that filesystem policy is reimplemented
    here; this is purely a file-aware wrapper around :func:`compile_program`.

    Plain ``import "lib.gm"`` therefore works in the IR backend.  Aliased
    imports (``import "lib.gm" as m``) also flatten: the loader exposes module
    functions, constants, records and enums as dotted names (``m.fn``,
  ``m.pi``, ``geo.Point``, ``geo.Color``, ``geo.Option.Some``), and this
  backend resolves them through the same tables it uses for local
  declarations.  Nullary and payload enum declarations (constructors and
  match bindings) compile through the same path.
  """
    return compile_program(g2.check_file(str(path)))


def run_program(ast: Any, env: Any = None) -> RunResult:
    """Compile (if needed) and run a program, returning printed output + globals.

    ``env`` seeds the global frame: a ``dict`` of initial bindings, a
    :class:`Frame`, or ``None``.
    """
    program = ast if isinstance(ast, Program) else compile_program(ast)
    vm = _VM(env, program.records, program.enums)
    vm._exec(program.main, vm.root)
    return RunResult(list(vm.out), dict(vm.root.outermost().vars))


def run_file(path: Any, env: Any = None) -> RunResult:
    """Compile a ``.gm`` file with :func:`compile_file` and run it on the VM.

    This is the file-aware counterpart of :func:`run_program`, so plain imports
    (and missing/cyclic import errors) behave exactly like the interpreter.
    """
    return run_program(compile_file(path), env=env)
