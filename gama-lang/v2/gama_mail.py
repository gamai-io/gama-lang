#!/usr/bin/env python3
"""Strictly guarded SMTP mail connector for GAMA-L 2 (new, standalone module).

Design policy:
  * Opt-in only: nothing is sent unless ``GAMA_L_MAIL_ENABLED=1``.
  * Configuration comes exclusively from ``GAMA_L_SMTP_*`` environment
    variables; no credentials are stored in code.
  * Header injection is rejected before any socket is opened: ``\\r`` and
    ``\\n`` are not allowed in the ``to`` address or the subject.
  * All SMTP/OS failures are surfaced as :class:`MailError` so callers (LSP,
    CLI, agents) never have to know smtplib internals.

This module performs no network I/O at import time.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

__all__ = ["MailError", "send_mail"]

ENABLED_ENV = "GAMA_L_MAIL_ENABLED"
HOST_ENV = "GAMA_L_SMTP_HOST"
FROM_ENV = "GAMA_L_SMTP_FROM"
PORT_ENV = "GAMA_L_SMTP_PORT"
USER_ENV = "GAMA_L_SMTP_USER"
PASS_ENV = "GAMA_L_SMTP_PASS"
TLS_ENV = "GAMA_L_SMTP_TLS"

DEFAULT_PORT = 587
TIMEOUT = 20


class MailError(Exception):
    """Raised when a mail cannot be safely configured or sent."""


def _where(line: int, col: int) -> str:
    """Human-readable source position suffix for error messages."""
    if line:
        return " at line %d:%d" % (line, col)
    return ""


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _required(name: str, line: int, col: int) -> str:
    value = _env(name).strip()
    if not value:
        raise MailError("%s is not set%s" % (name, _where(line, col)))
    return value


def _port(line: int, col: int) -> int:
    raw = _env(PORT_ENV).strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise MailError(
            "%s must be an integer, got %r%s" % (PORT_ENV, raw, _where(line, col))
        )
    if not 1 <= port <= 65535:
        raise MailError(
            "%s out of range: %d%s" % (PORT_ENV, port, _where(line, col))
        )
    return port


def _reject_header_injection(name: str, value: str, line: int, col: int) -> None:
    if "\r" in value or "\n" in value:
        raise MailError(
            "header injection rejected in %s%s" % (name, _where(line, col))
        )


def send_mail(to: str, subject: str, body: str, line: int = 0, col: int = 0) -> bool:
    """Send a single plain-text mail through SMTP.

    Returns ``True`` on success. Raises :class:`MailError` when mail is
    disabled, misconfigured, rejected for header injection, or when the SMTP
    exchange fails. Never raises ``smtplib`` exceptions to the caller.
    """
    if _env(ENABLED_ENV) != "1":
        raise MailError(
            "mail disabled: set %s=1 to enable%s" % (ENABLED_ENV, _where(line, col))
        )

    host = _required(HOST_ENV, line, col)
    sender = _required(FROM_ENV, line, col)
    port = _port(line, col)

    _reject_header_injection("to", to, line, col)
    _reject_header_injection("subject", subject, line, col)

    user = _env(USER_ENV).strip()
    password = _env(PASS_ENV)
    use_tls = port == DEFAULT_PORT or _env(TLS_ENV).strip() == "1"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.set_content(body)

    smtp = None
    try:
        smtp = smtplib.SMTP(host, port, timeout=TIMEOUT)
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.sendmail(sender, [to], message.as_string())
        return True
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError("SMTP failure: %s%s" % (exc, _where(line, col))) from exc
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass
