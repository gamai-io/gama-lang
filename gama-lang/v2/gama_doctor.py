"""Health checks for the GAMA-L 2 core.

Runs a fixed, deterministic set of checks (no network, no unittest suite):
interpreter version, module imports, every ``examples/*.gm`` parses/type-checks
and executes, the formatter is idempotent, the LSP module exposes its entry
points, the SQL write guard behaves, and the package manager can install and
use a local path dependency.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
IMPORT_MODULES = ("gamal2", "gama_lsp", "gama_fmt", "gama_cli", "gama_mail", "gama_registry", "gama_registry_server", "gama_ai", "release")
LSP_API = ("main", "completion", "hover", "definition", "references", "document_symbols", "semantic_tokens", "code_actions", "formatting", "rename")


def _examples_dir(root: pathlib.Path) -> pathlib.Path:
    for candidate in (root / "examples",
                      root / "gama-lang" / "v2" / "examples"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("examples directory not found under %s" % root)


def _examples(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(_examples_dir(root).glob("*.gm"))


def _with_root_on_path(root: pathlib.Path, fn):
    entry = str(root)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        importlib.invalidate_caches()
        return fn()
    finally:
        if added:
            try:
                sys.path.remove(entry)
            except ValueError:
                pass


def _check_python() -> str:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python >= 3.10 required, found %d.%d" % sys.version_info[:2])
    return "%d.%d.%d (>=3.10)" % sys.version_info[:3]


def _check_imports(root: pathlib.Path) -> str:
    def run():
        for name in IMPORT_MODULES:
            importlib.import_module(name)
        return ", ".join(IMPORT_MODULES)
    return _with_root_on_path(root, run)


def _check_examples(root: pathlib.Path) -> str:
    files = _examples(root)
    if not files:
        raise RuntimeError("no examples found")
    import gamal2 as G
    failures = []
    for path in files:
        try:
            G.check_file(str(path))
        except Exception as ex:
            failures.append("%s: %s: %s" % (path.name, type(ex).__name__, ex))
    if failures:
        raise AssertionError("; ".join(failures))
    return "%d/%d examples parse and type-check" % (len(files), len(files))


def _check_run(root: pathlib.Path) -> str:
    files = _examples(root)
    if not files:
        raise RuntimeError("no examples found")
    import gamal2 as G
    failures = []
    ran = 0
    for path in files:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                G.run_file(str(path))
            ran += 1
        except Exception as ex:
            failures.append("%s: %s: %s" % (path.name, type(ex).__name__, ex))
    if failures:
        raise AssertionError("; ".join(failures))
    if not any(p.name == "with_import.gm" for p in files):
        raise AssertionError("with_import.gm missing from examples")
    return "%d/%d examples executed (with_import.gm ok)" % (ran, ran)


def _check_formatter(root: pathlib.Path) -> str:
    files = _examples(root)
    import gamal2 as G
    failures = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        once = G.fmt_source(src)
        twice = G.fmt_source(once)
        if once != twice:
            failures.append(path.name)
    if failures:
        raise AssertionError("not idempotent: %s" % ", ".join(failures))
    return "idempotent on %d/%d examples" % (len(files), len(files))


def _check_lsp(root: pathlib.Path) -> str:
    mod = _with_root_on_path(root, lambda: importlib.import_module("gama_lsp"))
    missing = [name for name in LSP_API if not callable(getattr(mod, name, None))]
    if missing:
        raise AssertionError("missing entry points: %s" % ", ".join(missing))
    return ", ".join(LSP_API)


def _check_sql(root: pathlib.Path) -> str:
    import gamal2 as G
    with tempfile.TemporaryDirectory() as td:
        db = pathlib.Path(td) / "doctor.db"
        con = sqlite3.connect(str(db))
        try:
            con.execute("create table t(a int)")
            con.commit()
        finally:
            con.close()
        old_db = os.environ.get("GAMA_L_DB")
        old_allow = os.environ.get("GAMA_L_ALLOW_WRITE")
        os.environ["GAMA_L_DB"] = str(db)
        os.environ.pop("GAMA_L_ALLOW_WRITE", None)
        try:
            try:
                G.sql_write("insert into t(a) values (1)")
            except G.GamaError as ex:
                if "disabled" not in str(ex).lower():
                    raise
            else:
                raise AssertionError("write allowed without GAMA_L_ALLOW_WRITE")
            os.environ["GAMA_L_ALLOW_WRITE"] = "1"
            if not G.sql_write("insert into t(a) values (7)"):
                raise AssertionError("sql_write returned False")
            con = sqlite3.connect(str(db))
            try:
                rows = [r[0] for r in con.execute("select a from t order by a")]
            finally:
                con.close()
            if rows != [7]:
                raise AssertionError("unexpected rows after insert: %r" % (rows,))
        finally:
            for key, old in (("GAMA_L_DB", old_db),
                             ("GAMA_L_ALLOW_WRITE", old_allow)):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
    return "denied without flag, insert with GAMA_L_ALLOW_WRITE=1, row verified"


def _check_package(root: pathlib.Path) -> str:
    import gamal2 as G
    with tempfile.TemporaryDirectory() as td:
        app = pathlib.Path(td) / "app"
        dep = app / "deps" / "math"
        dep.mkdir(parents=True)
        (dep / "lib.gm").write_text(
            "fn add(a: Int, b: Int) -> Int effects {} { return a + b }\n",
            encoding="utf-8")
        (app / "main.gm").write_text(
            'import "math/lib.gm"\nprint add(20,22)\n', encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = G._pkg_init(app)
        if rc != 0:
            raise AssertionError("pkg init failed: %s" % buf.getvalue().strip())
        (app / "gamal.toml").write_text(
            '[package]\nname = "app"\nversion = "0.1.0"\n\n'
            '[dependencies]\nmath = { path = "deps/math" }\n',
            encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = G._pkg_install(app)
        if rc != 0:
            raise AssertionError("pkg install failed: %s" % buf.getvalue().strip())
        if not (app / ".gamal" / "modules" / "math" / "lib.gm").is_file():
            raise AssertionError("dependency not copied into .gamal/modules")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            it = G.run_file(str(app / "main.gm"))
        if it.out != ["42"]:
            raise AssertionError("import/run produced %r" % (it.out,))
    return "path dependency installed, import/run -> 42"


def _make(name: str, fn) -> dict:
    try:
        detail = fn()
        return {"name": name, "ok": True, "detail": str(detail)}
    except Exception as ex:
        return {"name": name, "ok": False,
                "detail": "%s: %s" % (type(ex).__name__, ex)}


def collect(root=None) -> dict:
    base = pathlib.Path(root).resolve() if root else ROOT
    checks = [
        _make("python", _check_python),
        _make("imports", lambda: _check_imports(base)),
        _make("examples", lambda: _check_examples(base)),
        _make("run", lambda: _check_run(base)),
        _make("formatter", lambda: _check_formatter(base)),
        _make("lsp", lambda: _check_lsp(base)),
        _make("sql", lambda: _check_sql(base)),
        _make("package", lambda: _check_package(base)),
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "python": "%d.%d.%d" % sys.version_info[:3],
        "root": str(base),
        "checks": checks,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gama_doctor",
                                     description="Check GAMA-L 2 core health.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--root", default=None, help="project directory (default: module dir)")
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    result = collect(args.root)
    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print("GAMA-L doctor")
        print("root: %s" % result["root"])
        print("python: %s" % result["python"])
        for check in result["checks"]:
            mark = "ok" if check["ok"] else "FAIL"
            print("[%s] %s - %s" % (mark, check["name"], check["detail"]))
        passed = sum(1 for c in result["checks"] if c["ok"])
        print("doctor: %s (%d/%d)" % ("OK" if result["ok"] else "PROBLEMS",
                                      passed, len(result["checks"])))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
