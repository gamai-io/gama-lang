# GAMA-L

Minimal VS Code package for the GAMA-L 2 language.

- Language id: `gama-l`
- File extension: `.gm`
- Scope name: `source.gama-l`

It provides TextMate syntax highlighting, bracket/comment configuration,
snippets, and a **minimal built-in LSP client** for diagnostics
(no `vscode-languageclient`, no npm dependencies).

## Requirements

- **VS Code 1.80+**
- **Python 3.10+** available on `PATH` (or set `gama-l.pythonPath`)
- **`gamal2.py`** from the repository, e.g. `gama-lang/v2/gamal2.py`

Both `gamal2.py lsp` (language server) and `gamal2.py doctor --json` are used.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `gama-l.pythonPath` | `python3` | Interpreter used to start the server |
| `gama-l.serverScript` | `${workspaceFolder}/gama-lang/v2/gamal2.py` | Path to `gamal2.py`; `${workspaceFolder}` is expanded to the first workspace folder |

Changing either setting restarts the language server for that window.

## Run it (Extension Development Host)

1. Open VS Code.
2. `File > Open Folder...` and select this `vscode/` folder.
3. Press **F5** — a new "Extension Development Host" window opens with the
   extension loaded.
4. Open any `.gm` file: highlighting works and diagnostics from
   `gamal2.py lsp` appear in the Problems panel.

The F5 host uses the folder you opened, so `${workspaceFolder}` resolves to
this `vscode/` folder. Point `gama-l.serverScript` at the real `gamal2.py`
(e.g. `../gamal2.py` when the repo is the opened folder, or an absolute path)
if the default does not resolve in your layout.

`GAMA-L: Run Doctor` (Command Palette) runs `python3 <serverScript> doctor --json`
and reports `ok` / the failed checks.

To install permanently, copy this folder into your VS Code extensions
directory, or package it with `vsce` (not included here).

## Notes

- No `npm install` is needed; `extension.js` uses only Node built-ins
  (`child_process`, `fs`, `path`) plus the VS Code API.
- Diagnostics come from the server via `textDocument/publishDiagnostics`
  (severity 1 → Error, 2 → Warning, 3 → Information).
- If the server script is missing or the process dies, the extension shows a
  single warning and keeps running; it never takes down the extension host.

## What is highlighted

- Comments starting with `#`
- Single- and double-quoted strings with escapes
- Integers and decimals
- Keywords, type names, and builtin functions
- Pipe stages (`filter`, `select`, `sum`, `limit`, `count`, `map`)
- Operators and punctuation
- Function names after `fn`, bindings after `let`, and `prove` targets

## License

MIT — see `LICENSE`.
