import json
import os
import pathlib
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gama_ai

ENV_NAMES = (
    "GAMA_L_AI_URL",
    "GAMA_L_AI_KEY",
    "GAMA_L_AI_MODEL",
    "GAMA_L_AI_ALLOW_HTTP",
)

OK_BODY = json.dumps(
    {
        "choices": [
            {"message": {"role": "assistant", "content": "zdravo"}, "text": "ignored"}
        ]
    }
).encode("utf-8")


class ChatHandler(BaseHTTPRequestHandler):
    """Scripted chat endpoint: pops one ``(status, body)`` per request."""

    responses = []
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        type(self).requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": raw}
        )
        status, body = (
            type(self).responses.pop(0)
            if type(self).responses
            else (200, OK_BODY)
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(handler_cls):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


class AiTests(unittest.TestCase):
    def setUp(self):
        self._clean_env()
        ChatHandler.responses = []
        ChatHandler.requests = []
        self._backoff = gama_ai.RETRY_BACKOFF
        gama_ai.RETRY_BACKOFF = (0.0, 0.0)
        self.addCleanup(self._restore)
        self.httpd, self.thread = _start(ChatHandler)
        self.addCleanup(_stop, self.httpd, self.thread)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def _restore(self):
        gama_ai.RETRY_BACKOFF = self._backoff
        self._clean_env()

    def _clean_env(self):
        for name in ENV_NAMES:
            os.environ.pop(name, None)

    def _configure(self, **extra):
        os.environ["GAMA_L_AI_URL"] = self.base + "/v1"
        os.environ["GAMA_L_AI_ALLOW_HTTP"] = "1"
        for key, value in extra.items():
            os.environ[key] = value

    def test_runtime_ask_uses_connector(self):
        import gamal2 as G
        self._configure()
        it = G.run('print ask("Cao")\n')
        self.assertEqual(it.out, ["zdravo"])
        self.assertEqual(len(ChatHandler.requests), 1)

    def test_successful_answer_and_request_shape(self):
        self._configure(GAMA_L_AI_KEY="secret", GAMA_L_AI_MODEL="my-model")
        self.assertEqual(gama_ai.ask("Cao", system="Ti si asistent"), "zdravo")

        self.assertEqual(len(ChatHandler.requests), 1)
        sent = ChatHandler.requests[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["headers"].get("Authorization"), "Bearer secret")
        body = json.loads(sent["body"].decode("utf-8"))
        self.assertEqual(body["model"], "my-model")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(
            body["messages"],
            [
                {"role": "system", "content": "Ti si asistent"},
                {"role": "user", "content": "Cao"},
            ],
        )

    def test_default_model_and_no_system_or_key(self):
        self._configure()
        self.assertEqual(gama_ai.ask("Cao"), "zdravo")
        sent = ChatHandler.requests[0]
        self.assertIsNone(sent["headers"].get("Authorization"))
        body = json.loads(sent["body"].decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Cao"}])

    def test_retry_after_500_then_success(self):
        self._configure()
        ChatHandler.responses = [(500, b"{}"), (200, OK_BODY)]
        self.assertEqual(gama_ai.ask("Cao"), "zdravo")
        self.assertEqual(len(ChatHandler.requests), 2)

    def test_retry_exhausted_raises(self):
        self._configure()
        ChatHandler.responses = [(503, b"{}"), (503, b"{}"), (503, b"{}")]
        with self.assertRaises(gama_ai.AIError) as cm:
            gama_ai.ask("Cao")
        self.assertIn("503", str(cm.exception))
        self.assertEqual(len(ChatHandler.requests), 3)

    def test_401_is_not_retried(self):
        self._configure()
        ChatHandler.responses = [(401, b"{}")]
        with self.assertRaises(gama_ai.AIError) as cm:
            gama_ai.ask("Cao")
        self.assertIn("401", str(cm.exception))
        self.assertEqual(len(ChatHandler.requests), 1)

    def test_response_without_choices_raises(self):
        self._configure()
        ChatHandler.responses = [(200, json.dumps({"choices": []}).encode("utf-8"))]
        with self.assertRaises(gama_ai.AIError):
            gama_ai.ask("Cao")

    def test_plain_http_requires_opt_in(self):
        os.environ["GAMA_L_AI_URL"] = self.base + "/v1"
        with self.assertRaises(gama_ai.AIError):
            gama_ai.ask("Cao")
        self.assertEqual(ChatHandler.requests, [])

    def test_oversized_response_raises(self):
        self._configure()
        ChatHandler.responses = [(200, b"x" * (gama_ai.MAX_RESPONSE_BYTES + 1))]
        with self.assertRaises(gama_ai.AIError):
            gama_ai.ask("Cao")

    def test_missing_url_raises(self):
        with self.assertRaises(gama_ai.AIError):
            gama_ai.ask("Cao")

    def test_unsupported_scheme_raises(self):
        os.environ["GAMA_L_AI_URL"] = "ftp://example.test/v1"
        os.environ["GAMA_L_AI_ALLOW_HTTP"] = "1"
        with self.assertRaises(gama_ai.AIError):
            gama_ai.ask("Cao")


if __name__ == "__main__":
    unittest.main()
