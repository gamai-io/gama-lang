"""IR ``prove``/``verify`` parity with the reference interpreter.

The standalone IR backend compiles proof statements to VM ops and owns its
receipt/chain/ledger state (no ``gamal2.Interpreter`` at runtime).  These tests
compare it against the interpreter for:

* record shape, hash, id and chain ``prev`` links;
* duplicate keys (same value = idempotent, different value = error);
* tamper detection and ``not_a_proof``;
* HMAC signatures (``GAMA_L_PROOF_KEY``, ``GAMA_L_REQUIRE_SIG``);
* intent/lease binding and ``GAMA_L_REQUIRE_INTENT_LEASE``;
* the optional persistent JSONL ledger (``GAMA_L_PROOF_LEDGER``), including
  cross-run idempotency, chain continuation, structural corruption and the
  refuse-to-extend rule.

Every test isolates ``GAMA_L_*`` variables so the ambient shell cannot leak
state between cases.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_ir as IR

_PROOF_ENV = (
    "GAMA_L_PROOF_KEY",
    "GAMA_L_PROOF_LEDGER",
    "GAMA_L_PROOF_INTENT",
    "GAMA_L_PROOF_LEASE",
    "GAMA_L_REQUIRE_SIG",
    "GAMA_L_REQUIRE_INTENT_LEASE",
)


def _clear_proof_env():
    for name in _PROOF_ENV:
        os.environ.pop(name, None)


def _set_env(**values):
    _clear_proof_env()
    for name, value in values.items():
        if value is not None:
            os.environ[name] = str(value)


def _run_ir(src):
    return IR.run_program(G.check(src))


def _run_both(src):
    """Run ``src`` under the interpreter and the IR with the same env."""
    return G.run(src), _run_ir(src)


def _strip_ts(rec):
    out = dict(rec)
    out.pop("ts", None)
    return out


def _ledger_records(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _verify_src(record):
    return "verify " + json.dumps(record, ensure_ascii=False) + "\n"


class ProofEnvCase(unittest.TestCase):
    def setUp(self):
        _clear_proof_env()

    def tearDown(self):
        _clear_proof_env()


class BasicParity(ProofEnvCase):
    def test_prove_verify_output_and_receipt_match(self):
        src = 'prove p = { a: 1, b: "x" }\nverify p\n'
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertTrue(ir.globals["_verify"]["ok"])
        rec_i, rec_r = it.proofs["p"], ir.globals["p"]
        self.assertEqual(_strip_ts(rec_r), _strip_ts(rec_i))
        self.assertEqual(rec_r["sha256"], rec_i["sha256"])
        self.assertEqual(rec_r["id"], rec_i["id"])
        self.assertEqual(rec_r["prev"], "GENESIS")

    def test_dotted_proof_name_binds_literally(self):
        src = 'prove inv.posted = { a: 1 }\nprint inv.posted.a\nverify inv.posted\n'
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertEqual(ir.globals["_verify"], it.env.get("_verify"))
        self.assertIn("inv.posted", ir.globals)

    def test_verify_not_a_proof(self):
        src = 'verify 5\n'
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertEqual(ir.globals["_verify"], {"ok": False, "errors": ["not_a_proof"]})

    def test_verify_inside_function_prints_like_interpreter(self):
        src = ('fn check(x: Json) -> Int effects { proof.read } {\n'
               '  verify x\n'
               '  return 1\n'
               '}\n'
               'prove p = { a: 1 }\n'
               'print check(p)\n')
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)


class DuplicateKeys(ProofEnvCase):
    def test_same_value_is_idempotent(self):
        src = 'prove p = { a: 1 }\nprove p = { a: 1 }\nverify p\n'
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertTrue(ir.globals["_verify"]["ok"])
        self.assertEqual(ir.globals["p"]["sha256"], it.proofs["p"]["sha256"])
        # Only one chain entry (the second call is a hit, not an append).
        self.assertEqual(len(it.proof_chain), 1)

    def test_different_value_raises_same_message(self):
        src = 'prove p = { a: 1 }\nprove p = { a: 2 }\n'
        with self.assertRaises(G.GamaRuntimeError) as ctx_i:
            G.run(src)
        with self.assertRaises(IR.IRRuntimeError) as ctx_r:
            _run_ir(src)
        self.assertEqual(str(ctx_r.exception), str(ctx_i.exception))
        self.assertIsInstance(ctx_r.exception, G.GamaError)


class ChainAndTamper(ProofEnvCase):
    def test_chain_prev_links_match_interpreter(self):
        src = 'prove a = { n: 1 }\nprove b = { n: 2 }\nverify b\nverify a\n'
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertEqual(ir.globals["b"]["prev"], ir.globals["a"]["sha256"])
        self.assertEqual(ir.globals["b"]["prev"], it.proofs["b"]["prev"])
        self.assertTrue(ir.globals["_verify"]["ok"])

    def test_changed_value_is_rejected(self):
        src = ('prove p = { a: 1 }\n'
               'let q = { name: "p", value: { a: 2 }, sha256: p.sha256, prev: p.prev }\n'
               'verify q\n')
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertFalse(ir.globals["_verify"]["ok"])
        self.assertIn("hash_mismatch", ir.globals["_verify"]["errors"])
        self.assertFalse(it.env.get("_verify")["ok"])

    def test_unknown_prev_is_untrusted_chain(self):
        src = ('prove p = { a: 1 }\n'
               'let q = { name: "q", value: { a: 1 }, sha256: "00", prev: "nope" }\n'
               'verify q\n')
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertEqual(ir.globals["_verify"]["errors"],
                         it.env.get("_verify")["errors"])


class Hmac(ProofEnvCase):
    def test_signature_matches_and_verifies(self):
        _set_env(GAMA_L_PROOF_KEY="s3cret")
        src = 'prove p = { a: 1 }\nverify p\n'
        it, ir = _run_both(src)
        self.assertEqual(ir.out, it.out)
        self.assertTrue(ir.globals["_verify"]["ok"])
        self.assertIn("sig", ir.globals["p"])
        self.assertEqual(ir.globals["p"]["sig"], it.proofs["p"]["sig"])

    def test_tampered_value_reports_hash_and_signature(self):
        _set_env(GAMA_L_PROOF_KEY="s3cret")
        it = G.run('prove p = { a: 1 }\n')
        rec = dict(it.proofs["p"])
        rec["value"] = {"a": 999}
        ir = _run_ir(_verify_src(rec))
        self.assertFalse(ir.globals["_verify"]["ok"])
        self.assertIn("hash_mismatch", ir.globals["_verify"]["errors"])
        self.assertIn("bad_signature", ir.globals["_verify"]["errors"])

    def test_missing_key_is_reported(self):
        _set_env(GAMA_L_PROOF_KEY="k")
        it = G.run('prove p = { a: 1 }\n')
        self.assertIn("sig", it.proofs["p"])
        _set_env()  # no GAMA_L_PROOF_KEY
        ir = _run_ir(_verify_src(it.proofs["p"]))
        self.assertEqual(ir.globals["_verify"]["errors"], ["missing_key"])

    def test_require_sig_rejects_unsigned_receipt(self):
        _set_env(GAMA_L_REQUIRE_SIG=1)
        it = G.run('prove p = { a: 1 }\n')
        ir = _run_ir(_verify_src(it.proofs["p"]))
        self.assertIn("missing_signature", ir.globals["_verify"]["errors"])


class IntentLease(ProofEnvCase):
    def test_fields_and_verification_match(self):
        _set_env(GAMA_L_PROOF_INTENT="report-42", GAMA_L_PROOF_LEASE="lease-7",
                 GAMA_L_PROOF_KEY="k")
        src = 'prove p = { a: 1 }\nverify p\n'
        it, ir = _run_both(src)
        self.assertEqual(_strip_ts(ir.globals["p"]), _strip_ts(it.proofs["p"]))
        self.assertTrue(ir.globals["_verify"]["ok"])

    def test_unbound_receipt_rejected_when_env_set(self):
        it = G.run('prove p = { a: 1 }\n')
        _set_env(GAMA_L_PROOF_INTENT="i", GAMA_L_PROOF_LEASE="l")
        ir = _run_ir(_verify_src(it.proofs["p"]))
        self.assertIn("intent_missing", ir.globals["_verify"]["errors"])
        self.assertIn("lease_missing", ir.globals["_verify"]["errors"])

    def test_mismatched_intent_and_lease(self):
        _set_env(GAMA_L_PROOF_INTENT="i1", GAMA_L_PROOF_LEASE="l1")
        it = G.run('prove p = { a: 1 }\n')
        _set_env(GAMA_L_PROOF_INTENT="i2", GAMA_L_PROOF_LEASE="l2")
        ir = _run_ir(_verify_src(it.proofs["p"]))
        self.assertIn("intent_mismatch", ir.globals["_verify"]["errors"])
        self.assertIn("lease_mismatch", ir.globals["_verify"]["errors"])

    def test_reprove_with_different_binding_is_rejected(self):
        # The binding is persisted in the ledger, so a second run that reuses
        # the key under another intent must be refused (in-process runs start
        # from a fresh proof table, exactly like separate interpreter runs).
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            src = 'prove p = { a: 1 }\n'
            _set_env(GAMA_L_PROOF_LEDGER=ledger,
                     GAMA_L_PROOF_INTENT="i1", GAMA_L_PROOF_LEASE="l1")
            _run_ir(src)
            _set_env(GAMA_L_PROOF_LEDGER=ledger,
                     GAMA_L_PROOF_INTENT="i2", GAMA_L_PROOF_LEASE="l1")
            with self.assertRaises(IR.IRRuntimeError) as ctx:
                _run_ir(src)
            self.assertIn("bound to intent", str(ctx.exception))

    def test_require_intent_lease_without_env(self):
        it = G.run('prove p = { a: 1 }\n')
        _set_env(GAMA_L_REQUIRE_INTENT_LEASE=1)
        ir = _run_ir(_verify_src(it.proofs["p"]))
        self.assertIn("intent_not_configured", ir.globals["_verify"]["errors"])
        self.assertIn("lease_not_configured", ir.globals["_verify"]["errors"])


class PersistentLedger(ProofEnvCase):
    def test_cross_run_idempotency_and_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "sub" / "proofs.jsonl"
            _set_env(GAMA_L_PROOF_LEDGER=ledger)
            ir1 = _run_ir('prove p = { a: 1 }\nverify p\n')
            ir2 = _run_ir('prove p = { a: 1 }\nverify p\n')
            self.assertEqual(len(_ledger_records(ledger)), 1)
            self.assertEqual(ir1.globals["p"]["sha256"],
                             ir2.globals["p"]["sha256"])
            self.assertTrue(ir2.globals["_verify"]["ok"])

    def test_duplicate_different_value_does_not_append(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            _set_env(GAMA_L_PROOF_LEDGER=ledger)
            _run_ir('prove p = { a: 1 }\n')
            before = len(_ledger_records(ledger))
            with self.assertRaises(IR.IRRuntimeError) as ctx:
                _run_ir('prove p = { a: 2 }\n')
            self.assertIn("duplicate key", str(ctx.exception))
            self.assertEqual(len(_ledger_records(ledger)), before)

    def test_chain_continues_from_last_persisted_record(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            _set_env(GAMA_L_PROOF_LEDGER=ledger)
            ir1 = _run_ir('prove first = { a: 1 }\n')
            ir2 = _run_ir('prove second = { b: 2 }\nverify second\n')
            recs = _ledger_records(ledger)
            self.assertEqual([r["name"] for r in recs], ["first", "second"])
            self.assertEqual(recs[1]["prev"], recs[0]["sha256"])
            self.assertEqual(ir2.globals["second"]["prev"],
                             ir1.globals["first"]["sha256"])
            self.assertTrue(ir2.globals["_verify"]["ok"])

    def test_persisted_records_match_interpreter(self):
        with tempfile.TemporaryDirectory() as td:
            led_i = pathlib.Path(td) / "i.jsonl"
            led_r = pathlib.Path(td) / "r.jsonl"
            src = 'prove a = { n: 1 }\nprove b = { n: 2 }\n'
            _set_env(GAMA_L_PROOF_LEDGER=led_i)
            G.run(src)
            _set_env(GAMA_L_PROOF_LEDGER=led_r)
            _run_ir(src)
            rec_i = [_strip_ts(r) for r in _ledger_records(led_i)]
            rec_r = [_strip_ts(r) for r in _ledger_records(led_r)]
            self.assertEqual(rec_r, rec_i)

    def test_structural_corruption_is_reported_and_refused(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            _set_env(GAMA_L_PROOF_LEDGER=ledger)
            _run_ir('prove a = { n: 1 }\nprove b = { n: 2 }\n')
            recs = _ledger_records(ledger)
            # Break the chain: second record's prev no longer points at the
            # first record's sha.
            recs[1]["prev"] = "f" * 64
            ledger.write_text("\n".join(json.dumps(r) for r in recs) + "\n",
                              encoding="utf-8")
            ir = _run_ir(_verify_src(recs[0]))
            self.assertIn("broken_chain", ir.globals["_verify"]["errors"])
            with self.assertRaises(IR.IRRuntimeError) as ctx:
                _run_ir('prove c = { n: 3 }\n')
            self.assertIn("not a single valid chain", str(ctx.exception))
            # The refused proof must not have been appended.
            self.assertEqual([r["name"] for r in _ledger_records(ledger)],
                             ["a", "b"])

    def test_forked_ledger_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            _set_env(GAMA_L_PROOF_LEDGER=ledger)
            _run_ir('prove a = { n: 1 }\nprove b = { n: 2 }\n')
            recs = _ledger_records(ledger)
            # Two records sharing the same predecessor = a forked chain.
            recs[1]["prev"] = "GENESIS"
            ledger.write_text("\n".join(json.dumps(r) for r in recs) + "\n",
                              encoding="utf-8")
            ir = _run_ir(_verify_src(recs[0]))
            self.assertIn("forked_chain", ir.globals["_verify"]["errors"])

    def test_malformed_line_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            ledger.write_text("not json\n", encoding="utf-8")
            _set_env(GAMA_L_PROOF_LEDGER=ledger)
            ir = _run_ir(_verify_src({"name": "x", "value": 1, "sha256": "00",
                                       "prev": "GENESIS"}))
            self.assertIn("malformed_line", ir.globals["_verify"]["errors"])


class NoInterpreterAtRuntime(ProofEnvCase):
    def test_ir_never_instantiates_the_interpreter(self):
        src = 'prove p = { a: 1 }\nverify p\n'
        ast = G.check(src)
        with mock.patch.object(G, "Interpreter",
                               side_effect=AssertionError("interpreter used")):
            result = IR.run_program(ast)
        self.assertTrue(result.globals["_verify"]["ok"])


if __name__ == "__main__":
    unittest.main()
