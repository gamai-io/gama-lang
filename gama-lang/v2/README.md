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
There is no central registry yet, but any git URL (including private remotes)
works with the host's git credentials.

## Editor support (LSP, stdio)

```bash
python3 gamal2.py lsp
```

JSON-RPC language server: `initialize`, `didOpen/didChange/didSave`
diagnostics, completion, hover, `shutdown`, `exit`. Positions are converted
between LSP UTF-16 and Python indexes. A syntax-only VS Code package is in
`vscode/` (TextMate grammar + snippets); it does not start the LSP itself.

## Implemented

- `import "file.gm"` with cycle detection (relative to the importing file)
- `let` with optional type annotation, `:=` assignment
- `fn` with typed parameters, return type, `effects { ... }`
- `if` / `else` blocks, `return`
- literals: text, int, decimal, bool, null, list, map
- arithmetic/comparison/boolean operators
- pipes: `filter`, `select`, `sum`, `limit`, `count`, `map`
- `ask` (offline mock, or OpenAI-compatible `GAMA_L_ASK_URL`), `sql` (demo table, or read-only SQLite via `GAMA_L_DB`), `remember/recall`
- `wait_user` (non-interactive returns `Denied`; falsy)
- `prove` (hash-chained receipt, optional HMAC via `GAMA_L_PROOF_KEY`) and `verify` (tamper detection)
- static type errors and static effect violations
- CLI `run`, `check`, `fmt`, `test`, `pkg`, `lsp`

## Tests

```bash
python3 -m unittest discover -s tests -v   # 48/48
```

Conformance coverage: arithmetic/pipes (20 cases), type errors, effect
violations (including `sql.write`/`mail` stubs), HITL returns `Denied`,
proof/tamper, duplicate proof keys, optional HMAC proof signatures (`GAMA_L_PROOF_KEY`; `GAMA_L_REQUIRE_SIG=1` requires them),
imports/cycles/diamonds, multi-table SQLite schema checks, git/path package
dependencies, LSP completion/hover/didChange, offline `ask` = `Unavailable`,
10 examples, and deterministic `fmt`.

## Not implemented yet

- central package registry (git and local dependencies work)
- full type inference and generics
- real SQL write/AI/mail connectors (read-only SQLite and optional `ask` work)
- full AST formatter (current `fmt` indents and preserves comments)
- LSP definition/references/code actions; VS Code package is syntax-only
- distribution build

The old v0.1 transpiler remains in `gama-lang/` for reference. Do not call this
full 2.0 until the remaining conformance tests and connectors are done.
