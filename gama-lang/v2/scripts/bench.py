#!/usr/bin/env python3
"""Reproducible micro-benchmarks for the GAMA-L core (stdlib only).

What is measured
----------------
* ``parse``  -- lexer + parser only (``gamal2.parse``).
* ``check``  -- static checker on an already parsed AST (excludes parse).
* ``run``    -- tree-walking interpreter on an already checked AST
                (excludes parse and check).
* ``ir``     -- opt-in bytecode backend (``gama_ir``): compile time and run
                time are reported separately; ``speedup`` compares only the
                interpreter ``run`` median against the IR ``run`` median.
* startup / import, process peak RSS (``resource`` when available) and
  formatter time are reported as separate sections.

The programs are small, deterministic and offline.  IR-unsupported programs
are reported as unsupported with the concrete reason; they are never faked or
silently skipped.

Usage
-----
    python3 scripts/bench.py --runs 2 --json
    python3 scripts/bench.py --runs 5 --seed 7
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import statistics
import subprocess
import sys
import tempfile
import time

V2 = pathlib.Path(__file__).resolve().parents[1]
if str(V2) not in sys.path:
    sys.path.insert(0, str(V2))

import gamal2 as g2  # noqa: E402
import gama_ir  # noqa: E402

try:  # pragma: no cover - platform dependent
    import resource
except ImportError:  # pragma: no cover
    resource = None

SCHEMA_VERSION = 1

ARITH_LOOP = """\
let total = 0
for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] {
  total := total + i * 2
}
print total
"""

WHILE_LOOP = """\
let total = 0
let i = 0
while i < 100 {
  total := total + i
  i := i + 1
}
print total
"""

RECURSION = """\
fn fib(n: Int) -> Int effects {} {
  if n < 2 { return n }
  return fib(n - 1) + fib(n - 2)
}
print fib(12)
"""

NAMED_FUNCTION = """\
fn add(a: Int, b: Int) -> Int effects {} {
  return a + b
}
print add(20, 22)
"""

LIST_OPS = """\
let xs = [1, 2, 3, 4, 5]
print xs[2]
print xs | count
"""

SQL_MAP = """\
let rows = sql("select konto, naziv from customers")
let names = rows | map naziv
print names | count
"""

RECORDS = """\
record Point { x: Int, y: Int }
let p = Point { x: 3, y: 4 }
print p.x + p.y
"""

ENUMS = """\
enum Color { Red, Green, Blue }
let c = Color.Green
print c
"""

PROOF = """\
let total = 117.00
prove invoice = { invoice: "MOCK-2026-001", total: total }
verify invoice
print total
"""

NAMESPACE_LIB = """\
fn add(a: Int, b: Int) -> Int effects {} {
  return a + b
}
let lib_value = 41
"""

NAMESPACE_MAIN = """\
import "lib.gm" as math
print math.add(math.lib_value, 1)
"""

FORMAT_SAMPLE = """\
# benchmark sample
fn add(a: Int, b: Int) -> Int effects {} {
  return a + b
}
record Point { x: Int, y: Int }
enum Color { Red, Green, Blue }
let p = Point { x: 1, y: 2 }
let c = Color.Red
if p.x > 0 {
  print add(p.x, 2)
}
"""


def _programs() -> list[dict]:
    """Benchmark program definitions (stable order; output is sorted by name)."""
    return [
        {"name": "arithmetic_loop", "category": "loop", "src": ARITH_LOOP},
        {"name": "while_loop", "category": "loop", "src": WHILE_LOOP},
        {"name": "recursion_fib", "category": "function", "src": RECURSION},
        {"name": "named_function", "category": "function", "src": NAMED_FUNCTION},
        {"name": "list_ops", "category": "collection", "src": LIST_OPS},
        {"name": "sql_map", "category": "collection", "src": SQL_MAP},
        {"name": "records", "category": "record", "src": RECORDS},
        {"name": "enums", "category": "enum", "src": ENUMS},
        {"name": "proof", "category": "proof", "src": PROOF},
        {
            "name": "namespace",
            "category": "namespace",
            "src": NAMESPACE_MAIN,
            "namespace_lib": NAMESPACE_LIB,
        },
    ]


def _stats(samples: list[float]) -> dict:
    if not samples:
        raise ValueError("no samples")
    return {
        "median_ms": round(float(statistics.median(samples)), 4),
        "min_ms": round(float(min(samples)), 4),
        "max_ms": round(float(max(samples)), 4),
        "samples": [round(float(x), 4) for x in samples],
    }


def _measure(fn, runs: int, warmup: int) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return _stats(samples)


def _prepare_units(programs: list[dict], root: pathlib.Path) -> list[dict]:
    """Create parse/check/run callables and a parsed AST for each program."""
    units = []
    for spec in programs:
        if "namespace_lib" in spec:
            ns = root / "namespace"
            ns.mkdir(parents=True, exist_ok=True)
            (ns / "lib.gm").write_text(spec["namespace_lib"], encoding="utf-8")
            main = ns / "main.gm"
            main.write_text(spec["src"], encoding="utf-8")
            path = str(main)

            def parse_fn(path=path):
                # load_file resolves imports and flattens the module.
                return g2.load_file(path)

        else:
            src = spec["src"]

            def parse_fn(src=src):
                return g2.parse(src)

        ast = parse_fn()

        def check_fn(ast=ast):
            return g2.Checker().program(ast)

        def run_fn(ast=ast):
            return g2.Interpreter().run(ast).out

        units.append(
            {
                "spec": spec,
                "ast": ast,
                "parse_fn": parse_fn,
                "check_fn": check_fn,
                "run_fn": run_fn,
                "parse_includes_import": "namespace_lib" in spec,
            }
        )
    return units


def _ir_result(ast, interp_out: list[str], runs: int, warmup: int) -> dict:
    """Measure the opt-in IR backend or report why it does not apply."""
    try:
        program = gama_ir.compile_program(ast)
        ir_out = gama_ir.run_program(program).out
    except gama_ir.IRUnsupported as exc:
        # An unsupported construct is a documented limitation, not a failure.
        return {"supported": False, "reason": f"IRUnsupported: {exc}"}

    compile_stats = _measure(lambda: gama_ir.compile_program(ast), runs, warmup)
    run_stats = _measure(lambda: gama_ir.run_program(program).out, runs, warmup)
    return {
        "supported": True,
        "reason": None,
        "compile_ms": compile_stats,
        "run_ms": run_stats,
        "outputs_match": list(ir_out) == list(interp_out),
        "output": list(ir_out),
    }


def _timed_child(code: str) -> float | None:
    """Run ``code`` in a fresh interpreter and return its float stdout, if any."""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(V2),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(V2)},
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _startup(runs: int, warmup: int) -> dict:
    import_code = (
        "import time\n"
        "t = time.perf_counter()\n"
        "import gamal2\n"
        "print((time.perf_counter() - t) * 1000.0)\n"
    )
    ir_code = (
        "import time\n"
        "t = time.perf_counter()\n"
        "import gama_ir\n"
        "print((time.perf_counter() - t) * 1000.0)\n"
    )
    rss_code = (
        "import resource\n"
        "import gamal2, gama_ir\n"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)\n"
    )

    for _ in range(warmup):
        _timed_child("pass")

    import_samples = []
    ir_samples = []
    process_samples = []
    for _ in range(runs):
        import_samples.append(_timed_child(import_code))
        ir_samples.append(_timed_child(ir_code))
        start = time.perf_counter()
        _timed_child("pass")
        process_samples.append((time.perf_counter() - start) * 1000.0)

    import_samples = [x for x in import_samples if x is not None]
    ir_samples = [x for x in ir_samples if x is not None]
    rss = _timed_child(rss_code)

    result = {
        "process_startup_ms": _stats(process_samples),
        "import_gamal2_ms": _stats(import_samples) if import_samples else None,
        "import_gamal2_ir_ms": _stats(ir_samples) if ir_samples else None,
        "peak_rss_kb": int(rss) if rss is not None else None,
        "rss_note": (
            "resource.ru_maxrss for a fresh child after importing gamal2+gama_ir; "
            "it is a process high-water mark, not per-program memory."
        ),
    }
    return result


def _formatter(runs: int, warmup: int) -> dict:
    return _measure(lambda: g2.fmt_source(FORMAT_SAMPLE), runs, warmup)


def run_benchmark(
    runs: int = 3,
    warmup: int = 1,
    seed: int = 0,
    only: list[str] | None = None,
    include_startup: bool = True,
) -> dict:
    if runs < 1:
        raise ValueError("runs must be >= 1")
    if warmup < 0:
        raise ValueError("warmup must be >= 0")

    programs = _programs()
    if only:
        wanted = {name.strip() for name in only if name.strip()}
        programs = [p for p in programs if p["name"] in wanted]
        missing = wanted - {p["name"] for p in programs}
        if missing:
            raise ValueError("unknown program(s): " + ", ".join(sorted(missing)))
    if not programs:
        raise ValueError("no programs selected")

    order = [p["name"] for p in programs]
    random.Random(seed).shuffle(order)
    by_name = {p["name"]: p for p in programs}
    shuffled = [by_name[name] for name in order]

    with tempfile.TemporaryDirectory(prefix="gama-l-bench-") as td:
        units = _prepare_units(shuffled, pathlib.Path(td))
        results = {}
        for unit in units:
            spec = unit["spec"]
            parse_stats = _measure(unit["parse_fn"], runs, warmup)
            check_stats = _measure(unit["check_fn"], runs, warmup)
            run_stats = _measure(unit["run_fn"], runs, warmup)
            interp_out = unit["run_fn"]()
            ir = _ir_result(unit["ast"], interp_out, runs, warmup)
            if ir["supported"]:
                ir["speedup"] = round(
                    run_stats["median_ms"] / ir["run_ms"]["median_ms"], 4
                ) if ir["run_ms"]["median_ms"] else None
            results[spec["name"]] = {
                "name": spec["name"],
                "category": spec["category"],
                "lines": len(spec["src"].splitlines()),
                "parse_ms": parse_stats,
                "check_ms": check_stats,
                "run_ms": run_stats,
                "output_lines": len(interp_out),
                "parse_includes_import": unit["parse_includes_import"],
                "ir": ir,
            }

    payload = {
        "schema": SCHEMA_VERSION,
        "tool": "gama-l-bench",
        "python": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        "runs": runs,
        "warmup": warmup,
        "seed": seed,
        "programs": [results[name] for name in sorted(results)],
        "notes": [
            "parse_ms = lexer + parser only; check_ms excludes parse; run_ms excludes parse and check.",
            "IR compile and run are measured separately; speedup = interpreter run median / IR run median.",
            "Unsupported IR programs are reported with a reason, not skipped or faked.",
            "Namespace parse uses load_file, so it includes import resolution/flattening.",
            "Warmup iterations run before timed samples on every measured callable.",
            "Peak RSS is process-wide (resource.ru_maxrss), not per program.",
            "Micro-benchmarks on one machine; not a general language performance claim.",
            "No network is used.",
        ],
    }
    if include_startup:
        payload["startup"] = _startup(runs=runs, warmup=warmup)
        payload["formatter"] = {"format_ms": _formatter(runs=runs, warmup=warmup)}
    return payload


def _human(data: dict) -> str:
    lines = [
        "GAMA-L benchmark (runs=%d, warmup=%d, seed=%d)" % (
            data["runs"], data["warmup"], data["seed"]
        ),
        "python %s  platform %s" % (data["python"], data["platform"]),
        "",
        "%-17s %-12s %5s %11s %11s %11s %11s %9s" % (
            "program", "category", "lines", "parse_ms", "check_ms", "run_ms",
            "ir_run_ms", "speedup",
        ),
    ]
    for p in data["programs"]:
        ir = p["ir"]
        ir_run = ("%.4f" % ir["run_ms"]["median_ms"]) if ir.get("supported") else "-"
        speedup = ("%.2fx" % ir["speedup"]) if ir.get("speedup") else "-"
        lines.append(
            "%-17s %-12s %5d %11.4f %11.4f %11.4f %11s %9s" % (
                p["name"], p["category"], p["lines"],
                p["parse_ms"]["median_ms"], p["check_ms"]["median_ms"],
                p["run_ms"]["median_ms"], ir_run, speedup,
            )
        )

    unsupported = [p for p in data["programs"] if not p["ir"].get("supported")]
    if unsupported:
        lines.append("")
        lines.append("IR unsupported (reported, not benchmarked):")
        for p in unsupported:
            lines.append("  %-17s %s" % (p["name"], p["ir"].get("reason")))

    startup = data.get("startup")
    if startup:
        lines.append("")
        lines.append("Startup / memory:")
        lines.append(
            "  process startup        %.4f ms (median)"
            % startup["process_startup_ms"]["median_ms"]
        )
        if startup.get("import_gamal2_ms"):
            lines.append(
                "  import gamal2          %.4f ms (median)"
                % startup["import_gamal2_ms"]["median_ms"]
            )
        if startup.get("import_gamal2_ir_ms"):
            lines.append(
                "  import gamal2+gama_ir  %.4f ms (median)"
                % startup["import_gamal2_ir_ms"]["median_ms"]
            )
        if startup.get("peak_rss_kb") is not None:
            lines.append("  peak RSS               %d KiB (process high-water)" % startup["peak_rss_kb"])
        else:
            lines.append("  peak RSS               unavailable (`resource` not supported)")

    fmt = data.get("formatter")
    if fmt:
        lines.append("")
        lines.append("Formatter:")
        lines.append("  format_source          %.4f ms (median)" % fmt["format_ms"]["median_ms"])

    lines.append("")
    lines.append("Notes:")
    for note in data["notes"]:
        lines.append("  - " + note)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description="Reproducible GAMA-L micro-benchmarks (stdlib only, offline).",
    )
    parser.add_argument("--runs", type=int, default=3, help="timed samples per measurement (default 3)")
    parser.add_argument("--warmup", type=int, default=1, help="untimed warmup iterations (default 1)")
    parser.add_argument("--seed", type=int, default=0, help="seed for deterministic program-order shuffle")
    parser.add_argument("--json", action="store_true", help="emit schema-stable JSON instead of a table")
    parser.add_argument("--only", default=None, help="comma-separated program names (debugging)")
    parser.add_argument("--no-startup", action="store_true", help="skip subprocess startup/memory/formatter")
    args = parser.parse_args(argv)

    try:
        data = run_benchmark(
            runs=args.runs,
            warmup=args.warmup,
            seed=args.seed,
            only=args.only.split(",") if args.only else None,
            include_startup=not args.no_startup,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        sys.stdout.write(_human(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
