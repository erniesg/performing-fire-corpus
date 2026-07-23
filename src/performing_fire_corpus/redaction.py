"""Centralized sanitization for durable records and diagnostic output."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PARTS = (
    "account",
    "authorization",
    "cookie",
    "credential",
    "owner_id",
    "response_body",
    "secret",
    "set-cookie",
    "tenant_id",
    "token",
    "user_id",
)
_BODY_KEYS = {"body", "content", "html", "media", "raw_body", "response"}
_SIGNED_QUERY_KEYS = {
    "access_token",
    "api_key",
    "awsaccesskeyid",
    "credential",
    "key",
    "signature",
    "sig",
    "token",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
    "x-goog-credential",
    "x-goog-signature",
}
_SECRET_ENV_KEY = re.compile(
    r"(?:api[_-]?key|auth|cookie|credential|password|secret|session|token)",
    re.IGNORECASE,
)
_LOCAL_PATH = re.compile(
    r"(?:file://)?(?:/home/|/Users/|/tmp/)[^\s\"']+"
    r"|(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"']+"
)


def _sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace(" ", "_")
    return normalized in _BODY_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def _environment_secrets(environ: Mapping[str, str] | None) -> tuple[str, ...]:
    source = os.environ if environ is None else environ
    return tuple(
        value
        for key, value in source.items()
        if _SECRET_ENV_KEY.search(key) and isinstance(value, str) and len(value) >= 4
    )


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return value
    changed = False
    cleaned_query: list[tuple[str, str]] = []
    for key, child in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in _SIGNED_QUERY_KEYS:
            child = REDACTED
            changed = True
        cleaned_query.append((key, child))
    if not changed:
        return value
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(cleaned_query), parsed.fragment)
    )


def _sanitize_text(value: str, environment_secrets: tuple[str, ...]) -> str:
    cleaned = _sanitize_url(value)
    for secret in environment_secrets:
        cleaned = cleaned.replace(secret, REDACTED)
    return _LOCAL_PATH.sub(REDACTED, cleaned)


def sanitize(
    value: Any, *, environ: Mapping[str, str] | None = None
) -> Any:
    """Return a JSON-compatible copy with private or secret-like data removed."""

    environment_secrets = _environment_secrets(environ)

    def clean(child: Any, *, key: object | None = None) -> Any:
        if key is not None and _sensitive_key(key):
            return REDACTED
        if isinstance(child, BaseException):
            return _sanitize_text(str(child), environment_secrets)
        if isinstance(child, (bytes, bytearray, memoryview)):
            return REDACTED
        if isinstance(child, str):
            return _sanitize_text(child, environment_secrets)
        if isinstance(child, Mapping):
            return {
                str(nested_key): clean(nested_value, key=nested_key)
                for nested_key, nested_value in child.items()
            }
        if isinstance(child, (list, tuple, set, frozenset)):
            return [clean(item) for item in child]
        if child is None or isinstance(child, (bool, int, float)):
            return child
        return _sanitize_text(str(child), environment_secrets)

    return clean(value)
