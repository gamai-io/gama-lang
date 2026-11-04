"""Executable conformance checks for SPEC_2.0.md (T1-T10).

Deterministic and offline by design: no network calls, no fixed clocks, no
shared state between tests. The suite imports the reference runtime
(``gamal2``) and uses only public entry points (``run``/``check``/``run_file``/
``fmt_source``).
"""
import hashlib
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G  # noqa: E402

V2_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = V2_ROOT / "examples"


class _OfflineCase(unittest.TestCase):
    """Base case that removes every GAMA_L_* environment knob for isolation."""

    def setUp(self):
        self._saved = {k: v for k, v in os.environ.items() if k.startswith("GAMA_L_")}
        for key in list(self._saved):
            os.environ.pop(key, None)
        self.addCleanup(self._restore)

    def _restore(self):
        for key in [k for k in os.environ if k.startswith("GAMA_L_")]:
            os.environ.pop(key, None)
        os.environ.update(self._saved)


class T1ArithmeticAndPipes(_OfflineCase):
    """T1: 20 arithmetic/pipe programs produce exact expected values."""

    CASES = [
        ("print 1 + 2\n", ["3"]),
        ("print 10 - 4\n", ["6"]),
        ("print 6 * 7\n", ["42"]),
        ("print 20 / 4\n", ["5"]),
        ("print 2 + 3 * 4\n", ["14"]),
        ("print (2 + 3) * 4\n", ["20"]),
        ("print 117.00 + 17.00\n", ["134.00"]),
        ("print 100 * (1 + 0.17)\n", ["117.00"]),
        ("print round(100 * (1 + 0.17), 2)\n", ["117.00"]),
        ("print -5 + 8\n", ["3"]),
        ("print 2 * (3 + 4) - 5\n", ["9"]),
        ("print 7 % 3\n", ["1"]),
        ("print 1.5 + 2.5\n", ["4.0"]),
        ("print 0.1 + 0.2\n", ["0.3"]),
        ('let r = sql("x")\nprint r | count\n', ["2"]),
        ('let r = sql("x")\nprint r | filter mjesto == "Sarajevo" | count\n', ["1"]),
        ('let r = sql("x")\nlet s = r | select konto, naziv\nprint s | count\n', ["2"]),
        ('let r = sql("x")\nprint r | sum neto\n', ["350.00"]),
        ('let r = sql("x")\nprint r | limit 1 | count\n', ["1"]),
        ('let r = sql("x")\nprint r | map mjesto | count\n', ["2"]),
    ]

    def test_t1_arithmetic_and_pipes_20_cases(self):
        self.assertEqual(len(self.CASES), 20)
        for index, (src, expected) in enumerate(self.CASES, start=1):
            with self.subTest(case=index, src=src):
                self.assertEqual(G.run(src).out, expected)


class T2StaticTypeErrors(_OfflineCase):
    """T2: a type error is rejected before execution with line/column."""

    CASES = [
        ('let x: Int = "text"\n', 1, "Text != Int"),
        ("let x = 1\nlet y: Int = \"bad\"\n", 2, "Text != Int"),
        ("let x = 1\nprint missingName\n", 2, "unknown variable"),
        ('let d: Decimal = "a"\n', 1, "Text != Decimal"),
    ]

    def test_t2_type_errors_carry_line_and_column(self):
        for src, expected_line, fragment in self.CASES:
            with self.subTest(src=src):
                with self.assertRaises(G.GamaTypeError) as caught:
                    G.check(src)
                err = caught.exception
                self.assertIn(fragment, err.msg)
                self.assertGreaterEqual(err.line, 1)
                self.assertGreaterEqual(err.col, 1)
                self.assertEqual(err.line, expected_line)

    def test_t2_rejected_before_any_execution(self):
        # The checker rejects statically; the print is never evaluated.
        with self.assertRaises(G.GamaTypeError):
            G.run('let x: Int = "text"\nprint "SIDE EFFECT"\n')


class T3EffectDeclarations(_OfflineCase):
    """T3: a function with ``effects {}`` cannot perform undeclared effects."""

    def test_t3_effects_empty_rejects_sql_write(self):
        src = (
            "fn f() -> Int effects {} {\n"
            '  sql_write("insert into t values (1)")\n'
            "  return 1\n"
            "}\n"
        )
        with self.assertRaises(G.GamaEffectError) as caught:
            G.check(src)
        self.assertIn("sql.write", caught.exception.msg)

    def test_t3_effects_empty_rejects_mail(self):
        src = (
            "fn g() -> Int effects {} {\n"
            '  mail("a@example.test", "s", "b")\n'
            "  return 1\n"
            "}\n"
        )
        with self.assertRaises(G.GamaEffectError) as caught:
            G.check(src)
        self.assertIn("mail", caught.exception.msg)

    def test_t3_declared_effect_is_allowed(self):
        src = (
            "fn h() -> Int effects { sql.read } {\n"
            '  return len(sql("x"))\n'
            "}\n"
            "print h()\n"
        )
        self.assertEqual(G.run(src).out, ["2"])


