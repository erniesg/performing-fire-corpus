"""Versioned, rights-aware full-corpus object contracts.

This module is intentionally independent from a concrete storage provider.  It
uses exact-key operations only and is exercised with fake storage in portable
tests.  Nothing here authorizes a live create or deletion.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.storage import dedicated_staging_prefix


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^source_[a-z0-9][a-z0-9._-]{0,127}$")
_ASSET_ID = re.compile(r"^asset_[a-z0-9][a-z0-9._-]{0,127}$")
_TRANSFORMATION_ID = re.compile(r"^transform_[a-z0-9][a-z0-9._-]{0,127}$")
_MANIFEST_ID = re.compile(r"^manifest_[a-z0-9][a-z0-9._-]{0,127}$")
_TOMBSTONE_ID = re.compile(r"^tombstone_[a-z0-9][a-z0-9._-]{0,127}$")
_RETENTION_WORK_ID = re.compile(
    r"^retention_work_[a-z0-9][a-z0-9._-]{0,127}$"
)
_RUN_ID = re.compile(r"^run_[a-z0-9][a-z0-9._-]{0,127}$")
_TOOL_ID = re.compile(r"^tool_[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][a-z0-9.-]+)?$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_SAFE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_EVIDENCE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_OBJECT_KEY = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*\\)"
    r"[a-z0-9][a-z0-9._/-]{0,511}$"
)
_LOCAL_PATH = re.compile(
    r"(?:^|[\s\"'])(?:file://|/Users/|/home/|/tmp/|[A-Za-z]:[\\/])"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_URL = re.compile(r"\b(?:https?|s3|r2|file)://", re.IGNORECASE)
_FORBIDDEN_METADATA_KEY_PARTS = (
    "account",
    "authorization",
    "cookie",
    "credential",
    "description",
    "endpoint",
    "email",
    "local_path",
    "name",
    "owner",
    "private",
    "prose",
    "provider",
    "secret",
    "signed",
    "text",
    "title",
    "token",
    "url",
)
_RETRIEVAL_ORDER = {
    "approved": 0,
    "metadata_only": 1,
    "blocked": 2,
}
_UTC = timezone.utc
_CHUNK_SIZE = 64 * 1024


class CorpusObjectError(RuntimeError):
    """A stable, sanitized full-corpus object-contract failure."""

    def __init__(self, code: str, next_action: str) -> None:
        self.code = code
        self.next_action = next_action
        super().__init__(f"{code}: {next_action}")


class ExactObjectStorage(Protocol):
    """The only storage operations allowed by this contract."""

    def head_object(self, key: str) -> Mapping[str, object] | None: ...

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool: ...

    def delete_exact_object(self, key: str) -> bool: ...


def _fail(code: str, next_action: str) -> None:
    raise CorpusObjectError(code, next_action)


def _require(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _fail(f"invalid_{label}", f"Provide a stable sanitized {label}.")
    return value


def _require_sha256(value: object, label: str = "sha256") -> str:
    return _require(value, _SHA256, label)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError):
        _fail(
            "invalid_metadata",
            "Use only deterministic JSON-compatible sanitized metadata.",
        )


def _assert_safe_metadata(value: object, *, field: str = "metadata") -> None:
    """Reject rather than redact anything unsafe for a durable contract."""

    if sanitize(value, environ={}) != value:
        _fail("unsafe_metadata", "Remove private, secret-like, or local values.")
    if isinstance(value, (bytes, bytearray, memoryview)):
        _fail("unsafe_metadata", "Binary content is not durable metadata.")
    if isinstance(value, str):
        if (
            "\r" in value
            or "\n" in value
            or _LOCAL_PATH.search(value)
            or _EMAIL.search(value)
            or _URL.search(value)
        ):
            _fail("unsafe_metadata", f"Remove unsafe values from {field}.")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_METADATA_KEY_PARTS):
                _fail("unsafe_metadata", f"Remove forbidden field {field}.{key}.")
            _assert_safe_metadata(child, field=f"{field}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_safe_metadata(child, field=f"{field}[{index}]")
        return
    if value is None or isinstance(value, (bool, int, float)):
        _canonical(value)
        return
    _fail("unsafe_metadata", f"Use a JSON-compatible value for {field}.")


def _namespace_key(
    prefix: str,
    namespace: str,
    source_id: str,
    asset_id: str,
    sha256: str,
    *,
    scoped_id: str | None = None,
    scoped_pattern: re.Pattern[str] | None = None,
    scoped_label: str = "identifier",
) -> str:
    if not dedicated_staging_prefix(prefix):
        _fail("invalid_namespace_prefix", "Use one normalized dedicated prefix.")
    _require(source_id, _SOURCE_ID, "source_id")
    _require(asset_id, _ASSET_ID, "asset_id")
    _require_sha256(sha256)
    segments = [prefix.rstrip("/"), "v1", namespace, source_id, asset_id]
    if scoped_id is not None:
        if scoped_pattern is None:
            _fail("invalid_namespace", "Use a reviewed namespace identifier.")
        segments.append(_require(scoped_id, scoped_pattern, scoped_label))
    segments.append(sha256)
    key = "/".join(segments)
    if not _OBJECT_KEY.fullmatch(key):
        _fail("invalid_object_key", "Use a normalized immutable object key.")
    return key


def raw_object_key(
    prefix: str, source_id: str, asset_id: str, sha256: str
) -> str:
    """Return a versioned content-addressed raw-object key."""

    return _namespace_key(prefix, "raw", source_id, asset_id, sha256)


def derived_object_key(
    prefix: str,
    source_id: str,
    asset_id: str,
    transformation_id: str,
    sha256: str,
) -> str:
    """Return a versioned derived key scoped by its transformation."""

    return _namespace_key(
        prefix,
        "derived",
        source_id,
        asset_id,
        sha256,
        scoped_id=transformation_id,
        scoped_pattern=_TRANSFORMATION_ID,
        scoped_label="transformation_id",
    )


def manifest_object_key(
    prefix: str,
    source_id: str,
    asset_id: str,
    manifest_id: str,
    sha256: str,
) -> str:
    """Return a versioned immutable manifest key."""

    return _namespace_key(
        prefix,
        "manifests",
        source_id,
        asset_id,
        sha256,
        scoped_id=manifest_id,
        scoped_pattern=_MANIFEST_ID,
        scoped_label="manifest_id",
    )


def tombstone_object_key(
    prefix: str,
    source_id: str,
    asset_id: str,
    tombstone_id: str,
    sha256: str,
) -> str:
    """Return a versioned immutable tombstone key."""

    return _namespace_key(
        prefix,
        "tombstones",
        source_id,
        asset_id,
        sha256,
        scoped_id=tombstone_id,
        scoped_pattern=_TOMBSTONE_ID,
        scoped_label="tombstone_id",
    )


def _normalized_media_type(value: object) -> str:
    normalized = str(value).partition(";")[0].strip().lower()
    if not _MEDIA_TYPE.fullmatch(normalized):
        _fail("invalid_media_type", "Provide one normalized MIME type.")
    return normalized


def _matching_head(
    metadata: Mapping[str, object] | None,
    *,
    byte_size: int,
    media_type: str,
    sha256: str,
) -> bool:
    if metadata is None:
        return False
    try:
        stored_size = int(metadata.get("byte_size", -1))
    except (TypeError, ValueError):
        return False
    return (
        stored_size == byte_size
        and str(metadata.get("media_type", "")).partition(";")[0].strip().lower()
        == media_type
        and metadata.get("sha256") == sha256
    )


def _file_digest(path: Path) -> tuple[int, str]:
    size = 0
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_SIZE):
                size += len(chunk)
                hasher.update(chunk)
    except OSError:
        _fail("object_file_unavailable", "Provide the bounded object file again.")
    return size, hasher.hexdigest()


def _verified_receipt(
    *,
    key: str,
    object_kind: str,
    source_id: str,
    asset_id: str,
    transformation_id: str | None,
    byte_size: int,
    media_type: str,
    sha256: str,
    rights_snapshot_sha256: str,
    retention_class: str,
    creation_run_id: str,
    evidence_ref: str,
    create_disposition: str,
) -> dict[str, object]:
    receipt_digest = hashlib.sha256(
        _canonical(
            {
                "asset_id": asset_id,
                "object_key": key,
                "rights_snapshot_sha256": rights_snapshot_sha256,
                "source_id": source_id,
            }
        )
    ).hexdigest()
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "object_receipt",
        "receipt_id": f"receipt_{receipt_digest}",
        "object_kind": object_kind,
        "source_id": source_id,
        "asset_id": asset_id,
        "object_key": key,
        "byte_size": byte_size,
        "media_type": media_type,
        "sha256": sha256,
        "rights_snapshot_sha256": rights_snapshot_sha256,
        "retention_class": retention_class,
        "creation_run_id": creation_run_id,
        "evidence_ref": evidence_ref,
        "verification_state": "verified",
        "create_disposition": create_disposition,
    }
    if transformation_id is not None:
        value["transformation_id"] = transformation_id
    return value


def immutable_create_and_verify(
    storage: ExactObjectStorage,
    *,
    key: str,
    path: str | Path,
    object_kind: str,
    source_id: str,
    asset_id: str,
    byte_size: int,
    media_type: str,
    sha256: str,
    rights_snapshot_sha256: str,
    retention_class: str,
    creation_run_id: str,
    evidence_ref: str,
    transformation_id: str | None = None,
) -> dict[str, object]:
    """Create once and return a receipt only after a matching exact-key HEAD.

    A lost create response is recovered only when the immediate exact-key HEAD
    matches every immutable fact.  Provider errors never enter the receipt.
    """

    if object_kind not in {"raw", "derived", "manifest"}:
        _fail("invalid_object_kind", "Use a reviewed full-corpus object kind.")
    source_id = _require(source_id, _SOURCE_ID, "source_id")
    asset_id = _require(asset_id, _ASSET_ID, "asset_id")
    sha256 = _require_sha256(sha256)
    rights_snapshot_sha256 = _require_sha256(
        rights_snapshot_sha256, "rights_snapshot_sha256"
    )
    media_type = _normalized_media_type(media_type)
    if (
        not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or byte_size < 0
    ):
        _fail("invalid_byte_size", "Provide the exact non-negative byte size.")
    retention_class = _require(retention_class, _SAFE_LABEL, "retention_class")
    creation_run_id = _require(creation_run_id, _RUN_ID, "creation_run_id")
    evidence_ref = _require(evidence_ref, _EVIDENCE_REF, "evidence_ref")
    if not isinstance(key, str) or not _OBJECT_KEY.fullmatch(key):
        _fail("invalid_object_key", "Provide one exact immutable object key.")
    if transformation_id is not None:
        transformation_id = _require(
            transformation_id, _TRANSFORMATION_ID, "transformation_id"
        )

    prefix_marker = "v1/"
    if prefix_marker not in key:
        _fail("object_key_mismatch", "Use a versioned corpus namespace.")
    prefix = key[: key.index(prefix_marker)]
    expected_key = {
        "raw": raw_object_key(prefix, source_id, asset_id, sha256),
        "derived": (
            derived_object_key(
                prefix,
                source_id,
                asset_id,
                transformation_id or "",
                sha256,
            )
            if object_kind == "derived"
            else ""
        ),
        "manifest": "",
    }[object_kind]
    if object_kind == "manifest":
        # Manifests have their own stable ID in the key.  Require the common
        # fixed namespace and immutable suffix without accepting arbitrary keys.
        expected_start = (
            f"{prefix}v1/manifests/{source_id}/{asset_id}/manifest_"
        )
        if not key.startswith(expected_start) or not key.endswith(f"/{sha256}"):
            _fail("object_key_mismatch", "Use the reviewed manifest namespace.")
    elif key != expected_key:
        _fail("object_key_mismatch", "The object key does not match its facts.")

    actual_size, actual_sha256 = _file_digest(Path(path))
    if actual_size != byte_size or actual_sha256 != sha256:
        _fail(
            "object_file_mismatch",
            "The bounded file does not match the declared size and hash.",
        )

    try:
        existing = storage.head_object(key)
    except Exception:
        _fail("exact_head_failed", "Retry the exact-key verification safely.")
    if existing is not None:
        if not _matching_head(
            existing,
            byte_size=byte_size,
            media_type=media_type,
            sha256=sha256,
        ):
            _fail(
                "immutable_object_conflict",
                "Hold the exact key for operator conflict review.",
            )
        disposition = "reused"
    else:
        disposition = "created"
        try:
            created = storage.create_file_if_absent(
                key,
                Path(path),
                byte_size=byte_size,
                media_type=media_type,
                sha256=sha256,
            )
            if not isinstance(created, bool):
                _fail(
                    "immutable_create_unconfirmed",
                    "Retry only after exact-key verification.",
                )
            if not created:
                disposition = "reused"
        except CorpusObjectError:
            raise
        except Exception:
            disposition = "recovered_after_lost_response"

    try:
        verified = storage.head_object(key)
    except Exception:
        _fail("exact_head_failed", "Retry the exact-key verification safely.")
    if verified is None:
        _fail(
            "immutable_create_unconfirmed",
            "The create is unverified; retry by this exact key only.",
        )
    if not _matching_head(
        verified,
        byte_size=byte_size,
        media_type=media_type,
        sha256=sha256,
    ):
        _fail(
            "immutable_object_conflict",
            "Hold the exact key for operator conflict review.",
        )
    return _verified_receipt(
        key=key,
        object_kind=object_kind,
        source_id=source_id,
        asset_id=asset_id,
        transformation_id=transformation_id,
        byte_size=byte_size,
        media_type=media_type,
        sha256=sha256,
        rights_snapshot_sha256=rights_snapshot_sha256,
        retention_class=retention_class,
        creation_run_id=creation_run_id,
        evidence_ref=evidence_ref,
        create_disposition=disposition,
    )


def cluster_exact_content(
    provenance_edges: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Cluster one exact hash while preserving every provenance/rights edge."""

    normalized: list[dict[str, str]] = []
    for edge in provenance_edges:
        source_id = _require(edge.get("source_id"), _SOURCE_ID, "source_id")
        asset_id = _require(edge.get("asset_id"), _ASSET_ID, "asset_id")
        sha256 = _require_sha256(edge.get("sha256"))
        rights_snapshot = _require_sha256(
            edge.get("rights_snapshot_sha256"), "rights_snapshot_sha256"
        )
        decision = edge.get("retrieval_decision")
        if decision not in _RETRIEVAL_ORDER:
            _fail(
                "invalid_retrieval_decision",
                "Use approved, metadata_only, or blocked.",
            )
        normalized.append(
            {
                "source_id": source_id,
                "asset_id": asset_id,
                "sha256": sha256,
                "rights_snapshot_sha256": rights_snapshot,
                "retrieval_decision": str(decision),
            }
        )
    if not normalized:
        _fail("empty_content_cluster", "Provide at least one provenance edge.")
    hashes = {edge["sha256"] for edge in normalized}
    if len(hashes) != 1:
        _fail("mixed_content_cluster", "Cluster only one exact content hash.")
    unique = {
        (
            edge["source_id"],
            edge["asset_id"],
            edge["rights_snapshot_sha256"],
            edge["retrieval_decision"],
        ): edge
        for edge in normalized
    }
    edges = sorted(
        unique.values(),
        key=lambda edge: (
            edge["source_id"],
            edge["asset_id"],
            edge["rights_snapshot_sha256"],
            edge["retrieval_decision"],
        ),
    )
    effective = max(
        (edge["retrieval_decision"] for edge in edges),
        key=_RETRIEVAL_ORDER.__getitem__,
    )
    return {
        "schema_version": 1,
        "record_type": "content_cluster",
        "sha256": next(iter(hashes)),
        "effective_retrieval_decision": effective,
        "provenance_edges": edges,
    }


