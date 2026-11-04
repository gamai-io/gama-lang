"""Cross-run persistence tests for the optional proof ledger (T6).

Each test spawns fresh interpreter processes so only the JSONL file can carry
state between runs. Ledger is enabled only via ``GAMA_L_PROOF_LEDGER``.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

V2_ROOT = pathlib.Path(__file__).resolve().parents[1]

_RUNNER = r"""
import json, sys
import gamal2 as G
src = sys.argv[1]
try:
    it = G.run(src)
    payload = {
        "ok": True,
        "out": it.out,
        "proofs": it.proofs,
        "chain": it.proof_chain,
        "verify": it.env.v.get("_verify"),
        "prev": it.prev_proof,
    }
    print(json.dumps(payload, default=str))
except Exception as exc:
    print(json.dumps({"ok": False, "type": type(exc).__name__, "msg": str(exc)}))
    sys.exit(3)
"""


def _run(src, ledger=None):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("GAMA_L_"):
            env.pop(key, None)
    if ledger is not None:
        env["GAMA_L_PROOF_LEDGER"] = str(ledger)
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, src],
        cwd=str(V2_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    payload = json.loads(lines[-1]) if lines else {}
    return proc.returncode, payload, proc


def _ledger_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class ProofLedgerCrossRun(unittest.TestCase):
    def test_second_run_same_key_value_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, r1, _ = _run("prove p = { a: 1 }\nverify p\n", ledger)
            self.assertEqual(rc1, 0)
            self.assertTrue(r1["ok"])
            self.assertEqual(len(_ledger_lines(ledger)), 1)
            rc2, r2, _ = _run("prove p = { a: 1 }\nverify p\n", ledger)
            self.assertEqual(rc2, 0)
            self.assertTrue(r2["ok"])
            self.assertTrue(r2["verify"]["ok"])
            self.assertEqual(len(_ledger_lines(ledger)), 1)
            self.assertEqual(r1["proofs"]["p"]["sha256"], r2["proofs"]["p"]["sha256"])

    def test_second_run_different_value_fails_and_does_not_append(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, _, _ = _run("prove p = { a: 1 }\n", ledger)
            self.assertEqual(rc1, 0)
            before = len(_ledger_lines(ledger))
            rc2, r2, _ = _run("prove p = { a: 2 }\n", ledger)
            self.assertNotEqual(rc2, 0)
            self.assertFalse(r2["ok"])
            self.assertIn("duplicate key", r2["msg"])
            self.assertEqual(len(_ledger_lines(ledger)), before)

    def test_chain_prev_continues_from_last_persisted_record(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, r1, _ = _run("prove first = { a: 1 }\n", ledger)
            self.assertEqual(rc1, 0)
            rc2, r2, _ = _run("prove second = { b: 2 }\nverify second\n", ledger)
            self.assertEqual(rc2, 0)
            self.assertTrue(r2["verify"]["ok"])
            first_sha = r1["proofs"]["first"]["sha256"]
            records = _ledger_lines(ledger)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[1]["prev"], first_sha)
            self.assertEqual(r2["proofs"]["second"]["prev"], first_sha)

    def test_verify_rejects_untrusted_prev_against_loaded_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, _, _ = _run("prove first = { a: 1 }\n", ledger)
            self.assertEqual(rc1, 0)
            src = 'verify { name: "x", value: { a: 1 }, sha256: "00", prev: "not-in-ledger" }\n'
            rc2, r2, _ = _run(src, ledger)
            self.assertEqual(rc2, 0)
            self.assertFalse(r2["verify"]["ok"])
            self.assertIn("untrusted_chain", r2["verify"]["errors"])

    def test_unset_ledger_creates_no_file_and_stays_in_memory(self):
        with tempfile.TemporaryDirectory() as td:
            rc, r, _ = _run("prove p = { a: 1 }\n")
            self.assertEqual(rc, 0)
            self.assertTrue(r["ok"])
            self.assertEqual(list(pathlib.Path(td).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
