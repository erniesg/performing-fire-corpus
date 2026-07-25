"""Synthetic-only project-native consent and lifecycle enforcement."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.governance import (
    GovernanceError,
    PROJECT_NATIVE_SOURCE_IDS,
    validate_project_native_contract,
)
from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
PROJECT_NATIVE_OPERATIONS = frozenset(
    {
        "derived_processing",
        "indexing",
        "metadata_inventory",
        "public_retrieval",
        "retention",
        "score_generation",
        "search_visibility",
        "subject_export",
    }
)
PROJECT_NATIVE_RETENTION_DEFAULT_DAYS = {
    "artist_submission": 90,
    "generated_score": 90,
    "performer_annotation": 60,
    "performer_choice": 60,
    "visitor_input": 30,
    "visual_system_history": 30,
    "visual_system_state": 30,
}
PROJECT_NATIVE_DELETION_SLA_HOURS = {
    "artist_submission": 168,
    "generated_score": 72,
    "performer_annotation": 72,
    "performer_choice": 72,
    "visitor_input": 72,
    "visual_system_history": 72,
    "visual_system_state": 72,
}
_DATA_CLASS_SOURCE = {
    "artist_submission": "project-native-artist-submissions",
    "generated_score": "project-native-generated-scores",
    "performer_annotation": "project-native-performer-annotations",
    "performer_choice": "project-native-performer-annotations",
    "visitor_input": "project-native-visitor-inputs",
    "visual_system_history": "project-native-visual-system-state",
    "visual_system_state": "project-native-visual-system-state",
}
_PARTICIPANT_DATA_CLASSES = frozenset(
    {
        "artist_submission",
        "performer_annotation",
        "performer_choice",
        "visitor_input",
    }
)
_DERIVED_DATA_CLASSES = frozenset(
    {
        "generated_score",
        "visual_system_history",
        "visual_system_state",
    }
)
_CONFIDENTIALITY_RANK = {
    "public": 0,
    "restricted": 1,
    "sensitive": 2,
}
_HASH = re.compile(r"^[0-9a-f]{64}$")


class ProjectNativeLifecycleError(ValueError):
    """Raised when project-native data could escape its reviewed lifecycle."""


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "v1"
        / f"{name}.json"
    )


def _validate_schema(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectNativeLifecycleError(f"{name} record must be an object")
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).validate(dict(value))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise ProjectNativeLifecycleError(
            f"{name} record does not match the strict schema"
        ) from error
    if sanitize(value, environ={}) != value:
        raise ProjectNativeLifecycleError(
            f"{name} record contains private or secret-like data"
        )
    return copy.deepcopy(dict(value))


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
        raise ProjectNativeLifecycleError(
            "project-native data must be deterministic JSON"
        ) from error


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _parse_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ProjectNativeLifecycleError(
            f"{field} is not a valid timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise ProjectNativeLifecycleError(
            f"{field} must be timezone-aware"
        )
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProjectNativeLifecycleError(
            "lifecycle time must be timezone-aware"
        )
    return (
        value.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _sorted_unique(values: Sequence[str], field: str) -> list[str]:
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or any(not isinstance(value, str) for value in values)
    ):
        raise ProjectNativeLifecycleError(f"{field} must contain stable IDs")
    result = sorted(values)
    if len(result) != len(set(result)):
        raise ProjectNativeLifecycleError(f"{field} contains duplicates")
    return result


def _validate_object_key(
    value: str,
    *,
    namespace: str,
    source_id: str,
    contribution_id: str,
) -> None:
    prefix = (
        f"performing-fire/v1/{namespace}/{source_id}/"
        f"{contribution_id}/"
    )
    if (
        not value.startswith(prefix)
        or not _HASH.fullmatch(value.removeprefix(prefix))
        or ".." in value
        or "\\" in value
        or "//" in value
    ):
        raise ProjectNativeLifecycleError(
            f"{namespace} object key is outside the exact immutable namespace"
        )


def _validate_consent_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate_schema("consent", value)
    if record["source_id"] not in PROJECT_NATIVE_SOURCE_IDS:
        raise ProjectNativeLifecycleError(
            "consent targets an unreviewed project-native family"
        )
    if (
        record["state"] == "active"
        and (
            not record["withdrawal_supported"]
            or not record["notice_version"]
            or not record["authority_class"]
            or not record["deletion_owner_role"]
        )
    ):
        raise ProjectNativeLifecycleError(
            "active consent is not affirmative and withdrawable"
        )
    if _parse_time(
        record["decided_at"], "consent.decided_at"
    ) >= _parse_time(record["expires_at"], "consent.expires_at"):
        raise ProjectNativeLifecycleError(
            "consent authority chronology is invalid"
        )
    return record


def _require_consent_effective(
    consent: Mapping[str, Any],
    *,
    at: datetime,
    operation: str,
) -> None:
    if at.tzinfo is None:
        raise ProjectNativeLifecycleError(
            f"{operation} time must be timezone-aware"
        )
    current = at.astimezone(UTC)
    if not (
        _parse_time(consent["decided_at"], "consent.decided_at")
        <= current
        < _parse_time(consent["expires_at"], "consent.expires_at")
    ):
        raise ProjectNativeLifecycleError(
            f"{operation} lacks currently effective consent"
        )


def _validate_bound_record(
    name: str,
    value: Mapping[str, Any],
    *,
    id_field: str,
    hash_field: str,
    id_prefix: str,
) -> dict[str, Any]:
    record = _validate_schema(name, value)
    payload = {
        key: child
        for key, child in record.items()
        if key not in {id_field, hash_field}
    }
    expected_id = f"{id_prefix}{_sha256(payload)[:24]}"
    if record[id_field] != expected_id:
        raise ProjectNativeLifecycleError(
            f"{name} identifier is not bound to its exact payload"
        )
    without_hash = {
        key: child
        for key, child in record.items()
        if key != hash_field
    }
    if record[hash_field] != _sha256(without_hash):
        raise ProjectNativeLifecycleError(
            f"{name} hash is not bound to its exact payload"
        )
    return record


def _validate_export_job_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    record = _validate_bound_record(
        "project-native-export-job",
        value,
        id_field="export_job_id",
        hash_field="export_job_sha256",
        id_prefix="project_native_export_",
    )
    for field in ("consent_ids", "contribution_ids", "object_keys"):
        if record[field] != _sorted_unique(record[field], field):
            raise ProjectNativeLifecycleError(
                f"{field} must use canonical sorted order"
            )
    if _parse_time(record["expires_at"], "export.expires_at") <= _parse_time(
        record["requested_at"],
        "export.requested_at",
    ):
        raise ProjectNativeLifecycleError(
            "subject export window is invalid"
        )
    return record


def _validate_deletion_work_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    record = _validate_bound_record(
        "project-native-deletion-work",
        value,
        id_field="work_id",
        hash_field="work_sha256",
        id_prefix="project_native_deletion_work_",
    )
    target_ids = [target["contribution_id"] for target in record["targets"]]
    if (
        target_ids != sorted(target_ids)
        or len(target_ids) != len(set(target_ids))
        or target_ids != record["contribution_ids"]
    ):
        raise ProjectNativeLifecycleError(
            "deletion targets are not bound to contribution IDs"
        )
    for target in record["targets"]:
        for field in (
            "raw_object_keys",
            "derived_object_keys",
            "index_document_ids",
            "cache_entry_ids",
            "score_export_ids",
        ):
            if target[field] != _sorted_unique(target[field], field):
                raise ProjectNativeLifecycleError(
                    "deletion target arrays are not canonical"
                )
    for field in (
        "raw_object_keys",
        "derived_object_keys",
        "index_document_ids",
        "cache_entry_ids",
        "score_export_ids",
    ):
        expected = sorted(
            {
                item
                for target in record["targets"]
                for item in target[field]
            }
        )
        if record[field] != expected:
            raise ProjectNativeLifecycleError(
                "deletion aggregate targets do not match lineage"
            )
    if (
        (record["state"] == "legal_hold_review")
        != (record["legal_hold_id"] is not None)
    ):
        raise ProjectNativeLifecycleError(
            "deletion work legal-hold state is inconsistent"
        )
    if _parse_time(
        record["deletion_due_at"],
        "deletion_work.deletion_due_at",
    ) <= _parse_time(
        record["requested_at"],
        "deletion_work.requested_at",
    ):
        raise ProjectNativeLifecycleError(
            "deletion work due time is invalid"
        )
    return record


def validate_project_native_contribution(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one content-free contribution lifecycle record."""

    record = _validate_schema("project-native-contribution", value)
    if (
        record["source_id"] not in PROJECT_NATIVE_SOURCE_IDS
        or _DATA_CLASS_SOURCE.get(record["data_class"])
        != record["source_id"]
    ):
        raise ProjectNativeLifecycleError(
            "data class does not belong to its project-native source"
        )
    if record["data_class"] in _PARTICIPANT_DATA_CLASSES:
        if record["subject_ref"] is None or len(record["consent_ids"]) != 1:
            raise ProjectNativeLifecycleError(
                "participant contribution lacks one pseudonymous consent subject"
            )
        if record["input_contribution_ids"]:
            raise ProjectNativeLifecycleError(
                "participant contribution cannot claim derived inputs"
            )
        if record["system_provenance_id"] is not None:
            raise ProjectNativeLifecycleError(
                "participant contribution cannot claim system provenance"
            )
    else:
        if (
            record["subject_ref"] is not None
            or not record["input_contribution_ids"]
            or record["system_provenance_id"] is None
        ):
            raise ProjectNativeLifecycleError(
                "derived contribution lacks input and system provenance"
            )
    if record["contribution_id"] in record["input_contribution_ids"]:
        raise ProjectNativeLifecycleError(
            "contribution cannot derive from itself"
        )
    for field in (
        "allowed_audiences",
        "allowed_uses",
        "consent_ids",
        "input_contribution_ids",
        "index_document_ids",
        "cache_entry_ids",
        "score_export_ids",
    ):
        if record[field] != _sorted_unique(record[field], field):
            raise ProjectNativeLifecycleError(
                f"{field} must use canonical sorted order"
            )
    for namespace, field in (
        ("raw", "raw_object_keys"),
        ("derived", "derived_object_keys"),
    ):
        if record[field] != _sorted_unique(record[field], field):
            raise ProjectNativeLifecycleError(
                f"{field} must use canonical sorted order"
            )
        for object_key in record[field]:
            _validate_object_key(
                object_key,
                namespace=namespace,
                source_id=record["source_id"],
                contribution_id=record["contribution_id"],
            )
    if (
        record["withdrawal_state"] != "current"
        or record["deletion_state"] != "active"
    ) and record["allowed_uses"]:
        raise ProjectNativeLifecycleError(
            "inactive contribution cannot retain allowed uses"
        )
    created_at = _parse_time(record["created_at"], "created_at")
    retention_expiry = _parse_time(
        record["retention_expires_at"],
        "retention_expires_at",
    )
    if retention_expiry <= created_at:
        raise ProjectNativeLifecycleError(
            "retention expiry must follow contribution creation"
        )
    default_expiry = created_at + timedelta(
        days=PROJECT_NATIVE_RETENTION_DEFAULT_DAYS[record["data_class"]]
    )
    if retention_expiry > default_expiry:
        raise ProjectNativeLifecycleError(
            "retention exceeds the data-class default"
        )
    return record


