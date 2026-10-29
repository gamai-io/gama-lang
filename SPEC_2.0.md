# GAMA-L 2.0 — language specification (draft, implementable)

Status: **design target**, not the current runtime. Current `gama-lang/` is v0.1
(transpiler). This document defines what must be true before calling anything 2.0.

## One sentence

A small, typed, dataflow language where **AI, SQL, human approval and proof are
language primitives**, and every consequential program can emit a verifiable
receipt.

## Non-goals for 2.0

- Not a general-purpose replacement for Python.
- Not an operating system.
- Not a new model.
- No claim of production readiness before the conformance suite passes.

## Core syntax (normative examples)

```gama
# values and pipes
customers := sql("SELECT konto, naziv, mjesto FROM kupdob")
active    := customers | filter mjesto == "Sarajevo" | select konto, naziv

# AI is a keyword, not an import
summary := ask "Summarize these customers" with active

# memory is scoped and typed
remember customer.default = "ALFA"
name := recall customer.default

# human gate
ok := wait user "Post this invoice?"

# proof is a first-class statement
prove invoice.posted {
  invoice: "MOCK-2026-001",
  total: 75.47,
  erp_id: "MOCK-2026-001"
}

# modules
use sql
use proof

# function with explicit effects
make total(net: Decimal, rate: Decimal = 0.17) -> Decimal effects {} {
  return round(net * (1 + rate), 2)
}
```

## Semantics

1. **Values** are typed: `Text`, `Int`, `Decimal`, `Bool`, `Table`, `Json`, `Null`.
2. **Pipes** are left-to-right transformations; each stage is pure unless it
   declares an effect.
3. **Effects** are explicit: `sql.read`, `sql.write`, `net`, `mail`, `exec`,
   `proof.anchor`. A function without an effect declaration cannot perform one.
4. **AI calls** are non-deterministic and must be marked: `ask` returns a value
   plus `model`, `prompt_hash`, and `confidence` metadata.
5. **`prove`** canonicalizes the value, hashes it, and emits a receipt bound to
   the current intent/lease. It never turns a false claim into a true one.
6. **`wait user`** blocks; in non-interactive mode it fails closed.
7. **Errors** are values (`Result`), not exceptions across module boundaries.

## Compiler pipeline

```
source -> lexer -> parser -> typed AST -> effect check -> IR -> Python backend
                                                       -> receipt hooks
```

The Python backend is an implementation detail. The language must have a
conformance suite independent of the backend.

## Acceptance tests (must pass for 2.0)

- T1: 20 arithmetic/pipe programs produce exact expected values.
- T2: type error is rejected before execution with line/column.
- T3: a function declaring `effects {}` cannot call `sql.write` or `mail`.
- T4: `wait user` in non-interactive mode returns `Denied`, never proceeds.
- T5: `prove` receipt verifies; changing one field invalidates it.
- T6: same `prove` idempotency key does not emit a second effect.
- T7: module import cycle is rejected with a clear error.
- T8: `ask` records prompt hash and model; offline mode returns `Unavailable`.
- T9: 10 example programs run from a clean checkout without network.
- T10: `gamal check` and `gamal fmt` are deterministic.

## Deliverables for a credible 2.0

- `gamal` CLI: `run`, `check`, `fmt`, `test`, `prove`.
- Grammar file (EBNF), reference parser, typed AST.
- Standard library: `sql`, `http`, `mail`, `memory`, `proof`, `table`.
- Conformance suite and CI on Python 3.10–3.12.
- VS Code syntax + diagnostics.
- One real example: invoice -> SQL/ERP -> proof, with no hidden effects.

## Honest status

- v0.1 exists and can transpile a narrow subset.
- 2.0 does **not** exist yet.
- The first milestone is `gamal check` + typed AST + T1–T5, not a marketing release.
