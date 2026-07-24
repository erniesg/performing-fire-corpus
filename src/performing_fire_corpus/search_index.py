"""Fail-closed, field-level provenance and search-index contracts."""

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

from performing_fire_corpus.redaction import sanitize


UTC = timezone.utc
_UNSAFE_VALUE = re.compile(
    r"(?:https?://|file://|"
    r"x-amz-|signature=|credential=|full source prose)",
    re.IGNORECASE,
)
_ABSOLUTE_OR_TRAVERSAL = re.compile(
    r"^(?:/|\\\\|[A-Za-z]:[\\/])|(?:^|[\\/])\.\.(?:[\\/]|$)"
)


class SearchIndexError(ValueError):
    """Raised when indexed data is unsafe, stale, or internally inconsistent."""


class IndexAuthorityResolver(Protocol):
    """Trusted current visibility and deletion authority boundary."""

    def resolve_index_document(
        self, *, index_document_id: str
    ) -> Mapping[str, Any] | None: ...

    def resolve_visibility_policy(
        self, *, index_document_id: str, field_id: str
    ) -> Mapping[str, Any] | None: ...

    def resolve_provenance_edge(
        self, *, provenance_edge_id: str
    ) -> Mapping[str, Any] | None: ...

    def resolve_deletion_event(
        self, *, deletion_event_id: str
    ) -> Mapping[str, Any] | None: ...


