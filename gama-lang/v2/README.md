# GAMA-L 2.0 core (executable)

This is a real parser + static checker + tree-walking interpreter, not the old
transpiler. It is a **core subset** of `SPEC_2.0.md`; the rest is still roadmap.

## Run

```bash
python3 gamal2.py run examples/invoice.gm
python3 gamal2.py check examples/invoice.gm
python3 gamal2.py test
```

## Package manager (local + git)

```bash
python3 gamal2.py pkg init .
python3 gamal2.py pkg install .
python3 gamal2.py pkg update .
python3 gamal2.py pkg list .
python3 gamal2.py pkg search registry.json
python3 gamal2.py pkg add registry.json math 0.1.0
```

```toml
[dependencies]
math = { path = "math" }
lib = { git = "https://example.com/lib.git", rev = "v1.2.0" }
```

Path dependencies must stay inside the project root; symlinks are rejected.
Git dependencies clone into `.gamal/cache/<name>` and are pinned in
`.gamal/lock.json`; reinstall fails on a lock mismatch until `pkg update`.
Modules resolve from `.gamal/modules/<name>` via `import "name/file.gm"`.
A local/static registry is a JSON file (path, `file://`, or `http(s)://`)
consumed by `pkg search|resolve|add`. For a loopback/private HTTP registry set
`GAMA_L_REGISTRY_ALLOW_PRIVATE=1` (the client blocks private hosts by default).
Serve one with:

```bash
python3 gama_registry_server.py --registry registry.json --port 8089
```

Any git URL (including private remotes) works with the host's git credentials.

## Editor support (LSP, stdio)

```bash
python3 gamal2.py lsp
```

JSON-RPC language server: `initialize`, `didOpen/didChange/didSave`
diagnostics, completion, hover, signature help, definition, references,
document symbols, semantic tokens, code actions, rename, document formatting,
`shutdown`, `exit`. Malformed frames return a JSON-RPC error and no longer
kill the server; multiline strings keep correct line/column positions.
Positions are converted between LSP UTF-16 and Python indexes.

Completion covers the current keywords, types (`Function`, `Result`, `Table`,
`Json`, `Null`, …), all builtins with signature details, document `let`/`fn`
names, and import aliases. For `import "lib.gm" as <cursor>` the only
inferable alias is a sanitized module filename stem; it is omitted when it
collides with a top-level name or an existing alias. After `alias.`, completion
and hover read the imported file's direct top-level `fn` declarations.
Signature help works for known builtins, user `fn`/lambda-typed `let` values,
and namespaced module functions.

Tooling limitations: namespaced completion does not follow re-exported imports
and never exposes a module's top-level `let` values; the core rejects any
non-`fn` top-level statement under an alias (`use` is allowed as a no-op).
Builtin parameter names are LSP-only labels, since the core has no
builtin parameter names. Diagnostics still use the in-memory `check(text)`
path, so a document with an `import` reports the core's "import requires
check_file/run_file" message even though the file would check/run fine on
disk.

A syntax-only VS Code package is in `vscode/` (TextMate grammar + snippets +
a dependency-free LSP client). It highlights `import ... as` namespaces,
`Function`, namespaced calls, and lambdas, and ships snippets for `impas`,
`callns`, `iife`, and `fnlettype`. The extension requires a trusted workspace
before spawning Python, supports `gama-lsp` from PATH or a configured script,
and restarts with bounded backoff.

## Distribution

`pyproject.toml` defines the `gama-l` package and console scripts `gama-l`,
`gama-lsp`, `gama-doctor`, `gama-registry`, `gama-release`; modules are
`gamal2`, `gama_lsp`, `gama_cli`, `gama_fmt`, `gama_doctor`, `gama_mail`,
`gama_registry`, `gama_registry_server`, `gama_ai`, `release`.
The CLI wrapper is `gama_cli.py`. `release.py build` writes deterministic
wheel/tar, `SHA256SUMS`, and `RELEASE.json`; `release.py verify` checks them.

## Doctor

```bash
python3 gamal2.py doctor
python3 gamal2.py doctor --json
```

Checks Python, module imports, every example parse/run, formatter idempotence,
LSP API (including rename/formatting/code actions), SQL write guard, and a
local package install.

## Formatting

```bash
python3 gamal2.py fmt file.gm          # print
python3 gamal2.py fmt --check file.gm  # rc 1 if unformatted
python3 gamal2.py fmt --write file.gm  # write in place
```

## Implemented

- `import "file.gm"` with cycle detection (relative to the importing file)
- module namespaces: `import "lib.gm" as math`, then `math.add(2,3)`; plain imports still flatten (including top-level `let`); aliased modules expose only `fn` definitions and reject other top-level statements (`use` is allowed as a harmless no-op)
- `let` with optional type annotation, `:=` assignment
- `fn` and SPEC `make` with typed parameters, defaults, return type, `effects { ... }`
- first-class functions and lambdas: `fn(x: Int) -> Int { ... }`, `let f = ...`, `f(41)`, passing functions as values, named functions as values after definition, IIFE `(fn...)(41)`, and recursive lambdas
- `if` / `else` blocks, `return`
- `for x in list/text`, `while cond`, `break`/`continue`, list/text indexing
- `Ok`/`Err` Result values and `match` arms (`Ok(v)`, `Err(e)`, `_`)
- `expr?` Result propagation: unwraps `Ok`, returns `Err` from the enclosing function; the function must return `Result`
- core builtins: `unwrap`, `unwrap_or`, `is_ok`, `is_err`; `split`, `join`, `contains`, `trim`, `upper`, `lower`, `replace`, `starts_with`, `ends_with`; `keys`, `values`, `has`; numeric `abs`, `min`, `max`, `floor`, `ceil`; text/list `reverse`, `slice`, `index_of`, `sort`; conversion `to_int` (`to_text` is already covered by `text`)
- literals: text, int, decimal, bool, null, list, map
- arithmetic/comparison/boolean operators
- pipes: `filter`, `select`, `sum`, `limit`, `count`, `map`
- `ask` (offline mock, or OpenAI-compatible `GAMA_L_ASK_URL`), `sql` (demo table, or read-only SQLite via `GAMA_L_DB`), `remember/recall`
- `wait_user` (non-interactive returns `Denied`; falsy)
- guarded `sql_write` (INSERT/UPDATE/DELETE; needs `GAMA_L_ALLOW_WRITE=1`, rejects DDL/stacked statements)
- guarded SMTP `mail` (needs `GAMA_L_MAIL_ENABLED=1`; config via `GAMA_L_SMTP_*`)
- SPEC syntax aliases: `make`, `ask "..." with context`, `remember a.b = v`, `recall a.b`, `wait user "..."`, `use sql`
- `round(value, digits)`
- stdlib semantics: `abs` preserves Int/Decimal; `min`/`max` take one or more numbers; `floor`/`ceil` return Int; `reverse`/`slice` work on Text and List (`slice(seq, start, end)` is half-open with explicit `0 <= start <= end <= len`, no negative indices); `index_of` returns the first index or `-1`; `sort` returns a new ascending list and requires all numbers or all Text (stable, as Python's sort); `to_int` accepts Int, an integral Decimal, or Text of an optional sign plus digits (surrounding whitespace is trimmed)
- `ask` uses `gama_ai` when `GAMA_L_AI_URL` is set; config via `GAMA_L_AI_KEY`,
  `GAMA_L_AI_MODEL`, `GAMA_L_AI_ALLOW_HTTP=1` for local HTTP; HTTPS by default,
  retries 429/5xx, 1 MiB response cap; redirects are refused
- `prove` (hash-chained receipt, optional HMAC via `GAMA_L_PROOF_KEY`, optional persistent JSONL ledger via `GAMA_L_PROOF_LEDGER`, optional intent/lease binding via `GAMA_L_PROOF_INTENT`/`GAMA_L_PROOF_LEASE`) and `verify` (tamper detection, intent/lease mismatch rejection; `GAMA_L_REQUIRE_INTENT_LEASE=1` requires both env values and a match)
- runtime limits via `GAMA_L_MAX_CALL_DEPTH` (default 1000; CPython frames may trigger first, lower it for predictable caps) and `GAMA_L_MAX_OUTPUT_CHARS` (default 1000000)
- optional global step limit via `GAMA_L_MAX_STEPS` (unset, blank, malformed, or negative = disabled): counts every executed statement dispatch (including nested block and function-body statements) plus one tick per `for`/`while` body iteration; expression evaluation and the function call itself are not counted separately. On exceed raises `GamaRuntimeError` with the count, the limit, and the source line/col. `GAMA_L_LOOP_LIMIT` (default 1000000) still bounds `while` iterations independently
- optional expression step limit via `GAMA_L_MAX_EXPR_STEPS` (unset, blank, malformed, or negative = disabled): counts every `Interpreter.expr` evaluation; nested calls count individually (e.g. `1 + 2` is three steps: the `Binary` plus both `Literal`s). On exceed raises `GamaRuntimeError` with the count, the limit, and the source line/col. It is independent of `GAMA_L_MAX_STEPS`, call depth, output, and loop limits, and remains counted when the cap is disabled
- standalone bytecode IR prototype (`gama_ir.py`) for a documented subset: statements/expressions, `if`/`else if`, `while`, `for`, list/map literals and indexing, `break`/`continue`, `fn`, `Ok`/`Err` + `match`, and the core builtins `len`/`text`/`number`/`round`; not integrated into the interpreter/CLI
- static type errors and static effect violations
- CLI `run`, `check`, `fmt`, `test`, `pkg`, `lsp`

## Tests

```bash
python3 -m unittest discover -s tests -v   # 761/761
python3 gamal2.py doctor --json                # 8/8 checks
```

Conformance coverage: arithmetic/pipes (20 cases), type errors, effect
violations (including `sql.write`/`mail` stubs), HITL returns `Denied`,
proof/tamper, duplicate proof keys, optional HMAC proof signatures (`GAMA_L_PROOF_KEY`; `GAMA_L_REQUIRE_SIG=1` requires them),
imports/cycles/diamonds, multi-table SQLite schema checks, git/path package
dependencies, LSP completion/hover/definition/references/symbols/tokens/code actions/
rename/formatting/didChange, SPEC syntax aliases/defaults, loops/indexing,
Result/match, VS Code LSP client,
HTTP/local registry, AI connector, guarded SQL writes and SMTP mail, offline
`ask` = `Unavailable`, 11 examples, canonical `fmt` check/write, doctor, and
reproducible release metadata.

Conformance: see `CONFORMANCE.md` (SPEC 2.0 T1–T10: **PASS 10/10**, including optional cross-run proof ledger).

## Not implemented yet

- deployed/public package registry (the HTTP server is implemented and testable)
- full export lists/renamed exports; aliased modules reject non-`fn` top-level statements instead of exporting module state (`use` stays a no-op)
- external intent/lease registry with expiry/revocation; IR/bytecode and expression-level step accounting
- full type inference, records/enums/generics
- full AST formatter (current `fmt` is token-based, preserves comments and
  strings, but does not reformat multi-line strings)
- Marketplace packaging/signing for the VS Code extension
- publishing a built release (build/checksums work)

The old v0.1 transpiler remains in `gama-lang/` for reference. Do not call this
full 2.0 until the remaining conformance tests and connectors are done.
