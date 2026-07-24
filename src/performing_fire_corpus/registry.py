from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.policy import (
    PUBLIC_SOURCE_HOSTS,
    AcquisitionPolicyError,
    validate_public_url,
)


_SOURCE_ID = re.compile(r"^[a-z]+(?:-[a-z]+)*$")
_REGISTRY_LOCATOR_HOSTS = PUBLIC_SOURCE_HOSTS | frozenset(
    {"www.googleapis.com"}
)
_UNSAFE_IDENTIFIER = re.compile(
    r"(?:[/?#:@=\\]|(?:^|[-_\s])\d+(?:$|[-_\s])|/Users/|/home/|/tmp/)",
    re.IGNORECASE,
)


class RegistryError(ValueError):
    """Raised when a source registry violates the portable public contract."""


class UnknownSourceError(RegistryError):
    """Raised when work targets a source absent from the reviewed registry."""


def normalize_source_id(value: str) -> str:
    candidate = value.strip()
    if not candidate or _UNSAFE_IDENTIFIER.search(candidate):
        raise RegistryError("source identifier contains an unsafe or unstable value")
    normalized = re.sub(r"[\s_]+", "-", candidate.lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not _SOURCE_ID.fullmatch(normalized):
        raise RegistryError("source identifier must use stable lowercase semantic words")
    return normalized


def canonicalize_public_url(value: str) -> str:
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as error:
        raise RegistryError("public locator is malformed") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise RegistryError(
            "public locator must be credential-free canonical HTTPS without query or fragment"
        )
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise RegistryError("public locator hostname is invalid") from error
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        raise RegistryError("public locator must use a public hostname")
    decoded_segments = unquote(parsed.path).split("/")
    if any(segment in (".", "..") for segment in decoded_segments):
        raise RegistryError("public locator path traversal is prohibited")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    normalized = urlunsplit(("https", host, path, "", ""))
    try:
        return validate_public_url(
            normalized, allowed_hosts=_REGISTRY_LOCATOR_HOSTS
        ).url
    except AcquisitionPolicyError as error:
        raise RegistryError("public locator violates acquisition host policy") from error


def _registry_schema() -> dict[str, Any]:
    schema_path = files("performing_fire_corpus").joinpath(
        "schemas", "v1", "source-registry.json"
    )
    if not schema_path.is_file():
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "schemas"
            / "v1"
            / "source-registry.json"
        )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_registry(value: dict[str, Any]) -> dict[str, Any]:
    try:
        Draft202012Validator(
            _registry_schema(), format_checker=FormatChecker()
        ).validate(value)
    except (ValidationError, TypeError, ValueError) as error:
        raise RegistryError("source registry does not match the strict schema") from error

    sources = value["sources"]
    source_ids = [item["source_id"] for item in sources]
    if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
        raise RegistryError("source records must have unique sorted stable identifiers")

    claimed_names: dict[str, str] = {}
    endpoint_ids: set[str] = set()
    for source in sources:
        source_id = source["source_id"]
        if normalize_source_id(source_id) != source_id:
            raise RegistryError("source identifier is not canonical")
        for name in (source_id, *source["aliases"]):
            normalized_name = normalize_source_id(name)
            owner = claimed_names.get(normalized_name)
            if owner is not None and owner != source_id:
                raise RegistryError("source identifier or alias collision")
            claimed_names[normalized_name] = source_id
        if source["aliases"] != sorted(source["aliases"]):
            raise RegistryError("source aliases must be sorted")

        canonical_url = source["canonical_url"]
        if (
            canonical_url is not None
            and canonicalize_public_url(canonical_url) != canonical_url
        ):
            raise RegistryError("source canonical URL is not normalized")

        endpoints = source["endpoints"]
        ordered_endpoint_ids = [item["endpoint_id"] for item in endpoints]
        if ordered_endpoint_ids != sorted(ordered_endpoint_ids):
            raise RegistryError("source endpoints must be sorted by endpoint identifier")
        for endpoint in endpoints:
            endpoint_id = endpoint["endpoint_id"]
            if endpoint_id in endpoint_ids:
                raise RegistryError("endpoint identifier collision")
            endpoint_ids.add(endpoint_id)
            if canonicalize_public_url(endpoint["public_url"]) != endpoint["public_url"]:
                raise RegistryError("endpoint URL is not normalized")
            if (
                "platform_identifier" in endpoint
                and endpoint["verification_state"] != "confirmed"
            ):
                raise RegistryError(
                    "platform identifiers require bounded identity confirmation"
                )

        if source["source_class"] == "project_native" and (
            canonical_url is not None
            or source["aliases"]
            or endpoints
            or source["host_policy_id"] != "project-native-private"
        ):
            raise RegistryError(
                "project-native families must remain empty private-data contracts"
            )

    return value


def load_registry(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RegistryError("source registry could not be loaded") from error
    if not isinstance(value, dict):
        raise RegistryError("source registry root must be an object")
    return validate_registry(value)


def canonical_registry_bytes(value: dict[str, Any]) -> bytes:
    validate_registry(value)
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def require_source(registry: dict[str, Any], source_id: str) -> dict[str, Any]:
    normalized = normalize_source_id(source_id)
    if normalized != source_id:
        raise UnknownSourceError("source identifier must match the reviewed stable ID")
    for source in validate_registry(registry)["sources"]:
        if source["source_id"] == normalized:
            return source
    raise UnknownSourceError("source is absent from the reviewed registry")


def validate_registry_migration(
    previous: dict[str, Any], candidate: dict[str, Any]
) -> None:
    previous_sources = {
        item["source_id"]: item for item in validate_registry(previous)["sources"]
    }
    candidate_sources = {
        item["source_id"]: item for item in validate_registry(candidate)["sources"]
    }
    removed_sources = set(previous_sources) - set(candidate_sources)
    if removed_sources:
        raise RegistryError("reviewed source identifiers cannot be removed or rewritten")

    previous_alias_owners = {
        alias: source_id
        for source_id, source in previous_sources.items()
        for alias in source["aliases"]
    }
    candidate_alias_owners = {
        alias: source_id
        for source_id, source in candidate_sources.items()
        for alias in source["aliases"]
    }
    if any(
        candidate_alias_owners.get(alias) != owner
        for alias, owner in previous_alias_owners.items()
    ):
        raise RegistryError("reviewed source aliases cannot be removed or rebound")

    for source_id, earlier in previous_sources.items():
        later = candidate_sources[source_id]
        if (
            earlier["source_class"] != later["source_class"]
            or earlier["host_policy_id"] != later["host_policy_id"]
            or earlier["canonical_url"] != later["canonical_url"]
        ):
            raise RegistryError("reviewed source identity semantics cannot change")
        earlier_endpoints = {
            item["endpoint_id"]: item for item in earlier["endpoints"]
        }
        later_endpoints = {
            item["endpoint_id"]: item for item in later["endpoints"]
        }
        if not set(earlier_endpoints).issubset(later_endpoints):
            raise RegistryError("reviewed endpoint identifiers cannot be removed")
        for endpoint_id, earlier_endpoint in earlier_endpoints.items():
            later_endpoint = later_endpoints[endpoint_id]
            if (
                earlier_endpoint["endpoint_kind"]
                != later_endpoint["endpoint_kind"]
                or earlier_endpoint["public_url"] != later_endpoint["public_url"]
                or (
                    "platform_identifier" in earlier_endpoint
                    and earlier_endpoint["platform_identifier"]
                    != later_endpoint.get("platform_identifier")
                )
            ):
                raise RegistryError(
                    "reviewed endpoint identifiers cannot be silently rebound"
                )
