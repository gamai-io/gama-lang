# -*- coding: utf-8 -*-
"""Tests for the reproducible benchmark suite (scripts/bench.py)."""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
BENCH_PATH = V2 / "scripts" / "bench.py"

spec = importlib.util.spec_from_file_location("gama_bench", BENCH_PATH)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def _check_stats(testcase, stats):
    testcase.assertEqual(set(stats), {"median_ms", "min_ms", "max_ms", "samples"})
    testcase.assertGreaterEqual(stats["min_ms"], 0.0)
    testcase.assertLessEqual(stats["min_ms"], stats["median_ms"])
    testcase.assertLessEqual(stats["median_ms"], stats["max_ms"])
    testcase.assertTrue(stats["samples"])


class BenchmarkTests(unittest.TestCase):
    def test_json_schema_and_ir_reporting(self):
        data = bench.run_benchmark(
            runs=1, warmup=0, seed=0,
            only=["arithmetic_loop", "records", "proof"], include_startup=False,
        )
        self.assertEqual(data["schema"], bench.SCHEMA_VERSION)
        self.assertEqual(data["tool"], "gama-l-bench")
        self.assertEqual(set(data), {
            "schema", "tool", "python", "platform", "runs", "warmup",
            "seed", "programs", "notes",
        })
        self.assertEqual({p["name"] for p in data["programs"]},
                         {"arithmetic_loop", "records", "proof"})
        for p in data["programs"]:
            self.assertEqual(set(p), {
                "name", "category", "lines", "parse_ms", "check_ms", "run_ms",
                "output_lines", "parse_includes_import", "ir",
            })
            _check_stats(self, p["parse_ms"])
            _check_stats(self, p["check_ms"])
            _check_stats(self, p["run_ms"])
            self.assertGreaterEqual(p["lines"], 1)
            self.assertGreaterEqual(p["output_lines"], 1)

        arith = next(p for p in data["programs"] if p["name"] == "arithmetic_loop")
        self.assertTrue(arith["ir"]["supported"])
        self.assertIsNone(arith["ir"]["reason"])
        self.assertTrue(arith["ir"]["outputs_match"])
        _check_stats(self, arith["ir"]["compile_ms"])
        _check_stats(self, arith["ir"]["run_ms"])
        self.assertIsInstance(arith["ir"]["speedup"], float)

        records = next(p for p in data["programs"] if p["name"] == "records")
        self.assertTrue(records["ir"]["supported"])
        self.assertTrue(records["ir"]["outputs_match"])

        proof = next(p for p in data["programs"] if p["name"] == "proof")
        # IR now supports prove/verify with interpreter parity.
        self.assertTrue(proof["ir"]["supported"])
        self.assertTrue(proof["ir"]["outputs_match"])
        _check_stats(self, proof["ir"]["compile_ms"])
        _check_stats(self, proof["ir"]["run_ms"])
        self.assertIsInstance(proof["ir"]["speedup"], float)

    def test_deterministic_structure(self):
        kwargs = dict(runs=1, warmup=0, seed=3,
                      only=["arithmetic_loop", "enums"], include_startup=False)
        first = bench.run_benchmark(**kwargs)
        second = bench.run_benchmark(**kwargs)

        def stable(data):
            return [
                {
                    "name": p["name"],
                    "category": p["category"],
                    "lines": p["lines"],
                    "output_lines": p["output_lines"],
                    "parse_includes_import": p["parse_includes_import"],
                    "ir_supported": p["ir"]["supported"],
                    "ir_reason": p["ir"].get("reason"),
                    "ir_outputs_match": p["ir"].get("outputs_match"),
                    "ir_output": p["ir"].get("output"),
                }
                for p in data["programs"]
            ]

        self.assertEqual(stable(first), stable(second))
        self.assertEqual(first["notes"], second["notes"])
        self.assertEqual(first["programs"][0]["ir"].get("output"),
                         second["programs"][0]["ir"].get("output"))

    def test_cli_json_and_human(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main([
                "--runs", "1", "--warmup", "0",
                "--only", "named_function", "--json",
            ])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["tool"], "gama-l-bench")
        self.assertEqual(data["programs"][0]["name"], "named_function")
        self.assertIn("startup", data)
        self.assertIn("formatter", data)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bench.main([
                "--runs", "1", "--warmup", "0", "--only", "sql_map",
                "--no-startup",
            ])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("GAMA-L benchmark", text)
        self.assertIn("IR unsupported", text)
        self.assertIn("IRUnsupported:", text)
        self.assertIn("sql_map", text)


if __name__ == "__main__":
    unittest.main()
