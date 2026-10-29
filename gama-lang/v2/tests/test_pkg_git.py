import contextlib, io, json, pathlib, re, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gamal2 as G

LIB = 'fn add(a: Int, b: Int) -> Int effects {} { return a+b }\n'


class PkgGitTests(unittest.TestCase):
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
        (self.app / "main.gm").write_text('import "math/lib.gm"\nprint add(20,22)\n', encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        p = subprocess.run(["git", *args], cwd=str(self.repo), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def _install(self, toml_body):
        (self.app / "gamal.toml").write_text(
            '[package]\nname = "app"\n\n[dependencies]\n' + toml_body + "\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = G._pkg_install(self.app)
        return rc, buf.getvalue()

    def _lock(self):
        return json.loads((self.app / ".gamal" / "lock.json").read_text(encoding="utf-8"))

    def test_git_dependency(self):
        rc, out = self._install('math = { git = "../mathrepo" }')
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.app / ".gamal" / "modules" / "math" / "lib.gm").exists())
        entry = self._lock()["math"]
        self.assertEqual(entry["source"], "../mathrepo")
        self.assertRegex(entry["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(entry["commit"], self.head)
        it = G.run_file(str(self.app / "main.gm"))
        self.assertEqual(it.out, ["42"])

    def test_git_rev_dependency(self):
        rc, out = self._install('math = { git = "../mathrepo", rev = "%s" }' % self.head)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._lock()["math"]["commit"], self.head)
        self.assertEqual(G.run_file(str(self.app / "main.gm")).out, ["42"])

    def test_git_cache_reused_on_reinstall(self):
        self.assertEqual(self._install('math = { git = "../mathrepo" }')[0], 0)
        marker = self.app / ".gamal" / "cache" / "math" / "cached_marker"
        marker.write_text("x", encoding="utf-8")
        self.assertEqual(self._install('math = { git = "../mathrepo" }')[0], 0)
        self.assertTrue(marker.exists(), "second install must reuse .gamal/cache")
        self.assertEqual(self._lock()["math"]["commit"], self.head)

    def test_missing_git_source_fails(self):
        rc, out = self._install('math = { git = "../does-not-exist" }')
        self.assertEqual(rc, 1)
        self.assertIn("git clone failed", out)

    def test_update_pulls_new_commit(self):
        self.assertEqual(self._install('math = { git = "../mathrepo" }')[0], 0)
        (self.repo / "lib.gm").write_text(LIB + "fn sub(a: Int, b: Int) -> Int effects {} { return a-b }\n", encoding="utf-8")
        self._git("add", "lib.gm"); self._git("commit", "--quiet", "-m", "second")
        new_head = self._git("rev-parse", "HEAD").stdout.strip()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = G._pkg_install(self.app, update=True)
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(self._lock()["math"]["commit"], new_head)
    def test_lock_mismatch_keeps_installed_module(self):
        self.assertEqual(self._install('math = { git = "../mathrepo" }')[0], 0)
        mod = self.app / ".gamal" / "modules" / "math" / "lib.gm"
        before = mod.read_text(encoding="utf-8")
        cache = self.app / ".gamal" / "cache" / "math"
        (cache / "lib.gm").write_text(LIB + "fn sub(a: Int, b: Int) -> Int effects {} { return a-b }\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(cache), "-c", "user.email=test@example.com",
                        "-c", "user.name=Test", "commit", "-qam", "cache-only"], check=True)
        rc, out = self._install('math = { git = "../mathrepo" }')
        self.assertEqual(rc, 1)
        self.assertIn("lock mismatch", out)
        self.assertEqual(mod.read_text(encoding="utf-8"), before)
    def test_path_outside_root_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("SECRET", encoding="utf-8")
        rc, out = self._install('keys = { path = "%s" }' % outside)
        self.assertEqual(rc, 1)
        self.assertIn("unsafe path", out)
        self.assertFalse((self.app / ".gamal" / "modules" / "keys").exists())
    def test_symlink_dependency_rejected(self):
        dep = self.app / "math"; dep.mkdir()
        (self.base / "secret.txt").write_text("SECRET", encoding="utf-8")
        (dep / "leak.txt").symlink_to(self.base / "secret.txt")
        rc, out = self._install('math = { path = "math" }')
        self.assertEqual(rc, 1)
        self.assertIn("symlink not allowed", out)
    def test_path_dependency_still_works(self):
        dep = self.app / "math"
        dep.mkdir()
        (dep / "lib.gm").write_text(LIB, encoding="utf-8")
        rc, out = self._install('math = { path = "math" }')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._lock()["math"]["path"], str(dep.resolve()))
        self.assertEqual(G.run_file(str(self.app / "main.gm")).out, ["42"])


if __name__ == "__main__":
    unittest.main()
