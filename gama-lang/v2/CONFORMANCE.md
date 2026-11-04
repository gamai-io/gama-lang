# SPEC 2.0 conformance — executable status

This table maps the acceptance tests in [`SPEC_2.0.md`](../SPEC_2.0.md) (T1–T10) to
the checks in [`tests/test_conformance.py`](tests/test_conformance.py) and records
what actually passes on the current `v2` runtime.

Run:

```bash
cd gama-lang/v2
python3 -m unittest discover -s tests -v
```

The conformance tests are deterministic and offline: no network calls, no fixed
clock, and every `GAMA_L_*` environment variable is stripped for each case.

> **Status: 2.0 is NOT complete.** The core runtime passes the checks below as
> scoped, but several 2.0 deliverables are still missing (see *Gaps*). Nothing
> here should be read as a claim that GAMA-L 2.0 is finished.

## Results

| Criterion | Status | Test / evidence | Note |
|---|---|---|---|
| **T1** — 20 arithmetic/pipe programs produce exact values | **PASS** | `T1ArithmeticAndPipes.test_t1_arithmetic_and_pipes_20_cases` | Exactly 20 table-driven cases; integers, decimals, precedence, `%`, `round`, and `sql`/`filter`/`select`/`sum`/`limit`/`map`/`count` pipes assert exact output. |
| **T2** — type error rejected before execution with line/column | **PASS** | `T2StaticTypeErrors.test_t2_type_errors_carry_line_and_column`, `...test_t2_rejected_before_any_execution` | `GamaTypeError` carries `line >= 1` and `col >= 1`; a trailing `print` is never evaluated because checking fails first. |
| **T3** — `effects {}` cannot call `sql.write` or `mail` | **PASS** | `T3EffectDeclarations.test_t3_effects_empty_rejects_sql_write`, `..._rejects_mail`, `..._declared_effect_is_allowed` | Static `GamaEffectError` for `sql.write` and `mail`; an explicitly declared `sql.read` effect is accepted. |
| **T4** — `wait user` non-interactive returns `Denied`, never proceeds | **PASS** | `T4HumanInTheLoop.test_t4_wait_user_denied_and_never_proceeds` | Returns `Denied` (falsy); the guarded branch is not entered, execution continues after the gate. |
| **T5** — `prove` receipt verifies; one changed field invalidates it | **PASS** (in-process) | `T5ProofReceipts.test_t5_receipt_verifies`, `...test_t5_single_field_change_invalidates` | Valid receipt → `ok: true`, no errors. Changing one value while keeping `sha256`/`prev` → `ok: false`, `hash_mismatch`. Chains are per-interpreter; see T6. |
| **T6** — same `prove` idempotency key does not emit a second effect | **PASS** (in-process; optional cross-run via `GAMA_L_PROOF_LEDGER`) | `T6ProofIdempotency.test_t6_same_key_same_value_single_record`, `...test_t6_same_key_different_value_is_rejected`, `test_proof_ledger.ProofLedgerCrossRun.*` | Within one run: same key + same value reuses the record, `proof_chain` stays length 1; same key + different value raises `duplicate key`. With `GAMA_L_PROOF_LEDGER=<file>`, records persist as JSONL and load on startup: repeat runs reuse the record without appending, different values still fail, chain `prev` continues from the last persisted record, and `verify` rejects untrusted `prev`. Intent/lease binding is implemented (`test_intent_lease.*`): when `GAMA_L_PROOF_INTENT`/`GAMA_L_PROOF_LEASE` are set, new records carry and sign them; `verify` rejects a mismatch (or a missing binding when the env is set); `GAMA_L_REQUIRE_INTENT_LEASE=1` requires both env values and a match. Uniqueness is `(intent, lease, key)`: re-proving an existing key under a different binding is an error, never a silent idempotent hit. Legacy receipts without the fields still verify when the env vars are unset. |
| **T7** — module import cycle rejected with a clear error | **PASS** | `T7ImportCycles.test_t7_import_cycle_rejected` | Temp project (`a.gm` ↔ `b.gm`) raises `GamaError: import cycle: …` before execution. |
| **T8** — `ask` records prompt hash and model; offline returns `Unavailable` | **PASS** | `T8AskMetadata.test_t8_offline_ask_unavailable_with_metadata` | Offline: output `Unavailable`, `last_ask.model == "mock"`, `last_ask.prompt_hash == sha256(prompt)`. |
| **T9** — 10 example programs run from a clean checkout without network | **PASS** | `T9ExamplesOffline.test_t9_examples_run_offline` | All 11 shipped examples run with every `GAMA_L_*` network/env knob removed (contract requires ≥10). |
| **T10** — `gamal check` and `gamal fmt` are deterministic | **PASS** (current formatter) | `T10Determinism.test_t10_fmt_deterministic_and_idempotent`, `..._check_deterministic`, `..._preserves_multiline_string` | `fmt` output is identical across calls and idempotent, and formatted output still checks. Errors are byte-identical across repeated `check` calls. Caveat: the formatter is AST-based but still moves comments inside expressions to a safe position and does not line-wrap long expressions. |

