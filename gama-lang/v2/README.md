# GAMA-L 2.0 core (executable)

This is a real parser + static checker + tree-walking interpreter, not the old
transpiler. It is a **core subset** of `SPEC_2.0.md`; the rest is still roadmap.

## Run

```bash
python3 gamal2.py run examples/invoice.gm
python3 gamal2.py check examples/invoice.gm
python3 gamal2.py test
```

## Package manager (local/vendored)

```bash
python3 gamal2.py pkg init .
python3 gamal2.py pkg install .
python3 gamal2.py pkg list .
```

`gamal.toml` supports local path dependencies:

```toml
[dependencies]
math = { path = "../math" }
```

Installed modules go to `.gamal/modules/<name>` and are resolved by
`import "name/file.gm"`. There is no remote registry yet.

## Implemented

- `import "file.gm"` with cycle detection (relative to the importing file)
- `let` with optional type annotation, `:=` assignment
- `fn` with typed parameters, return type, `effects { ... }`
- `if` / `else` blocks, `return`
- literals: text, int, decimal, bool, null, list, map
- arithmetic/comparison/boolean operators
- pipes: `filter`, `select`, `sum`, `limit`, `count`, `map`
- `ask` (offline mock, or OpenAI-compatible `GAMA_L_ASK_URL`), `sql` (demo table, or read-only SQLite via `GAMA_L_DB`), `remember/recall`
- `wait_user` (non-interactive fails closed)
- `prove` (hash-chained receipt) and `verify` (tamper detection)
- static type errors and static effect violations
- CLI `run`, `check`, `fmt`, `test`, `pkg`

## Tests

```bash
python3 -m unittest discover -s tests -v   # 34/34
```

Conformance coverage: arithmetic/pipes (20 cases), type errors, effect
violations (including `sql.write`/`mail` stubs), HITL returns `Denied`,
proof/tamper, duplicate proof keys, optional HMAC proof signatures,
imports/cycles/diamonds, table column checks, offline `ask` = `Unavailable`,
10 examples, and deterministic `fmt`.

## Not implemented yet

- modules/imports and package manager
- full type inference and generics
- real SQL/AI/mail connectors (the core uses demo/mock adapters)
- full AST formatter (current `fmt` normalizes whitespace only), package manager, LSP, VS Code syntax, distribution build

The old v0.1 transpiler remains in `gama-lang/` for reference. Do not call this
full 2.0 until the remaining conformance tests and connectors are done.
