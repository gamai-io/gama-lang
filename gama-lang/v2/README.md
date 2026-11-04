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
and hover read the imported file's direct top-level `fn` declarations
(namespaced completion does not list module `let` constants yet).
Signature help works for known builtins, user `fn`/lambda-typed `let` values,
and namespaced module functions.

The core exposes a module's top-level `let` constants as `alias.name`, typed
and evaluated at import time, and module functions see the same renamed
binding. Any other non-declaration top-level statement under an alias
(`print`, `prove`, `verify`, loops, assignments, expression statements) is
rejected (`use` is allowed as a no-op).
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

### Reproducible release verification

`scripts/verify_release.sh` runs the whole release smoke test from a clean
temporary directory (no artifacts are written into the repo):

1. `release.py build` twice and compares the SHA256 of the wheel and sdist to
   prove the build is byte-reproducible;
2. `release.py verify` plus `sha256sum -c SHA256SUMS`, and checks the
   `RELEASE.json` manifest;
3. installs the wheel into a fresh virtualenv and runs `gama-l --help`,
   `gama-l run examples/hello.gm`, and `gama-l doctor --json` (8/8);
4. validates `vscode/package.json` and every referenced extension asset, and
   packages the extension when `vsce` is available offline (otherwise it
   reports the missing tool and never publishes).

```bash
bash scripts/verify_release.sh
```

The wheel ships code only; `gama-l doctor` needs a tree with `examples/`, so
run it from the source checkout or pass `--root` to a directory that exposes
`examples/`. Publishing (PyPI, VS Code Marketplace) stays manual.

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

## Opt-in bytecode IR (CLI)

The standalone bytecode IR prototype in `gama_ir.py` is exposed as an **opt-in**
CLI path. The default `run` command keeps using the reference tree-walking
interpreter; nothing about that backend changes.

```bash
python3 gamal2.py ir run FILE.gm        # compile on gama_ir and run on the IR VM
python3 gamal2.py ir compile FILE.gm    # print the instruction listing
python3 gamal2.py ir disasm FILE.gm     # alias for compile
python3 gamal2.py ir compile --json FILE.gm
```

`ir run` parses and type-checks the file with the normal front end, compiles the
checked AST with `gama_ir`, and prints the VM output. Plain
`import "lib.gm"` statements are resolved by the same core loader `run` uses
(flattened, with the project-root bound, cycle detection and depth limit), so
imported functions, constants and records work in the IR backend too. Aliased
imports (`import "lib.gm" as m`) flatten, but `m.fn(...)` is a dotted call and
is rejected with `IRUnsupported`; `gama_ir.compile_program` on a bare source
string has no filesystem context for imports — use `gama_ir.compile_file` for
files. A construct outside the IR
subset exits nonzero with `IRUnsupported: IR does not support ... (line, col)`;
an IR runtime failure reports `IRRuntimeError`; parse/check/file errors keep
their usual message. `compile`/`disasm` print one `index  OPCODE [arg]` line per
instruction (or `{"file": ..., "instructions": [...]}` with `--json`). This is
also reachable as `gama-l ir ...` through the packaged console script.

Exact supported subset:

- Statements: `let`/`:=`, `print`, `if`/`else` (incl. `else if` chains),
  `while`, `for` over list/text, `break`, `continue`, `match` on `Ok`/`Err`,
  named `fn` definitions, `return` (inside functions), expression statements,
  and `use` as a no-op.
- Expressions: `Int`/`Decimal`/`Text`/`Bool`/`null`, list `[a, b]` and map
  `{k: v}` literals, variables, `!`/`-`, `+ - * / %`, `== != < <= > >=`,
  short-circuit `&&`/`||`, indexing `base[i]` (list/text, reference `int`
  coercion, negative indices), and calls to named user-defined functions
  (recursion and default parameters included).
- Builtins: `len`, `text`, `number`, `round`, `Ok`, `Err` only.