## Summary

- **PASS: 10** — T1, T2, T3, T4, T5, T6, T7, T8, T9, T10
- **PARTIAL: 0**
- **GAP: 0** among T1–T10 as scoped above

## Gaps (why 2.0 is not complete)

These are outside the T1–T10 checks or only partially covered; see
`README.md` → *Not implemented yet*:

1. **External intent/lease registry with expiry/revocation (beyond T6).** The
   optional `GAMA_L_PROOF_LEDGER=<file>` JSONL ledger provides persistent
   receipts and cross-run idempotency, and receipts can bind to
   `GAMA_L_PROOF_INTENT`/`GAMA_L_PROOF_LEASE` with mismatch rejection. There is
   no external registry, expiry, or revocation; the ledger is append-only with
   no compaction or key rotation (the separate `gama_ledger.py` tool can verify
   and explicitly truncate to a valid prefix, never silently).
2. **Renamed exports and `export enum`.** Explicit `export fn`/`export let`/
   `export record` lists are implemented (alias imports show only exported
   declarations; modules without `export` keep the legacy all-visible alias
   behaviour, and plain imports always flatten). Renamed exports and
   `export enum` are not implemented; side-effect top-level statements remain
   rejected and `use` remains a no-op.
3. **Compiler/IR integration and production resource budget.** A standalone
   bytecode IR prototype (`gama_ir.py`) covers a documented subset and is
   exposed via the opt-in `gamal2 ir run|compile` CLI, but it is not the
   production run path. Runtime limits are enforced (call depth, output,
   statement/expression steps, loop limit); there is no production resource
   budget or cost model.
4. **Full type system.** No full inference. Nominal records with typed fields,
   positional/named construction and dotted field access are implemented
   (`tests/test_records.py`); nullary and single-payload enums are implemented
   (`tests/test_enums.py`, `tests/test_payload_enums.py`). Generics,
   multi-payload/generic enum variants, record methods,
   field update/copy syntax, and structural subtyping are missing; the IR
   prototype does not compile record/enum declarations or literals.
5. **Formatter edges.** `fmt` is AST-based; it preserves comments and strings
   and is idempotent, but comments inside expressions may move to a safe
   position and long expressions are not line-wrapped (T10 still passes).
6. **Deployed public registry, marketplace signing, published release.**
   Build/checksums and a local HTTP registry exist; publishing does not.
7. **Full standard library.** `sql`, `mail`, `memory`, and `proof` are present
   (SQL uses the demo table or a read-only SQLite DB; mail is SMTP-gated); `http`
   and a full `table` module are not implemented. The invoice demo exists as
   `examples/invoice.gm` (SQL → pipe → `prove`/`verify`) but runs on the demo
   table and a mock invoice, not a real ERP.

## Method

- Harness: `gamal2.py` public API — `run`, `check`, `run_file`, `fmt_source`.
- Isolation: each case removes all `GAMA_L_*` variables and restores them on
  teardown; SQL uses the built-in demo table unless a test sets a temp DB.
- Determinism: no wall-clock assertions, no network, no shared files; the T7
  case uses `tempfile.TemporaryDirectory()`.