def _validate_receipt(value: Mapping[str, object]) -> dict[str, object]:
    required = {
        "receipt_id",
        "object_kind",
        "source_id",
        "asset_id",
        "object_key",
        "byte_size",
        "media_type",
        "sha256",
        "rights_snapshot_sha256",
        "retention_class",
        "creation_run_id",
        "evidence_ref",
        "verification_state",
        "create_disposition",
    }
    if (
        value.get("schema_version") != 1
        or value.get("record_type") != "object_receipt"
        or not required.issubset(value)
        or value.get("verification_state") != "verified"
    ):
        _fail("invalid_object_receipt", "Provide a verified version-1 receipt.")
    allowed = required | {"schema_version", "record_type"}
    if value.get("object_kind") == "derived":
        allowed.add("transformation_id")
    if set(value) != allowed:
        _fail(
            "invalid_object_receipt",
            "Use only strict version-1 object receipt fields.",
        )
    _require(value.get("receipt_id"), re.compile(r"^receipt_[a-z0-9][a-z0-9._-]{0,127}$"), "receipt_id")
    _require(value.get("source_id"), _SOURCE_ID, "source_id")
    _require(value.get("asset_id"), _ASSET_ID, "asset_id")
    _require_sha256(value.get("sha256"))
    _require_sha256(value.get("rights_snapshot_sha256"), "rights_snapshot_sha256")
    _require(value.get("retention_class"), _SAFE_LABEL, "retention_class")
    _require(value.get("creation_run_id"), _RUN_ID, "creation_run_id")
    if value.get("create_disposition") not in {
        "created",
        "reused",
        "recovered_after_lost_response",
    }:
        _fail(
            "invalid_object_receipt",
            "Use a verified immutable create disposition.",
        )
    _require(value.get("evidence_ref"), _EVIDENCE_REF, "evidence_ref")
    if not isinstance(value.get("object_key"), str) or not _OBJECT_KEY.fullmatch(
        str(value["object_key"])
    ):
        _fail("invalid_object_key", "Provide one exact immutable object key.")
    _normalized_media_type(value.get("media_type"))
    if (
        not isinstance(value.get("byte_size"), int)
        or isinstance(value.get("byte_size"), bool)
        or int(value["byte_size"]) < 0
    ):
        _fail("invalid_byte_size", "Provide the verified object byte size.")
    object_kind = value.get("object_kind")
    if object_kind not in {"raw", "derived", "manifest"}:
        _fail("invalid_object_kind", "Use a reviewed full-corpus object kind.")
    key = str(value["object_key"])
    marker = "v1/"
    if marker not in key:
        _fail("object_key_mismatch", "Use a versioned corpus namespace.")
    prefix = key[: key.index(marker)]
    if object_kind == "raw":
        expected_key = raw_object_key(
            prefix,
            str(value["source_id"]),
            str(value["asset_id"]),
            str(value["sha256"]),
        )
        if key != expected_key:
            _fail("object_key_mismatch", "Receipt facts do not match the raw key.")
    elif object_kind == "derived":
        transformation_id = _require(
            value.get("transformation_id"),
            _TRANSFORMATION_ID,
            "transformation_id",
        )
        expected_key = derived_object_key(
            prefix,
            str(value["source_id"]),
            str(value["asset_id"]),
            transformation_id,
            str(value["sha256"]),
        )
        if key != expected_key:
            _fail(
                "object_key_mismatch",
                "Receipt facts do not match the derived key.",
            )
    else:
        expected_start = (
            f"{prefix}v1/manifests/{value['source_id']}/{value['asset_id']}/"
            "manifest_"
        )
        if not key.startswith(expected_start) or not key.endswith(
            f"/{value['sha256']}"
        ):
            _fail(
                "object_key_mismatch",
                "Receipt facts do not match the manifest key.",
            )
    _assert_safe_metadata(value)
    return dict(value)


