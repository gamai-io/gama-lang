"""LSP support for generic named functions.

Covers hover, signature help, completions, diagnostics and semantic tokens
for ``fn id<T>(...)`` declarations and (where the parser allows them) calls.
"""
import json
import pathlib
import subprocess
import sys
import unittest

V2 = pathlib.Path(__file__).resolve().parents[1]
GAMAL2 = V2 / "gamal2.py"
sys.path.insert(0, str(V2))
URI = "file:///tmp/lsp_generics_demo.gm"

GENERIC_DOC = (
    "fn id<T>(x: T) -> T { return x }\n"
    "fn pair<A, B>(a: A, b: B) -> A { return a }\n"
    "print id(1)\n"
)


class LspClient:
    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, str(GAMAL2), "lsp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(V2),
        )
        self._id = 0

    def send(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
        self.p.stdin.flush()

    def request(self, method, params):
        self._id += 1
        self.send({"jsonrpc": "2.0", "id": self._id,
                   "method": method, "params": params})
        return self._id

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def read(self):
        headers = {}
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise EOFError("server closed stdout")
            if line in (b"\r\n", b"\n"):
                break
            k, _, v = line.decode("ascii", "replace").partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        return json.loads(self.p.stdout.read(n).decode("utf-8"))

    def wait_for(self, method=None, mid=None, limit=100):
        other = []
        for _ in range(limit):
            msg = self.read()
            if mid is not None and msg.get("id") == mid:
                return msg, other
            if method is not None and msg.get("method") == method:
                return msg, other
            other.append(msg)
        raise AssertionError("message not received: method=%r id=%r"
                             % (method, mid))

    def close(self):
        try:
            self.send({"jsonrpc": "2.0", "id": 9_999_999,
                       "method": "exit", "params": {}})
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


class LspServerGenericTests(unittest.TestCase):
    """Exercise the real JSON-RPC loop so generic responses stay valid JSON."""

    def setUp(self):
        self.c = LspClient()
        mid = self.c.request("initialize", {})
        self.c.wait_for(mid=mid)
        self.c.notify("initialized", {})

    def tearDown(self):
        self.c.close()

    def open(self, text):
        self.c.notify("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "gama-l", "version": 1, "text": text}})
        self.c.wait_for(method="textDocument/publishDiagnostics")

    def hover(self, line, character):
        mid = self.c.request("textDocument/hover", {
            "textDocument": {"uri": URI},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0]["result"]

    def signature(self, line, character):
        mid = self.c.request("textDocument/signatureHelp", {
            "textDocument": {"uri": URI},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0]["result"]

    def completion(self, line, character):
        mid = self.c.request("textDocument/completion", {
            "textDocument": {"uri": URI},
            "position": {"line": line, "character": character},
        })
        return self.c.wait_for(mid=mid)[0]["result"]

    def test_hover_shows_type_parameters_and_positions(self):
        self.open(GENERIC_DOC)
        id_hover = self.hover(0, 4)["contents"]["value"]
        self.assertIn("fn id<T>(x: T) -> T", id_hover)
        self.assertIn("generic function `id`", id_hover)
        self.assertIn("`T` (position 0)", id_hover)

        pair_hover = self.hover(1, 4)["contents"]["value"]
        self.assertIn("fn pair<A, B>(a: A, b: B) -> A", pair_hover)
        self.assertIn("`A` (position 0)", pair_hover)
        self.assertIn("`B` (position 1)", pair_hover)

    def test_signature_help_generic_function(self):
        declarations = "\n".join(GENERIC_DOC.split("\n")[:2]) + "\n"
        self.open(declarations + "print pair(\n")
        sig = self.signature(2, len("print pair("))
        self.assertIsNotNone(sig)
        info = sig["signatures"][0]
        self.assertEqual(info["label"], "pair<A, B>(a: A, b: B) -> A")
        self.assertEqual([p["label"] for p in info["parameters"]],
                         ["a: A", "b: B"])
        self.assertIn("type parameter `A`",
                      info["parameters"][0]["documentation"])
        self.assertIn("`A` (position 0)", info["documentation"]["value"])
        self.assertIn("`B` (position 1)", info["documentation"]["value"])
        self.assertEqual(sig["activeParameter"], 0)

        self.c.notify("textDocument/didChange", {
            "textDocument": {"uri": URI, "version": 2},
            "contentChanges": [{"text": declarations + "print pair(1, \n"}],
        })
        self.c.wait_for(method="textDocument/publishDiagnostics")
        sig = self.signature(2, len("print pair(1, "))
        self.assertEqual(sig["activeParameter"], 1)

    def test_signature_help_explicit_type_arguments(self):
        # The core parser does not accept `id<Int>(...)` yet (it reads it as
        # comparisons), but the LSP can still recover the explicit arguments
        # once the base name resolves to a generic declaration.
        self.open("fn id<T>(x: T) -> T { return x }\nprint id<Int>(\n")
        sig = self.signature(1, len("print id<Int>("))
        self.assertIsNotNone(sig)
        info = sig["signatures"][0]
        self.assertEqual(info["label"], "id<Int>(x: Int) -> Int")
        self.assertEqual(info["parameters"][0]["label"], "x: Int")
        self.assertEqual(sig["activeParameter"], 0)

    def test_completion_after_generic_name(self):
        self.open("fn id<T>(x: T) -> T { return x }\nprint id<\n")
        res = self.completion(1, len("print id<"))
        labels = {it["label"]: it for it in res["items"]}
        self.assertIn("T", labels)
        self.assertEqual(labels["T"]["kind"], 25)  # KIND_TYPE
        self.assertIn("type parameter of id", labels["T"]["detail"])
        self.assertIn("Int", labels)
        self.assertIn("Bool", labels)


class LspGenericUnitTests(unittest.TestCase):
    def setUp(self):
        import gama_lsp
        self.L = gama_lsp

    def test_completion_offers_enclosing_type_parameters(self):
        text = ("fn id<T>(x: T) -> T { return x }\n"
                "fn caller<U>(u: U) -> U { return id<\n")
        res = self.L.completion(URI, text, 1,
                                len("fn caller<U>(u: U) -> U { return id<"))
        items = {it["label"]: it for it in res["items"]}
        self.assertIn("U", items)
        self.assertEqual(items["U"]["detail"], "type parameter in scope")
        self.assertEqual(items["U"]["kind"], 25)
        # The callee's own parameter is still shown for context.
        self.assertIn("T", items)
        self.assertEqual(items["T"]["detail"], "type parameter of id")

    def test_completion_does_not_misfire_on_comparison(self):
        text = ("fn id<T>(x: T) -> T { return x }\n"
                "let x = a < \n")
        res = self.L.completion(URI, text, 1, len("let x = a < "))
        details = [it.get("detail") for it in res["items"]]
        self.assertNotIn("type parameter of id", details)
        # Normal keyword/type completions are preserved.
        labels = {it["label"] for it in res["items"]}
        self.assertIn("Int", labels)
        self.assertIn("let", labels)

    def test_hover_tolerates_incomplete_explicit_call(self):
        text = "fn id<T>(x: T) -> T { return x }\nprint id<Int>(\n"
        hover = self.L.hover(URI, text, 0, 4)
        self.assertIsNotNone(hover)
        self.assertIn("fn id<T>(x: T) -> T", hover["contents"]["value"])
        self.assertIn("Type parameters", hover["contents"]["value"])

    def test_completion_type_parameter_declaration_only_builtins(self):
        res = self.L.completion(URI, "fn pick<\n", 0, len("fn pick<"))
        labels = {it["label"] for it in res["items"]}
        self.assertIn("Int", labels)
        self.assertIn("Any", labels)
        self.assertFalse([it for it in res["items"]
                          if "type parameter of" in (it.get("detail") or "")])

    def test_semantic_tokens_mark_type_parameters(self):
        data = self.L.semantic_tokens(URI, GENERIC_DOC)["data"]
        self.assertEqual(len(data) % 5, 0)
        lines = GENERIC_DOC.split("\n")
        decoded = []
        line = 0
        start = 0
        for i in range(0, len(data), 5):
            delta_line, delta_start, length, ttype, _ = data[i:i + 5]
            line += delta_line
            start = start + delta_start if delta_line == 0 else delta_start
            decoded.append((lines[line][start:start + length], ttype))
        self.assertIn(("T", 1), decoded)   # token type 1 == `type`
        self.assertIn(("A", 1), decoded)
        self.assertIn(("B", 1), decoded)

    def test_semantic_tokens_and_diagnostics_explicit_call_do_not_crash(self):
        text = "fn id<T>(x: T) -> T { return x }\nprint id<Int>(1)\n"
        tokens = self.L.semantic_tokens(URI, text)
        self.assertIsInstance(tokens["data"], list)
        self.assertEqual(len(tokens["data"]) % 5, 0)
        # The core parser reads `id<Int>(1)` as comparisons, so the checker
        # reports `unknown variable Int`. That is a documented parser
        # limitation, not an LSP crash; diagnostics must stay structured.
        diagnostics = self.L.diagnostics(URI, text)
        self.assertIsInstance(diagnostics, list)
        for diag in diagnostics:
            self.assertEqual(diag["source"], "gama-l")
            self.assertIn("range", diag)

    def test_generic_declarations_diagnose_cleanly(self):
        self.assertEqual(self.L.diagnostics(URI, GENERIC_DOC), [])


if __name__ == "__main__":
    unittest.main()