Not supported (raises `IRUnsupported`; the normal interpreter still supports
them): `prove`/`verify`, `import`, field access `a.b`, namespaced/dotted calls,
pipes, `try`/`expr?`, lambdas and first-class/expression callees, structural
matching beyond the `Ok`/`Err` arms, and all other builtins (`sql`, `ask`,
`split`, `unwrap`, `wait_user`, mail, remember/recall, ...). The IR also does
not enforce `GAMA_L_MAX_STEPS`/`GAMA_L_LOOP_LIMIT` (it does honour
`GAMA_L_MAX_CALL_DEPTH`); it is a prototype, not a hardened runtime. Map
literals are JSON values, so text-keyed maps are not indexable. Successful
programs produce byte-for-byte the same output as the interpreter, and runtime
error messages keep the interpreter's text and line/col.

## Implemented

- `import "file.gm"` with cycle detection (relative to the importing file)
- module namespaces: `import "lib.gm" as math`, then `math.add(2,3)` and `math.pi`; plain imports still flatten (including top-level `let`); aliased modules expose top-level `let` constants and `fn` definitions, evaluated at import time, and reject side-effect top-level statements (`use` is allowed as a harmless no-op)
- explicit module exports: `export fn`, `export let` and `export record` mark the alias-visible surface; other top-level declarations stay private but remain callable inside the module. A module with at least one `export` exposes only exported names through `import ... as`; a module with no `export` keeps the legacy behaviour (all top-level `fn`/`let`/`record` visible). Plain imports always flatten all declarations for compatibility. `export` is only allowed at top level, duplicate export names are rejected, and `export enum` is currently rejected (enums stay module-global).
- `let` with optional type annotation, `:=` assignment
- `fn` and SPEC `make` with typed parameters, defaults, return type, `effects { ... }`
- first-class functions and lambdas: `fn(x: Int) -> Int { ... }`, `let f = ...`, `f(41)`, passing functions as values, named functions as values after definition, IIFE `(fn...)(41)`, and recursive lambdas
- `if` / `else` blocks, `return`
- `for x in list/text`, `while cond`, `break`/`continue`, list/text indexing
- `Ok`/`Err` Result values and `match` arms (`Ok(v)`, `Err(e)`, `_`)
- `expr?` Result propagation: unwraps `Ok`, returns `Err` from the enclosing function; the function must return `Result`
- core builtins: `unwrap`, `unwrap_or`, `is_ok`, `is_err`; `split`, `join`, `contains`, `trim`, `upper`, `lower`, `replace`, `starts_with`, `ends_with`; `keys`, `values`, `has`; numeric `abs`, `min`, `max`, `floor`, `ceil`; text/list `reverse`, `slice`, `index_of`, `sort`; conversion `to_int` (`to_text` is already covered by `text`)
- literals: text, int, decimal, bool, null, list, map
- arithmetic/comparison/boolean operators; a newline immediately after a binary operator continues the expression on the next line (statement boundaries still require the operator to be on the same line)
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
- `prove` (hash-chained receipt, optional HMAC via `GAMA_L_PROOF_KEY`, optional persistent JSONL ledger via `GAMA_L_PROOF_LEDGER`, optional intent/lease binding via `GAMA_L_PROOF_INTENT`/`GAMA_L_PROOF_LEASE`) and `verify` (tamper detection, intent/lease mismatch rejection; `GAMA_L_REQUIRE_INTENT_LEASE=1` requires both env values and a match). Concurrent appends to one ledger are serialized (POSIX `flock`) across tail-read, hash and append, so parallel runs extend a single chain; a forked or malformed ledger is refused by `prove` and reported by `verify`
- runtime limits via `GAMA_L_MAX_CALL_DEPTH` (default 200; the effective cap is additionally clamped to `(sys.getrecursionlimit() - 100) // 4`, so an explicit higher value cannot exhaust the CPython stack — deep recursion raises `GamaError`, never `RecursionError`) and `GAMA_L_MAX_OUTPUT_CHARS` (default 1000000)
- optional global step limit via `GAMA_L_MAX_STEPS` (unset, blank, malformed, or negative = disabled): counts every executed statement dispatch (including nested block and function-body statements) plus one tick per `for`/`while` body iteration; expression evaluation and the function call itself are not counted separately. On exceed raises `GamaRuntimeError` with the count, the limit, and the source line/col. `GAMA_L_LOOP_LIMIT` (default 1000000) still bounds `while` iterations independently; a present but blank or non-integer value raises `GamaRuntimeError` instead of leaking `ValueError`
- optional expression step limit via `GAMA_L_MAX_EXPR_STEPS` (unset, blank, malformed, or negative = disabled): counts every `Interpreter.expr` evaluation; nested calls count individually (e.g. `1 + 2` is three steps: the `Binary` plus both `Literal`s). On exceed raises `GamaRuntimeError` with the count, the limit, and the source line/col. It is independent of `GAMA_L_MAX_STEPS`, call depth, output, and loop limits, and remains counted when the cap is disabled
- standalone bytecode IR prototype (`gama_ir.py`) for a documented subset: statements/expressions, `if`/`else if`, `while`, `for`, list/map literals and indexing, `break`/`continue`, `fn`, `Ok`/`Err` + `match`, and the core builtins `len`/`text`/`number`/`round`; also records/enums, field access and enum variants; plain imports resolve through `compile_file`, aliased/dotted calls remain unsupported; opt-in via `gamal2 ir run|compile` (see above), the interpreter and default `run` backend are unchanged
- enums: nullary `enum Color { Red, Green, Blue }` and payload `enum Option { Some(Int, Text), None }`; values are `Enum.Variant` (nullary) or `Variant(args...)` / `Enum.Variant(args...)` (one or more payloads), annotation `let x: Option = Some(5)`, nominal equality (cross-enum `==` is a type error), and `match` arms `Enum.Variant`, `Variant`, `Variant(x)`, `Enum.Variant(x)`, `_`. Payload printing is deterministic: `Enum.Variant(<field-value>)` where the payload is rendered like a record field value (text JSON-quoted, decimals plain fixed form, `null` for null) and nullary variants print `Enum.Variant`. Wrong payload type/arity, unknown or ambiguous constructors, and nullary/payload pattern mismatches raise `GamaTypeError`. Limits: payload variants are non-generic (types must be simple identifiers); a bare constructor must be unique across enums (otherwise qualify `Enum.Variant(...)`); nullary variants cannot be called with `()`; payload variants cannot be used as a bare value; enums remain top-level and distinct from records/functions
- nominal record (struct) types: `record Point { x: Int, y: Int }`, positional construction `Point(1, 2)`, named construction `Point { x: 3, y: 4 }` (local record names only), typed fields, dotted access `p.x` (including nested `l.a.x`), `let p: Point = ...`, and nominal/value equality. Constructors enforce arity, missing/extra/duplicate fields and field types (`GamaTypeError`); unknown field access is rejected statically when the base type is a record. Printing is deterministic: declaration-order fields, `Name { x: 1, y: "text" }`, decimals with their plain fixed form, and text JSON-quoted. Records are exported through aliased imports (`geo.Point`) and their type names are namespaced (`geo.Point { ... }`). Gaps: no record methods/update/with syntax, constructor calls are not first-class values, `record` is contextual (not a reserved word), named construction requires a local declaration, the IR backend now supports records/enums for the documented subset, and LSP has record/enum completion, hover, definition, references and rename
- LSP completion/hover for records/enums (fields, variants, declarations) and VS Code grammar/snippets
- reproducible benchmark suite (`scripts/bench.py`) for interpreter vs IR and core operations
- AST-based formatter for the current language (comments and multi-line strings preserved; idempotent; records/enums included; deterministic width wrapping at 88 columns by default, configurable via `fmt --width`)
- static type errors and static effect violations
- CLI `run`, `check`, `fmt`, `test`, `pkg`, `lsp`, `ir`

