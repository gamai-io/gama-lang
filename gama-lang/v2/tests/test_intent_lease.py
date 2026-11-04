"""Intent/lease binding for proof receipts (T6 extension).

A receipt is bound to the ``GAMA_L_PROOF_INTENT`` / ``GAMA_L_PROOF_LEASE``
values that were set when it was created. The binding is committed in the
hash chain and, when ``GAMA_L_PROOF_KEY`` is set, in the HMAC payload.

Uniqueness decision: ``(intent, lease, key)`` is the effective proof identity.
Re-proving an existing key under a different (or newly set) intent/lease is a
clear error, never a silent idempotent hit, so one ledger key cannot carry two
different bindings.

Legacy receipts (no ``intent``/``lease`` fields) keep verifying when the new
env vars are unset; their hash and signature payload is unchanged.
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


def _run(src, ledger=None, intent=None, lease=None, key=None, require=None):
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("GAMA_L_"):
            env.pop(name, None)
    if ledger is not None:
        env["GAMA_L_PROOF_LEDGER"] = str(ledger)
    if intent is not None:
        env["GAMA_L_PROOF_INTENT"] = intent
    if lease is not None:
        env["GAMA_L_PROOF_LEASE"] = lease
    if key is not None:
        env["GAMA_L_PROOF_KEY"] = key
    if require is not None:
        env["GAMA_L_REQUIRE_INTENT_LEASE"] = str(require)
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


def _verify_src(record):
    return "verify " + json.dumps(record, ensure_ascii=False) + "\n"


class IntentLeaseRecord(unittest.TestCase):
    def test_record_includes_intent_and_lease_when_env_set(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc, r, _ = _run("prove p = { a: 1 }\nverify p\n", ledger,
                            intent="report-42", lease="lease-7", key="s3cret")
            self.assertEqual(rc, 0)
            self.assertTrue(r["ok"])
            rec = r["proofs"]["p"]
            self.assertEqual(rec["intent"], "report-42")
            self.assertEqual(rec["lease"], "lease-7")
            self.assertIn("sig", rec)
            self.assertTrue(r["verify"]["ok"], r["verify"])
            persisted = _ledger_lines(ledger)
            self.assertEqual(persisted[0]["intent"], "report-42")
            self.assertEqual(persisted[0]["lease"], "lease-7")

    def test_record_omits_fields_when_env_unset(self):
        rc, r, _ = _run("prove p = { a: 1 }\n")
        self.assertEqual(rc, 0)
        rec = r["proofs"]["p"]
        self.assertNotIn("intent", rec)
        self.assertNotIn("lease", rec)

    def test_binding_changes_hash_for_same_key_and_value(self):
        _, plain, _ = _run("prove p = { a: 1 }\n")
        _, bound, _ = _run("prove p = { a: 1 }\n", intent="i1", lease="l1")
        self.assertNotEqual(plain["proofs"]["p"]["sha256"],
                            bound["proofs"]["p"]["sha256"])


class IntentLeaseVerify(unittest.TestCase):
    def _bound_record(self, intent="report-42", lease="lease-7", key=None):
        rc, r, _ = _run("prove p = { a: 1 }\n", intent=intent, lease=lease, key=key)
        self.assertEqual(rc, 0)
        return r["proofs"]["p"]

    def test_verify_accepts_matching_env(self):
        rec = self._bound_record()
        rc, r, _ = _run(_verify_src(rec), intent="report-42", lease="lease-7")
        self.assertEqual(rc, 0)
        self.assertTrue(r["verify"]["ok"], r["verify"])

    def test_verify_rejects_intent_mismatch(self):
        rec = self._bound_record()
        rc, r, _ = _run(_verify_src(rec), intent="other-intent", lease="lease-7")
        self.assertEqual(rc, 0)
        self.assertFalse(r["verify"]["ok"])
        self.assertIn("intent_mismatch", r["verify"]["errors"])

    def test_verify_rejects_lease_mismatch(self):
        rec = self._bound_record()
        rc, r, _ = _run(_verify_src(rec), intent="report-42", lease="other-lease")
        self.assertEqual(rc, 0)
        self.assertFalse(r["verify"]["ok"])
        self.assertIn("lease_mismatch", r["verify"]["errors"])

    def test_verify_rejects_unbound_receipt_when_env_set(self):
        rc, r, _ = _run("prove p = { a: 1 }\n")
        self.assertEqual(rc, 0)
        rec = r["proofs"]["p"]
        rc, r, _ = _run(_verify_src(rec), intent="report-42", lease="lease-7")
        self.assertEqual(rc, 0)
        self.assertFalse(r["verify"]["ok"])
        self.assertIn("intent_missing", r["verify"]["errors"])
        self.assertIn("lease_missing", r["verify"]["errors"])

    def test_require_mode_without_env_is_rejected(self):
        rc, r, _ = _run("prove p = { a: 1 }\n")
        self.assertEqual(rc, 0)
        rec = r["proofs"]["p"]
        rc, r, _ = _run(_verify_src(rec), require=1)
        self.assertEqual(rc, 0)
        self.assertFalse(r["verify"]["ok"])
        self.assertIn("intent_not_configured", r["verify"]["errors"])
        self.assertIn("lease_not_configured", r["verify"]["errors"])

    def test_require_mode_with_matching_env_is_ok(self):
        rec = self._bound_record()
        rc, r, _ = _run(_verify_src(rec), intent="report-42", lease="lease-7", require=1)
        self.assertEqual(rc, 0)
        self.assertTrue(r["verify"]["ok"], r["verify"])

    def test_legacy_receipt_verifies_when_env_unset(self):
        rc, r, _ = _run("prove p = { a: 1 }\nverify p\n")
        self.assertEqual(rc, 0)
        self.assertTrue(r["verify"]["ok"], r["verify"])
        self.assertNotIn("intent", r["proofs"]["p"])
        # Same legacy record verified in a fresh process with no binding env.
        rc, r, _ = _run(_verify_src(r["proofs"]["p"]))
        self.assertEqual(rc, 0)
        self.assertTrue(r["verify"]["ok"], r["verify"])

    def test_hmac_tamper_value_is_rejected(self):
        rec = self._bound_record(key="s3cret")
        tampered = dict(rec)
        tampered["value"] = {"a": 999}
        rc, r, _ = _run(_verify_src(tampered), intent="report-42",
                        lease="lease-7", key="s3cret")
        self.assertEqual(rc, 0)
        self.assertFalse(r["verify"]["ok"])
        self.assertIn("hash_mismatch", r["verify"]["errors"])
        self.assertIn("bad_signature", r["verify"]["errors"])

    def test_hmac_tamper_signature_is_rejected(self):
        rec = self._bound_record(key="s3cret")
        tampered = dict(rec)
        tampered["sig"] = "00" * 32
        rc, r, _ = _run(_verify_src(tampered), intent="report-42",
                        lease="lease-7", key="s3cret")
        self.assertEqual(rc, 0)
        self.assertFalse(r["verify"]["ok"])
        self.assertEqual(r["verify"]["errors"], ["bad_signature"])


class IntentLeaseLedger(unittest.TestCase):
    def test_same_key_same_binding_is_idempotent_across_runs(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, r1, _ = _run("prove p = { a: 1 }\nverify p\n", ledger,
                              intent="i1", lease="l1")
            self.assertEqual(rc1, 0)
            self.assertTrue(r1["verify"]["ok"], r1["verify"])
            rc2, r2, _ = _run("prove p = { a: 1 }\nverify p\n", ledger,
                              intent="i1", lease="l1")
            self.assertEqual(rc2, 0)
            self.assertTrue(r2["verify"]["ok"], r2["verify"])
            self.assertEqual(len(_ledger_lines(ledger)), 1)
            self.assertEqual(r1["proofs"]["p"]["sha256"],
                             r2["proofs"]["p"]["sha256"])

    def test_same_key_different_intent_across_runs_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, _, _ = _run("prove p = { a: 1 }\n", ledger,
                             intent="intent-A", lease="lease-7")
            self.assertEqual(rc1, 0)
            before = len(_ledger_lines(ledger))
            rc2, r2, _ = _run("prove p = { a: 1 }\n", ledger,
                              intent="intent-B", lease="lease-7")
            self.assertNotEqual(rc2, 0)
            self.assertFalse(r2["ok"])
            self.assertIn("bound to intent", r2["msg"])
            self.assertEqual(len(_ledger_lines(ledger)), before)

    def test_same_key_different_lease_across_runs_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, _, _ = _run("prove p = { a: 1 }\n", ledger,
                             intent="intent-A", lease="lease-7")
            self.assertEqual(rc1, 0)
            before = len(_ledger_lines(ledger))
            rc2, r2, _ = _run("prove p = { a: 1 }\n", ledger,
                              intent="intent-A", lease="lease-8")
            self.assertNotEqual(rc2, 0)
            self.assertFalse(r2["ok"])
            self.assertIn("bound to lease", r2["msg"])
            self.assertEqual(len(_ledger_lines(ledger)), before)

    def test_legacy_key_cannot_be_reused_with_binding(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc1, _, _ = _run("prove p = { a: 1 }\n", ledger)
            self.assertEqual(rc1, 0)
            before = len(_ledger_lines(ledger))
            rc2, r2, _ = _run("prove p = { a: 1 }\n", ledger,
                              intent="intent-A", lease="lease-7")
            self.assertNotEqual(rc2, 0)
            self.assertFalse(r2["ok"])
            self.assertIn("bound to", r2["msg"])
            self.assertEqual(len(_ledger_lines(ledger)), before)


if __name__ == "__main__":
    unittest.main()