def reconcile_receipt_commit(
    storage: ExactObjectStorage,
    *,
    expected_receipt: Mapping[str, object],
    receipt_artifact: Mapping[str, object] | None,
    ledger_record: Mapping[str, object] | None,
) -> dict[str, object]:
    """Reconcile crash boundaries between exact HEAD, receipt, and ledger.

    This function is intentionally read-only.  Its stable next action tells the
    caller which one missing durable record may be written without recreating
    or relisting the object.
    """

    expected = _validate_receipt(expected_receipt)
    receipt = (
        None if receipt_artifact is None else _validate_receipt(receipt_artifact)
    )
    ledger = None if ledger_record is None else _validate_receipt(ledger_record)
    if receipt is not None and receipt != expected:
        _fail(
            "receipt_commit_conflict",
            "Hold the exact receipt artifact for operator review.",
        )
    if ledger is not None and ledger != expected:
        _fail(
            "receipt_commit_conflict",
            "Hold the exact ledger record for operator review.",
        )
    try:
        metadata = storage.head_object(str(expected["object_key"]))
    except Exception:
        _fail("exact_head_failed", "Retry the exact-key verification safely.")
    if metadata is None:
        _fail(
            "verified_object_missing",
            "Hold the receipt state until the exact object is reconciled.",
        )
    if not _matching_head(
        metadata,
        byte_size=int(expected["byte_size"]),
        media_type=_normalized_media_type(expected["media_type"]),
        sha256=str(expected["sha256"]),
    ):
        _fail(
            "immutable_object_conflict",
            "Hold the exact key for operator conflict review.",
        )
    if receipt is not None and ledger is not None:
        next_action = "complete"
    elif receipt is not None:
        next_action = "write_ledger_from_receipt"
    elif ledger is not None:
        next_action = "write_receipt_from_ledger"
    else:
        next_action = "write_receipt_then_ledger"
    return {
        "schema_version": 1,
        "record_type": "receipt_reconciliation",
        "next_action": next_action,
        "verified_receipt": expected,
    }


