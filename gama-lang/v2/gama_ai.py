#!/usr/bin/env python3
"""Minimal, guarded OpenAI-compatible chat connector for GAMA-L 2.

This is a standalone module: it does not modify or import the runtime
(``gamal2``) and performs no network I/O at import time.

Configuration comes exclusively from the environment:

  ``GAMA_L_AI_URL``         base URL (``https://api.example/v1``) or a full
                            ``.../chat/completions`` endpoint (required).
  ``GAMA_L_AI_KEY``         bearer token; omitted when empty.
  ``GAMA_L_AI_MODEL``       model name; defaults to ``deepseek-chat``.
  ``GAMA_L_AI_ALLOW_HTTP``  set to ``1`` to permit plain ``http://`` URLs.

Policy:
  * A URL without a scheme gets ``https://`` prepended.
  * Non-https URLs are rejected unless ``GAMA_L_AI_ALLOW_HTTP=1``.
  * ``/chat/completions`` is appended when the URL does not already end with
    it.
  * Requests are ``temperature: 0`` and responses are capped at 1 MiB.
  * Transient HTTP statuses (429/500/502/503/504) are retried twice with a
    0.2 s / 0.5 s backoff; client errors (400/401/403) are never retried.
  * Every failure surfaces as :class:`AIError`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

__all__ = ["AIError", "ask", "MAX_RESPONSE_BYTES", "RETRY_BACKOFF", "RETRY_STATUS"]

URL_ENV = "GAMA_L_AI_URL"
KEY_ENV = "GAMA_L_AI_KEY"
MODEL_ENV = "GAMA_L_AI_MODEL"
ALLOW_HTTP_ENV = "GAMA_L_AI_ALLOW_HTTP"

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 30

MAX_RESPONSE_BYTES = 1024 * 1024
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
RETRY_BACKOFF = (0.2, 0.5)

ENDPOINT_SUFFIX = "/chat/completions"


class AIError(Exception):
    """Raised when an AI request cannot be configured or completed."""


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _endpoint() -> str:
    raw = _env(URL_ENV).strip()
    if not raw:
        raise AIError("%s is not set" % URL_ENV)

    if "://" not in raw:
        raw = "https://" + raw

    scheme = raw.split("://", 1)[0].lower()
    if scheme != "https":
        if scheme != "http":
            raise AIError("unsupported URL scheme %r in %s" % (scheme, URL_ENV))
        if _env(ALLOW_HTTP_ENV).strip() != "1":
            raise AIError(
                "refusing plain http URL: set %s=1 to allow it" % ALLOW_HTTP_ENV
            )

    if not raw.endswith(ENDPOINT_SUFFIX):
        raw = raw.rstrip("/") + ENDPOINT_SUFFIX
    return raw


def _payload(prompt: str, system: str | None, model: str) -> bytes:
    messages = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": messages, "temperature": 0}
    return json.dumps(body).encode("utf-8")


def _content(doc: object) -> str:
    if not isinstance(doc, dict):
        raise AIError("response is not a JSON object")
    choices = doc.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AIError("response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise AIError("response choice is not an object")

    message = first.get("message")
    if isinstance(message, dict):
        text = message.get("content")
        if isinstance(text, str):
            return text
    text = first.get("text")
    if isinstance(text, str):
        return text
    raise AIError("response has no message content")


def _parse(raw: bytes) -> str:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AIError("response larger than %d bytes" % MAX_RESPONSE_BYTES)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise AIError("invalid JSON response: %s" % ex) from None
    return _content(doc)


def _request(url: str, data: bytes, headers: dict, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as ex:
        ex.close()
        raise
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise AIError("request to %s failed: %s" % (url, ex)) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AIError("response larger than %d bytes" % MAX_RESPONSE_BYTES)
    return raw


def ask(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Send one chat request and return the assistant text.

    Raises :class:`AIError` on misconfiguration, transport failure, a
    non-retryable status, an exhausted retry budget, an oversized body, or a
    response without usable content.
    """
    url = _endpoint()
    if model is None:
        model = _env(MODEL_ENV).strip() or DEFAULT_MODEL

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    key = _env(KEY_ENV).strip()
    if key:
        headers["Authorization"] = "Bearer " + key

    data = _payload(prompt, system, model)
    attempts = len(RETRY_BACKOFF) + 1

    for attempt in range(attempts):
        try:
            raw = _request(url, data, headers, timeout)
            return _parse(raw)
        except urllib.error.HTTPError as ex:
            if ex.code in RETRY_STATUS and attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])
                continue
            raise AIError("HTTP %s %s from %s" % (ex.code, ex.reason, url)) from None

    raise AIError("retries exhausted for %s" % url)
