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

Generic named functions are reflected in hover and signature help with their
declared type parameters (`fn id<T>(x: T) -> T`, `fn pair<A, B>(...)`) and a
`Type parameters: ... (position N)` summary; parameters that use a type
parameter carry a matching `type parameter` note. Completion after `id<`
offers type parameters that are in scope from an enclosing generic function,
then the callee's declared type parameters for context, then the built-in type
names; `fn id<` offers the built-in type names only (type-parameter names are
chosen by the author and cannot be inferred). The core parser still reads
`id<Int>(1)` for locally declared generic functions (count/name checks,
formatter round-trip); imported/namespaced generic calls and nested type
arguments are not recognized. LSP signature help also recovers explicit
arguments when the base name resolves to a generic `fn` declaration, and
semantic tokens mark declared type-parameter names as types.

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
`Function`, namespaced calls, lambdas, record `with { ... }` updates,
payload/multi-payload enums, aliased variants (`geo.Color.Red`), `export
fn/let/record`, and the postfix `?` operator. Snippets cover those features
(`recordwith`, `payloadenum`, `multipayloadenum`, `payload`, `pair`,
`aliasvariant`, `matchpayload`, `exportfn`, `exportlet`, `exportrecord`,
`tryexpr`) plus `impas`, `callns`, `iife`, and `fnlettype`. Grammar limits:
record-update scopes end at the first `}`, only three-part alias chains are
scoped, and `export enum` remains unsupported by the core. The extension requires a trusted workspace
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
`examples/`.

### Release dry run (recommended gate)

`scripts/release_dry_run.sh` is the single "everything except publish" gate. It
builds twice, verifies hashes, installs the wheel into a fresh virtualenv, runs
the shipped CLI and doctor, checks the wheel with `twine` when available,
packages the VSIX when `vsce` is available, and prints a manifest with versions,
sizes, hashes, and tool availability. All work happens in a temporary directory.

```bash
cd gama-lang/v2
bash scripts/release_dry_run.sh

# Optional: keep the verified artifacts + manifest (outside the repo, e.g. CI)
KEEP_DIST=/tmp/gama-l-dist bash scripts/release_dry_run.sh
```

Exact checks:

1. `release.py build --out dist-a` and `release.py build --out dist-b`; the
   wheel and sdist SHA-256 must match across both builds.
2. `release.py verify --out dist-a` and `sha256sum -c SHA256SUMS`.
3. `python3 -m venv`, install the wheel with `--no-index --no-deps`, then
   `gama-l --help`, `gama-l run examples/hello.gm`, and
   `gama-l doctor --json --root <temp-root>` (8/8).
4. `twine check` on the wheel when twine is installed.
5. `vsce package` (or cached `npx --no-install @vscode/vsce`) when available.
6. A manifest containing the core version, extension version, artifact sizes
   and hashes, doctor result, and tool matrix.

Limitations:

- `release.py` writes a deterministic *source* archive, not a PEP 517 sdist (it
  has no `PKG-INFO`). `twine check` therefore validates the wheel only; the
  sdist is covered by `release.py verify` and `sha256sum`. The sdist is not
  directly PyPI-uploadable.
- `twine` and `vsce` are optional. Missing tools are reported and skipped; real
  build/checksum/install/CLI/doctor failures exit non-zero.
- Byte-reproducibility is checked within a single run on the same
  Python/toolchain (two builds); builds on different Python versions are not
  compared.
- Artifacts are never written into the repository (the `dist/` directories are
  git-ignored) and nothing is published. `KEEP_DIST` is only for CI/scratch
  space, not for committing.
- `gama-l doctor` runs against the installed wheel by pointing `--root` at a
  temporary directory that symlinks the source `examples/`.

### Release workflow (tag → artifacts; publish is opt-in)

`.github/workflows/release.yml` runs on `v*` tag pushes and on manual dispatch.
It runs the full test suite (Python 3.10 and 3.12), then the release job runs
`scripts/release_dry_run.sh` with `KEEP_DIST` set, validates the workflow YAML,
and uploads `dist` (wheel, sdist, `SHA256SUMS`, `RELEASE.json`, manifest, and
VSIX when packaged) as the `gama-l-release` artifact.