def build_project_native_contribution(
    *,
    contribution_id: str,
    subject_ref: str,
    source_id: str,
    data_class: str,
    purpose_code: str,
    consent: Mapping[str, Any],
    confidentiality_class: str,
    allowed_audiences: Sequence[str],
    allowed_uses: Sequence[str],
    provenance_id: str,
    input_contribution_ids: Sequence[str],
    raw_object_keys: Sequence[str],
    derived_object_keys: Sequence[str],
    index_document_ids: Sequence[str],
    cache_entry_ids: Sequence[str],
    score_export_ids: Sequence[str],
    retention_expires_at: str | None,
    system_provenance_id: str | None,
    created_at: str,
) -> dict[str, Any]:
    """Create one minimized participant-originated lifecycle record."""

    consent_value = _validate_consent_record(consent)
    if _DATA_CLASS_SOURCE.get(data_class) != source_id:
        raise ProjectNativeLifecycleError(
            "data class does not belong to its project-native source"
        )
    if (
        consent_value["state"] != "active"
        or consent_value["source_id"] != source_id
        or consent_value["subject_ref"] != subject_ref
        or consent_value["purpose_code"] != purpose_code
        or consent_value["confidentiality_class"] != confidentiality_class
    ):
        raise ProjectNativeLifecycleError(
            "contribution does not match current specific consent"
        )
    audiences = _sorted_unique(allowed_audiences, "allowed_audiences")
    uses = _sorted_unique(allowed_uses, "allowed_uses")
    if not set(audiences).issubset(consent_value["allowed_viewer_roles"]):
        raise ProjectNativeLifecycleError(
            "contribution audience exceeds consent"
        )
    if not set(uses).issubset(consent_value["allowed_uses"]):
        raise ProjectNativeLifecycleError(
            "contribution use exceeds consent"
        )
    created = _parse_time(created_at, "created_at")
    _require_consent_effective(
        consent_value,
        at=created,
        operation="contribution intake",
    )
    retention_text = (
        _utc_text(
            min(
                created
                + timedelta(
                    days=PROJECT_NATIVE_RETENTION_DEFAULT_DAYS[data_class]
                ),
                _parse_time(
                    consent_value["expires_at"],
                    "consent.expires_at",
                ),
            )
        )
        if retention_expires_at is None
        else retention_expires_at
    )
    if _parse_time(retention_text, "retention_expires_at") > _parse_time(
        consent_value["expires_at"], "consent.expires_at"
    ):
        raise ProjectNativeLifecycleError(
            "contribution retention exceeds consent"
        )
    record = {
        "schema_version": 1,
        "record_type": "project_native_contribution",
        "contribution_id": contribution_id,
        "subject_ref": subject_ref,
        "source_id": source_id,
        "data_class": data_class,
        "purpose_code": purpose_code,
        "consent_ids": [consent_value["consent_id"]],
        "consent_notice_version": consent_value["notice_version"],
        "consent_state": consent_value["state"],
        "confidentiality_class": confidentiality_class,
        "allowed_audiences": audiences,
        "allowed_uses": uses,
        "provenance_id": provenance_id,
        "input_contribution_ids": _sorted_unique(
            input_contribution_ids,
            "input_contribution_ids",
        ),
        "raw_object_keys": _sorted_unique(
            raw_object_keys,
            "raw_object_keys",
        ),
        "derived_object_keys": _sorted_unique(
            derived_object_keys,
            "derived_object_keys",
        ),
        "index_document_ids": _sorted_unique(
            index_document_ids,
            "index_document_ids",
        ),
        "cache_entry_ids": _sorted_unique(
            cache_entry_ids,
            "cache_entry_ids",
        ),
        "score_export_ids": _sorted_unique(
            score_export_ids,
            "score_export_ids",
        ),
        "retention_expires_at": retention_text,
        "withdrawal_state": "current",
        "export_policy": consent_value["export_policy"],
        "deletion_state": "active",
        "legal_hold_id": None,
        "system_provenance_id": system_provenance_id,
        "created_at": created_at,
    }
    return validate_project_native_contribution(record)