def _schema_resource(name: str) -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", f"{name}.json"
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "v1" / f"{name}.json"


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SearchIndexError(f"{name} record must be an object")
    record = copy.deepcopy(dict(value))
    try:
        schema = json.loads(_schema_resource(name).read_text(encoding="utf-8"))
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
        raise SearchIndexError(
            f"{name} record does not match the strict schema"
        ) from error
    if sanitize(record, environ={}) != record:
        raise SearchIndexError(f"{name} record contains private data")
    return record


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise SearchIndexError(f"{field} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise SearchIndexError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _field_value_sha256(value: str) -> str:
    return hashlib.sha256((value + "\n").encode("utf-8")).hexdigest()


def validate_provenance_edge(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate("provenance-edge", value)
    inputs = record["input_provenance_edge_ids"]
    if inputs != sorted(set(inputs)) or record["provenance_edge_id"] in inputs:
        raise SearchIndexError("provenance inputs must be unique and canonical")
    transformed = record["origin_class"] in {
        "derived_observation",
        "generated_score",
    }
    if transformed and (
        record["transformation_id"] is None or not inputs
    ):
        raise SearchIndexError(
            "derived or generated provenance requires complete inputs"
        )
    if not transformed and (
        record["transformation_id"] is not None or inputs
    ):
        raise SearchIndexError(
            "source and project-native provenance cannot invent a transform"
        )
    if _parse_time(record["evidence_at"], "evidence_at") >= _parse_time(
        record["evidence_expires_at"], "evidence_expires_at"
    ):
        raise SearchIndexError("provenance evidence must expire later")
    return record


def validate_index_document(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate("index-document", value)
    field_ids = [item["field_id"] for item in record["fields"]]
    names = [item["name"] for item in record["fields"]]
    if (
        field_ids != sorted(set(field_ids))
        or len(names) != len(set(names))
    ):
        raise SearchIndexError("index fields must have unique canonical identities")
    for item in record["fields"]:
        text = str(item["value"])
        if _UNSAFE_VALUE.search(text) or _ABSOLUTE_OR_TRAVERSAL.search(text):
            raise SearchIndexError("raw content or locator is forbidden in the index")
    for name in ("languages", "mediums"):
        if record[name] != sorted(set(record[name])):
            raise SearchIndexError(f"{name} must be unique and sorted")
    return record


def _requires_consent(
    document: Mapping[str, Any], field: Mapping[str, Any]
) -> bool:
    return (
        str(document["source_id"]).startswith("project-native-")
        or field["origin_class"] == "project_native"
        or field["visibility_class"] == "project_private"
    )


def _policy_authorizes_field(
    document: Mapping[str, Any],
    field: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    evaluated: datetime,
) -> bool:
    consent_required = _requires_consent(document, field)
    return (
        policy["index_document_id"] == document["index_document_id"]
        and policy["field_id"] == field["field_id"]
        and policy["rights_snapshot_sha256"]
        == field["rights_snapshot_sha256"]
        and policy["consent_snapshot_sha256"]
        == field["consent_snapshot_sha256"]
        and policy["rights_state"] == "approved"
        and policy["retention_state"] == "retain"
        and (
            (
                field["consent_snapshot_sha256"] is not None
                and policy["consent_state"] == "approved"
            )
            if consent_required
            else policy["consent_state"] in {"approved", "not_required"}
        )
        and not (
            consent_required and "public" in policy["allowed_audiences"]
        )
        and _parse_time(policy["decided_at"], "decided_at") <= evaluated
        and evaluated < _parse_time(policy["expires_at"], "expires_at")
        and evaluated
        < _parse_time(
            policy["evidence_expires_at"], "evidence_expires_at"
        )
    )


def validate_visibility_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate("visibility-policy", value)
    if _parse_time(record["decided_at"], "decided_at") >= _parse_time(
        record["expires_at"], "expires_at"
    ):
        raise SearchIndexError("visibility authority must expire later")
    for field in ("allowed_operations", "allowed_audiences"):
        if record[field] != sorted(set(record[field])):
            raise SearchIndexError(f"{field} must be unique and sorted")
    if (
        record["consent_snapshot_sha256"] is None
    ) != (record["consent_state"] == "not_required"):
        raise SearchIndexError(
            "consent not-required state must match the absent snapshot"
        )
    return record


def validate_duplicate_cluster(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate("duplicate-cluster", value)
    members = record["members"]
    identities = [
        (item["index_document_id"], item["source_id"], item["asset_id"])
        for item in members
    ]
    document_ids = [item["index_document_id"] for item in members]
    source_assets = [(item["source_id"], item["asset_id"]) for item in members]
    if (
        len(identities) != len(set(identities))
        or len(document_ids) != len(set(document_ids))
        or len(source_assets) != len(set(source_assets))
    ):
        raise SearchIndexError("duplicate members must preserve distinct records")
    if len(members) < 2:
        raise SearchIndexError("duplicate clusters require at least two records")
    if members != sorted(
        members, key=lambda item: item["index_document_id"]
    ) or any(
        item["provenance_edge_ids"]
        != sorted(set(item["provenance_edge_ids"]))
        for item in members
    ):
        raise SearchIndexError("duplicate members must be canonical")
    return record


def validate_deletion_event(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate("deletion-event", value)
    replacement_fields = (
        "replacement_document_sha256",
        "replacement_provenance_edge_sha256",
        "replacement_visibility_policy_sha256",
    )
    if record["reindex_action"] == "replace_exact_field":
        if any(record[field] is None for field in replacement_fields):
            raise SearchIndexError(
                "replacement event requires exact current record hashes"
            )
    elif any(record[field] is not None for field in replacement_fields):
        raise SearchIndexError(
            "removal event cannot carry replacement authority"
        )
    return record


def _snapshot_hash(value: Mapping[str, Any]) -> str:
    payload = {key: child for key, child in value.items() if key != "snapshot_sha256"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def validate_index_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _validate("index-snapshot", value)
    if record["snapshot_sha256"] != _snapshot_hash(record):
        raise SearchIndexError("index snapshot binding is invalid")
    documents = [validate_index_document(item) for item in record["documents"]]
    edges = [validate_provenance_edge(item) for item in record["provenance_edges"]]
    policies = [
        validate_visibility_policy(item) for item in record["visibility_policies"]
    ]
    clusters = [
        validate_duplicate_cluster(item) for item in record["duplicate_clusters"]
    ]
    events = [validate_deletion_event(item) for item in record["deletion_events"]]
    event_lineage_edges = [
        validate_provenance_edge(item)
        for item in record["event_lineage_edges"]
    ]
    document_index = {item["index_document_id"]: item for item in documents}
    source_assets = [
        (item["source_id"], item["asset_id"]) for item in documents
    ]
    edge_index = {item["provenance_edge_id"]: item for item in edges}
    policy_index = {
        (item["index_document_id"], item["field_id"]): item
        for item in policies
    }
    policy_keys = set(policy_index)
    policy_ids = [item["visibility_policy_id"] for item in policies]
    cluster_ids = [item["duplicate_cluster_id"] for item in clusters]
    event_ids = [item["deletion_event_id"] for item in events]
    event_lineage_ids = [
        item["provenance_edge_id"] for item in event_lineage_edges
    ]
    if (
        len(document_index) != len(documents)
        or len(source_assets) != len(set(source_assets))
        or len(edge_index) != len(edges)
        or len(policy_keys) != len(policies)
        or len(policy_ids) != len(set(policy_ids))
        or len(cluster_ids) != len(set(cluster_ids))
        or len(event_ids) != len(set(event_ids))
        or len(event_lineage_ids) != len(set(event_lineage_ids))
    ):
        raise SearchIndexError("snapshot record identities must be unique")
    required_edge_ids: set[str] = set()
    required_policy_keys: set[tuple[str, str]] = set()
    for document in documents:
        for item in document["fields"]:
            required_edge_ids.add(str(item["provenance_edge_id"]))
            edge = edge_index.get(item["provenance_edge_id"])
            key = (document["index_document_id"], item["field_id"])
            required_policy_keys.add(key)
            if (
                edge is None
                or edge["index_document_id"] != document["index_document_id"]
                or edge["field_id"] != item["field_id"]
                or edge["field_name"] != item["name"]
                or edge["field_value_sha256"]
                != _field_value_sha256(str(item["value"]))
                or edge["source_id"] != document["source_id"]
                or edge["asset_id"] != document["asset_id"]
                or edge["origin_class"] != item["origin_class"]
                or key not in policy_keys
            ):
                raise SearchIndexError(
                    "every indexed field requires exact provenance and policy"
                )
            policy = policy_index[key]
            if (
                policy["rights_snapshot_sha256"]
                != item["rights_snapshot_sha256"]
                or policy["consent_snapshot_sha256"]
                != item["consent_snapshot_sha256"]
                or _parse_time(
                    policy["evidence_expires_at"],
                    "evidence_expires_at",
                )
                > _parse_time(
                    edge["evidence_expires_at"],
                    "evidence_expires_at",
                )
            ):
                raise SearchIndexError(
                    "field policy is not bound to provenance authority"
                )
            if _requires_consent(document, item) and (
                item["consent_snapshot_sha256"] is None
                or policy["consent_state"] != "approved"
                or "public" in policy["allowed_audiences"]
            ):
                raise SearchIndexError(
                    "project-native or private fields require consent and non-public visibility"
                )
    if required_edge_ids != set(edge_index) or required_policy_keys != policy_keys:
        raise SearchIndexError(
            "snapshot contains orphaned provenance or visibility authority"
        )
    authoritative_cluster_ids = set(cluster_ids)
    if any(
        item["duplicate_cluster_id"] is not None
        and item["duplicate_cluster_id"] not in authoritative_cluster_ids
        for item in documents
    ):
        raise SearchIndexError("document duplicate cluster is not authoritative")
    for cluster in clusters:
        expected_document_ids = {
            item["index_document_id"]
            for item in documents
            if item["duplicate_cluster_id"]
            == cluster["duplicate_cluster_id"]
        }
        member_document_ids = {
            item["index_document_id"] for item in cluster["members"]
        }
        if member_document_ids != expected_document_ids:
            raise SearchIndexError(
                "duplicate cluster membership must be bidirectionally complete"
            )
        for member in cluster["members"]:
            document = document_index.get(member["index_document_id"])
            if (
                document is None
                or document["source_id"] != member["source_id"]
                or document["asset_id"] != member["asset_id"]
                or document["duplicate_cluster_id"]
                != cluster["duplicate_cluster_id"]
                or any(
                    edge_id not in edge_index
                    or edge_index[edge_id]["index_document_id"]
                    != member["index_document_id"]
                    for edge_id in member["provenance_edge_ids"]
                )
                or set(member["provenance_edge_ids"])
                != {
                    item["provenance_edge_id"]
                    for item in document["fields"]
                }
            ):
                raise SearchIndexError(
                    "duplicate cluster member is not preserved in the snapshot"
                )
    for edge in edges:
        if any(
            input_id not in edge_index
            for input_id in edge["input_provenance_edge_ids"]
        ):
            raise SearchIndexError("provenance lineage is incomplete")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(edge_id: str) -> None:
        if edge_id in visiting:
            raise SearchIndexError("provenance lineage contains a cycle")
        if edge_id in visited:
            return
        visiting.add(edge_id)
        for input_id in edge_index[edge_id]["input_provenance_edge_ids"]:
            visit(str(input_id))
        visiting.remove(edge_id)
        visited.add(edge_id)

    for edge_id in edge_index:
        visit(edge_id)
    for event in events:
        key = (event["index_document_id"], event["field_id"])
        present = key in required_policy_keys or any(
            edge["index_document_id"] == key[0]
            and edge["field_id"] == key[1]
            for edge in edges
        )
        if (
            event["reindex_action"] == "remove_exact_field"
            and present
        ) or (
            event["reindex_action"] == "replace_exact_field"
            and not present
        ):
            raise SearchIndexError(
                "index event outcome does not match its exact action"
            )
        if event["reindex_action"] == "replace_exact_field":
            document = document_index.get(key[0])
            edge = next(
                (
                    item
                    for item in edges
                    if (
                        item["index_document_id"],
                        item["field_id"],
                    )
                    == key
                ),
                None,
            )
            policy = policy_index.get(key)
            if (
                document is None
                or edge is None
                or policy is None
                or event["replacement_document_sha256"]
                != _record_hash(document)
                or event["replacement_provenance_edge_sha256"]
                != _record_hash(edge)
                or event["replacement_visibility_policy_sha256"]
                != _record_hash(policy)
            ):
                raise SearchIndexError(
                    "replacement event binding is inconsistent"
                )
    if bool(events) != bool(event_lineage_edges):
        raise SearchIndexError(
            "index events require their complete pre-event lineage"
        )
    if event_lineage_edges:
        lineage_by_id = {
            item["provenance_edge_id"]: item
            for item in event_lineage_edges
        }
        lineage_by_key = {
            (item["index_document_id"], item["field_id"]): item
            for item in event_lineage_edges
        }
        event_by_key = {
            (item["index_document_id"], item["field_id"]): item
            for item in events
        }
        if len(event_by_key) != len(events):
            raise SearchIndexError("index event exact targets must be unique")
        reverse: dict[str, set[str]] = {
            edge_id: set() for edge_id in lineage_by_id
        }
        for edge in event_lineage_edges:
            for input_id in edge["input_provenance_edge_ids"]:
                if input_id not in reverse:
                    raise SearchIndexError(
                        "event provenance lineage is incomplete"
                    )
                reverse[str(input_id)].add(
                    str(edge["provenance_edge_id"])
                )
        removed_ids: set[str] = set()
        for key, event in event_by_key.items():
            target = lineage_by_key.get(key)
            if target is None:
                raise SearchIndexError(
                    "index event target is absent from event lineage"
                )
            if event["reindex_action"] == "remove_exact_field":
                removed_ids.add(str(target["provenance_edge_id"]))
            pending = list(reverse[str(target["provenance_edge_id"])])
            seen: set[str] = set()
            while pending:
                dependent_id = pending.pop()
                if dependent_id in seen:
                    continue
                seen.add(dependent_id)
                dependent = lineage_by_id[dependent_id]
                dependent_key = (
                    dependent["index_document_id"],
                    dependent["field_id"],
                )
                if dependent_key not in event_by_key:
                    raise SearchIndexError(
                        "dependent event lineage is incomplete"
                    )
                pending.extend(reverse[dependent_id])
        expected_final_edge_ids = set(lineage_by_id) - removed_ids
        if set(edge_index) != expected_final_edge_ids:
            raise SearchIndexError(
                "final provenance does not match exact event outcomes"
            )
    canonical = {
        "documents": "index_document_id",
        "provenance_edges": "provenance_edge_id",
        "visibility_policies": "visibility_policy_id",
        "duplicate_clusters": "duplicate_cluster_id",
        "deletion_events": "deletion_event_id",
        "event_lineage_edges": "provenance_edge_id",
    }
    if any(
        record[field]
        != sorted(record[field], key=lambda item: item[id_field])
        for field, id_field in canonical.items()
    ):
        raise SearchIndexError("index snapshot arrays must be canonical")
    return record


def build_index_snapshot(
    *,
    snapshot_id: str,
    documents: Sequence[Mapping[str, Any]],
    provenance_edges: Sequence[Mapping[str, Any]],
    visibility_policies: Sequence[Mapping[str, Any]],
    duplicate_clusters: Sequence[Mapping[str, Any]],
    deletion_events: Sequence[Mapping[str, Any]],
    built_at: str,
    authority_resolver: IndexAuthorityResolver,
) -> dict[str, Any]:
    checked_documents = [validate_index_document(item) for item in documents]
    checked_edges = [
        validate_provenance_edge(item) for item in provenance_edges
    ]
    checked_policies = [
        validate_visibility_policy(item) for item in visibility_policies
    ]
    checked_events = [validate_deletion_event(item) for item in deletion_events]
    for document in checked_documents:
        try:
            current = authority_resolver.resolve_index_document(
                index_document_id=str(document["index_document_id"])
            )
            checked_current = (
                None
                if current is None
                else validate_index_document(current)
            )
        except Exception:
            checked_current = None
        if checked_current != document:
            raise SearchIndexError(
                "index document is missing, stale, or corrected"
            )
    for edge in checked_edges:
        try:
            current = authority_resolver.resolve_provenance_edge(
                provenance_edge_id=str(edge["provenance_edge_id"])
            )
            checked_current = (
                None
                if current is None
                else validate_provenance_edge(current)
            )
        except Exception:
            checked_current = None
        if checked_current != edge:
            raise SearchIndexError(
                "provenance edge is missing, stale, or corrected"
            )
    for policy in checked_policies:
        try:
            current = authority_resolver.resolve_visibility_policy(
                index_document_id=str(policy["index_document_id"]),
                field_id=str(policy["field_id"]),
            )
            checked_current = (
                None
                if current is None
                else validate_visibility_policy(current)
            )
        except Exception:
            checked_current = None
        if checked_current != policy:
            raise SearchIndexError(
                "visibility policy is missing, stale, or revoked"
            )
    for event in checked_events:
        try:
            current = authority_resolver.resolve_deletion_event(
                deletion_event_id=str(event["deletion_event_id"])
            )
            checked_current = (
                None if current is None else validate_deletion_event(current)
            )
        except Exception:
            raise SearchIndexError(
                "current deletion authority could not be resolved"
            ) from None
        if checked_current != event:
            raise SearchIndexError(
                "deletion event is missing, stale, or revoked"
            )
        if _parse_time(event["occurred_at"], "occurred_at") > _parse_time(
            built_at, "built_at"
        ):
            raise SearchIndexError(
                "index event is not yet effective"
            )
        if (
            event["reindex_action"] == "replace_exact_field"
            and event["reason_code"]
            not in {"source_corrected", "transformation_replaced"}
        ):
            raise SearchIndexError(
                "replacement requires correction or transformation authority"
            )
    original_fields = {
        (item["index_document_id"], field["field_id"])
        for item in checked_documents
        for field in item["fields"]
    }
    event_by_key = {
        (item["index_document_id"], item["field_id"]): item
        for item in checked_events
    }
    if len(event_by_key) != len(checked_events) or any(
        key not in original_fields for key in event_by_key
    ):
        raise SearchIndexError(
            "index events must target unique present exact fields"
        )
    edge_by_key = {
        (item["index_document_id"], item["field_id"]): item
        for item in checked_edges
    }
    document_by_id = {
        item["index_document_id"]: item for item in checked_documents
    }
    policy_by_key = {
        (item["index_document_id"], item["field_id"]): item
        for item in checked_policies
    }
    for key, event in event_by_key.items():
        if event["reindex_action"] == "replace_exact_field":
            document = document_by_id.get(key[0])
            edge = edge_by_key.get(key)
            policy = policy_by_key.get(key)
            if (
                document is None
                or edge is None
                or policy is None
                or event["replacement_document_sha256"]
                != _record_hash(document)
                or event["replacement_provenance_edge_sha256"]
                != _record_hash(edge)
                or event["replacement_visibility_policy_sha256"]
                != _record_hash(policy)
            ):
                raise SearchIndexError(
                    "replacement event does not bind current replacement records"
                )
    edge_key_by_id = {
        item["provenance_edge_id"]: key for key, item in edge_by_key.items()
    }
    dependents: dict[str, set[str]] = {
        edge_id: set() for edge_id in edge_key_by_id
    }
    for edge in checked_edges:
        for input_id in edge["input_provenance_edge_ids"]:
            if input_id not in dependents:
                raise SearchIndexError(
                    "snapshot build provenance lineage is incomplete"
                )
            dependents[str(input_id)].add(str(edge["provenance_edge_id"]))
    for key in event_by_key:
        seed = edge_by_key.get(key)
        if seed is None:
            raise SearchIndexError(
                "index event target lacks exact provenance"
            )
        pending = list(dependents[str(seed["provenance_edge_id"])])
        seen: set[str] = set()
        while pending:
            dependent_id = pending.pop()
            if dependent_id in seen:
                continue
            seen.add(dependent_id)
            dependent_key = edge_key_by_id[dependent_id]
            if dependent_key not in event_by_key:
                raise SearchIndexError(
                    "dependent derived fields require exact reindex events"
                )
            pending.extend(dependents[dependent_id])
    deleted = {
        key
        for key, event in event_by_key.items()
        if event["reindex_action"] == "remove_exact_field"
    }
    retained_documents = []
    for document in checked_documents:
        kept = copy.deepcopy(document)
        kept["fields"] = [
            item
            for item in kept["fields"]
            if (kept["index_document_id"], item["field_id"]) not in deleted
        ]
        retained_documents.append(kept)
    retained_edges = [
        item
        for item in checked_edges
        if (item["index_document_id"], item["field_id"]) not in deleted
    ]
    retained_policies = [
        item
        for item in checked_policies
        if (item["index_document_id"], item["field_id"]) not in deleted
    ]
    built_time = _parse_time(built_at, "built_at")
    retained_document_index = {
        item["index_document_id"]: item for item in retained_documents
    }
    retained_edge_index = {
        (item["index_document_id"], item["field_id"]): item
        for item in retained_edges
    }
    retained_policy_index = {
        (item["index_document_id"], item["field_id"]): item
        for item in retained_policies
    }
    for key, edge in retained_edge_index.items():
        document = retained_document_index.get(key[0])
        field = next(
            (
                item
                for item in document["fields"]
                if item["field_id"] == key[1]
            ),
            None,
        ) if document is not None else None
        policy = retained_policy_index.get(key)
        if (
            document is None
            or field is None
            or policy is None
            or not _policy_authorizes_field(
                document, field, policy, evaluated=built_time
            )
            or _parse_time(edge["evidence_at"], "evidence_at")
            > built_time
            or built_time
            >= _parse_time(
                edge["evidence_expires_at"], "evidence_expires_at"
            )
        ):
            raise SearchIndexError(
                "retained index field lacks current effective authority"
            )
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "index_snapshot",
        "index_snapshot_id": snapshot_id,
        "snapshot_sha256": "0" * 64,
        "built_at": built_at,
        "documents": sorted(
            retained_documents, key=lambda item: item["index_document_id"]
        ),
        "provenance_edges": sorted(
            retained_edges, key=lambda item: item["provenance_edge_id"]
        ),
        "visibility_policies": sorted(
            retained_policies, key=lambda item: item["visibility_policy_id"]
        ),
        "duplicate_clusters": sorted(
            (validate_duplicate_cluster(item) for item in duplicate_clusters),
            key=lambda item: item["duplicate_cluster_id"],
        ),
        "deletion_events": sorted(
            checked_events, key=lambda item: item["deletion_event_id"]
        ),
        "event_lineage_edges": sorted(
            checked_edges if checked_events else [],
            key=lambda item: item["provenance_edge_id"],
        ),
    }
    record["snapshot_sha256"] = _snapshot_hash(record)
    return validate_index_snapshot(record)


def query_index(
    snapshot: Mapping[str, Any],
    *,
    operation: str,
    audience: str,
    current_time: str,
    authority_resolver: IndexAuthorityResolver,
    source_id: str | None = None,
    language: str | None = None,
    period: str | None = None,
    medium: str | None = None,
    selection_state: str | None = None,
    duplicate_cluster_id: str | None = None,
) -> list[dict[str, Any]]:
    record = validate_index_snapshot(snapshot)
    evaluated = _parse_time(current_time, "current_time")
    if _parse_time(record["built_at"], "built_at") > evaluated:
        return []
    policies = {
        (item["index_document_id"], item["field_id"]): item
        for item in record["visibility_policies"]
    }
    edges = {
        item["provenance_edge_id"]: item
        for item in record["provenance_edges"]
    }
    results = []
    for document in record["documents"]:
        try:
            current_document_value = authority_resolver.resolve_index_document(
                index_document_id=str(document["index_document_id"])
            )
            current_document = (
                None
                if current_document_value is None
                else validate_index_document(current_document_value)
            )
        except Exception:
            current_document = None
        if current_document != document:
            continue
        if (
            (source_id is not None and document["source_id"] != source_id)
            or (language is not None and language not in document["languages"])
            or (period is not None and document["period"] != period)
            or (medium is not None and medium not in document["mediums"])
            or (
                selection_state is not None
                and document["selection_state"] != selection_state
            )
            or (
                duplicate_cluster_id is not None
                and document["duplicate_cluster_id"]
                != duplicate_cluster_id
            )
        ):
            continue
        visible = []
        for item in document["fields"]:
            embedded = policies[
                (document["index_document_id"], item["field_id"])
            ]
            try:
                current_value = authority_resolver.resolve_visibility_policy(
                    index_document_id=str(document["index_document_id"]),
                    field_id=str(item["field_id"]),
                )
                policy = (
                    None
                    if current_value is None
                    else validate_visibility_policy(current_value)
                )
            except Exception:
                policy = None
            embedded_edge = edges[item["provenance_edge_id"]]
            try:
                current_edge_value = authority_resolver.resolve_provenance_edge(
                    provenance_edge_id=str(item["provenance_edge_id"])
                )
                current_edge = (
                    None
                    if current_edge_value is None
                    else validate_provenance_edge(current_edge_value)
                )
            except Exception:
                current_edge = None
            if (
                policy is not None
                and current_edge == embedded_edge
                and _policy_authorizes_field(
                    document, item, policy, evaluated=evaluated
                )
                and _parse_time(
                    policy["evidence_expires_at"],
                    "evidence_expires_at",
                )
                <= _parse_time(
                    current_edge["evidence_expires_at"],
                    "evidence_expires_at",
                )
                and _parse_time(
                    current_edge["evidence_at"], "evidence_at"
                )
                <= evaluated
                and operation in policy["allowed_operations"]
                and audience in policy["allowed_audiences"]
            ):
                visible.append(
                    {
                        "field_id": item["field_id"],
                        "name": item["name"],
                        "value": item["value"],
                        "origin_class": item["origin_class"],
                        "provenance_edge_id": item["provenance_edge_id"],
                        "visibility_policy_id": policy[
                            "visibility_policy_id"
                        ],
                        "snapshot_policy_id": embedded[
                            "visibility_policy_id"
                        ],
                    }
                )
        if visible:
            results.append(
                {
                    "index_document_id": document["index_document_id"],
                    "source_id": document["source_id"],
                    "asset_id": document["asset_id"],
                    "visible_field_ids": [
                        item["field_id"] for item in visible
                    ],
                    "fields": visible,
                }
            )
    return results