## Benchmark (reproducible, offline)

`scripts/bench.py` measures the existing parse/check/run pipeline without
modifying the interpreter, IR prototype, or CLI. It uses only the Python
standard library and never touches the network.

```bash
python3 scripts/bench.py --runs 5 --seed 0
python3 scripts/bench.py --runs 5 --json
```

What is measured (separately, so the numbers add up):

- `parse_ms` — lexer + parser only (`gamal2.parse`).
- `check_ms` — static checker on a pre-parsed AST (parse excluded).
- `run_ms` — tree-walking interpreter on a checked AST (parse/check excluded).
- IR (only for the documented IR subset) — `compile_ms` and `run_ms` are
  reported separately; `speedup` is interpreter `run` median / IR `run` median.
  Programs outside the IR subset are listed with the concrete
  `IRUnsupported` reason, not skipped or faked. Where IR runs, its output is
  compared against the interpreter's output (`outputs_match`).
- Startup/import — inside-child import times for `gamal2` and `gamal2+gama_ir`,
  plus cold Python process startup.
- Memory — `resource.ru_maxrss` from a fresh child after importing both
  modules (process high-water mark, not per-program memory; unavailable on
  platforms without `resource`).
- Formatter — end-to-end `gamal2.fmt_source` on a mixed sample.

