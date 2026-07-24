"""Fail-closed acquisition URL and rights policy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from performing_fire_corpus.redaction import sanitize


PUBLIC_SOURCE_HOSTS = frozenset(
    {
        "antiegg.kr",
        "njp.ggcf.kr",
        "njpvideo.ggcf.kr",
        "www.youtube.com",
    }
)
_SIGNED_QUERY_KEYS = frozenset(
    {
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
)
_CREDENTIAL_QUERY_NAMES = frozenset(
    re.sub(r"[^a-z0-9]", "", value.lower())
    for value in (
        *_SIGNED_QUERY_KEYS,
        "auth",
        "authorization",
        "client_secret",
        "cookie",
        "jsessionid",
        "password",
        "refresh_token",
        "session",
    )
)
_CREDENTIAL_QUERY_WORDS = frozenset(
    {
        "auth",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "session",
        "token",
    }
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ENCODED_CONTROL = re.compile(
    r"%(?:0[0-9a-f]|1[0-9a-f]|2f|3b|5c|7f)",
    re.IGNORECASE,
)


class AcquisitionPolicyError(RuntimeError):
    """A safe, durable acquisition-policy failure."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = str(sanitize(reason))
        super().__init__(f"{code}: {self.reason}")


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    hostname: str
    port: int


def _reject(code: str, reason: str) -> None:
    raise AcquisitionPolicyError(code, reason)


def _credential_query_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    words = {
        word.lower()
        for word in re.split(
            r"[^A-Za-z0-9]+",
            _CAMEL_BOUNDARY.sub(" ", value),
        )
        if word
    }
    lowered_words = {word.lower() for word in words}
    return (
        normalized in _CREDENTIAL_QUERY_NAMES
        or "apikey" in normalized
        or "accesstoken" in normalized
        or "sessionid" in normalized
        or (
            "signature" in normalized
            and normalized != "signaturestyle"
        )
        or bool(lowered_words & _CREDENTIAL_QUERY_WORDS)
        or {"api", "key"}.issubset(lowered_words)
        or (
            "signature" in lowered_words
            and bool(
                lowered_words
                & {
                    "hmac",
                    "request",
                    "signed",
                    "url",
                }
            )
        )
    )


def _credential_path_parameter(path: str) -> bool:
    for segment in path.split("/"):
        for parameter in segment.split(";")[1:]:
            if _credential_query_key(parameter.split("=", 1)[0]):
                return True
    return False


def validate_public_url(
    url: str, *, allowed_hosts: frozenset[str] = PUBLIC_SOURCE_HOSTS
) -> ValidatedURL:
    """Validate a URL without DNS or network access."""

    if not isinstance(url, str) or not url or len(url) > 2048:
        _reject("invalid_url", "URL is missing or exceeds the policy limit.")
    if any(character.isspace() or ord(character) < 32 for character in url):
        _reject("invalid_url", "URL contains whitespace or control characters.")
    if "\\" in url or _ENCODED_CONTROL.search(url):
        _reject("ambiguous_url", "URL contains an ambiguous path or delimiter.")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        explicit_port = parsed.port
    except (TypeError, ValueError):
        _reject("invalid_url", "URL could not be parsed safely.")
    if parsed.scheme.lower() != "https":
        _reject("https_required", "Only HTTPS source URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        _reject("userinfo_forbidden", "URL user information is forbidden.")
    if parsed.fragment:
        _reject("fragment_forbidden", "URL fragments are not acquisition inputs.")
    if not hostname or not parsed.netloc:
        _reject("invalid_url", "URL has no unambiguous hostname.")
    try:
        ascii_hostname = hostname.encode("ascii").decode("ascii").lower()
    except UnicodeError:
        _reject("ambiguous_hostname", "Hostname must use its reviewed ASCII form.")
    if ascii_hostname != hostname.lower() or ascii_hostname.endswith("."):
        _reject("ambiguous_hostname", "Hostname is not in reviewed canonical form.")
    try:
        address = ip_address(ascii_hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        _reject("non_public_host", "Hostname is not a public network destination.")
    if ascii_hostname not in allowed_hosts:
        _reject("host_not_allowed", "Hostname is not in the reviewed public allowlist.")
    if explicit_port not in (None, 443):
        _reject("port_not_allowed", "Only the default HTTPS port is allowed.")
    if _credential_path_parameter(parsed.path):
        _reject(
            "signed_path_forbidden",
            "Credential-like path parameters are forbidden.",
        )
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        _reject("invalid_query", "URL query could not be parsed safely.")
    if any(_credential_query_key(key) for key, _ in query):
        _reject("signed_query_forbidden", "Credential-like query parameters are forbidden.")
    normalized_netloc = ascii_hostname if explicit_port is None else f"{ascii_hostname}:443"
    normalized = urlunsplit(
        ("https", normalized_netloc, parsed.path or "/", parsed.query, "")
    )
    return ValidatedURL(normalized, ascii_hostname, 443)


def validate_redirect(source_url: str, target_url: str) -> ValidatedURL:
    """Revalidate both sides of a redirect before it may be followed."""

    validate_public_url(source_url)
    return validate_public_url(target_url)


def require_transfer_rights(
    asset_id: str, rights: Mapping[str, Any] | None
) -> None:
    """Require a complete matching approval before any content transfer."""

    allowed_fields = {
        "schema_version",
        "record_type",
        "rights_id",
        "asset_id",
        "state",
        "decision_reason",
        "decision_at",
    }
    timestamp_valid = False
    if isinstance(rights, Mapping) and isinstance(rights.get("decision_at"), str):
        try:
            timestamp = datetime.fromisoformat(
                str(rights["decision_at"]).replace("Z", "+00:00")
            )
            timestamp_valid = (
                str(rights["decision_at"]).endswith("Z")
                and timestamp.tzinfo is not None
            )
        except ValueError:
            pass
    complete = (
        isinstance(rights, Mapping)
        and set(rights) == allowed_fields
        and rights.get("schema_version") == 1
        and rights.get("record_type") == "rights"
        and isinstance(rights.get("rights_id"), str)
        and bool(re.fullmatch(r"rights_[a-z0-9][a-z0-9._-]{0,127}", rights["rights_id"]))
        and rights.get("asset_id") == asset_id
        and rights.get("state") == "approved"
        and isinstance(rights.get("decision_reason"), str)
        and bool(str(rights["decision_reason"]).strip())
        and sanitize(rights["decision_reason"], environ={}) == rights["decision_reason"]
        and timestamp_valid
    )
    if not complete:
        _reject(
            "rights_not_approved",
            "Content transfer is blocked until a complete matching approval exists.",
        )