- Concurrency: `release-${{ github.ref }}` with `cancel-in-progress: false`.
- Timeouts: 20 minutes for tests, 30 minutes for release.
- A tag push **never publishes**: it builds and uploads artifacts only.
- Publishing is opt-in via manual dispatch with `publish: true` **and**
  repository secrets. The PyPI step requires `PYPI_API_TOKEN`; the VS Code
  Marketplace step requires `VSCE_PAT`. Without those secrets the publish steps
  are skipped and only artifacts are uploaded.

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
checked AST with `gama_ir`, and prints the VM output. `import "lib.gm"`
statements are resolved by the same core loader `run` uses (flattened, with the
project-root bound, cycle detection and depth limit), so imported functions,
constants and records work in the IR backend too. Aliased imports
(`import "lib.gm" as m`) flatten into dotted declarations (`m.fn`, `m.pi`,
`m.Point`, `m.Color`, `m.Option.Some`) and are supported: `m.fn(...)`, `m.pi`,
`m.Color.Red`, payload constructors `m.Option.Some(5)` and positional record
construction `m.Point(1, 2)` (including `let p: m.Point`) resolve against the
same tables as local names. Enum payload declarations, constructors and match
bindings compile the same way locally and through aliases.
`gama_ir.compile_program` on a bare source string has no filesystem context for
imports — use `gama_ir.compile_file` for files. A construct outside the IR
subset exits nonzero with `IRUnsupported: IR does not support ... (line, col)`;
an IR runtime failure reports `IRRuntimeError`; parse/check/file errors keep
their usual message. `compile`/`disasm` print one `index  OPCODE [arg]` line per
instruction (or `{"file": ..., "instructions": [...]}` with `--json`). This is
also reachable as `gama-l ir ...` through the packaged console script.

Exact supported subset:

- Statements: `let`/`:=`, `print`, `if`/`else` (incl. `else if` chains),
  `while`, `for` over list/text, `break`, `continue`, `match` on `Ok`/`Err`
  and enum variants (nullary, one-payload and multi-payload patterns),
  named `fn` definitions, `return` (inside functions), expression statements,
  `prove name = expr` / `verify expr` (hash-chained receipts with optional HMAC
  and the optional persistent JSONL ledger — see below), and `use` as a no-op.
- Expressions: `Int`/`Decimal`/`Text`/`Bool`/`null`, list `[a, b]` and map
  `{k: v}` literals, variables, enum variants and payload constructors
  (`Color.Red`, `Some(5)`, `Option.Some(5)`, `Pair(1, 2)`), `!`/`-`,
  `+ - * / %`, `== != < <= > >=`, short-circuit `&&`/`||`, indexing `base[i]`
  (list/text, reference `int` coercion, negative indices), calls to named
  user-defined functions (recursion and default parameters included), lambda
  expressions `fn(x: Int) -> Int { ... }` compiled to IR function values that
  capture the defining lexical frame, calls through function values (`f(2)`
  for a `Function`-typed binding or parameter), named functions read as values
  (`let g = add`, including namespaced functions), expression callees such as
  IIFEs `(fn ...)(41)` and returned lambdas `makeAdder(5)(37)`, and Result
  propagation
  `expr?` inside a function returning `Result`/`Any` (an `Err` returns from
  that function; `?` elsewhere is `IRUnsupported`), and pipe expressions
  `base | filter <expr> | select a, b | sum a | limit <expr> | count | map a`.
  Pipe stages run left to right on the same value exactly like the reference
  interpreter: `filter`/`limit` sub-expressions are compiled to their own
  code and evaluated with the active row bound (`filter` rows shadow outer
  bindings; `limit` sees the enclosing scope), and `select`/`sum`/`count`/`map`
  use the interpreter's runtime operations, evaluation order and error
  messages. The normal checker rules apply, so column stages (`select`, `sum`,
  `map`, and column-referencing `filter`) need a `Table`/`Any` base; `count`,
  `limit` and constant `filter` work on lists, and the runtime also accepts
  list/text bases from unchecked ASTs. A hand-built AST with an unknown stage
  keyword or missing stage argument is rejected with `IRUnsupported`.
- Builtins: `len`, `text`, `number`, `round`, `Ok`, `Err` only.

Not supported (raises `IRUnsupported`; the normal interpreter still supports
them): unresolved `import` nodes (file imports go through
`compile_file`), bare `Attr` expression nodes, host callables injected through
`run_program(env=...)` (first-class functions cover IR-compiled lambdas and
named functions only), structural matching beyond `Ok`/`Err`/enum arms
(records cannot be destructured; enum payload variants are fully supported),
and all other builtins (`sql`, `ask`, `split`, `unwrap`, `wait_user`, mail,
remember/recall, ...). First-class calls are checked for callability and arity
before their arguments are evaluated and raise `IRRuntimeError` with the
interpreter's messages. The IR also does
not enforce `GAMA_L_MAX_STEPS`/`GAMA_L_LOOP_LIMIT` (it does honour
`GAMA_L_MAX_CALL_DEPTH`); it is a prototype, not a hardened runtime. Map
literals are JSON values, so text-keyed maps are not indexable. Successful
programs produce byte-for-byte the same output as the interpreter, and runtime
error messages keep the interpreter's text and line/col.