Methodology: `--runs N` timed samples per measurement and `--warmup W` untimed
iterations; median/min/max (and raw samples in JSON). Program execution order is
shuffled with `--seed` (results are sorted by name); the same seed/arguments
produce the same structure and IR outputs. JSON includes `schema`, `tool`,
`runs`, `warmup`, `seed`, one entry per program, and explicit `notes`.

Limits (do not overclaim): the programs are small and are not a representative
workload or a general language-performance claim; timings are single-machine
micro-benchmarks and include CPython overhead; the IR is an opt-in prototype,
not the default backend; peak RSS is a whole-process high-water mark; and a
faster number here is not a conformance or correctness result. Use more runs
and a quiet machine for comparisons, and read medians rather than single
samples.

## Tests

```bash
python3 -m unittest discover -s tests -v   # 1549/1549
python3 gamal2.py doctor --json                # 8/8 checks
../../scripts/ci_local.sh                      # py_compile + tests + doctor + VS Code JSON/smoke
```

Conformance coverage: arithmetic/pipes (20 cases), type errors, effect
violations (including `sql.write`/`mail` stubs), HITL returns `Denied`,
proof/tamper, duplicate proof keys, optional HMAC proof signatures (`GAMA_L_PROOF_KEY`; `GAMA_L_REQUIRE_SIG=1` requires them),
imports/cycles/diamonds, multi-table SQLite schema checks, git/path package
dependencies, LSP completion/hover/definition/references/symbols/tokens/code actions/
rename/formatting/didChange, SPEC syntax aliases/defaults, loops/indexing,
records (construction/access/naming/equality/namespaces), Result/match, VS Code LSP client,
HTTP/local registry, AI connector, guarded SQL writes and SMTP mail, offline
`ask` = `Unavailable`, 11 examples, canonical `fmt` check/write, doctor, and
reproducible release metadata.

Conformance: see `CONFORMANCE.md` (SPEC 2.0 T1–T10: **PASS 10/10**, including optional cross-run proof ledger).

## Not implemented yet

- deployed/public package registry (the HTTP server is implemented and testable)
- renamed exports (`export fn a as b`), `export enum`, and selective re-exports; explicit export lists mark the alias surface, but modules without any `export` keep the legacy all-visible alias behaviour
- external intent/lease registry with expiry/revocation (env-bound intent/lease receipts are implemented; there is no external registry)
- full type inference and generics; generic enum variants; record methods/field update/structural subtyping
- formatter remaining edges: comments inside short (unwrapped) expressions and inside pipe stages may move to a safe position; `record`/`enum` declarations, `fn` headers, control-flow headers and `prove` are not width-wrapped; indivisible tokens longer than the width still overflow
- VS Code Marketplace signing/publishing (VSIX packaging is reproducible locally)
- publishing a built release to PyPI (build/checksums/verify work)

The old v0.1 transpiler remains in `gama-lang/` for reference. Do not call this
full 2.0 until the remaining conformance tests and connectors are done.