class T4HumanInTheLoop(_OfflineCase):
    """T4: ``wait user`` in non-interactive mode returns Denied, never proceeds."""

    def test_t4_wait_user_denied_and_never_proceeds(self):
        it = G.run(
            'let ok = wait user "Deploy now?"\n'
            'if ok {\n  print "PROCEEDED"\n}\n'
            'print "after"\n'
        )
        self.assertEqual(it.out, ["after"])


class T5ProofReceipts(_OfflineCase):
    """T5: a prove receipt verifies; changing one field invalidates it."""

    def test_t5_receipt_verifies(self):
        it = G.run('prove p = { a: 1, b: "x" }\nverify p\n')
        self.assertTrue(it.env.get("_verify")["ok"])
        self.assertEqual(it.env.get("_verify")["errors"], [])

    def test_t5_single_field_change_invalidates(self):
        it = G.run(
            "prove p = { a: 1, b: 2 }\n"
            'let q = { name: "p", value: { a: 1, b: 3 }, sha256: p.sha256, prev: p.prev }\n'
            "verify q\n"
        )
        verdict = it.env.get("_verify")
        self.assertFalse(verdict["ok"])
        self.assertIn("hash_mismatch", verdict["errors"])


class T6ProofIdempotency(_OfflineCase):
    """T6: the same prove idempotency key does not emit a second effect.

    PASS within one interpreter run (same key+value reuses the record and does
    not advance the chain). Cross-run persistence is not implemented in the
    core runtime; see CONFORMANCE.md.
    """

    def test_t6_same_key_same_value_single_record(self):
        it = G.run('prove p = { a: 1 }\nprove p = { a: 1 }\nprint "done"\n')
        self.assertEqual(len(it.proof_chain), 1)
        self.assertEqual(len(it.proofs), 1)
        self.assertEqual(it.out, ["done"])

    def test_t6_same_key_different_value_is_rejected(self):
        with self.assertRaises(G.GamaRuntimeError) as caught:
            G.run("prove p = { a: 1 }\nprove p = { a: 2 }\n")
        self.assertIn("duplicate key", caught.exception.msg)


class T7ImportCycles(_OfflineCase):
    """T7: a module import cycle is rejected with a clear error."""

    def test_t7_import_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "gamal.toml").write_text("[dependencies]\n", encoding="utf-8")
            (root / "a.gm").write_text('import "b.gm"\nprint 1\n', encoding="utf-8")
            (root / "b.gm").write_text('import "a.gm"\nprint 2\n', encoding="utf-8")
            with self.assertRaises(G.GamaError) as caught:
                G.run_file(str(root / "a.gm"))
            self.assertIn("import cycle", caught.exception.msg)


class T8AskMetadata(_OfflineCase):
    """T8: ``ask`` records prompt hash and model; offline returns Unavailable."""

    def test_t8_offline_ask_unavailable_with_metadata(self):
        it = G.run('let a = ask "hi"\nprint a\n')
        self.assertEqual(it.out, ["Unavailable"])
        self.assertIsNotNone(it.last_ask)
        self.assertEqual(it.last_ask["model"], "mock")
        self.assertEqual(
            it.last_ask["prompt_hash"],
            hashlib.sha256(b"hi").hexdigest(),
        )


class T9ExamplesOffline(_OfflineCase):
    """T9: 10 example programs run from a clean checkout without network."""

    def test_t9_examples_run_offline(self):
        files = sorted(EXAMPLES.glob("*.gm"))
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            with self.subTest(example=path.name):
                interpreter = G.run_file(str(path))
                self.assertIsNotNone(interpreter)


class T10Determinism(_OfflineCase):
    """T10: ``gamal check`` and ``gamal fmt`` are deterministic."""

    SOURCE = (
        "let x = 1\n"
        "if x > 0 {\n"
        'print "a"\n'
        "} else {\n"
        'print "b"\n'
        "}\n"
        'let s = "multi\nline"\n'
        "print s\n"
    )

    def test_t10_fmt_deterministic_and_idempotent(self):
        first = G.fmt_source(self.SOURCE)
        self.assertEqual(first, G.fmt_source(self.SOURCE))
        self.assertEqual(G.fmt_source(first), first)
        G.check(first)

    def test_t10_fmt_preserves_multiline_string(self):
        # Current formatter is token-based: multi-line strings are preserved
        # verbatim rather than re-indented (documented limitation).
        self.assertIn('"multi\nline"', G.fmt_source(self.SOURCE))

    def test_t10_check_deterministic(self):
        self.assertEqual(repr(G.check(self.SOURCE)), repr(G.check(self.SOURCE)))
        bad = 'let x: Int = "text"\n'
        outcomes = []
        for _ in range(2):
            with self.assertRaises(G.GamaTypeError) as caught:
                G.check(bad)
            err = caught.exception
            outcomes.append((err.msg, err.line, err.col))
        self.assertEqual(outcomes[0], outcomes[1])


if __name__ == "__main__":
    unittest.main()