def build_derivation_manifest(
    *,
    manifest_id: str,
    source_id: str,
    asset_id: str,
    transformation_id: str,
    tool_id: str,
    tool_version: str,
    contract_version: int,
    parameters: Mapping[str, object],
    inputs: Iterable[Mapping[str, object]],
    outputs: Iterable[Mapping[str, object]],
    rights_inheritance: str,
    redaction_state: str,
    evidence_ref: str,
) -> dict[str, object]:
    """Build a deterministic raw-to-derived manifest with sanitized facts only."""

    manifest_id = _require(manifest_id, _MANIFEST_ID, "manifest_id")
    source_id = _require(source_id, _SOURCE_ID, "source_id")
    asset_id = _require(asset_id, _ASSET_ID, "asset_id")
    transformation_id = _require(
        transformation_id, _TRANSFORMATION_ID, "transformation_id"
    )
    tool_id = _require(tool_id, _TOOL_ID, "tool_id")
    tool_version = _require(tool_version, _VERSION, "tool_version")
    if (
        not isinstance(contract_version, int)
        or isinstance(contract_version, bool)
        or contract_version < 1
    ):
        _fail("invalid_contract_version", "Use a positive contract version.")
    if rights_inheritance != "most_restrictive":
        _fail(
            "invalid_rights_inheritance",
            "Derived outputs must inherit the most restrictive input decision.",
        )
    redaction_state = _require(redaction_state, _SAFE_LABEL, "redaction_state")
    evidence_ref = _require(evidence_ref, _EVIDENCE_REF, "evidence_ref")
    _assert_safe_metadata(parameters, field="parameters")
    parameter_digest = hashlib.sha256(_canonical(parameters)).hexdigest()
    input_values = [_validate_receipt(value) for value in inputs]
    output_values = [_validate_receipt(value) for value in outputs]
    if not input_values or not output_values:
        _fail("manifest_receipts_required", "Provide verified inputs and outputs.")
    for value in input_values + output_values:
        if value["source_id"] != source_id or value["asset_id"] != asset_id:
            _fail(
                "manifest_provenance_mismatch",
                "Receipts must match the manifest source and asset.",
            )
    for value in output_values:
        if (
            value.get("object_kind") != "derived"
            or value.get("transformation_id") != transformation_id
        ):
            _fail(
                "manifest_output_mismatch",
                "Every output must match the reviewed transformation.",
            )
    input_rights = {
        str(value["rights_snapshot_sha256"]) for value in input_values
    }
    if any(
        str(value["rights_snapshot_sha256"]) not in input_rights
        for value in output_values
    ):
        _fail(
            "manifest_rights_mismatch",
            "Derived outputs must inherit a current input rights snapshot.",
        )
    return {
        "schema_version": 1,
        "record_type": "derivation_manifest",
        "manifest_id": manifest_id,
        "source_id": source_id,
        "asset_id": asset_id,
        "transformation_id": transformation_id,
        "tool_id": tool_id,
        "tool_version": tool_version,
        "contract_version": contract_version,
        "parameters": dict(parameters),
        "parameters_sha256": parameter_digest,
        "input_receipt_ids": sorted(str(value["receipt_id"]) for value in input_values),
        "input_object_keys": sorted(str(value["object_key"]) for value in input_values),
        "input_sha256": sorted(str(value["sha256"]) for value in input_values),
        "output_receipt_ids": sorted(
            str(value["receipt_id"]) for value in output_values
        ),
        "output_object_keys": sorted(
            str(value["object_key"]) for value in output_values
        ),
        "output_sha256": sorted(str(value["sha256"]) for value in output_values),
        "input_rights_snapshot_sha256": sorted(
            {str(value["rights_snapshot_sha256"]) for value in input_values}
        ),
        "output_rights_snapshot_sha256": sorted(
            {str(value["rights_snapshot_sha256"]) for value in output_values}
        ),
        "rights_inheritance": rights_inheritance,
        "redaction_state": redaction_state,
        "evidence_ref": evidence_ref,
    }


