# GAMA-L

Minimal VS Code **syntax highlighting** package for the GAMA-L 2 language.

- Language id: `gama-l`
- File extension: `.gm`
- Scope name: `source.gama-l`

This package is **syntax-only**. It provides TextMate grammar highlighting,
bracket/comment configuration, and snippets. It does **not** include an LSP,
completion, diagnostics, or code execution.

## Open it in VS Code

1. Open VS Code.
2. `File > Open Folder...` and select this `vscode/` folder.
3. Press `F5` to launch an Extension Development Host.
4. Open any `.gm` file — it will be highlighted as GAMA-L.

To install permanently, copy this folder into your VS Code extensions
directory, or package it with `vsce` (not included here).

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
