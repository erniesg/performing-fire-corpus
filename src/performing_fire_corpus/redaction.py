"""Centralized sanitization for durable records and diagnostic output."""

from __future__ import annotations

import base64
import binascii
import json
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
_EMAIL_VALUE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9.-])"
)
_ACCOUNT_IDENTIFIER_VALUE = re.compile(
    r"(?<![A-Za-z0-9])(?:acct|account|owner|tenant|user)[_-][A-Za-z0-9]{6,}"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_INDEX_TOKENISH_VALUE = (
    r"(?:"
    r"(?=[A-Za-z0-9._+/=-]{0,255}[0-9._+/=-])"
    r"[A-Za-z0-9._+/=-]{16,}|"
    r"(?=[A-Za-z0-9._-]{0,255}[A-Z][A-Za-z0-9._-]*[A-Z])"
    r"[A-Za-z0-9._-]{20,}|"
    r"([A-Za-z]{4,16})\1{1,}|"
    r"(?![A-Za-z]*([A-Za-z])(?:[A-Za-z]*\2){2})[A-Za-z]{24,40}"
    r")"
)
_INDEX_LABELED_CREDENTIAL = re.compile(
    r"\b(?i:"
    r"authorization +(?:(?:basic|bearer) +)?|"
    r"basic +|bearer +|credentials? +|"
    r"password +|token +|api +key +|"
    r"(?:aws +)?secret +access +key +|"
    r"client +secret +|github +token +|jwt +"
    r")"
    + _INDEX_TOKENISH_VALUE
    + r"(?![A-Za-z0-9._+/=-])"
)
_INDEX_RAW_CREDENTIAL = re.compile(
    r"\b(?:"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{20,}|"
    r"(?:xox[bcaprs]|xapp)-[A-Za-z0-9-]{20,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"AIza[A-Za-z0-9_-]{20,}"
    r")\b"
)
_INDEX_JWT_CANDIDATE = re.compile(
    r"\b([A-Za-z0-9_-]{2,256})\."
    r"([A-Za-z0-9_-]{2,4096})\."
    r"([A-Za-z0-9_-]{8,4096})\b"
)


def _json_object_segment(value: str) -> bool:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
        if not 2 <= len(decoded) <= 512:
            return False
        parsed = json.loads(decoded.decode("utf-8"))
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return False
    return isinstance(parsed, dict)


def contains_secret_like_text(value: str) -> bool:
    """Return whether portable index text resembles a credential.

    This is the authoritative v1 detector used by every index `safeText`
    schema format and runtime validator. Schemas without this format checker
    are structural checks only and are not admission authority.
    """

    if not isinstance(value, str):
        return True
    if (
        _INDEX_LABELED_CREDENTIAL.search(value)
        or _INDEX_RAW_CREDENTIAL.search(value)
    ):
        return True
    return any(
        _json_object_segment(match.group(1))
        for match in _INDEX_JWT_CANDIDATE.finditer(value)
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
    cleaned = _LOCAL_PATH.sub(REDACTED, cleaned)
    cleaned = _EMAIL_VALUE.sub(REDACTED, cleaned)
    return _ACCOUNT_IDENTIFIER_VALUE.sub(REDACTED, cleaned)


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
