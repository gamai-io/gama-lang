import contextlib
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G
import gama_registry as R

LIB = 'fn add(a: Int, b: Int) -> Int effects {} { return a+b }\n'

VALID = {
    "version": 1,
    "packages": {
        "math": {
            "version": "0.1.0",
            "git": "../mathrepo",
            "rev": "0123456789abcdef0123456789abcdef01234567",
        },
        "Alpha": {
            "version": "2.0.0",
            "git": "https://example.com/alpha.git",
        },
    },
}


class RegistryLoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, data, name="registry.json"):
        path = self.base / name
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_load_search_resolve(self):
        path = self._write(VALID)
        index = R.load_registry(str(path))
        self.assertIsInstance(index, dict)

        rows = R.search(index)
        self.assertEqual([r["name"] for r in rows], ["Alpha", "math"])
        self.assertEqual(
            rows[1],
            {
                "name": "math",
                "version": "0.1.0",
                "git": "../mathrepo",
                "rev": "0123456789abcdef0123456789abcdef01234567",
            },
        )
        self.assertIsNone(rows[0]["rev"])

        self.assertEqual([r["name"] for r in R.search(index, "ma")], ["math"])
        self.assertEqual([r["name"] for r in R.search(index, "MATH")], ["math"])
        self.assertEqual(R.search(index, "zzz"), [])

        self.assertEqual(R.resolve(index, "math")["git"], "../mathrepo")
        self.assertEqual(R.resolve(index, "math", "0.1.0")["version"], "0.1.0")
        self.assertEqual(R.resolve(index, "Alpha")["version"], "2.0.0")

    def test_load_file_url(self):
        path = self._write(VALID)
        index = R.load_registry(path.as_uri())
        self.assertEqual(R.resolve(index, "math")["version"], "0.1.0")

    def test_missing_file(self):
        with self.assertRaises(R.RegistryError):
            R.load_registry(str(self.base / "nope.json"))

    def test_bad_json(self):
        path = self._write("{ not json")
        with self.assertRaises(R.RegistryError):
            R.load_registry(str(path))

    def test_bad_schema(self):
        cases = [
            [],
            {"packages": {}},
            {"version": 2, "packages": {}},
            {"version": True, "packages": {}},
            {"version": 1},
            {"version": 1, "packages": []},
            {"version": 1, "packages": {"math": []}},
            {"version": 1, "packages": {"math": {"version": "1.0.0"}}},
            {"version": 1, "packages": {"math": {"git": "../m"}}},
            {"version": 1, "packages": {"math": {"version": "", "git": "../m"}}},
            {"version": 1, "packages": {"math": {"version": "1", "git": ""}}},
            {"version": 1, "packages": {"math": {"version": "1", "git": "../m", "rev": 1}}},
        ]
        for i, case in enumerate(cases):
            path = self._write(case, name="bad_%d.json" % i)
            with self.assertRaises(R.RegistryError, msg="case %d" % i):
                R.load_registry(str(path))

    def test_invalid_package_names(self):
        for i, name in enumerate(["..", "a/b", "a\\b", ".hidden", "pkg name"]):
            data = {"version": 1, "packages": {name: {"version": "1", "git": "../m"}}}
            path = self._write(data, name="name_%d.json" % i)
            with self.assertRaises(R.RegistryError, msg="name %r" % name):
                R.load_registry(str(path))

    def test_resolve_errors(self):
        index = R.load_registry(str(self._write(VALID)))
        with self.assertRaises(R.RegistryError):
            R.resolve(index, "missing")
        with self.assertRaises(R.RegistryError):
            R.resolve(index, "math", "9.9.9")
        with self.assertRaises(R.RegistryError):
            R.resolve(index, "../math")


class AddDependencyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.app = self.base / "app"
        self.app.mkdir()
        self.cfg = self.app / "gamal.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def _count(self, text, name):
        return len(re.findall(r"(?m)^\s*%s\s*=" % re.escape(name), text))

    def test_creates_minimal_file(self):
        self.assertFalse(self.cfg.exists())
        self.assertTrue(R.add_dependency(self.app, "math", {"git": "../mathrepo"}))
        self.assertTrue(self.cfg.exists())
        text = self.cfg.read_text(encoding="utf-8")
        self.assertIn("[dependencies]", text)
        self.assertIn('math = { git = "../mathrepo" }', text)
        self.assertNotIn("rev", text)
        data = G._read_toml(self.cfg)
        self.assertEqual(data["dependencies"]["math"]["git"], "../mathrepo")
        self.assertNotIn("rev", data["dependencies"]["math"])

    def test_update_without_duplicates(self):
        self.assertTrue(
            R.add_dependency(self.app, "math", {"git": "../old", "rev": "aaaa"})
        )
        self.assertTrue(
            R.add_dependency(self.app, "math", {"git": "../new", "rev": "bbbb"})
        )
        text = self.cfg.read_text(encoding="utf-8")
        self.assertEqual(self._count(text, "math"), 1)
        data = G._read_toml(self.cfg)
        self.assertEqual(data["dependencies"]["math"], {"git": "../new", "rev": "bbbb"})

        before = text
        self.assertTrue(
            R.add_dependency(self.app, "math", {"git": "../new", "rev": "bbbb"})
        )
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), before)
        self.assertEqual(self._count(before, "math"), 1)

    def test_preserves_other_dependencies_and_sections(self):
        self.cfg.write_text(
            '[package]\nname = "demo"\n\n'
            "[dependencies]\n"
            'other = { git = "../other" }\n'
            'math = { git = "../old", rev = "oldsha" }\n\n'
            "[scripts]\n"
            'build = "gamal2.py run main.gm"\n',
            encoding="utf-8",
        )
        self.assertTrue(
            R.add_dependency(self.app, "math", {"git": "../new", "rev": "newsha"})
        )
        text = self.cfg.read_text(encoding="utf-8")
        self.assertEqual(self._count(text, "math"), 1)
        self.assertIn('other = { git = "../other" }', text)
        self.assertIn("[scripts]", text)
        data = G._read_toml(self.cfg)
        self.assertEqual(data["package"]["name"], "demo")
        self.assertEqual(data["dependencies"]["other"]["git"], "../other")
        self.assertEqual(data["dependencies"]["math"], {"git": "../new", "rev": "newsha"})
        self.assertEqual(data["scripts"]["build"], "gamal2.py run main.gm")

    def test_appends_section_when_missing(self):
        self.cfg.write_text('[package]\nname = "demo"\n', encoding="utf-8")
        self.assertTrue(R.add_dependency(self.app, "math", {"git": "../mathrepo"}))
        data = G._read_toml(self.cfg)
        self.assertEqual(data["package"]["name"], "demo")
        self.assertEqual(data["dependencies"]["math"]["git"], "../mathrepo")

    def test_unsafe_value_rejected(self):
        for value in ("../a,b", "../a#b", '../a"b'):
            with self.assertRaises(R.RegistryError, msg=value):
                R.add_dependency(self.app, "math", {"git": value})

    def test_invalid_name_rejected_before_creating_file(self):
        with self.assertRaises(R.RegistryError):
            R.add_dependency(self.app, "../math", {"git": "../mathrepo"})
        self.assertFalse(self.cfg.exists())

    def test_missing_git_rejected(self):
        with self.assertRaises(R.RegistryError):
            R.add_dependency(self.app, "math", {"rev": "abc"})


class RegistryEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.repo = self.base / "mathrepo"
        self.repo.mkdir()
        (self.repo / "lib.gm").write_text(LIB, encoding="utf-8")
        self._git("init", "--quiet")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("add", "lib.gm")
        self._git("commit", "--quiet", "-m", "init")
        self.head = self._git("rev-parse", "HEAD").stdout.strip()

        self.app = self.base / "app"
        self.app.mkdir()
        (self.app / "main.gm").write_text(
            'import "math/lib.gm"\nprint add(20,22)\n', encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        proc = subprocess.run(
            ["git", *args], cwd=str(self.repo), capture_output=True, text=True, timeout=60
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_registry_to_install_and_run(self):
        registry = self.base / "registry.json"
        registry.write_text(
            json.dumps(
                {
                    "version": 1,
                    "packages": {
                        "math": {
                            "version": "0.1.0",
                            "git": "../mathrepo",
                            "rev": self.head,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        index = R.load_registry(str(registry))
        entry = R.resolve(index, "math", "0.1.0")
        self.assertTrue(R.add_dependency(self.app, "math", entry))

        self.assertEqual(
            G._read_toml(self.app / "gamal.toml")["dependencies"]["math"]["rev"],
            self.head,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = G._pkg_install(self.app)
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue((self.app / ".gamal" / "modules" / "math" / "lib.gm").exists())

        result = G.run_file(str(self.app / "main.gm"))
        self.assertEqual(result.out, ["42"])


if __name__ == "__main__":
    unittest.main()
