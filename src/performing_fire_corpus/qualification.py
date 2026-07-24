"""Fail-closed, operation-specific qualification for reviewed corpus assets."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.corpus_objects import CorpusObjectError, raw_object_key
from performing_fire_corpus.governance import (
    CANONICAL_ENDPOINT_IDS,
    PROJECT_NATIVE_SOURCE_IDS,
    evaluate_source_operation,
    validate_source_governance,
)
from performing_fire_corpus.policy import (
    AcquisitionPolicyError,
    validate_public_url,
)
from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
QUALIFICATION_OPERATIONS = (
    "metadata_retention",
    "download",
    "raw_storage",
    "ocr",
    "transcription",
    "video_understanding",
    "indexing",
    "score_generation",
    "public_retrieval",
)
_SOURCE_REQUIREMENTS = {
    "metadata_retention": ("metadata_inventory", "retention"),
    "download": ("acquisition_eligibility", "media_acquisition"),
    "raw_storage": ("retention",),
    "ocr": ("derivative_eligibility", "derived_processing"),
    "transcription": ("derivative_eligibility", "derived_processing"),
    "video_understanding": ("derivative_eligibility", "derived_processing"),
    "indexing": ("indexing",),
    "score_generation": ("derivative_eligibility", "derived_processing"),
    "public_retrieval": ("public_retrieval",),
}
_CONTENT_OPERATIONS = frozenset(QUALIFICATION_OPERATIONS) - {
    "metadata_retention"
}
_DERIVED_OPERATIONS = frozenset(
    {
        "ocr",
        "transcription",
        "video_understanding",
        "indexing",
        "score_generation",
        "public_retrieval",
    }
)
_ASSET_KEYS = frozenset(
    {
        "source_id",
        "endpoint_id",
        "asset_id",
        "asset_kind",
        "public_url",
        "expected_host",
        "media_type",
        "max_bytes",
        "access_state",
        "retention_class",
        "deletion_policy",
        "derivative_policy",
        "retrieval_policy",
        "planned_object_key",
    }
)
_DECISION_KEYS = frozenset(
    {
        "operation",
        "state",
        "decision_scope",
        "basis_code",
        "authority_class",
        "evidence_ref",
        "decided_at",
        "expires_at",
        "review_trigger",
        "asset_facts_sha256",
        "retention_class",
    }
)
_APPROVAL_FIELDS = (
    "decision_scope",
    "basis_code",
    "authority_class",
    "evidence_ref",
    "decided_at",
    "expires_at",
    "review_trigger",
    "asset_facts_sha256",
    "retention_class",
)
_ASSET_ID = re.compile(r"^asset_[a-z0-9][a-z0-9._-]{0,127}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_LABEL = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_EVIDENCE = re.compile(r"^evidence_[a-z0-9][a-z0-9._-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ACCESS_STATES = frozenset(
    {
        "available",
        "expired_url",
        "http_401",
        "http_403",
        "login_required",
        "signed_url",
        "subscription_required",
        "unknown",
    }
)
_RETENTION_CLASSES = frozenset(
    {
        "inventory_metadata",
        "project_native_expiring",
        "selected_derived",
        "selected_raw",
    }
)
_ASSET_KINDS = frozenset(
    {"attachment", "caption", "document", "image", "media", "prose"}
)
_APPROVED_CONTENT_BASES = frozenset(
    {"asset_specific_permission", "reviewed_lawful_basis"}
)
_APPROVED_CONTENT_AUTHORITIES = frozenset(
    {
        "authorized_licensor",
        "copyright_holder",
        "legal_reviewer",
        "rights_reviewer",
    }
)
_SOURCE_PUBLIC_HOSTS = {
    "antiegg-fluxus": frozenset({"antiegg.kr"}),
    "njp-center-main": frozenset({"njp.ggcf.kr"}),
    "njp-center-video-archive": frozenset({"njp.ggcf.kr"}),
    "njp-video-library": frozenset({"njpvideo.ggcf.kr"}),
    "njp-youtube-official": frozenset({"www.youtube.com"}),
}


class QualificationError(ValueError):
    """Raised when qualification input or current authority is unsafe."""


class QualificationAuthorityResolver(Protocol):
    """Trusted current qualification authority used by portable ledger queries."""

    def resolve_asset_qualification(
        self, *, source_id: str, asset_id: str
    ) -> Mapping[str, Any] | None: ...


def _schema_resource() -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", "asset-qualification.json"
    )
    if packaged.is_file():
        return packaged
    return (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "v1"
        / "asset-qualification.json"
    )


def _canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise QualificationError(
            "qualification data must be deterministic JSON"
        ) from error


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _parse_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise QualificationError(f"{field} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise QualificationError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise QualificationError("evaluation time must be timezone-aware")
    return (
        value.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _validate_planned_key(
    value: Any, *, source_id: str, asset_id: str
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QualificationError("planned object key must be exact or absent")
    marker = f"/v1/raw/{source_id}/{asset_id}/"
    if value.count(marker) != 1:
        raise QualificationError("planned object key is outside the raw namespace")
    prefix, sha256 = value.split(marker, 1)
    try:
        expected = raw_object_key(
            f"{prefix}/",
            source_id,
            asset_id,
            sha256,
        )
    except CorpusObjectError as error:
        raise QualificationError("planned object key is invalid") from error
    if expected != value:
        raise QualificationError("planned object key does not match asset facts")
    return value


def _validate_asset_facts(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ASSET_KEYS:
        raise QualificationError("asset facts must use the strict reviewed shape")
    record = copy.deepcopy(dict(value))
    if sanitize(record, environ={}) != record:
        raise QualificationError("asset facts contain private or secret-like data")
    source_id = record["source_id"]
    if source_id in PROJECT_NATIVE_SOURCE_IDS:
        raise QualificationError(
            "project-native assets require the consent lifecycle path"
        )
    if source_id not in CANONICAL_ENDPOINT_IDS:
        raise QualificationError("asset source is not canonical")
    endpoint_id = record["endpoint_id"]
    if endpoint_id not in CANONICAL_ENDPOINT_IDS[source_id]:
        raise QualificationError("asset endpoint does not belong to its source")
    asset_id = record["asset_id"]
    if not isinstance(asset_id, str) or _ASSET_ID.fullmatch(asset_id) is None:
        raise QualificationError("asset identifier is invalid")
    if record["asset_kind"] not in _ASSET_KINDS:
        raise QualificationError("asset kind is invalid")
    expected_host = record["expected_host"]
    if (
        not isinstance(expected_host, str)
        or _HOST.fullmatch(expected_host) is None
        or expected_host != expected_host.lower()
        or expected_host not in _SOURCE_PUBLIC_HOSTS[source_id]
    ):
        raise QualificationError(
            "expected host is outside the source-scoped host boundary"
        )
    public_url = record["public_url"]
    try:
        checked_url = validate_public_url(
            public_url,
            allowed_hosts=_SOURCE_PUBLIC_HOSTS[source_id],
        )
    except (AcquisitionPolicyError, TypeError) as error:
        raise QualificationError(
            "public URL is outside the source-scoped host boundary or "
            "credential-bearing"
        ) from error
    if (
        not isinstance(public_url, str)
        or checked_url.hostname != expected_host
        or checked_url.url != public_url
    ):
        raise QualificationError(
            "public URL must use its exact reviewed canonical form"
        )
    media_type = record["media_type"]
    if not isinstance(media_type, str) or _MEDIA_TYPE.fullmatch(media_type) is None:
        raise QualificationError("media type is invalid")
    max_bytes = record["max_bytes"]
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= 1024 * 1024 * 1024
    ):
        raise QualificationError("byte bound must be positive and bounded")
    if record["access_state"] not in _ACCESS_STATES:
        raise QualificationError("access state is invalid")
    if record["retention_class"] not in _RETENTION_CLASSES:
        raise QualificationError("retention class is invalid")
    if record["deletion_policy"] not in {
        "delete_on_revocation",
        "metadata_only",
        "review_on_revocation",
        "same_proof_cleanup",
    }:
        raise QualificationError("deletion policy is invalid")
    if record["derivative_policy"] not in {"none", "operation_specific"}:
        raise QualificationError("derivative policy is invalid")
    if record["retrieval_policy"] not in {"none", "public", "restricted"}:
        raise QualificationError("retrieval policy is invalid")
    record["planned_object_key"] = _validate_planned_key(
        record["planned_object_key"],
        source_id=str(source_id),
        asset_id=str(asset_id),
    )
    return record


def asset_facts_sha256(value: Mapping[str, Any]) -> str:
    """Bind the exact URL, host, MIME, bounds, and lifecycle facts."""

    return _sha256(_validate_asset_facts(value))


def _decision_value(value: Any, field: str, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None
    return value


def _safe_review_trigger(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or "\r" in value
        or "\n" in value
        or sanitize(value, environ={}) != value
    ):
        return None
    return value


def _source_expiry(record: Mapping[str, Any], evaluated_at: datetime) -> datetime:
    expiries: list[datetime] = []
    for collection in ("observations", "decisions"):
        for item in record[collection]:
            expiries.append(_parse_time(item["expires_at"], "source expiry"))
    return min(expiries) if expiries else evaluated_at


def _source_reasons(
    governance: Mapping[str, Any],
    operation: str,
    *,
    asset_id: str,
    asset_kind: str,
    source_id: str,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    requirements = set(_SOURCE_REQUIREMENTS[operation])
    requirements.add("deletion")
    if operation in _CONTENT_OPERATIONS and asset_kind == "caption":
        requirements.add("caption_retention")
    if operation in _CONTENT_OPERATIONS and asset_kind == "prose":
        requirements.add("prose_retention")
    if operation in {"indexing", "public_retrieval"}:
        requirements.add("search_visibility")
    for source_operation in sorted(requirements):
        result = evaluate_source_operation(
            governance,
            source_operation,
            reviewed_asset_sources={asset_id: source_id},
            now=now,
        )
        reasons.extend(
            f"source:{source_operation}:{reason}"
            for reason in result["reasons"]
        )
    return reasons


def _compile_decision(
    operation: str,
    raw: Mapping[str, Any] | None,
    *,
    asset: Mapping[str, Any],
    governance: Mapping[str, Any],
    snapshot_sha256: str,
    now: datetime,
) -> dict[str, Any]:
    if raw is None:
        return {
            "operation": operation,
            "state": "pending",
            "eligible": False,
            "decision_scope": None,
            "basis_code": None,
            "authority_class": None,
            "evidence_ref": None,
            "decided_at": None,
            "expires_at": None,
            "review_trigger": None,
            "asset_facts_sha256": None,
            "retention_class": None,
            "reasons": ["decision:missing"],
        }

    state = raw.get("state")
    if state not in {"approved", "blocked", "pending", "revoked"}:
        state = "pending"
    scope = raw.get("decision_scope")
    if scope not in {"asset_specific", "source_policy"}:
        scope = None
    basis_code = _decision_value(raw.get("basis_code"), "basis_code", _LABEL)
    authority_class = _decision_value(
        raw.get("authority_class"), "authority_class", _LABEL
    )
    evidence_ref = _decision_value(
        raw.get("evidence_ref"), "evidence_ref", _EVIDENCE
    )
    decided_at = raw.get("decided_at")
    expires_at = raw.get("expires_at")
    review_trigger = _safe_review_trigger(raw.get("review_trigger"))
    decision_snapshot = _decision_value(
        raw.get("asset_facts_sha256"), "asset_facts_sha256", _HASH
    )
    retention_class = raw.get("retention_class")
    if retention_class not in _RETENTION_CLASSES:
        retention_class = None
    output = {
        "operation": operation,
        "state": state,
        "eligible": False,
        "decision_scope": scope,
        "basis_code": basis_code,
        "authority_class": authority_class,
        "evidence_ref": evidence_ref,
        "decided_at": decided_at if isinstance(decided_at, str) else None,
        "expires_at": expires_at if isinstance(expires_at, str) else None,
        "review_trigger": review_trigger,
        "asset_facts_sha256": decision_snapshot,
        "retention_class": retention_class,
        "reasons": [],
    }
    reasons: list[str] = []
    missing = [
        field
        for field in _APPROVAL_FIELDS
        if output[field] is None
    ]
    if state == "approved" and missing:
        output["state"] = "pending"
        reasons.append("decision:incomplete")
    elif state != "approved":
        reasons.append(f"decision:{state}")

    if output["state"] == "approved":
        try:
            decided = _parse_time(output["decided_at"], "decided_at")
            expires = _parse_time(output["expires_at"], "expires_at")
        except QualificationError:
            output["state"] = "pending"
            output["decided_at"] = None
            output["expires_at"] = None
            reasons.append("decision:invalid_time")
        else:
            if decided >= expires:
                output["state"] = "pending"
                reasons.append("decision:invalid_window")
            elif decided > now:
                reasons.append("decision:not_yet_effective")
            elif expires <= now:
                reasons.append("decision:expired")

    if basis_code == "public_visibility":
        output["state"] = "blocked"
        reasons.append("basis:public_visibility")
    if decision_snapshot is not None and decision_snapshot != snapshot_sha256:
        reasons.append("asset_facts:changed")
    if (
        retention_class is not None
        and retention_class != asset["retention_class"]
    ):
        reasons.append("retention:mismatch")

    if operation in _CONTENT_OPERATIONS:
        if asset["access_state"] != "available":
            reasons.append(f"access:{asset['access_state']}")
        if scope != "asset_specific":
            reasons.append("rights:asset_specific_scope_required")
        if basis_code not in _APPROVED_CONTENT_BASES:
            reasons.append("rights:affirmative_basis_required")
        if authority_class not in _APPROVED_CONTENT_AUTHORITIES:
            reasons.append("rights:reviewed_authority_required")
        if (
            asset["source_id"] == "njp-youtube-official"
            and asset["asset_kind"] in {"caption", "media"}
            and (
                scope != "asset_specific"
                or basis_code not in _APPROVED_CONTENT_BASES
            )
        ):
            reasons.append("youtube:asset_specific_rights_required")
        if (
            asset["source_id"] == "antiegg-fluxus"
            and asset["asset_kind"] in {"prose", "media"}
            and (
                scope != "asset_specific"
                or basis_code not in _APPROVED_CONTENT_BASES
            )
        ):
            reasons.append("antiegg:content_rights_required")
    if (
        operation in {"ocr", "transcription", "video_understanding", "score_generation"}
        and asset["derivative_policy"] != "operation_specific"
    ):
        reasons.append("derivative:prohibited")
    if operation == "public_retrieval" and asset["retrieval_policy"] != "public":
        reasons.append("retrieval:not_public")
    if operation == "raw_storage" and asset["retention_class"] == "inventory_metadata":
        reasons.append("retention:metadata_only")

    reasons.extend(
        _source_reasons(
            governance,
            operation,
            asset_id=str(asset["asset_id"]),
            asset_kind=str(asset["asset_kind"]),
            source_id=str(asset["source_id"]),
            now=now,
        )
    )
    output["reasons"] = sorted(set(reasons))
    output["eligible"] = output["state"] == "approved" and not output["reasons"]
    return output


def compile_asset_qualification(
    asset: Mapping[str, Any],
    source_governance: Mapping[str, Any],
    operation_decisions: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Compile one current qualification without inferring any operation."""

    if now.tzinfo is None:
        raise QualificationError("evaluation time must be timezone-aware")
    current = now.astimezone(UTC)
    asset_value = _validate_asset_facts(asset)
    try:
        governance = validate_source_governance(source_governance)
    except Exception as error:
        raise QualificationError("source governance is invalid") from error
    if (
        governance["source_id"] != asset_value["source_id"]
        or governance.get("endpoint_id")
        not in {None, asset_value["endpoint_id"]}
        or governance.get("asset_id")
        not in {None, asset_value["asset_id"]}
    ):
        raise QualificationError("source governance is not bound to the asset")
    decision_map: dict[str, Mapping[str, Any]] = {}
    if not isinstance(operation_decisions, Sequence) or isinstance(
        operation_decisions, (str, bytes, bytearray)
    ):
        raise QualificationError("operation decisions must be a bounded sequence")
    for decision in operation_decisions:
        if not isinstance(decision, Mapping) or not set(decision).issubset(
            _DECISION_KEYS
        ):
            raise QualificationError("operation decision shape is unsafe")
        operation = decision.get("operation")
        if operation not in QUALIFICATION_OPERATIONS:
            raise QualificationError("operation decision is unknown")
        if operation in decision_map:
            raise QualificationError("conflicting operation decisions")
        if sanitize(decision, environ={}) != decision:
            raise QualificationError("operation decision contains private data")
        decision_map[str(operation)] = copy.deepcopy(dict(decision))

    snapshot_sha256 = _sha256(asset_value)
    governance_sha256 = _sha256(governance)
    decisions = [
        _compile_decision(
            operation,
            decision_map.get(operation),
            asset=asset_value,
            governance=governance,
            snapshot_sha256=snapshot_sha256,
            now=current,
        )
        for operation in QUALIFICATION_OPERATIONS
    ]
    payload = {
        "schema_version": 1,
        "record_type": "asset_qualification",
        **asset_value,
        "asset_facts_sha256": snapshot_sha256,
        "source_governance_snapshot_sha256": governance_sha256,
        "source_governance_expires_at": _utc_text(
            _source_expiry(governance, current)
        ),
        "evaluated_at": _utc_text(current),
        "operation_decisions": decisions,
    }
    qualification_id = f"qualification_{_sha256(payload)[:24]}"
    record = {
        **payload,
        "qualification_id": qualification_id,
    }
    record["qualification_sha256"] = _sha256(record)
    return validate_asset_qualification(record, now=current)