def validate_project_native_contributions(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate a deterministic inventory without silent duplicate merging."""

    records = [validate_project_native_contribution(value) for value in values]
    ids = [record["contribution_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ProjectNativeLifecycleError(
            "duplicate contribution identifier"
        )
    raw_hash_owners: dict[tuple[str, str, str], str] = {}
    for record in records:
        if record["data_class"] not in _PARTICIPANT_DATA_CLASSES:
            continue
        for object_key in record["raw_object_keys"]:
            key = (
                record["source_id"],
                record["data_class"],
                object_key.rsplit("/", 1)[1],
            )
            owner = raw_hash_owners.setdefault(
                key,
                record["contribution_id"],
            )
            if owner != record["contribution_id"]:
                raise ProjectNativeLifecycleError(
                    "duplicate submission content requires explicit linkage"
                )
    return sorted(records, key=lambda item: item["contribution_id"])


def _validate_authoritative_graph(
    contributions: Sequence[Mapping[str, Any]],
    *,
    authoritative_contribution_ids: Sequence[str],
    lineage_authority: Mapping[str, Sequence[str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records = validate_project_native_contributions(contributions)
    ids = [record["contribution_id"] for record in records]
    expected_ids = _sorted_unique(
        authoritative_contribution_ids,
        "authoritative_contribution_ids",
    )
    if ids != expected_ids:
        raise ProjectNativeLifecycleError(
            "contribution inventory lacks authoritative completeness"
        )
    if not isinstance(lineage_authority, Mapping):
        raise ProjectNativeLifecycleError(
            "authoritative lineage resolver is required"
        )
    derived_ids = {
        record["contribution_id"]
        for record in records
        if record["input_contribution_ids"]
    }
    if set(lineage_authority) != derived_ids:
        raise ProjectNativeLifecycleError(
            "authoritative lineage resolver is incomplete"
        )
    inventory = {
        record["contribution_id"]: record for record in records
    }
    for contribution_id in sorted(derived_ids):
        expected_inputs = _sorted_unique(
            lineage_authority[contribution_id],
            f"lineage_authority.{contribution_id}",
        )
        record = inventory[contribution_id]
        if expected_inputs != record["input_contribution_ids"]:
            raise ProjectNativeLifecycleError(
                "derived contribution conflicts with authoritative lineage"
            )
        if any(input_id not in inventory for input_id in expected_inputs):
            raise ProjectNativeLifecycleError(
                "authoritative lineage references a missing contribution"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(contribution_id: str) -> None:
        if contribution_id in visiting:
            raise ProjectNativeLifecycleError(
                "authoritative contribution lineage contains a cycle"
            )
        if contribution_id in visited:
            return
        visiting.add(contribution_id)
        for input_id in inventory[contribution_id]["input_contribution_ids"]:
            visit(input_id)
        visiting.remove(contribution_id)
        visited.add(contribution_id)

    for contribution_id in ids:
        visit(contribution_id)
    return records, inventory


def _transitive_ancestors(
    contribution_id: str,
    inventory: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    ancestors: set[str] = set()
    pending = list(inventory[contribution_id]["input_contribution_ids"])
    while pending:
        input_id = pending.pop()
        if input_id in ancestors:
            continue
        ancestors.add(input_id)
        pending.extend(inventory[input_id]["input_contribution_ids"])
    return ancestors


def derive_project_native_contribution(
    contributions: Sequence[Mapping[str, Any]],
    *,
    input_contribution_ids: Sequence[str],
    authorities: Mapping[str, Mapping[str, Any]],
    lineage_authority: Mapping[str, Sequence[str]],
    redaction_applied: bool,
    contribution_id: str,
    source_id: str,
    data_class: str,
    provenance_id: str,
    system_provenance_id: str,
    derived_object_keys: Sequence[str],
    created_at: str,
) -> dict[str, Any]:
    """Build a derived record from a complete, currently authorized graph."""

    requested_inputs = _sorted_unique(
        input_contribution_ids,
        "input_contribution_ids",
    )
    records, inventory = _validate_authoritative_graph(
        contributions,
        authoritative_contribution_ids=[
            record["contribution_id"]
            for record in validate_project_native_contributions(contributions)
        ],
        lineage_authority=lineage_authority,
    )
    if (
        not requested_inputs
        or data_class not in _DERIVED_DATA_CLASSES
        or any(input_id not in inventory for input_id in requested_inputs)
    ):
        raise ProjectNativeLifecycleError(
            "derived contribution requires reviewed inputs and data class"
        )
    required_graph_ids = set(requested_inputs)
    for input_id in requested_inputs:
        required_graph_ids.update(_transitive_ancestors(input_id, inventory))
    if required_graph_ids != set(inventory):
        raise ProjectNativeLifecycleError(
            "derived contribution input graph is not exact"
        )
    direct_records = [inventory[input_id] for input_id in requested_inputs]
    created = _parse_time(created_at, "created_at")
    common_audiences = sorted(
        set.intersection(
            *(set(record["allowed_audiences"]) for record in direct_records)
        )
    )
    if not common_audiences:
        raise ProjectNativeLifecycleError(
            "derived inputs lack a common authorized audience"
        )
    for input_record in direct_records:
        result = evaluate_project_native_graph_operation(
            input_record,
            records,
            authorities,
            authoritative_contribution_ids=[
                record["contribution_id"] for record in records
            ],
            lineage_authority=lineage_authority,
            operation="derived_processing",
            audience=common_audiences[0],
            redaction_applied=redaction_applied,
            now=created,
        )
        if not result["eligible"]:
            raise ProjectNativeLifecycleError(
                "derived inputs lack compatible current authority"
            )
    records = direct_records
    purpose_codes = {record["purpose_code"] for record in records}
    if len(purpose_codes) != 1:
        raise ProjectNativeLifecycleError(
            "derived inputs have incompatible purposes"
        )
    consent_ids = sorted(
        {
            consent_id
            for record in records
            for consent_id in record["consent_ids"]
        }
    )
    uses = sorted(
        set.intersection(
            *(set(record["allowed_uses"]) for record in records)
        )
    )
    audiences = sorted(
        set.intersection(
            *(set(record["allowed_audiences"]) for record in records)
        )
    )
    confidentiality = max(
        (record["confidentiality_class"] for record in records),
        key=_CONFIDENTIALITY_RANK.__getitem__,
    )
    retention_expires_at = min(
        records,
        key=lambda item: _parse_time(
            item["retention_expires_at"],
            "retention_expires_at",
        ),
    )["retention_expires_at"]
    if (
        not uses
        or not audiences
        or any(
            record["consent_state"] != "active"
            or record["withdrawal_state"] != "current"
            or record["deletion_state"] != "active"
            for record in records
        )
    ):
        raise ProjectNativeLifecycleError(
            "derived inputs lack compatible current authority"
        )
    record = {
        "schema_version": 1,
        "record_type": "project_native_contribution",
        "contribution_id": contribution_id,
        "subject_ref": None,
        "source_id": source_id,
        "data_class": data_class,
        "purpose_code": next(iter(purpose_codes)),
        "consent_ids": consent_ids,
        "consent_notice_version": "inherited",
        "consent_state": "active",
        "confidentiality_class": confidentiality,
        "allowed_audiences": audiences,
        "allowed_uses": uses,
        "provenance_id": provenance_id,
        "input_contribution_ids": requested_inputs,
        "raw_object_keys": [],
        "derived_object_keys": _sorted_unique(
            derived_object_keys,
            "derived_object_keys",
        ),
        "index_document_ids": [],
        "cache_entry_ids": [],
        "score_export_ids": [],
        "retention_expires_at": retention_expires_at,
        "withdrawal_state": "current",
        "export_policy": "none",
        "deletion_state": "active",
        "legal_hold_id": None,
        "system_provenance_id": system_provenance_id,
        "created_at": created_at,
    }
    return validate_project_native_contribution(record)


def evaluate_project_native_operation(
    contribution: Mapping[str, Any],
    consent: Mapping[str, Any],
    retention: Mapping[str, Any],
    deletion: Mapping[str, Any],
    *,
    operation: str,
    audience: str,
    redaction_applied: bool,
    now: datetime,
) -> dict[str, Any]:
    """Evaluate one exact participant contribution against current authority."""

    record = validate_project_native_contribution(contribution)
    consent_value, retention_value, deletion_value = (
        validate_project_native_contract(consent, retention, deletion)
    )
    if len(record["consent_ids"]) != 1:
        raise ProjectNativeLifecycleError(
            "derived contribution requires all input authorities"
        )
    if (
        record["consent_ids"][0] != consent_value["consent_id"]
        or record["source_id"] != consent_value["source_id"]
        or record["subject_ref"] != consent_value["subject_ref"]
    ):
        raise ProjectNativeLifecycleError(
            "contribution is not bound to current consent"
        )
    if (
        record["purpose_code"] != consent_value["purpose_code"]
        or record["consent_notice_version"] != consent_value["notice_version"]
    ):
        raise ProjectNativeLifecycleError(
            "contribution purpose or notice does not match current consent"
        )
    if operation not in PROJECT_NATIVE_OPERATIONS:
        raise ProjectNativeLifecycleError(
            "project-native operation is unknown"
        )
    if now.tzinfo is None:
        raise ProjectNativeLifecycleError(
            "evaluation time must be timezone-aware"
        )
    current = now.astimezone(UTC)
    reasons: list[str] = []
    if consent_value["state"] != "active":
        reasons.append(f"consent:{consent_value['state']}")
    if record["consent_state"] != consent_value["state"]:
        reasons.append("consent:snapshot_stale")
    if operation not in consent_value["allowed_uses"]:
        reasons.append("consent:use_not_allowed")
    if operation not in record["allowed_uses"]:
        reasons.append("contribution:use_not_allowed")
    if audience not in consent_value["allowed_viewer_roles"]:
        reasons.append("consent:audience_not_allowed")
    if audience not in record["allowed_audiences"]:
        reasons.append("audience:not_allowed")
    if (
        operation == "public_retrieval"
        and (
            audience != "public"
            or record["confidentiality_class"] != "public"
        )
    ):
        reasons.append("confidentiality:not_public")
    if consent_value["redaction_required"] and redaction_applied is not True:
        reasons.append("redaction:required")
    if _parse_time(consent_value["decided_at"], "consent.decided_at") > current:
        reasons.append("consent:not_yet_effective")
    if _parse_time(consent_value["expires_at"], "consent.expires_at") <= current:
        reasons.append("consent:expired")
    if retention_value["state"] != "retain_until":
        reasons.append(f"retention:{retention_value['state']}")
    if _parse_time(
        retention_value["expires_at"], "retention.expires_at"
    ) <= current:
        reasons.append("retention:expired")
    if _parse_time(
        record["retention_expires_at"],
        "contribution.retention_expires_at",
    ) > _parse_time(retention_value["expires_at"], "retention.expires_at"):
        reasons.append("retention:authority_shortened")
    if _parse_time(
        record["retention_expires_at"],
        "contribution.retention_expires_at",
    ) <= current:
        reasons.append("contribution_retention:expired")
    if (
        _CONFIDENTIALITY_RANK[record["confidentiality_class"]]
        < _CONFIDENTIALITY_RANK[consent_value["confidentiality_class"]]
    ):
        reasons.append("confidentiality:authority_stricter")
    if deletion_value["trigger_state"] != "none":
        reasons.append(f"deletion:{deletion_value['trigger_state']}")
    if deletion_value["status"] != "not_requested":
        reasons.append(f"deletion_status:{deletion_value['status']}")
    if record["withdrawal_state"] != "current":
        reasons.append(f"withdrawal:{record['withdrawal_state']}")
    if record["deletion_state"] != "active":
        reasons.append(f"contribution_deletion:{record['deletion_state']}")
    return {
        "contribution_id": record["contribution_id"],
        "consent_id": consent_value["consent_id"],
        "operation": operation,
        "audience": audience,
        "eligible": not reasons,
        "reasons": reasons,
    }


def evaluate_project_native_graph_operation(
    contribution: Mapping[str, Any],
    contributions: Sequence[Mapping[str, Any]],
    authorities: Mapping[str, Mapping[str, Any]],
    *,
    authoritative_contribution_ids: Sequence[str],
    lineage_authority: Mapping[str, Sequence[str]],
    operation: str,
    audience: str,
    redaction_applied: bool,
    now: datetime,
) -> dict[str, Any]:
    """Recheck a contribution and every transitive input authority."""

    target = validate_project_native_contribution(contribution)
    records, inventory = _validate_authoritative_graph(
        contributions,
        authoritative_contribution_ids=authoritative_contribution_ids,
        lineage_authority=lineage_authority,
    )
    current_target = inventory.get(target["contribution_id"])
    if current_target is None:
        return {
            "contribution_id": target["contribution_id"],
            "operation": operation,
            "audience": audience,
            "eligible": False,
            "reasons": ["target:missing_from_authoritative_inventory"],
        }
    if _canonical(current_target) != _canonical(target):
        return {
            "contribution_id": target["contribution_id"],
            "operation": operation,
            "audience": audience,
            "eligible": False,
            "reasons": ["target:inventory_conflict"],
        }
    if operation not in PROJECT_NATIVE_OPERATIONS:
        raise ProjectNativeLifecycleError(
            "project-native operation is unknown"
        )
    if now.tzinfo is None:
        raise ProjectNativeLifecycleError(
            "evaluation time must be timezone-aware"
        )
    if not isinstance(authorities, Mapping):
        raise ProjectNativeLifecycleError(
            "current contribution authorities are required"
        )

    memo: dict[str, list[str]] = {}
    visiting: set[str] = set()

    def evaluate_one(contribution_id: str) -> list[str]:
        if contribution_id in memo:
            return memo[contribution_id]
        if contribution_id in visiting:
            return [f"input:{contribution_id}:cycle"]
        record = inventory.get(contribution_id)
        if record is None:
            return [f"input:{contribution_id}:missing"]
        visiting.add(contribution_id)
        reasons: list[str] = []
        if not record["input_contribution_ids"]:
            consent_id = record["consent_ids"][0]
            bundle = authorities.get(consent_id)
            if (
                not isinstance(bundle, Mapping)
                or set(bundle) != {"consent", "retention", "deletion"}
            ):
                reasons.append(
                    f"input:{contribution_id}:authority_missing"
                )
            else:
                try:
                    result = evaluate_project_native_operation(
                        record,
                        bundle["consent"],
                        bundle["retention"],
                        bundle["deletion"],
                        operation=operation,
                        audience=audience,
                        redaction_applied=redaction_applied,
                        now=now,
                    )
                except (
                    GovernanceError,
                    ProjectNativeLifecycleError,
                ):
                    reasons.append(
                        f"input:{contribution_id}:authority_invalid"
                    )
                else:
                    reasons.extend(
                        f"input:{contribution_id}:{reason}"
                        for reason in result["reasons"]
                    )
        else:
            input_records = [
                inventory.get(input_id)
                for input_id in record["input_contribution_ids"]
            ]
            for input_id in record["input_contribution_ids"]:
                reasons.extend(evaluate_one(input_id))
            if all(input_record is not None for input_record in input_records):
                checked_inputs = [
                    input_record
                    for input_record in input_records
                    if input_record is not None
                ]
                purpose_codes = {
                    input_record["purpose_code"]
                    for input_record in checked_inputs
                }
                expected_consents = sorted(
                    {
                        consent_id
                        for input_record in checked_inputs
                        for consent_id in input_record["consent_ids"]
                    }
                )
                expected_uses = sorted(
                    set.intersection(
                        *(
                            set(input_record["allowed_uses"])
                            for input_record in checked_inputs
                        )
                    )
                )
                expected_audiences = sorted(
                    set.intersection(
                        *(
                            set(input_record["allowed_audiences"])
                            for input_record in checked_inputs
                        )
                    )
                )
                expected_confidentiality = max(
                    (
                        input_record["confidentiality_class"]
                        for input_record in checked_inputs
                    ),
                    key=_CONFIDENTIALITY_RANK.__getitem__,
                )
                expected_retention = min(
                    checked_inputs,
                    key=lambda item: _parse_time(
                        item["retention_expires_at"],
                        "retention_expires_at",
                    ),
                )["retention_expires_at"]
                if (
                    len(purpose_codes) != 1
                    or record["purpose_code"] not in purpose_codes
                ):
                    reasons.append(
                        f"input:{contribution_id}:purpose_drift"
                    )
                if record["consent_ids"] != expected_consents:
                    reasons.append(
                        f"input:{contribution_id}:consent_lineage_drift"
                    )
                if record["allowed_uses"] != expected_uses:
                    reasons.append(
                        f"input:{contribution_id}:use_inheritance_drift"
                    )
                if record["allowed_audiences"] != expected_audiences:
                    reasons.append(
                        f"input:{contribution_id}:audience_inheritance_drift"
                    )
                if (
                    record["confidentiality_class"]
                    != expected_confidentiality
                ):
                    reasons.append(
                        f"input:{contribution_id}:confidentiality_drift"
                    )
                if record["retention_expires_at"] != expected_retention:
                    reasons.append(
                        f"input:{contribution_id}:retention_drift"
                    )
            if operation not in record["allowed_uses"]:
                reasons.append(
                    f"input:{contribution_id}:use_not_allowed"
                )
            if audience not in record["allowed_audiences"]:
                reasons.append(
                    f"input:{contribution_id}:audience_not_allowed"
                )
            if (
                operation == "public_retrieval"
                and (
                    audience != "public"
                    or record["confidentiality_class"] != "public"
                )
            ):
                reasons.append(
                    f"input:{contribution_id}:confidentiality_not_public"
                )
            if _parse_time(
                record["retention_expires_at"],
                "retention_expires_at",
            ) <= now.astimezone(UTC):
                reasons.append(
                    f"input:{contribution_id}:retention_expired"
                )
            if record["withdrawal_state"] != "current":
                reasons.append(
                    f"input:{contribution_id}:withdrawn"
                )
            if record["deletion_state"] != "active":
                reasons.append(
                    f"input:{contribution_id}:deletion_pending"
                )
        visiting.remove(contribution_id)
        memo[contribution_id] = sorted(set(reasons))
        return memo[contribution_id]

    reasons = evaluate_one(target["contribution_id"])
    return {
        "contribution_id": target["contribution_id"],
        "operation": operation,
        "audience": audience,
        "eligible": not reasons,
        "reasons": reasons,
    }


def build_subject_export_job(
    contributions: Sequence[Mapping[str, Any]],
    consents: Sequence[Mapping[str, Any]],
    *,
    authoritative_contribution_ids: Sequence[str],
    lineage_authority: Mapping[str, Sequence[str]],
    subject_ref: str,
    requested_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Build a body-free subject export job carrying IDs and exact keys only."""

    if (
        requested_at.tzinfo is None
        or expires_at.tzinfo is None
        or expires_at <= requested_at
    ):
        raise ProjectNativeLifecycleError(
            "subject export window is invalid"
        )
    all_records, inventory = _validate_authoritative_graph(
        contributions,
        authoritative_contribution_ids=authoritative_contribution_ids,
        lineage_authority=lineage_authority,
    )
    direct_records = [
        record
        for record in all_records
        if record["subject_ref"] == subject_ref
    ]
    consent_values = {
        record["consent_id"]: record
        for record in (_validate_consent_record(value) for value in consents)
    }
    if len(consent_values) != len(consents):
        raise ProjectNativeLifecycleError(
            "subject export contains duplicate consent authority"
        )
    if not direct_records:
        raise ProjectNativeLifecycleError(
            "subject export has no matching contributions"
        )
    subject_consent_ids = {
        record["consent_ids"][0] for record in direct_records
    }
    direct_ids = {
        record["contribution_id"] for record in direct_records
    }
    records = [
        record
        for record in all_records
        if (
            record["contribution_id"] in direct_ids
            or bool(
                _transitive_ancestors(record["contribution_id"], inventory)
                & direct_ids
            )
        )
    ]
    if any(
        not set(record["consent_ids"]).issubset(subject_consent_ids)
        for record in records
    ):
        raise ProjectNativeLifecycleError(
            "subject export cannot disclose a mixed-subject derivative"
        )
    current = requested_at.astimezone(UTC)
    for consent_id in sorted(subject_consent_ids):
        authority = consent_values.get(consent_id)
        if (
            authority is None
            or authority["subject_ref"] != subject_ref
            or authority["state"] != "active"
            or authority["export_policy"] != "subject_copy"
            or "subject_export" not in authority["allowed_uses"]
            or _parse_time(authority["expires_at"], "consent.expires_at")
            <= current
        ):
            raise ProjectNativeLifecycleError(
                "subject export lacks current exact consent"
            )
        _require_consent_effective(
            authority,
            at=current,
            operation="subject export",
        )
    latest_expiry = min(
        [
            _parse_time(record["retention_expires_at"], "retention_expires_at")
            for record in records
        ]
        + [
            _parse_time(
                consent_values[consent_id]["expires_at"],
                "consent.expires_at",
            )
            for consent_id in sorted(subject_consent_ids)
        ]
    )
    if expires_at.astimezone(UTC) > latest_expiry:
        raise ProjectNativeLifecycleError(
            "subject export exceeds current retention"
        )
    payload = {
        "schema_version": 1,
        "record_type": "project_native_export_job",
        "subject_ref": subject_ref,
        "consent_ids": sorted(
            subject_consent_ids
        ),
        "contribution_ids": [
            record["contribution_id"] for record in records
        ],
        "object_keys": sorted(
            {
                object_key
                for record in records
                for object_key in (
                    record["raw_object_keys"]
                    + record["derived_object_keys"]
                )
            }
        ),
        "requested_at": _utc_text(requested_at),
        "expires_at": _utc_text(expires_at),
        "state": "ready",
    }
    job_id = f"project_native_export_{_sha256(payload)[:24]}"
    record = {
        **payload,
        "export_job_id": job_id,
        "export_job_sha256": _sha256(
            {**payload, "export_job_id": job_id}
        ),
    }
    return _validate_export_job_record(record)


def _validate_legal_hold(
    value: Mapping[str, Any],
    *,
    contribution_ids: Sequence[str],
    now: datetime,
) -> dict[str, Any]:
    record = _validate_schema("project-native-legal-hold", value)
    if record["contribution_ids"] != sorted(record["contribution_ids"]):
        raise ProjectNativeLifecycleError(
            "legal hold scope must use canonical sorted order"
        )
    decided = _parse_time(record["decided_at"], "legal_hold.decided_at")
    review = _parse_time(record["review_at"], "legal_hold.review_at")
    expiry = _parse_time(record["expires_at"], "legal_hold.expires_at")
    if not decided < review < expiry:
        raise ProjectNativeLifecycleError(
            "legal hold review and expiry chronology is invalid"
        )
    if record["state"] != "active" or expiry <= now.astimezone(UTC):
        raise ProjectNativeLifecycleError(
            "legal hold is not current"
        )
    if review <= now.astimezone(UTC):
        raise ProjectNativeLifecycleError(
            "legal hold review is due"
        )
    if not set(contribution_ids).issubset(record["contribution_ids"]):
        raise ProjectNativeLifecycleError(
            "legal hold scope does not cover the deletion batch"
        )
    return record


def build_project_native_deletion_work(
    contributions: Sequence[Mapping[str, Any]],
    deletion: Mapping[str, Any],
    *,
    authoritative_contribution_ids: Sequence[str],
    lineage_authority: Mapping[str, Sequence[str]],
    legal_hold: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """Plan exact raw, derived, index, cache, and export removal."""

    if now.tzinfo is None:
        raise ProjectNativeLifecycleError(
            "deletion planning time must be timezone-aware"
        )
    deletion_value = _validate_schema("deletion", deletion)
    if (
        deletion_value["trigger_state"] == "none"
        or deletion_value["requested_at"] is None
        or deletion_value["deletion_due_at"] is None
        or deletion_value["status"] not in {
            "pending",
            "under_legal_hold_review",
        }
    ):
        raise ProjectNativeLifecycleError(
            "deletion work lacks a durable trigger"
        )
    if (
        deletion_value["status"] == "under_legal_hold_review"
    ) != (legal_hold is not None):
        raise ProjectNativeLifecycleError(
            "deletion status and legal-hold authority are inconsistent"
        )
    requested_at = _parse_time(
        deletion_value["requested_at"],
        "deletion.requested_at",
    )
    due_at = _parse_time(
        deletion_value["deletion_due_at"],
        "deletion.deletion_due_at",
    )
    expected_due_at = requested_at + timedelta(
        hours=deletion_value["deletion_sla_hours"]
    )
    if (
        requested_at > now.astimezone(UTC)
        or due_at != expected_due_at
    ):
        raise ProjectNativeLifecycleError(
            "deletion request chronology or SLA is invalid"
        )
    all_records, _ = _validate_authoritative_graph(
        contributions,
        authoritative_contribution_ids=authoritative_contribution_ids,
        lineage_authority=lineage_authority,
    )
    records = [
        record
        for record in all_records
        if deletion_value["consent_id"] in record["consent_ids"]
    ]
    if not records:
        raise ProjectNativeLifecycleError(
            "deletion work has no linked contributions"
        )
    maximum_sla = min(
        PROJECT_NATIVE_DELETION_SLA_HOURS[record["data_class"]]
        for record in records
    )
    if deletion_value["deletion_sla_hours"] > maximum_sla:
        raise ProjectNativeLifecycleError(
            "deletion SLA exceeds the data-class default"
        )
    contribution_ids = [
        record["contribution_id"] for record in records
    ]
    hold_value = None
    if legal_hold is not None:
        hold_value = _validate_legal_hold(
            legal_hold,
            contribution_ids=contribution_ids,
            now=now,
        )
    state = "legal_hold_review" if hold_value is not None else "pending"
    targets = [
        {
            "contribution_id": record["contribution_id"],
            "raw_object_keys": record["raw_object_keys"],
            "derived_object_keys": record["derived_object_keys"],
            "index_document_ids": record["index_document_ids"],
            "cache_entry_ids": record["cache_entry_ids"],
            "score_export_ids": record["score_export_ids"],
        }
        for record in records
    ]
    payload = {
        "schema_version": 1,
        "record_type": "project_native_deletion_work",
        "deletion_id": deletion_value["deletion_id"],
        "consent_id": deletion_value["consent_id"],
        "state": state,
        "legal_hold_id": (
            None
            if hold_value is None
            else hold_value["legal_hold_id"]
        ),
        "contribution_ids": contribution_ids,
        "raw_object_keys": sorted(
            {
                key
                for target in targets
                for key in target["raw_object_keys"]
            }
        ),
        "derived_object_keys": sorted(
            {
                key
                for target in targets
                for key in target["derived_object_keys"]
            }
        ),
        "index_document_ids": sorted(
            {
                key
                for target in targets
                for key in target["index_document_ids"]
            }
        ),
        "cache_entry_ids": sorted(
            {
                key
                for target in targets
                for key in target["cache_entry_ids"]
            }
        ),
        "score_export_ids": sorted(
            {
                key
                for target in targets
                for key in target["score_export_ids"]
            }
        ),
        "targets": targets,
        "requested_at": deletion_value["requested_at"],
        "deletion_due_at": deletion_value["deletion_due_at"],
    }
    work_id = f"project_native_deletion_work_{_sha256(payload)[:24]}"
    record = {
        **payload,
        "work_id": work_id,
        "work_sha256": _sha256({**payload, "work_id": work_id}),
    }
    return _validate_deletion_work_record(record)


def _exact_values(
    expected: Sequence[str],
    observed: Sequence[str],
    field: str,
) -> None:
    if _sorted_unique(observed, field) != list(expected):
        raise ProjectNativeLifecycleError(
            f"{field} completion is not exact"
        )


def _targets_for_contributions(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "contribution_id": record["contribution_id"],
            "raw_object_keys": record["raw_object_keys"],
            "derived_object_keys": record["derived_object_keys"],
            "index_document_ids": record["index_document_ids"],
            "cache_entry_ids": record["cache_entry_ids"],
            "score_export_ids": record["score_export_ids"],
        }
        for record in records
    ]


def complete_project_native_deletion(
    work: Mapping[str, Any],
    contributions: Sequence[Mapping[str, Any]],
    *,
    authoritative_contribution_ids: Sequence[str],
    lineage_authority: Mapping[str, Sequence[str]],
    deleted_raw_object_keys: Sequence[str],
    deleted_derived_object_keys: Sequence[str],
    removed_index_document_ids: Sequence[str],
    invalidated_cache_entry_ids: Sequence[str],
    removed_score_export_ids: Sequence[str],
    completed_at: datetime,
) -> list[dict[str, Any]]:
    """Verify exact completion and emit only content-free audit tombstones."""

    record = _validate_deletion_work_record(work)
    if record["state"] != "pending":
        raise ProjectNativeLifecycleError(
            "legal-hold deletion work cannot complete"
        )
    if completed_at.tzinfo is None:
        raise ProjectNativeLifecycleError(
            "deletion completion time must be timezone-aware"
        )
    all_records, _ = _validate_authoritative_graph(
        contributions,
        authoritative_contribution_ids=authoritative_contribution_ids,
        lineage_authority=lineage_authority,
    )
    affected_records = [
        contribution
        for contribution in all_records
        if record["consent_id"] in contribution["consent_ids"]
    ]
    authoritative_targets = _targets_for_contributions(affected_records)
    if (
        record["contribution_ids"]
        != [
            contribution["contribution_id"]
            for contribution in affected_records
        ]
        or _canonical(record["targets"])
        != _canonical(authoritative_targets)
    ):
        raise ProjectNativeLifecycleError(
            "deletion work conflicts with authoritative contribution targets"
        )
    for expected_field, observed, label in (
        ("raw_object_keys", deleted_raw_object_keys, "raw object"),
        (
            "derived_object_keys",
            deleted_derived_object_keys,
            "derived object",
        ),
        (
            "index_document_ids",
            removed_index_document_ids,
            "index document",
        ),
        (
            "cache_entry_ids",
            invalidated_cache_entry_ids,
            "cache entry",
        ),
        (
            "score_export_ids",
            removed_score_export_ids,
            "score export",
        ),
    ):
        _exact_values(record[expected_field], observed, label)
    completed_text = _utc_text(completed_at)
    tombstones: list[dict[str, Any]] = []
    for target in record["targets"]:
        counts = {
            "raw_objects": len(target["raw_object_keys"]),
            "derived_objects": len(target["derived_object_keys"]),
            "index_documents": len(target["index_document_ids"]),
            "cache_entries": len(target["cache_entry_ids"]),
            "score_exports": len(target["score_export_ids"]),
        }
        payload = {
            "schema_version": 1,
            "record_type": "project_native_audit_tombstone",
            "contribution_id": target["contribution_id"],
            "deletion_id": record["deletion_id"],
            "completed_at": completed_text,
            "removed_counts": counts,
        }
        tombstone = {
            **payload,
            "tombstone_id": (
                f"project_native_tombstone_{_sha256(payload)[:24]}"
            ),
        }
        tombstones.append(
            _validate_schema(
                "project-native-audit-tombstone",
                tombstone,
            )
        )
    return tombstones


def apply_project_native_withdrawal(
    contributions: Sequence[Mapping[str, Any]],
    consent: Mapping[str, Any],
    deletion: Mapping[str, Any],
    *,
    authoritative_contribution_ids: Sequence[str],
    lineage_authority: Mapping[str, Sequence[str]],
    legal_hold: Mapping[str, Any] | None,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Propagate one revoked consent into contribution state and exact work."""

    consent_value = _validate_consent_record(consent)
    deletion_value = _validate_schema("deletion", deletion)
    if (
        consent_value["state"] != "revoked"
        or len(consent_value["audit_events"]) != 1
        or consent_value["audit_events"][0]["event_type"]
        != "consent_revoked"
        or deletion_value["consent_id"] != consent_value["consent_id"]
        or deletion_value["source_id"] != consent_value["source_id"]
        or deletion_value["trigger_state"] != "consent_revoked"
        or deletion_value["requested_at"]
        != consent_value["audit_events"][0]["occurred_at"]
    ):
        raise ProjectNativeLifecycleError(
            "withdrawal lacks linked revoked consent and deletion authority"
        )
    records, inventory = _validate_authoritative_graph(
        contributions,
        authoritative_contribution_ids=authoritative_contribution_ids,
        lineage_authority=lineage_authority,
    )
    direct_ids = {
        record["contribution_id"]
        for record in records
        if (
            record["subject_ref"] == consent_value["subject_ref"]
            and consent_value["consent_id"] in record["consent_ids"]
        )
    }
    affected = [
        record
        for record in records
        if (
            consent_value["consent_id"] in record["consent_ids"]
            or bool(
                _transitive_ancestors(record["contribution_id"], inventory)
                & direct_ids
            )
        )
    ]
    if not affected:
        raise ProjectNativeLifecycleError(
            "withdrawal has no linked contributions"
        )
    work = build_project_native_deletion_work(
        records,
        deletion_value,
        authoritative_contribution_ids=authoritative_contribution_ids,
        lineage_authority=lineage_authority,
        legal_hold=legal_hold,
        now=now,
    )
    updated: list[dict[str, Any]] = []
    for record in records:
        value = copy.deepcopy(record)
        if consent_value["consent_id"] in value["consent_ids"]:
            value["consent_state"] = "revoked"
            value["allowed_uses"] = []
            value["withdrawal_state"] = "withdrawn"
            value["deletion_state"] = (
                "legal_hold_review"
                if work["state"] == "legal_hold_review"
                else "pending"
            )
            value["legal_hold_id"] = work["legal_hold_id"]
        updated.append(validate_project_native_contribution(value))
    return updated, work