def _parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"invalid_{label}", f"Provide a UTC {label}.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"invalid_{label}", f"Provide a UTC {label}.")
    if parsed.tzinfo is None or parsed.utcoffset() != _UTC.utcoffset(parsed):
        _fail(f"invalid_{label}", f"Provide a UTC {label}.")
    return parsed.astimezone(_UTC)


def build_retention_work(
    *,
    work_id: str,
    root_receipt: Mapping[str, object],
    derived_receipts: Iterable[Mapping[str, object]],
    expires_at: str,
    current_time: str,
    legal_hold_state: str,
    cleanup_authority: str,
    cleanup_run_id: str,
    reason_code: str,
    evidence_ref: str,
) -> dict[str, object]:
    """Describe exact-key retention work without granting broad deletion."""

    work_id = _require(work_id, _RETENTION_WORK_ID, "retention_work_id")
    root = _validate_receipt(root_receipt)
    if root.get("object_kind") != "raw":
        _fail("invalid_retention_root", "Retention work must start from raw data.")
    derived = [_validate_receipt(value) for value in derived_receipts]
    for value in derived:
        if (
            value.get("object_kind") != "derived"
            or value["source_id"] != root["source_id"]
            or value["asset_id"] != root["asset_id"]
        ):
            _fail(
                "retention_propagation_mismatch",
                "Derived deletion targets must descend from the same asset.",
            )
    expiry = _parse_time(expires_at, "expires_at")
    current = _parse_time(current_time, "current_time")
    if legal_hold_state not in {"none", "active"}:
        _fail("invalid_legal_hold_state", "Use none or active.")
    if cleanup_authority not in {"held_for_review", "same_proof_disposable"}:
        _fail(
            "invalid_cleanup_authority",
            "Use a reviewed exact-key cleanup authority.",
        )
    cleanup_run_id = _require(cleanup_run_id, _RUN_ID, "cleanup_run_id")
    if cleanup_authority == "same_proof_disposable":
        for value in [root, *derived]:
            if (
                value["creation_run_id"] != cleanup_run_id
                or value.get("create_disposition")
                not in {"created", "recovered_after_lost_response"}
            ):
                _fail(
                    "same_proof_authority_mismatch",
                    "Delete only objects created by this exact disposable proof.",
                )
    reason_code = _require(reason_code, _SAFE_LABEL, "reason_code")
    evidence_ref = _require(evidence_ref, _EVIDENCE_REF, "evidence_ref")
    targets = [
        {
            "receipt_id": str(value["receipt_id"]),
            "object_key": str(value["object_key"]),
            "sha256": str(value["sha256"]),
            "byte_size": int(value["byte_size"]),
            "media_type": str(value["media_type"]),
            "object_kind": str(value["object_kind"]),
            "creation_run_id": str(value["creation_run_id"]),
            "create_disposition": str(value["create_disposition"]),
        }
        for value in [*derived, root]
    ]
    targets.sort(key=lambda value: str(value["object_key"]))
    if legal_hold_state == "active":
        state = "legal_hold_conflict"
    elif current < expiry:
        state = "not_due"
    elif cleanup_authority == "held_for_review":
        state = "awaiting_review"
    else:
        state = "ready_exact_cleanup"
    return {
        "schema_version": 1,
        "record_type": "retention_work",
        "retention_work_id": work_id,
        "source_id": root["source_id"],
        "asset_id": root["asset_id"],
        "root_receipt_id": root["receipt_id"],
        "retention_class": root["retention_class"],
        "expires_at": expires_at,
        "evaluated_at": current_time,
        "legal_hold_state": legal_hold_state,
        "cleanup_authority": cleanup_authority,
        "cleanup_run_id": cleanup_run_id,
        "reason_code": reason_code,
        "state": state,
        "exact_object_keys": [value["object_key"] for value in targets],
        "targets": targets,
        "evidence_ref": evidence_ref,
    }


