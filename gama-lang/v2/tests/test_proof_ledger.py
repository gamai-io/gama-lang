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


def _verify_src(record):
    return "verify " + json.dumps(record, ensure_ascii=False) + "\n"


_VERIFY_LEDGER = r"""
import json, sys
import gamal2 as G
records = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
it = G.Interpreter()
bad = []
for rec in records:
    res = it.verify(rec)
    if not res["ok"]:
        bad.append({"name": rec.get("name"), "errors": res["errors"]})
print(json.dumps({"ok": not bad, "n": len(records),
                  "chain": len(it.proof_chain), "bad": bad}))
"""


def _concurrent_prove(ledger, idx, barrier, queue):
    """Load the ledger, wait for every peer, then prove one distinct key.

    Loading *before* the barrier deliberately exposes the read-then-append race
    that used to fork the chain; the append must serialize on its own.
    """
    import os as _os
    import sys as _sys

    _sys.path.insert(0, str(V2_ROOT))
    import gamal2 as G

    for key in list(_os.environ):
        if key.startswith("GAMA_L_"):
            _os.environ.pop(key, None)
    _os.environ["GAMA_L_PROOF_LEDGER"] = str(ledger)
    try:
        src = "prove key_%d = { n: %d }\n" % (idx, idx)
        ast = G.check(src)
        it = G.Interpreter()  # read the current tail before all peers append
        barrier.wait(timeout=30)
        it.run(ast)
        queue.put((idx, True, it.prev_proof, ""))
    except Exception as exc:  # pragma: no cover - surfaced via the queue
        queue.put((idx, False, "", "%s: %s" % (type(exc).__name__, exc)))


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

    def test_concurrent_proves_form_one_valid_chain(self):
        """N runs that all load the same tail must still produce one chain."""
        import multiprocessing as mp

        n = 6
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            try:
                ctx = mp.get_context("fork")
            except ValueError:  # pragma: no cover - non-POSIX
                ctx = mp.get_context()
            barrier = ctx.Barrier(n)
            queue = ctx.Queue()
            procs = [
                ctx.Process(
                    target=_concurrent_prove,
                    args=(str(ledger), idx, barrier, queue),
                )
                for idx in range(n)
            ]
            try:
                for proc in procs:
                    proc.start()
                results = [queue.get(timeout=60) for _ in range(n)]
            finally:
                for proc in procs:
                    proc.join(timeout=60)
                    if proc.is_alive():  # pragma: no cover - safety net
                        proc.terminate()
                        proc.join(5)
                queue.close()

            for proc in procs:
                self.assertEqual(proc.exitcode, 0)
            failures = [r for r in results if not r[1]]
            self.assertEqual(failures, [], failures)

            records = _ledger_lines(ledger)
            self.assertEqual(len(records), n)
            names = {rec["name"] for rec in records}
            self.assertEqual(names, {"key_%d" % i for i in range(n)})
            prev = "GENESIS"
            for rec in records:
                self.assertEqual(rec["prev"], prev)
                prev = rec["sha256"]
            self.assertEqual(len({rec["sha256"] for rec in records}), n)

            env = dict(os.environ)
            for key in list(env):
                if key.startswith("GAMA_L_"):
                    env.pop(key, None)
            env["GAMA_L_PROOF_LEDGER"] = str(ledger)
            proc = subprocess.run(
                [sys.executable, "-c", _VERIFY_LEDGER, str(ledger)],
                cwd=str(V2_ROOT), env=env, capture_output=True, text=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["n"], n)
            self.assertEqual(payload["chain"], n)

    def test_forked_ledger_is_reported_and_refused(self):
        """A ledger branched at GENESIS is reported by verify and not extended."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            one = root / "one.jsonl"
            two = root / "two.jsonl"
            ledger = root / "proofs.jsonl"
            self.assertEqual(_run("prove alpha = { a: 1 }\n", one)[0], 0)
            self.assertEqual(_run("prove beta = { b: 2 }\n", two)[0], 0)
            lines = []
            for path in (one, two):
                lines.extend(path.read_text(encoding="utf-8").splitlines())
            ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

            rc, r, _ = _run(_verify_src(json.loads(lines[0])), ledger)
            self.assertEqual(rc, 0)
            self.assertFalse(r["verify"]["ok"])
            self.assertIn("forked_chain", r["verify"]["errors"])

            before = ledger.read_text(encoding="utf-8")
            rc, r, _ = _run("prove gamma = { c: 3 }\n", ledger)
            self.assertNotEqual(rc, 0)
            self.assertFalse(r["ok"])
            self.assertIn("not a single valid chain", r["msg"])
            self.assertEqual(ledger.read_text(encoding="utf-8"), before)

    def test_malformed_ledger_is_reported_and_refused(self):
        """A corrupt line is reported by verify and blocks further appends."""
        with tempfile.TemporaryDirectory() as td:
            ledger = pathlib.Path(td) / "proofs.jsonl"
            rc, r, _ = _run("prove alpha = { a: 1 }\n", ledger)
            self.assertEqual(rc, 0)
            rec = r["proofs"]["alpha"]
            with open(ledger, "a", encoding="utf-8") as fh:
                fh.write("{not json}\n")

            rc, r, _ = _run(_verify_src(rec), ledger)
            self.assertEqual(rc, 0)
            self.assertFalse(r["verify"]["ok"])
            self.assertIn("malformed_line", r["verify"]["errors"])

            before = ledger.read_text(encoding="utf-8")
            rc, r, _ = _run("prove beta = { b: 2 }\n", ledger)
            self.assertNotEqual(rc, 0)
            self.assertFalse(r["ok"])
            self.assertIn("not a single valid chain", r["msg"])
            self.assertEqual(ledger.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
