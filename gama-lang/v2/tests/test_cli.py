import json, pathlib, subprocess, sys, tempfile, unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"


class CliTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(GAMAL2), *args],
                              cwd=str(cwd or V2), capture_output=True,
                              text=True, timeout=30)

    def test_doctor_json(self):
        p = self.run_cli("doctor", "--json")
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        data = json.loads(p.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["checks"]), 8)

    def test_registry_search_and_add(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            reg = root / "registry.json"
            reg.write_text(json.dumps({"version": 1, "packages": {
                "math": {"version": "0.1.0", "git": "../mathrepo", "rev": "abc"}}}), encoding="utf-8")
            p = self.run_cli("pkg", "search", str(reg), "mat", cwd=root)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("math", p.stdout)
            p = self.run_cli("pkg", "add", str(reg), "math", "0.1.0", cwd=root)
            self.assertEqual(p.returncode, 0, p.stderr)
            text = (root / "gamal.toml").read_text(encoding="utf-8")
            self.assertIn('math = { git = "../mathrepo", rev = "abc" }', text)


if __name__ == "__main__":
    unittest.main()