def _prefix_from_key(key: str) -> str:
    marker = "v1/"
    position = key.find(marker)
    if position <= 0:
        _fail("invalid_object_key", "Use a versioned dedicated namespace.")
    return key[:position]


def _tombstone(
    work: Mapping[str, object],
    target: Mapping[str, object],
    deletion_state: str,
) -> dict[str, object]:
    digest = hashlib.sha256(
        _canonical(
            {
                "object_key": target["object_key"],
                "retention_work_id": work["retention_work_id"],
                "sha256": target["sha256"],
            }
        )
    ).hexdigest()
    tombstone_id = f"tombstone_{digest}"
    key = tombstone_object_key(
        _prefix_from_key(str(target["object_key"])),
        str(work["source_id"]),
        str(work["asset_id"]),
        tombstone_id,
        str(target["sha256"]),
    )
    return {
        "schema_version": 1,
        "record_type": "object_tombstone",
        "tombstone_id": tombstone_id,
        "retention_work_id": work["retention_work_id"],
        "receipt_id": target["receipt_id"],
        "source_id": work["source_id"],
        "asset_id": work["asset_id"],
        "deleted_object_key": target["object_key"],
        "deleted_object_sha256": target["sha256"],
        "tombstone_object_key": key,
        "deletion_state": deletion_state,
        "deleted_at": work["evaluated_at"],
        "reason_code": work["reason_code"],
        "evidence_ref": work["evidence_ref"],
    }