`prove`/`verify` run on VM proof state, never on a `gamal2.Interpreter`
instance. They reuse the reference module-level canonical-JSON, hash-payload
and ledger-scan helpers, so receipts, `sha256`/`id`/`prev` links, HMAC `sig`
(`GAMA_L_PROOF_KEY`, `GAMA_L_REQUIRE_SIG`) and intent/lease fields
(`GAMA_L_PROOF_INTENT`/`GAMA_L_PROOF_LEASE`, `GAMA_L_REQUIRE_INTENT_LEASE`)
match the interpreter exactly. `GAMA_L_PROOF_LEDGER=<file>` loads and validates
the JSONL chain at startup, appends under one exclusive POSIX `flock` with a
tail re-read, and refuses to extend a malformed/forked ledger; without `fcntl`
cross-process atomicity is best-effort. Proof statements inherit the IR's
general limits: `GAMA_L_MAX_STEPS`/`GAMA_L_LOOP_LIMIT` are not enforced.

## Implemented

- `import "file.gm"` with cycle detection (relative to the importing file)
- module namespaces: `import "lib.gm" as math`, then `math.add(2,3)` and `math.pi`; plain imports still flatten (including top-level `let`); aliased modules expose top-level `fn` definitions, `let` constants, `record` types and `enum` declarations, evaluated at import time, and reject side-effect top-level statements (`use` is allowed as a harmless no-op). Enums are namespaced (`geo.Color`, `geo.Color.Red`) and stay visible through the alias even when explicit `export` lists exist.
- explicit module exports: `export fn`, `export let` and `export record` mark the alias-visible surface; other top-level declarations stay private but remain callable inside the module. A module with at least one `export` exposes only exported names through `import ... as`; a module with no `export` keeps the legacy behaviour (all top-level `fn`/`let`/`record` visible). Plain imports always flatten all declarations for compatibility. `export` is only allowed at top level, duplicate export names are rejected, and `export enum` is currently rejected: enums keep the legacy-global surface, so an aliased import always exposes `alias.Enum` regardless of any explicit export list.
- `let` with optional type annotation, `:=` assignment
- `fn` and SPEC `make` with typed parameters, defaults, return type, `effects { ... }`
- generic type parameters on named functions with type erasure: `fn id<T>(x: T) -> T { return x }`, `fn pair<A, B>(a: A, b: B) -> Text { ... }`. Type parameters are valid in parameter, return and `let` annotations and are erased to `Any` by the checker/runtime, so a call accepts any argument type. The checker infers a call result from the arguments when the return type is a type parameter (`id(5): Int`, `id("x"): Text`) and otherwise erases to `Any`; non-type-parameter positions keep normal arity/type checks. Duplicate type parameters and duplicate parameter names are rejected. Generic functions remain ordinary first-class named functions (recursion, defaults, higher-order use, and IR compilation with erasure all work). Limits: no higher-kinded or `&` multi-constraint bounds; union bounds and named-function bounds are supported, and generic record/enum bounds are covered by their declarations. Generic lambdas are covered separately. The IR backend supports named-function-as-value for plain and generic functions (type erasure applies to the latter)
- explicit call type arguments on local generic functions: `id<Int>(5)`, `pair<Int, Text>(...)`; the checker validates type-argument count and names, argument compatibility uses the explicit bindings, and the runtime/IR ignore them (erasure). Imported/namespaced generic calls and nested type arguments are not recognized; formatter round-trips explicit arguments.
- union generic bounds: `fn add<T: Int | Decimal>(a: T, b: T) -> T`, `fn show<T: Text | Bool>(x: T) -> Text`; inferred/explicit arguments match any alternative, unknown/duplicate alternatives rejected, formatter round-trips `T: A | B`.
- generic record/enum bounds: `record Box<T: Text> { value: T }`, `enum Opt<T: Int> { Some(T), None }`; annotation and constructor inference enforce the nominal bound, aliases rename bound refs, formatter/IR erase.
- generic records with type erasure: `record Box<T> { value: T }`, `record Pair<A, B> { first: A, second: B }`, constructors `Box(5)` / `Pair("x", 2)`, annotations `Box<Int>` / `Pair<Text, Int>` (arity checked) and explicit constructor arguments `Box<Int>(5)`. Type parameters are usable in field types and method signatures (`fn get(self: Box<T>) -> T`); they erase to `Any`, while concrete fields keep their type. A `let` annotation binds type parameters for the constructor check (`let b: Box<Int> = Box("x")` is rejected, while erased `Box("x")` is accepted), nested refs (`Box<Box<Int>>`) erase to the nominal base, duplicate type parameters/field names are rejected, namespaced records work (`geo.Box<Int>`, `geo.Box(5)`), and formatter/IR erase explicitly (the IR also compiles method-bearing records). Limits: no bounds, no method-level type parameters (methods use only the record's parameters), and no explicit type arguments on namespaced constructors (`geo.Box<Int>(5)`; annotate the binding instead)
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
- standalone bytecode IR prototype (`gama_ir.py`) for a documented subset: statements/expressions, `if`/`else if`, `while`, `for`, list/map literals and indexing, `break`/`continue`, `fn`, `Ok`/`Err` + `match`, `expr?` Result propagation inside a `Result`/`Any` function, and the core builtins `len`/`text`/`number`/`round`; also records/enums, field access and enum variants; plain and aliased imports resolve through `compile_file` (`m.fn(...)`, `m.pi`, `m.Point(1, 2)`, `let p: m.Point`, `m.Color.Red`, payload constructors `m.Option.Some(5)` and enum matches with payload bindings work); record copy/`with` updates (`p with { x = 3 }`) compile to a new same-type value (shallow, declaration order) and in-place field assignment (`p.x := 3`, nested `l.a.x := 5`, namespaced `geo.origin.x := 7`) mutates the receiver in place with interpreter-matching value evaluation order and runtime errors; generic named functions compile like ordinary functions (type parameters erased); pipe expressions (`filter`/`select`/`sum`/`limit`/`count`/`map`) compile to a single `PIPE` instruction that preserves the interpreter's evaluation order, row scoping and error messages; generic enums compile with type erasure (annotations and payload type arguments are not statically enforced by the IR, but enum identity, variants and payload arity are preserved); record methods compile with a per-nominal method table (receiver bound to `self`, defaults, method-to-method calls, recursion, nested receivers `o.inner.m()` and namespaced `geo.Point` methods) and dispatch only after named functions/enum constructors, matching the interpreter; first-class functions/lambdas, named function values and calls through values compile with closure capture; opt-in via `gamal2 ir run|compile` (see above), the interpreter and default `run` backend are unchanged
- enums: nullary `enum Color { Red, Green, Blue }` and payload `enum Option { Some(Int, Text), None }`; values are `Enum.Variant` (nullary) or `Variant(args...)` / `Enum.Variant(args...)` (one or more payloads), annotation `let x: Option = Some(5)`, nominal equality (cross-enum `==` is a type error), and `match` arms `Enum.Variant`, `Variant`, `Variant(x)`, `Enum.Variant(x)`, `_`. Payload printing is deterministic: `Enum.Variant(<field-value>)` where the payload is rendered like a record field value (text JSON-quoted, decimals plain fixed form, `null` for null) and nullary variants print `Enum.Variant`. Wrong payload type/arity, unknown or ambiguous constructors, and nullary/payload pattern mismatches raise `GamaTypeError`. Generic enums (`enum Option<T> { Some(T), None }`) are supported: type parameters and nested type refs are valid payload types and annotations (`Option<Int>`, `Option<Option<Int>>`) with recursive arity checking; type parameters erase to `Any` while concrete payloads keep their checks, and namespaced generic enums (`geo.Option<Int>`, `geo.Option.Some(5)`) work. Limits: a bare constructor must be unique across enums (otherwise qualify `Enum.Variant(...)`); nullary variants cannot be called with `()`; payload variants cannot be used as a bare value; enums remain top-level and distinct from records/functions
- nominal record (struct) types: `record Point { x: Int, y: Int }`, positional construction `Point(1, 2)`, named construction `Point { x: 3, y: 4 }` (local record names only), typed fields, dotted access `p.x` (including nested `l.a.x`), in-place field assignment `p.x := 3` / `l.a.x := 5`, copy/`with` updates `p with { x = 3, y = 4 }`, `let p: Point = ...`, and nominal/value equality. Constructors enforce arity, missing/extra/duplicate fields and field types (`GamaTypeError`); unknown field access is rejected statically when the base type is a record. Assignment resolves the longest dotted variable prefix as the receiver and mutates the record in place (aliases observe the update; namespaced constants such as `geo.origin.x := 7` work). `p with { ... }` type-checks the receiver as a record, rejects unknown/duplicate fields and wrong value types, and returns a new record; the receiver and its aliases are untouched. Unchanged nested record values are shallow-copied (shared), so mutating one through the copy is visible through the receiver, while a replaced field is independent. Wrong field/type, a non-record receiver and an unknown base variable are `GamaTypeError`. Printing is deterministic: declaration-order fields, `Name { x: 1, y: "text" }`, decimals with their plain fixed form, and text JSON-quoted. Records are exported through aliased imports (`geo.Point`) and their type names are namespaced; positional construction `geo.Point(1, 2)` and `with` updates such as `geo.origin with { x = 1 }` work, while named construction with a dotted name (`geo.Point { ... }`) is not accepted by the parser. Gaps: no overloaded/inherited/generic methods; static functions are declared as `fn Point.origin(...)`, constructor calls are not first-class values, `record` is contextual (not a reserved word), named construction requires a local declaration, the IR backend now supports records/enums for the documented subset (copy/`with` is supported with the same shallow semantics, and in-place field assignment mutates the receiver with interpreter parity), and LSP has record/enum completion, hover, definition, references and rename
- LSP completion/hover for records/enums (fields, variants, declarations) and VS Code grammar/snippets
- reproducible benchmark suite (`scripts/bench.py`) for interpreter vs IR and core operations
- record methods: `record Point { x: Int; y: Int; fn sum(self: Point) -> Int { return self.x + self.y } }`, call `p.sum()`, `self` bound to the receiver, method-to-method calls, defaults, recursion and receiver mutation; per nominal record; methods may use the record's type parameters but cannot declare their own; no overloading; static functions are declared as `fn Point.origin()`; inheritance is supported nominally; the IR compiles these with interpreter parity (including unchecked unknown-method/arity/non-record errors)
- generic lambdas: `fn<T>(x: T) -> T { ... }`, `fn<A, B>(...)`, erasure, higher-order use; explicit type args supported for `let NAME = fn<...>` only
- generic type bounds on named functions: `fn show<T: Text>(x: T) -> Text`, `fn numeric<T: Int>(...)`; one nominal bound per parameter (base type, record or enum), checked at inferred/explicit calls, erased at runtime
- record static functions: `fn Point.origin() -> Point`, `fn Point.of(x: Int) -> Point`, call `Point.origin()`; no receiver/self, namespaced `geo.Point.of`; value methods unchanged
- record inheritance: `record Dog extends Animal { ... }`; inherited fields come first, positional construction takes parent then child fields, methods are inherited with exact-signature overrides, `Child` is a nominal subtype of `Parent` in let/params/returns/fields, and equality stays nominal. Cycles/unknown parents/duplicate fields are rejected; the IR compiles inheritance with interpreter parity (inherited methods, overrides and dynamic dispatch on the actual value). LSP completion/hover/symbols show inherited members and `extends`.
- structural record compatibility (static checks only): a record value `S` is compatible with a target record `T` when `S` declares every `T` field with a compatible type; extra `S` fields are allowed, missing/wrong-type fields are rejected. It applies where nominal compatibility was already used: `let` annotations, function arguments and returns, record field values (positional and named construction, `with`, field assignment), method arguments and generic-constructor checks. Field types recurse (nested records compose) and cyclic record graphs are coinductive. Runtime stays nominal: values keep their record type for printing and equality (`Point3 == Point` remains a static type error), while field access and method calls use the static target type (a `Point3` value held in a `Point` binding runs `Point`'s method). Namespaced records work (`geo.Point3` used as `geo.Point`). Limits: fields only (methods on the value are irrelevant), generic record types are compared after erasure, and there is no coercion or value slicing. The bytecode IR performs no type checking of its own: it accepts programs the checker allows and preserves the same field and static-method dispatch at runtime (so unknown-method/arity errors still surface only at runtime)
- AST-based formatter for the current language (comments and multi-line strings preserved; idempotent; records/enums included; deterministic width wrapping at 88 columns by default, configurable via `fmt --width`, covering expressions, declarations and `fn`/control-flow headers)
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
python3 -m unittest discover -s tests -v   # 2171/2171
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
- full type inference (structural record compatibility is implemented for static checks only); higher-kinded and `&` multi-constraint bounds
- formatter remaining edges: a comment *before* a binary operator has no legal source position (the grammar only breaks after an operator), so the defensive renderer keeps it after the operator; comments in a commented `fn` effects set use source order (uncommented sets stay sorted); indivisible tokens longer than the width still overflow
- VS Code Marketplace signing/publishing (VSIX packaging is reproducible locally)
- publishing a built release by default: the release workflow has secret-gated,
  opt-in PyPI/Marketplace steps, but tag pushes only build and upload artifacts

The old v0.1 transpiler remains in `gama-lang/` for reference. Do not call this
full 2.0 until the remaining conformance tests and connectors are done.
