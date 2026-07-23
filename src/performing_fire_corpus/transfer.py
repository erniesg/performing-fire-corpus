"""Fail-closed planning and bounded transfer to immutable object storage."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from performing_fire_corpus.ledger import Ledger, LedgerError
from performing_fire_corpus.policy import require_transfer_rights, validate_public_url
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.storage import (
    StorageClient,
    dedicated_staging_prefix,
)


_IDENTIFIER = re.compile(r"^asset_[a-z0-9][a-z0-9._-]{0,127}$")
_SOURCE_IDENTIFIER = re.compile(r"^source_[a-z0-9][a-z0-9._-]{0,127}$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK_SIZE = 64 * 1024


class TransferError(RuntimeError):
    """A sanitized, durable transfer failure."""

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        created_object_receipt: Mapping[str, object] | None = None,
        attempted_object_receipt: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.reason = str(sanitize(reason, environ={}))
        self.created_object_receipt = (
            None
            if created_object_receipt is None
            else dict(created_object_receipt)
        )
        self.attempted_object_receipt = (
            None
            if attempted_object_receipt is None
            else dict(attempted_object_receipt)
        )
        super().__init__(f"{code}: {self.reason}")


@dataclass(frozen=True)
class TransferPlan:
    asset_id: str
    source_id: str
    public_url: str
    allowed_media_types: frozenset[str]
    maximum_bytes: int
    staging_prefix: str
    retention_decision: str
    evidence_ref: str
    rights: Mapping[str, object]


class HTTPResponse(Protocol):
    media_type: str
    content_length: int | None

    def iter_bytes(self, chunk_size: int) -> Iterable[bytes]: ...


class HTTPClient(Protocol):
    def open(self, url: str) -> HTTPResponse: ...


def _fail(
    code: str,
    reason: str,
    *,
    created_object_receipt: Mapping[str, object] | None = None,
    attempted_object_receipt: Mapping[str, object] | None = None,
) -> None:
    raise TransferError(
        code,
        reason,
        created_object_receipt=created_object_receipt,
        attempted_object_receipt=attempted_object_receipt,
    )


def plan_transfer(
    *,
    asset_id: str,
    source_id: str,
    public_url: str,
    rights: Mapping[str, object] | None,
    allowed_media_types: Iterable[str],
    maximum_bytes: int,
    staging_prefix: str,
    retention_decision: str,
    evidence_ref: str,
) -> TransferPlan:
    """Validate every gate without performing network or storage operations."""

    if not isinstance(asset_id, str) or not _IDENTIFIER.fullmatch(asset_id):
        _fail("invalid_asset_id", "A stable asset identifier is required.")
    if not isinstance(source_id, str) or not _SOURCE_IDENTIFIER.fullmatch(source_id):
        _fail("invalid_source_id", "A stable source identifier is required.")
    require_transfer_rights(asset_id, rights)
    validated_url = validate_public_url(public_url)
    media_types = frozenset(allowed_media_types)
    if not media_types or any(
        not isinstance(value, str) or not _MEDIA_TYPE.fullmatch(value)
        for value in media_types
    ):
        _fail("media_allowlist_required", "A valid media-type allowlist is required.")
    if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes <= 0:
        _fail("byte_bound_required", "A positive maximum byte size is required.")
    if not dedicated_staging_prefix(staging_prefix):
        _fail("dedicated_prefix_required", "A dedicated staging prefix is required.")
    if (
        not isinstance(retention_decision, str)
        or not retention_decision.strip()
        or sanitize(retention_decision, environ={}) != retention_decision
    ):
        _fail(
            "retention_decision_required",
            "A reviewed retention or exact-key cleanup decision is required.",
        )
    if (
        not isinstance(evidence_ref, str)
        or not evidence_ref.strip()
        or sanitize(evidence_ref, environ={}) != evidence_ref
        or "/" in evidence_ref
    ):
        _fail("invalid_evidence_ref", "A sanitized evidence reference is required.")
    return TransferPlan(
        asset_id=asset_id,
        source_id=source_id,
        public_url=validated_url.url,
        rights=dict(rights),
        allowed_media_types=media_types,
        maximum_bytes=maximum_bytes,
        staging_prefix=staging_prefix,
        retention_decision=retention_decision,
        evidence_ref=evidence_ref,
    )


def immutable_object_key(plan: TransferPlan, sha256: str) -> str:
    if not _SHA256.fullmatch(sha256):
        _fail("invalid_hash", "A lowercase SHA-256 digest is required.")
    return f"{plan.staging_prefix}v1/{plan.asset_id}/{sha256}"


def _normalize_media_type(value: str) -> str:
    return value.partition(";")[0].strip().lower()


def _matching_metadata(
    metadata: Mapping[str, object],
    *,
    byte_size: int,
    media_type: str,
    sha256: str,
) -> bool:
    try:
        stored_size = int(metadata.get("byte_size", -1))
    except (TypeError, ValueError):
        return False
    return (
        stored_size == byte_size
        and _normalize_media_type(str(metadata.get("media_type", ""))) == media_type
        and metadata.get("sha256") == sha256
    )


def _receipt(
    plan: TransferPlan,
    *,
    key: str,
    byte_size: int,
    media_type: str,
    sha256: str,
    attempt_state: str,
) -> dict[str, object]:
    receipt_digest = hashlib.sha256(
        f"{plan.asset_id}:{sha256}".encode("ascii")
    ).hexdigest()
    return {
        "schema_version": 1,
        "record_type": "object",
        "object_id": f"object_{receipt_digest}",
        "asset_id": plan.asset_id,
        "source_id": plan.source_id,
        "public_url": plan.public_url,
        "object_key": key,
        "byte_size": byte_size,
        "media_type": media_type,
        "sha256": sha256,
        "attempt_state": attempt_state,
        "evidence_ref": plan.evidence_ref,
    }


def transfer_approved_asset(
    plan: TransferPlan,
    *,
    http_client: HTTPClient,
    storage_client: StorageClient,
    ledger: Ledger,
    cache_directory: str | Path | None = None,
) -> dict[str, object]:
    """Stream one approved asset through disposable cache and record its receipt."""

    # Recheck authorization at the boundary so a manually constructed plan cannot
    # bypass the planner's fail-closed gates.
    checked = plan_transfer(
        asset_id=plan.asset_id,
        source_id=plan.source_id,
        public_url=plan.public_url,
        rights=plan.rights,
        allowed_media_types=plan.allowed_media_types,
        maximum_bytes=plan.maximum_bytes,
        staging_prefix=plan.staging_prefix,
        retention_decision=plan.retention_decision,
        evidence_ref=plan.evidence_ref,
    )
    temporary_path: Path | None = None
    created_object_receipt: dict[str, object] | None = None
    attempted_object_receipt: dict[str, object] | None = None
    try:
        response = http_client.open(checked.public_url)
        final_url = getattr(response, "final_url", checked.public_url)
        try:
            normalized_final_url = validate_public_url(final_url).url
        except Exception:
            _fail("source_url_mismatch", "The response source URL is not approved.")
        if normalized_final_url != checked.public_url:
            _fail("source_url_mismatch", "The response source URL is not approved.")
        media_type = _normalize_media_type(response.media_type)
        if media_type not in checked.allowed_media_types:
            _fail("media_type_mismatch", "Response media type is not approved.")
        if response.content_length is not None and (
            response.content_length < 0
            or response.content_length > checked.maximum_bytes
        ):
            _fail("size_limit_exceeded", "Response exceeds the approved byte bound.")

        hasher = hashlib.sha256()
        byte_size = 0
        cache_path = None if cache_directory is None else Path(cache_directory)
        if cache_path is not None:
            cache_path.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".transfer-",
            suffix=mimetypes.guess_extension(media_type) or ".bin",
            dir=cache_path,
            delete=False,
        )
        temporary_path = Path(handle.name)
        with handle:
            for chunk in response.iter_bytes(_CHUNK_SIZE):
                if not isinstance(chunk, bytes):
                    _fail("invalid_stream", "The response stream yielded invalid data.")
                byte_size += len(chunk)
                if byte_size > checked.maximum_bytes:
                    _fail("size_limit_exceeded", "Response exceeds the approved byte bound.")
                hasher.update(chunk)
                handle.write(chunk)
        if response.content_length is not None and byte_size != response.content_length:
            _fail("size_mismatch", "Response size does not match declared length.")

        sha256 = hasher.hexdigest()
        key = immutable_object_key(checked, sha256)
        expected = _receipt(
            checked,
            key=key,
            byte_size=byte_size,
            media_type=media_type,
            sha256=sha256,
            attempt_state="uploaded",
        )
        existing_receipt = ledger.get_object_by_key(key)
        existing_object = storage_client.head_object(key)
        if existing_receipt is not None:
            if (
                existing_receipt.get("object_key") != key
                or existing_receipt.get("byte_size") != byte_size
                or _normalize_media_type(
                    str(existing_receipt.get("media_type", ""))
                )
                != media_type
                or existing_receipt.get("sha256") != sha256
            ):
                _fail("receipt_conflict", "The immutable receipt conflicts with this transfer.")
            if existing_object is None or not _matching_metadata(
                existing_object,
                byte_size=byte_size,
                media_type=media_type,
                sha256=sha256,
            ):
                _fail("object_conflict", "The immutable object is absent or conflicting.")
            return existing_receipt
        created = False
        if existing_object is None:
            expected["attempt_state"] = "uploaded"
            attempted_object_receipt = dict(expected)
            created = storage_client.create_file_if_absent(
                key,
                temporary_path,
                byte_size=byte_size,
                media_type=media_type,
                sha256=sha256,
            )
            if not isinstance(created, bool):
                _fail(
                    "object_conflict",
                    "The immutable create result could not be verified.",
                    attempted_object_receipt=attempted_object_receipt,
                )
            if created:
                created_object_receipt = attempted_object_receipt
            attempted_object_receipt = None
        final_object = storage_client.head_object(key)
        if final_object is None or not _matching_metadata(
            final_object,
            byte_size=byte_size,
            media_type=media_type,
            sha256=sha256,
        ):
            _fail(
                "object_conflict",
                "The immutable object is absent or has conflicting metadata.",
                created_object_receipt=created_object_receipt,
            )
        expected["attempt_state"] = "uploaded" if created else "reused"
        try:
            return ledger.upsert(
                expected,
                operation_id=f"transfer-receipt:{sha256}",
            )
        except LedgerError as error:
            del error
            _fail(
                "receipt_conflict",
                "The immutable receipt could not be recorded safely.",
                created_object_receipt=created_object_receipt,
            )
    except TransferError:
        raise
    except Exception:
        _fail(
            "transfer_interrupted",
            "The bounded transfer was interrupted.",
            created_object_receipt=created_object_receipt,
            attempted_object_receipt=attempted_object_receipt,
        )
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
