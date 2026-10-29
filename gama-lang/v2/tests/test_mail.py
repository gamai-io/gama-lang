import os
import pathlib
import smtplib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gama_mail

ENV_NAMES = (
    "GAMA_L_MAIL_ENABLED",
    "GAMA_L_SMTP_HOST",
    "GAMA_L_SMTP_FROM",
    "GAMA_L_SMTP_PORT",
    "GAMA_L_SMTP_USER",
    "GAMA_L_SMTP_PASS",
    "GAMA_L_SMTP_TLS",
)


class FakeSMTP:
    """Records the SMTP exchange; never touches the network."""

    instances = []
    fail_sendmail = False

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def sendmail(self, sender, recipients, message):
        if FakeSMTP.fail_sendmail:
            raise smtplib.SMTPException("boom")
        self.calls.append("sendmail")
        self.sent.append((sender, recipients, message))

    def quit(self):
        self.calls.append("quit")


class MailTests(unittest.TestCase):
    def setUp(self):
        self._clean_env()
        FakeSMTP.instances = []
        FakeSMTP.fail_sendmail = False
        self._patcher = mock.patch.object(gama_mail.smtplib, "SMTP", FakeSMTP)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(self._clean_env)

    def _clean_env(self):
        for name in ENV_NAMES:
            os.environ.pop(name, None)

    def _enable(self, **extra):
        os.environ["GAMA_L_MAIL_ENABLED"] = "1"
        os.environ["GAMA_L_SMTP_HOST"] = "smtp.example.test"
        os.environ["GAMA_L_SMTP_FROM"] = "bot@example.test"
        for key, value in extra.items():
            os.environ[key] = value

    def test_runtime_uses_connector(self):
        import gamal2 as G
        self._enable()
        it = G.run('print mail("a@example.test", "Hi", "Body")\n')
        self.assertEqual(it.out, ["True"])
        self.assertEqual(len(FakeSMTP.instances), 1)
        self.assertIn("sendmail", FakeSMTP.instances[0].calls)

    def test_runtime_disabled(self):
        import gamal2 as G
        with self.assertRaises(G.GamaRuntimeError):
            G.run('print mail("a@example.test", "Hi", "Body")\n')

    def test_disabled_raises(self):
        with self.assertRaises(gama_mail.MailError):
            gama_mail.send_mail("a@example.test", "Hi", "Body")
        self.assertEqual(FakeSMTP.instances, [])

    def test_missing_host_raises(self):
        os.environ["GAMA_L_MAIL_ENABLED"] = "1"
        os.environ["GAMA_L_SMTP_FROM"] = "bot@example.test"
        with self.assertRaises(gama_mail.MailError):
            gama_mail.send_mail("a@example.test", "Hi", "Body")
        self.assertEqual(FakeSMTP.instances, [])

    def test_missing_from_raises(self):
        os.environ["GAMA_L_MAIL_ENABLED"] = "1"
        os.environ["GAMA_L_SMTP_HOST"] = "smtp.example.test"
        with self.assertRaises(gama_mail.MailError):
            gama_mail.send_mail("a@example.test", "Hi", "Body")
        self.assertEqual(FakeSMTP.instances, [])

    def test_successful_send(self):
        self._enable(GAMA_L_SMTP_USER="user", GAMA_L_SMTP_PASS="secret")
        result = gama_mail.send_mail("a@example.test", "Hello", "Body text")
        self.assertTrue(result)

        self.assertEqual(len(FakeSMTP.instances), 1)
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.host, "smtp.example.test")
        self.assertEqual(smtp.port, 587)
        self.assertEqual(smtp.timeout, 20)
        self.assertEqual(
            smtp.calls,
            ["ehlo", "starttls", ("login", "user", "secret"), "sendmail", "quit"],
        )

        self.assertEqual(len(smtp.sent), 1)
        sender, recipients, message = smtp.sent[0]
        self.assertEqual(sender, "bot@example.test")
        self.assertEqual(recipients, ["a@example.test"])
        self.assertIn("Subject: Hello", message)
        self.assertIn("Body text", message)

    def test_no_tls_or_login_when_port_not_587(self):
        self._enable(GAMA_L_SMTP_PORT="25")
        self.assertTrue(gama_mail.send_mail("a@example.test", "Hi", "Body"))
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.port, 25)
        self.assertEqual(smtp.calls, ["ehlo", "sendmail", "quit"])

    def test_header_injection_rejected_before_smtp(self):
        self._enable()
        cases = (
            ("to", "a@example.test\r\nBcc: evil@example.test"),
            ("to", "a@example.test\n"),
            ("subject", "Hello\r\nBcc: evil@example.test"),
            ("subject", "Hello\nBcc: evil@example.test"),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                with self.assertRaises(gama_mail.MailError):
                    if field == "to":
                        gama_mail.send_mail(value, "Hi", "Body")
                    else:
                        gama_mail.send_mail("a@example.test", value, "Body")
        self.assertEqual(FakeSMTP.instances, [])

    def test_smtp_failure_wrapped(self):
        self._enable()
        FakeSMTP.fail_sendmail = True
        with self.assertRaises(gama_mail.MailError):
            gama_mail.send_mail("a@example.test", "Hi", "Body")
        self.assertEqual(FakeSMTP.instances[0].calls, ["ehlo", "starttls", "quit"])


if __name__ == "__main__":
    unittest.main()