def execute_exact_cleanup(
    storage: ExactObjectStorage,
    work: Mapping[str, object],
) -> dict[str, object]:
    """Execute only same-proof, exact-key teardown and return durable states."""

    if (
        work.get("schema_version") != 1
        or work.get("record_type") != "retention_work"
    ):
        _fail("invalid_retention_work", "Provide version-1 retention work.")
    allowed_work_fields = {
        "schema_version",
        "record_type",
        "retention_work_id",
        "source_id",
        "asset_id",
        "root_receipt_id",
        "retention_class",
        "expires_at",
        "evaluated_at",
        "legal_hold_state",
        "cleanup_authority",
        "cleanup_run_id",
        "reason_code",
        "state",
        "exact_object_keys",
        "targets",
        "evidence_ref",
    }
    if set(work) != allowed_work_fields:
        _fail("invalid_retention_work", "Use only strict retention-work fields.")
    _require(work.get("retention_work_id"), _RETENTION_WORK_ID, "retention_work_id")
    source_id = _require(work.get("source_id"), _SOURCE_ID, "source_id")
    asset_id = _require(work.get("asset_id"), _ASSET_ID, "asset_id")
    _require(
        work.get("root_receipt_id"),
        re.compile(r"^receipt_[a-z0-9][a-z0-9._-]{0,127}$"),
        "receipt_id",
    )
    _require(work.get("retention_class"), _SAFE_LABEL, "retention_class")
    cleanup_run_id = _require(work.get("cleanup_run_id"), _RUN_ID, "cleanup_run_id")
    _require(work.get("reason_code"), _SAFE_LABEL, "reason_code")
    _require(work.get("evidence_ref"), _EVIDENCE_REF, "evidence_ref")
    expiry = _parse_time(work.get("expires_at"), "expires_at")
    evaluated = _parse_time(work.get("evaluated_at"), "evaluated_at")
    if evaluated < expiry:
        _fail("cleanup_not_ready", "Retention expiry has not been reached.")
    _assert_safe_metadata(work)
    if work.get("cleanup_authority") != "same_proof_disposable":
        _fail(
            "cleanup_authority_required",
            "Normal corpus data requires a separate reviewed deletion decision.",
        )
    if work.get("legal_hold_state") == "active":
        _fail(
            "legal_hold_conflict",
            "Resolve the legal hold before any exact-key deletion.",
        )
    if work.get("state") != "ready_exact_cleanup":
        _fail(
            "cleanup_not_ready",
            "Resolve retention timing and review gates before cleanup.",
        )
    if work.get("reason_code") != "proof_teardown":
        _fail(
            "cleanup_authority_required",
            "Same-proof cleanup is limited to reviewed proof teardown.",
        )
    targets = work.get("targets")
    if not isinstance(targets, list) or not targets:
        _fail("invalid_retention_work", "Provide explicit exact-key targets.")
    expected_keys = [str(target.get("object_key", "")) for target in targets]
    if expected_keys != work.get("exact_object_keys") or len(expected_keys) != len(
        set(expected_keys)
    ):
        _fail("invalid_retention_work", "Retention targets must be exact and unique.")

    tombstones: list[dict[str, object]] = []
    failed: list[str] = []
    for target in targets:
        allowed_target_fields = {
            "receipt_id",
            "object_key",
            "sha256",
            "byte_size",
            "media_type",
            "object_kind",
            "creation_run_id",
            "create_disposition",
        }
        if not isinstance(target, Mapping) or set(target) != allowed_target_fields:
            _fail(
                "invalid_retention_work",
                "Use only strict exact-key target fields.",
            )
        _require(
            target.get("receipt_id"),
            re.compile(r"^receipt_[a-z0-9][a-z0-9._-]{0,127}$"),
            "receipt_id",
        )
        target_sha256 = _require_sha256(target.get("sha256"))
        if (
            not isinstance(target.get("byte_size"), int)
            or isinstance(target.get("byte_size"), bool)
            or int(target["byte_size"]) < 0
        ):
            _fail("invalid_retention_work", "Use a verified target byte size.")
        target_media_type = _normalized_media_type(target.get("media_type"))
        if (
            target.get("creation_run_id") != cleanup_run_id
            or target.get("create_disposition")
            not in {"created", "recovered_after_lost_response"}
        ):
            _fail(
                "invalid_retention_work",
                "Every cleanup target must be created by this exact proof.",
            )
        key = str(target["object_key"])
        if not _OBJECT_KEY.fullmatch(key):
            _fail("invalid_object_key", "Retention targets must be exact keys.")
        marker = "v1/"
        if marker not in key:
            _fail("invalid_retention_work", "Use a versioned exact target key.")
        prefix = key[: key.index(marker)]
        segments = key[len(prefix) :].split("/")
        object_kind = target.get("object_kind")
        if object_kind == "raw":
            expected_key = raw_object_key(
                prefix, source_id, asset_id, target_sha256
            )
        elif object_kind == "derived" and len(segments) == 6:
            transformation_id = _require(
                segments[4], _TRANSFORMATION_ID, "transformation_id"
            )
            expected_key = derived_object_key(
                prefix,
                source_id,
                asset_id,
                transformation_id,
                target_sha256,
            )
        elif object_kind == "manifest" and len(segments) == 6:
            manifest_id = _require(segments[4], _MANIFEST_ID, "manifest_id")
            expected_key = manifest_object_key(
                prefix,
                source_id,
                asset_id,
                manifest_id,
                target_sha256,
            )
        else:
            _fail(
                "invalid_retention_work",
                "Target kind and namespace must match.",
            )
        if key != expected_key:
            _fail(
                "invalid_retention_work",
                "Target key must match the retained source and asset.",
            )
        try:
            existing = storage.head_object(key)
        except Exception:
            failed.append(key)
            continue
        if existing is None:
            tombstones.append(_tombstone(work, target, "already_absent"))
            continue
        if not _matching_head(
            existing,
            byte_size=int(target["byte_size"]),
            media_type=target_media_type,
            sha256=target_sha256,
        ):
            failed.append(key)
            continue
        try:
            storage.delete_exact_object(key)
            absent = storage.head_object(key) is None
        except Exception:
            absent = False
        if absent:
            tombstones.append(_tombstone(work, target, "deleted_exact_key"))
        else:
            failed.append(key)
    return {
        "schema_version": 1,
        "record_type": "cleanup_result",
        "retention_work_id": work["retention_work_id"],
        "state": "complete" if not failed else "failed_cleanup",
        "tombstones": tombstones,
        "failed_object_keys": failed,
        "evidence_ref": work["evidence_ref"],
    }
