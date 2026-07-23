"""Deterministic, offline discovery from checked-in synthetic fixtures."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from performing_fire_corpus.ledger import Ledger, LedgerError, validate_record
from performing_fire_corpus.policy import AcquisitionPolicyError, validate_public_url


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "tests" / "fixtures" / "discovery"
_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_LOCAL_ABSOLUTE_PATH = re.compile(r"^(?:/|[A-Za-z]:[\\/]|file://)")
_FORBIDDEN_FIELD_PARTS = frozenset(
    {
        "account",
        "article",
        "base64",
        "body",
        "caption",
        "content",
        "credential",
        "email",
        "embedding",
        "html",
        "media_encoding",
        "owner_id",
        "personal_information",
        "phone",
        "pii",
        "prose",
        "response",
        "token",
        "transcript",
        "user_id",
    }
)


class FixtureError(ValueError):
    """Raised when an offline fixture violates the synthetic-data contract."""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_fixture_safe(value: Any, *, field: str = "fixture") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise FixtureError(f"{field} contains forbidden field {key!r}")
            _assert_fixture_safe(child, field=f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_fixture_safe(child, field=f"{field}[{index}]")
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise FixtureError(f"{field} contains forbidden binary data")
    if isinstance(value, str) and _LOCAL_ABSOLUTE_PATH.search(value):
        raise FixtureError(f"{field} contains a local absolute path")


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FixtureError(f"{field} must be an object")
    return value


def _require_fields(
    value: Mapping[str, Any], field: str, expected: frozenset[str]
) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing:
        raise FixtureError(f"{field} is missing {', '.join(sorted(missing))}")
    if extra:
        raise FixtureError(f"{field} contains unknown fields: {', '.join(sorted(extra))}")


def _fixture_key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise FixtureError(f"{field} must be a lowercase synthetic key")
    return value


def _metadata(value: Any, field: str) -> dict[str, str]:
    mapping = _require_mapping(value, field)
    if not all(isinstance(key, str) and isinstance(child, str) for key, child in mapping.items()):
        raise FixtureError(f"{field} must contain only string keys and values")
    return dict(mapping)


def _validated_url(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise FixtureError(f"{field} must be an HTTPS URL")
    try:
        return validate_public_url(value).url
    except AcquisitionPolicyError as error:
        raise FixtureError(f"{field} violates public URL policy: {error.code}") from None


def load_fixture(path: str | Path) -> dict[str, Any]:
    """Load JSON only from the repository's synthetic discovery fixture tree."""

    fixture_path = Path(path).resolve()
    fixture_root = _FIXTURE_ROOT.resolve()
    if (
        fixture_path.suffix != ".json"
        or not fixture_path.is_file()
        or not _is_within(fixture_path, fixture_root)
    ):
        raise FixtureError("fixture must be a checked-in synthetic JSON fixture")
    try:
        value = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise FixtureError("fixture must contain valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise FixtureError("fixture root must be an object")
    return value


def build_records(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a synthetic fixture and construct stable public records."""

    value = _require_mapping(fixture, "fixture")
    _assert_fixture_safe(value)
    _require_fields(
        value,
        "fixture",
        frozenset(
            {
                "schema_version",
                "fixture_type",
                "synthetic",
                "source",
                "assets",
                "evidence",
            }
        ),
    )
    if (
        value["schema_version"] != 1
        or value["fixture_type"] != "synthetic_metadata"
        or value["synthetic"] is not True
    ):
        raise FixtureError("fixture must declare synthetic_metadata schema version 1")

    source_input = _require_mapping(value["source"], "source")
    _require_fields(
        source_input,
        "source",
        frozenset({"key", "public_url", "source_kind", "metadata"}),
    )
    source_key = _fixture_key(source_input["key"], "source.key")
    source_id = f"source_fixture_{source_key}"
    source = {
        "schema_version": 1,
        "record_type": "source",
        "source_id": source_id,
        "public_url": _validated_url(source_input["public_url"], "source.public_url"),
        "source_kind": source_input["source_kind"],
        "metadata": _metadata(source_input["metadata"], "source.metadata"),
    }

    evidence_input = _require_mapping(value["evidence"], "evidence")
    _require_fields(
        evidence_input, "evidence", frozenset({"recorded_at", "summary"})
    )
    if not isinstance(evidence_input["summary"], str):
        raise FixtureError("evidence.summary must be a string")

    assets_input = value["assets"]
    if not isinstance(assets_input, list) or not assets_input:
        raise FixtureError("assets must be a non-empty array")
    assets: list[dict[str, Any]] = []
    rights_records: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, child in enumerate(assets_input):
        field = f"assets[{index}]"
        asset_input = _require_mapping(child, field)
        _require_fields(
            asset_input,
            field,
            frozenset({"key", "public_url", "media_type", "metadata"}),
        )
        asset_key = _fixture_key(asset_input["key"], f"{field}.key")
        if asset_key in seen_keys:
            raise FixtureError("asset keys must be unique")
        seen_keys.add(asset_key)
        asset_id = f"asset_fixture_{asset_key}"
        asset_url = _validated_url(asset_input["public_url"], f"{field}.public_url")
        asset = {
            "schema_version": 1,
            "record_type": "asset",
            "asset_id": asset_id,
            "source_id": source_id,
            "public_url": asset_url,
            "media_type": asset_input["media_type"],
            "metadata": _metadata(asset_input["metadata"], f"{field}.metadata"),
        }
        rights = {
            "schema_version": 1,
            "record_type": "rights",
            "rights_id": f"rights_fixture_{asset_key}",
            "asset_id": asset_id,
            "state": "pending",
        }
        job = {
            "schema_version": 1,
            "record_type": "job",
            "job_id": f"job_fixture_{asset_key}_metadata",
            "asset_id": asset_id,
            "operation": "metadata_validation",
            "status": "queued",
            "required_capabilities": ["portable"],
            "retry_state": "ready",
            "attempt_count": 0,
            "max_attempts": 1,
            "checkpoint": {
                "sequence": 0,
                "summary": "Synthetic fixture metadata is ready for validation.",
            },
        }
        evidence = {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": f"evidence_fixture_{asset_key}",
            "subject_id": asset_id,
            "evidence_kind": "fixture_validation",
            "recorded_at": evidence_input["recorded_at"],
            "summary": evidence_input["summary"],
            "public_references": [asset_url],
        }
        assets.append(asset)
        rights_records.append(rights)
        jobs.append(job)
        evidence_records.append(evidence)

    records = {
        "source": source,
        "assets": assets,
        "rights": rights_records,
        "jobs": jobs,
        "evidence": evidence_records,
    }
    try:
        validate_record(source)
        for group in ("assets", "rights", "jobs", "evidence"):
            for record in records[group]:
                validate_record(record)
    except Exception as error:
        raise FixtureError(
            f"fixture does not satisfy the public record schema: {type(error).__name__}"
        ) from None
    return records


def _manifest(records: Mapping[str, Any]) -> dict[str, Any]:
    entries = []
    for asset, rights, job, evidence in zip(
        records["assets"],
        records["rights"],
        records["jobs"],
        records["evidence"],
        strict=True,
    ):
        entries.append(
            {
                "asset": asset,
                "rights": rights,
                "jobs": [job],
                "evidence": [evidence],
            }
        )
    record_counts = Counter({"source": 1})
    groups = {
        "asset": "assets",
        "rights": "rights",
        "job": "jobs",
        "evidence": "evidence",
    }
    for record_type, group in groups.items():
        record_counts[record_type] = len(records[group])
    return {
        "schema_version": 1,
        "manifest_type": "fixture_discovery",
        "source": records["source"],
        "assets": entries,
        "record_counts": dict(sorted(record_counts.items())),
        "state_counts": {"discovered": len(records["assets"])},
        "job_state_counts": {"queued": len(records["jobs"])},
        "evidence_references": sorted(
            {
                reference
                for evidence in records["evidence"]
                for reference in evidence["public_references"]
            }
        ),
    }


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    if not path.parent.is_dir():
        raise FixtureError("output parent directory must already exist")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(_canonical_bytes(manifest))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def discover_fixture(
    fixture_path: str | Path,
    database_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Upsert one checked-in fixture and emit its deterministic manifest."""

    fixture = load_fixture(fixture_path)
    records = build_records(fixture)
    database = Path(database_path).resolve()
    output = Path(output_path).resolve()
    fixture_root = _FIXTURE_ROOT.resolve()
    if _is_within(database, fixture_root) or _is_within(output, fixture_root):
        raise FixtureError("ledger and output paths may not overwrite checked-in fixtures")
    if database == output:
        raise FixtureError("ledger and output paths must be different")
    try:
        with Ledger(database) as ledger:
            ledger.upsert(records["source"])
            for asset, rights, job, evidence in zip(
                records["assets"],
                records["rights"],
                records["jobs"],
                records["evidence"],
                strict=True,
            ):
                ledger.upsert(asset)
                ledger.upsert(rights)
                ledger.create_job(job)
                ledger.upsert(evidence)
    except (LedgerError, OSError) as error:
        raise FixtureError(f"fixture discovery failed: {type(error).__name__}") from None
    manifest = _manifest(records)
    _write_manifest(output, manifest)
    return manifest