def _qualification_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(child)
        for key, child in value.items()
        if key not in {"qualification_id", "qualification_sha256"}
    }


def validate_asset_qualification(
    value: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate strict schema, bindings, ordering, and current eligibility."""

    if not isinstance(value, Mapping):
        raise QualificationError("asset qualification must be an object")
    record = copy.deepcopy(dict(value))
    try:
        schema = json.loads(_schema_resource().read_text(encoding="utf-8"))
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(record)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise QualificationError(
            "asset qualification does not match the strict schema"
        ) from error
    if sanitize(record, environ={}) != record:
        raise QualificationError("asset qualification contains private data")
    asset_value = {
        key: copy.deepcopy(record[key])
        for key in _ASSET_KEYS
    }
    checked_asset = _validate_asset_facts(asset_value)
    if record["asset_facts_sha256"] != _sha256(checked_asset):
        raise QualificationError("asset qualification facts are not hash-bound")
    if [item["operation"] for item in record["operation_decisions"]] != list(
        QUALIFICATION_OPERATIONS
    ):
        raise QualificationError("operation decisions are not canonical")
    for decision in record["operation_decisions"]:
        if decision["reasons"] != sorted(set(decision["reasons"])):
            raise QualificationError("qualification reasons are not canonical")
        if decision["state"] == "approved":
            if _parse_time(decision["decided_at"], "decided_at") >= _parse_time(
                decision["expires_at"], "expires_at"
            ):
                raise QualificationError("approved decision window is invalid")
        if not decision["eligible"]:
            continue
        if (
            decision["asset_facts_sha256"] != record["asset_facts_sha256"]
            or decision["retention_class"] != record["retention_class"]
            or decision["basis_code"] == "public_visibility"
        ):
            raise QualificationError("eligible decision is not bound to asset facts")
        operation = decision["operation"]
        if operation in _CONTENT_OPERATIONS:
            if (
                record["access_state"] != "available"
                or decision["decision_scope"] != "asset_specific"
            ):
                raise QualificationError(
                    "eligible content decision lacks exact access authority"
                )
            if (
                decision["basis_code"] not in _APPROVED_CONTENT_BASES
                or decision["authority_class"]
                not in _APPROVED_CONTENT_AUTHORITIES
            ):
                raise QualificationError(
                    "eligible content decision lacks affirmative reviewed rights"
                )
            if (
                (
                    record["source_id"] == "njp-youtube-official"
                    and record["asset_kind"] in {"caption", "media"}
                )
                or (
                    record["source_id"] == "antiegg-fluxus"
                    and record["asset_kind"] in {"prose", "media"}
                )
            ) and decision["basis_code"] not in _APPROVED_CONTENT_BASES:
                raise QualificationError(
                    "eligible content decision lacks asset-specific rights"
                )
        if (
            operation
            in {"ocr", "transcription", "video_understanding", "score_generation"}
            and record["derivative_policy"] != "operation_specific"
        ):
            raise QualificationError("eligible derivative contradicts policy")
        if (
            operation == "public_retrieval"
            and record["retrieval_policy"] != "public"
        ):
            raise QualificationError("eligible retrieval is not public")
        if (
            operation == "raw_storage"
            and record["retention_class"] == "inventory_metadata"
        ):
            raise QualificationError("eligible raw storage is metadata-only")
    payload = _qualification_payload(record)
    expected_id = f"qualification_{_sha256(payload)[:24]}"
    if record["qualification_id"] != expected_id:
        raise QualificationError("qualification identifier is not bound")
    without_hash = {
        key: copy.deepcopy(child)
        for key, child in record.items()
        if key != "qualification_sha256"
    }
    if record["qualification_sha256"] != _sha256(without_hash):
        raise QualificationError("qualification hash is invalid")
    evaluated_at = _parse_time(record["evaluated_at"], "evaluated_at")
    source_expiry = _parse_time(
        record["source_governance_expires_at"],
        "source_governance_expires_at",
    )
    if now is not None:
        if now.tzinfo is None:
            raise QualificationError("evaluation time must be timezone-aware")
        current = now.astimezone(UTC)
        if evaluated_at > current:
            raise QualificationError("qualification is not yet effective")
        if source_expiry <= current and any(
            item["eligible"] for item in record["operation_decisions"]
        ):
            raise QualificationError("source governance authority is expired")
        for decision in record["operation_decisions"]:
            if decision["eligible"] and (
                _parse_time(decision["decided_at"], "decided_at") > current
                or _parse_time(decision["expires_at"], "expires_at") <= current
            ):
                raise QualificationError("eligible decision is not current")
    return record


def _current_qualification(
    value: Mapping[str, Any],
    *,
    authority_resolver: QualificationAuthorityResolver,
    now: datetime,
) -> dict[str, Any] | None:
    candidate = validate_asset_qualification(value, now=now)
    try:
        current = authority_resolver.resolve_asset_qualification(
            source_id=str(candidate["source_id"]),
            asset_id=str(candidate["asset_id"]),
        )
        if current is None:
            return None
        checked = validate_asset_qualification(current, now=now)
    except Exception:
        return None
    if (
        checked["qualification_id"] != candidate["qualification_id"]
        or checked["qualification_sha256"] != candidate["qualification_sha256"]
        or _canonical(checked) != _canonical(candidate)
    ):
        return None
    return checked


def query_qualified_assets(
    values: Sequence[Mapping[str, Any]],
    *,
    operation: str,
    authority_resolver: QualificationAuthorityResolver,
    now: datetime,
) -> list[dict[str, Any]]:
    """Return only current exact qualifications eligible for one operation."""

    if operation not in QUALIFICATION_OPERATIONS:
        raise QualificationError("qualification operation is unknown")
    if now.tzinfo is None:
        raise QualificationError("evaluation time must be timezone-aware")
    targets: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for value in values:
        checked = validate_asset_qualification(value, now=now)
        target = (str(checked["source_id"]), str(checked["asset_id"]))
        if target in targets:
            raise QualificationError("duplicate qualification candidate")
        targets.add(target)
        current = _current_qualification(
            checked,
            authority_resolver=authority_resolver,
            now=now,
        )
        if current is None:
            continue
        decision = next(
            item
            for item in current["operation_decisions"]
            if item["operation"] == operation
        )
        if decision["eligible"]:
            results.append(current)
    return sorted(
        results,
        key=lambda item: (str(item["source_id"]), str(item["asset_id"])),
    )


def build_qualified_job(
    value: Mapping[str, Any],
    *,
    operation: str,
    authority_resolver: QualificationAuthorityResolver,
    now: datetime,
) -> dict[str, Any]:
    """Emit only stable IDs and one exact immutable R2 input key."""

    if operation not in _DERIVED_OPERATIONS:
        raise QualificationError(
            "only object-backed downstream operations emit minimal jobs"
        )
    eligible = query_qualified_assets(
        [value],
        operation=operation,
        authority_resolver=authority_resolver,
        now=now,
    )
    if len(eligible) != 1:
        raise QualificationError("current authority does not permit this job")
    current = eligible[0]
    raw_decision = next(
        item
        for item in current["operation_decisions"]
        if item["operation"] == "raw_storage"
    )
    object_key = current["planned_object_key"]
    if not raw_decision["eligible"] or object_key is None:
        raise QualificationError("current authority lacks an immutable input object")
    return {
        "qualification_id": current["qualification_id"],
        "source_id": current["source_id"],
        "asset_id": current["asset_id"],
        "operation": operation,
        "input_object_key": object_key,
    }
