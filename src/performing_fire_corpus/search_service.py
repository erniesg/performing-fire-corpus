"""Deterministic rights-filtered indexer and offline local search surface.

This module turns one validated index snapshot plus exact verified derived
object manifests into a deterministic corpus index, and serves rights-filtered
search and score-generation exports from it. It is a local reference surface:
there is no hosted operator UI, no production authentication, and no source or
object-store retrieval anywhere in this module.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from performing_fire_corpus.redaction import contains_secret_like_text, sanitize
from performing_fire_corpus.search_index import (
    IndexAuthorityResolver,
    SearchIndexError,
    canonical_json_bytes,
    field_value_sha256,
    parse_index_timestamp,
    policy_authorizes_field,
    query_index,
    record_sha256,
    validate_deletion_event,
    validate_index_document,
    validate_index_snapshot,
    validate_provenance_edge,
    validate_schema_record,
    validate_visibility_policy,
)
from performing_fire_corpus.selection import validate_coverage_target


SEARCH_OPERATION = "search_visibility"
SCORE_OPERATION = "score_generation"
SNIPPET_OPERATION = "snippet_render"
FEATURE_VALUE_OPERATION = "score_feature_value"
EXPORT_AUDIENCES = ("operator", "researcher")
MAX_SNIPPET_CHARS = 96
MAX_FEATURE_VALUE_CHARS = 128
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

COVERAGE_STATES = (
    "selected_contribution",
    "unselected_candidate",
    "outside_coverage_targets",
)
FACET_DIMENSIONS = (
    "source_id",
    "period",
    "language",
    "medium",
    "selection_state",
    "coverage_state",
    "duplicate_cluster_id",
)
_DERIVED_ORIGIN_CLASSES = frozenset({"derived_observation", "generated_score"})
_CORPUS_INDEX_ID = re.compile(r"^corpus_index_[a-z0-9][a-z0-9._-]{0,127}$")
_LOCAL_MEDIA_PATH = re.compile(
    r"^(?:/|~|[A-Za-z]:[\\/])"
    r"|(?:^|[^A-Za-z0-9])(?:file|https?|s3|r2):"
    r"|(?:^|/)\.{1,2}(?:/|$)"
    r"|\\"
    r"|/(?:home|Users|tmp|var|mnt|media)/"
)
_UNSAFE_EXPORT_TEXT = re.compile(
    r"://|file:|\?|&|\\|/home/|/Users/|/tmp/|-----BEGIN|X-Amz-",
    re.IGNORECASE,
)


class SearchServiceError(SearchIndexError):
    """Raised when indexing, search, or export authority is unsafe or stale."""


class DerivedObjectAuthority(Protocol):
    """Trusted current authority for exact verified derived objects."""

    def resolve_object_receipt(
        self, *, object_key: str
    ) -> Mapping[str, Any] | None: ...


def _resolve(call: Any, **kwargs: Any) -> Mapping[str, Any] | None:
    try:
        return call(**kwargs)
    except Exception:
        return None


def _checked(validator: Any, value: Mapping[str, Any] | None) -> Any:
    if value is None:
        return None
    try:
        return validator(value)
    except Exception:
        return None


def validate_derived_object(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exact derived object manifest entry."""

    record = validate_schema_record("derived-object", value)
    key = str(record["object_key"])
    if _LOCAL_MEDIA_PATH.search(key):
        raise SearchServiceError(
            "derived object key must be an exact remote object key"
        )
    suffix = (
        f"/v1/derived/{record['source_id']}/{record['asset_id']}"
        f"/{record['transformation_id']}/{record['sha256']}"
    )
    if not key.endswith(suffix) or len(key) <= len(suffix):
        raise SearchServiceError(
            "derived object key must be derived from its exact identity"
        )
    if record["retrieval_decision"] == "blocked":
        raise SearchServiceError("blocked derived objects cannot be indexed")
    return record


def _verified_receipt(
    derived: Mapping[str, Any], object_authority: DerivedObjectAuthority
) -> dict[str, Any]:
    resolved = _resolve(
        object_authority.resolve_object_receipt,
        object_key=str(derived["object_key"]),
    )
    receipt = _checked(
        lambda item: validate_schema_record("object-receipt", item), resolved
    )
    if receipt is None:
        raise SearchServiceError(
            "derived object is unverified or missing its current receipt"
        )
    mirrored = (
        "source_id",
        "asset_id",
        "object_key",
        "sha256",
        "byte_size",
        "media_type",
        "rights_snapshot_sha256",
        "retention_class",
        "retrieval_decision",
    )
    if (
        receipt["object_kind"] != "derived"
        or receipt["verification_state"] != "verified"
        or receipt.get("transformation_id") != derived["transformation_id"]
        or any(receipt[name] != derived[name] for name in mirrored)
    ):
        raise SearchServiceError(
            "derived object receipt does not match the exact manifest entry"
        )
    return receipt


def _coverage_for_document(
    document: Mapping[str, Any], targets: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    matched = sorted(
        str(target["coverage_target_id"])
        for target in targets
        if (
            (
                target["dimension"] == "source"
                and target["value"] == document["source_id"]
            )
            or (
                target["dimension"] == "period"
                and target["value"] == document["period"]
            )
            or (
                target["dimension"] == "language"
                and target["value"] in document["languages"]
            )
            or (
                target["dimension"] == "medium"
                and target["value"] in document["mediums"]
            )
        )
    )
    if not matched:
        return "outside_coverage_targets", []
    if document["selection_state"] == "selected_rich_corpus":
        return "selected_contribution", matched
    return "unselected_candidate", matched


def _policy_snapshot_sha256(policies: Sequence[Mapping[str, Any]]) -> str:
    return record_sha256(
        {
            "visibility_policies": sorted(
                (copy.deepcopy(dict(item)) for item in policies),
                key=lambda item: str(item["visibility_policy_id"]),
            )
        }
    )


def _index_hash(record: Mapping[str, Any]) -> str:
    payload = {
        key: value for key, value in record.items() if key != "index_sha256"
    }
    return record_sha256(payload)


def _entry_field_keys(entries: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    return {
        (str(entry["index_document_id"]), str(field_id))
        for entry in entries
        for field_id in entry["field_ids"]
    }


def build_corpus_index(
    *,
    index_id: str,
    snapshot: Mapping[str, Any],
    built_at: str,
    authority_resolver: IndexAuthorityResolver,
    derived_objects: Sequence[Mapping[str, Any]] = (),
    object_authority: DerivedObjectAuthority | None = None,
    coverage_targets: Sequence[Mapping[str, Any]] = (),
    previous_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic rights-filtered corpus index generation."""

    if not _CORPUS_INDEX_ID.fullmatch(str(index_id)):
        raise SearchServiceError("corpus index identifier is not canonical")
    record = validate_index_snapshot(snapshot)
    built_time = parse_index_timestamp(built_at, "built_at")
    if parse_index_timestamp(record["built_at"], "built_at") > built_time:
        raise SearchServiceError("index snapshot is not yet effective")
    targets = [validate_coverage_target(item) for item in coverage_targets]
    target_ids = sorted(str(item["coverage_target_id"]) for item in targets)
    if len(target_ids) != len(set(target_ids)):
        raise SearchServiceError("coverage targets must be unique")

    documents = {
        str(item["index_document_id"]): item for item in record["documents"]
    }
    edges = {
        (str(item["index_document_id"]), str(item["field_id"])): item
        for item in record["provenance_edges"]
    }
    policies = {
        (str(item["index_document_id"]), str(item["field_id"])): item
        for item in record["visibility_policies"]
    }
    events = {
        str(item["deletion_event_id"]): item for item in record["deletion_events"]
    }

    for document_id, document in documents.items():
        current = _checked(
            validate_index_document,
            _resolve(
                authority_resolver.resolve_index_document,
                index_document_id=document_id,
            ),
        )
        if current != document:
            raise SearchServiceError(
                "index document is missing, stale, or corrected"
            )
    for key, edge in edges.items():
        current = _checked(
            validate_provenance_edge,
            _resolve(
                authority_resolver.resolve_provenance_edge,
                provenance_edge_id=str(edge["provenance_edge_id"]),
            ),
        )
        if current != edge:
            raise SearchServiceError(
                "indexed provenance is missing, stale, or corrected"
            )
        if parse_index_timestamp(edge["evidence_at"], "evidence_at") > built_time:
            raise SearchServiceError("index cannot contain future provenance")
        if built_time >= parse_index_timestamp(
            edge["evidence_expires_at"], "evidence_expires_at"
        ):
            raise SearchServiceError("indexed provenance evidence has expired")
        document = documents[key[0]]
        field = next(
            item for item in document["fields"] if str(item["field_id"]) == key[1]
        )
        policy = _checked(
            validate_visibility_policy,
            _resolve(
                authority_resolver.resolve_visibility_policy,
                index_document_id=key[0],
                field_id=key[1],
            ),
        )
        if policy != policies[key]:
            raise SearchServiceError(
                "indexed visibility policy is missing, stale, or revoked"
            )
        if not policy_authorizes_field(
            document, field, policy, evaluated=built_time
        ):
            raise SearchServiceError(
                "indexed field lacks current effective visibility authority"
            )
    for event_id, event in events.items():
        current = _checked(
            validate_deletion_event,
            _resolve(
                authority_resolver.resolve_deletion_event,
                deletion_event_id=event_id,
            ),
        )
        if current != event:
            raise SearchServiceError(
                "index deletion event is missing, stale, or revoked"
            )

    bindings: dict[str, list[dict[str, Any]]] = {
        document_id: [] for document_id in documents
    }
    seen_keys: set[str] = set()
    for item in derived_objects:
        derived = validate_derived_object(item)
        if object_authority is None:
            raise SearchServiceError(
                "derived objects require a current object authority boundary"
            )
        _verified_receipt(derived, object_authority)
        matches = [
            document
            for document in documents.values()
            if document["source_id"] == derived["source_id"]
            and document["asset_id"] == derived["asset_id"]
        ]
        if len(matches) != 1:
            raise SearchServiceError(
                "derived object does not bind one exact index document"
            )
        document = matches[0]
        document_id = str(document["index_document_id"])
        backing = [
            field
            for field in document["fields"]
            if field["origin_class"] in _DERIVED_ORIGIN_CLASSES
            and edges[(document_id, str(field["field_id"]))]["transformation_id"]
            == derived["transformation_id"]
        ]
        if not backing:
            raise SearchServiceError(
                "derived object lacks exact indexed transformation provenance"
            )
        if str(derived["object_key"]) in seen_keys or any(
            entry["transformation_id"] == derived["transformation_id"]
            for entry in bindings[document_id]
        ):
            raise SearchServiceError("derived object bindings must be unique")
        seen_keys.add(str(derived["object_key"]))
        bindings[document_id].append(derived)

    entries = []
    for document_id, document in documents.items():
        coverage_state, matched_targets = _coverage_for_document(document, targets)
        entries.append(
            {
                "index_document_id": document_id,
                "source_id": document["source_id"],
                "asset_id": document["asset_id"],
                "selection_state": document["selection_state"],
                "coverage_state": coverage_state,
                "coverage_target_ids": matched_targets,
                "duplicate_cluster_id": document["duplicate_cluster_id"],
                "languages": list(document["languages"]),
                "period": document["period"],
                "mediums": list(document["mediums"]),
                "field_ids": sorted(
                    str(field["field_id"]) for field in document["fields"]
                ),
                "derived_objects": sorted(
                    bindings[document_id],
                    key=lambda item: str(item["object_key"]),
                ),
            }
        )
    entries.sort(key=lambda item: str(item["index_document_id"]))

    previous_entries: list[dict[str, Any]] = []
    if previous_index is not None:
        previous_entries = validate_corpus_index(previous_index)["entries"]
    previous_by_id = {
        str(item["index_document_id"]): item for item in previous_entries
    }
    current_by_id = {str(item["index_document_id"]): item for item in entries}
    superseded = sorted(
        _entry_field_keys(previous_entries) - _entry_field_keys(entries)
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "corpus_index",
        "corpus_index_id": index_id,
        "index_sha256": "0" * 64,
        "built_at": built_at,
        "index_snapshot_id": record["index_snapshot_id"],
        "snapshot_sha256": record["snapshot_sha256"],
        "policy_snapshot_sha256": _policy_snapshot_sha256(
            record["visibility_policies"]
        ),
        "coverage_target_ids": target_ids,
        "entries": entries,
        "upserted_document_ids": sorted(
            document_id
            for document_id, entry in current_by_id.items()
            if previous_by_id.get(document_id) != entry
        ),
        "removed_document_ids": sorted(set(previous_by_id) - set(current_by_id)),
        "superseded_fields": [
            {"index_document_id": document_id, "field_id": field_id}
            for document_id, field_id in superseded
        ],
        "snapshot": record,
    }
    result["index_sha256"] = _index_hash(result)
    return validate_corpus_index(result)


def validate_corpus_index(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one corpus index generation and recompute its bindings."""

    if not isinstance(value, Mapping):
        raise SearchServiceError("corpus index must be an object")
    record = copy.deepcopy(dict(value))
    expected_keys = {
        "schema_version",
        "record_type",
        "corpus_index_id",
        "index_sha256",
        "built_at",
        "index_snapshot_id",
        "snapshot_sha256",
        "policy_snapshot_sha256",
        "coverage_target_ids",
        "entries",
        "upserted_document_ids",
        "removed_document_ids",
        "superseded_fields",
        "snapshot",
    }
    if set(record) != expected_keys:
        raise SearchServiceError("corpus index shape is not canonical")
    if record["schema_version"] != 1 or record["record_type"] != "corpus_index":
        raise SearchServiceError("corpus index version is unsupported")
    if not _CORPUS_INDEX_ID.fullmatch(str(record["corpus_index_id"])):
        raise SearchServiceError("corpus index identifier is not canonical")
    if record["index_sha256"] != _index_hash(record):
        raise SearchServiceError("corpus index binding is invalid")
    snapshot = validate_index_snapshot(record["snapshot"])
    record["snapshot"] = snapshot
    if (
        record["index_snapshot_id"] != snapshot["index_snapshot_id"]
        or record["snapshot_sha256"] != snapshot["snapshot_sha256"]
        or record["policy_snapshot_sha256"]
        != _policy_snapshot_sha256(snapshot["visibility_policies"])
    ):
        raise SearchServiceError("corpus index is not bound to its snapshot")
    built_time = parse_index_timestamp(record["built_at"], "built_at")
    if parse_index_timestamp(snapshot["built_at"], "built_at") > built_time:
        raise SearchServiceError("index snapshot is not yet effective")
    documents = {
        str(item["index_document_id"]): item for item in snapshot["documents"]
    }
    entries = record["entries"]
    entry_ids = [str(item["index_document_id"]) for item in entries]
    if entry_ids != sorted(set(entry_ids)) or set(entry_ids) != set(documents):
        raise SearchServiceError("corpus index entries must mirror the snapshot")
    for entry in entries:
        document = documents[str(entry["index_document_id"])]
        if entry["coverage_state"] not in COVERAGE_STATES:
            raise SearchServiceError("corpus index coverage state is unknown")
        if (
            entry["source_id"] != document["source_id"]
            or entry["asset_id"] != document["asset_id"]
            or entry["selection_state"] != document["selection_state"]
            or entry["duplicate_cluster_id"] != document["duplicate_cluster_id"]
            or entry["languages"] != list(document["languages"])
            or entry["period"] != document["period"]
            or entry["mediums"] != list(document["mediums"])
            or entry["field_ids"]
            != sorted(str(field["field_id"]) for field in document["fields"])
            or entry["coverage_target_ids"]
            != sorted(set(entry["coverage_target_ids"]))
            or not set(entry["coverage_target_ids"]).issubset(
                set(record["coverage_target_ids"])
            )
        ):
            raise SearchServiceError(
                "corpus index entry does not match its exact document"
            )
        keys = [str(item["object_key"]) for item in entry["derived_objects"]]
        transformations = [
            str(item["transformation_id"]) for item in entry["derived_objects"]
        ]
        if keys != sorted(set(keys)) or len(transformations) != len(
            set(transformations)
        ):
            raise SearchServiceError("derived object bindings must be canonical")
        for binding in entry["derived_objects"]:
            checked = validate_derived_object(binding)
            if (
                checked != binding
                or checked["source_id"] != document["source_id"]
                or checked["asset_id"] != document["asset_id"]
            ):
                raise SearchServiceError(
                    "derived object binding does not match its exact document"
                )
    if record["coverage_target_ids"] != sorted(set(record["coverage_target_ids"])):
        raise SearchServiceError("coverage target identifiers must be canonical")
    for name in ("upserted_document_ids", "removed_document_ids"):
        if record[name] != sorted(set(record[name])):
            raise SearchServiceError(f"{name} must be unique and sorted")
    if not set(record["upserted_document_ids"]).issubset(set(entry_ids)):
        raise SearchServiceError("upserted documents must be present")
    if set(record["removed_document_ids"]) & set(entry_ids):
        raise SearchServiceError("removed documents cannot remain indexed")
    superseded = [
        (str(item["index_document_id"]), str(item["field_id"]))
        for item in record["superseded_fields"]
    ]
    if superseded != sorted(set(superseded)):
        raise SearchServiceError("superseded fields must be unique and sorted")
    if set(superseded) & _entry_field_keys(entries):
        raise SearchServiceError("superseded fields cannot remain indexed")
    if sanitize(record, environ={}) != record:
        raise SearchServiceError("corpus index contains private data")
    return record


def _prefetch_grants(
    entries: Sequence[Mapping[str, Any]],
    authority_resolver: IndexAuthorityResolver,
) -> dict[tuple[str, str], tuple[str, frozenset[str]]]:
    """Resolve the current grant of every candidate field before ranking.

    Every field of every structurally matching document is resolved exactly
    once whatever the rights outcome is, so authority traffic and timing carry
    no signal about which fields the caller is allowed to see.
    """

    grants: dict[tuple[str, str], tuple[str, frozenset[str]]] = {}
    for entry in entries:
        document_id = str(entry["index_document_id"])
        for field_id in entry["field_ids"]:
            policy = _checked(
                validate_visibility_policy,
                _resolve(
                    authority_resolver.resolve_visibility_policy,
                    index_document_id=document_id,
                    field_id=str(field_id),
                ),
            )
            grants[(document_id, str(field_id))] = (
                ("", frozenset())
                if policy is None
                else (
                    str(policy["visibility_policy_id"]),
                    frozenset(str(item) for item in policy["allowed_operations"]),
                )
            )
    return grants


def _granted_operations(
    grants: Mapping[tuple[str, str], tuple[str, frozenset[str]]],
    *,
    index_document_id: str,
    field_id: str,
    visibility_policy_id: str,
) -> frozenset[str]:
    policy_id, operations = grants.get(
        (index_document_id, field_id), ("", frozenset())
    )
    return operations if policy_id == visibility_policy_id else frozenset()


def _matches_filters(entry: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    return not (
        (
            filters["source_id"] is not None
            and entry["source_id"] != filters["source_id"]
        )
        or (
            filters["language"] is not None
            and filters["language"] not in entry["languages"]
        )
        or (filters["period"] is not None and entry["period"] != filters["period"])
        or (
            filters["medium"] is not None
            and filters["medium"] not in entry["mediums"]
        )
        or (
            filters["selection_state"] is not None
            and entry["selection_state"] != filters["selection_state"]
        )
        or (
            filters["duplicate_cluster_id"] is not None
            and entry["duplicate_cluster_id"] != filters["duplicate_cluster_id"]
        )
    )


def _snippet(value: str) -> str:
    collapsed = " ".join(str(value).split())
    if len(collapsed) <= MAX_SNIPPET_CHARS:
        return collapsed
    return collapsed[:MAX_SNIPPET_CHARS] + "…"


def _facets(results: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    counts: dict[str, dict[str, int]] = {name: {} for name in FACET_DIMENSIONS}
    for result in results:
        singles = (
            ("source_id", result["source_id"]),
            ("period", result["period"]),
            ("selection_state", result["selection_state"]),
            ("coverage_state", result["coverage_state"]),
            ("duplicate_cluster_id", result["duplicate_cluster_id"]),
        )
        for name, value in singles:
            if value is None:
                continue
            counts[name][str(value)] = counts[name].get(str(value), 0) + 1
        for name, values in (
            ("language", result["languages"]),
            ("medium", result["mediums"]),
        ):
            for value in values:
                counts[name][str(value)] = counts[name].get(str(value), 0) + 1
    return {
        name: [
            {"value": value, "count": count}
            for value, count in sorted(
                counts[name].items(), key=lambda item: (-item[1], item[0])
            )
        ]
        for name in FACET_DIMENSIONS
    }


def search_corpus_index(
    index: Mapping[str, Any],
    *,
    audience: str,
    current_time: str,
    authority_resolver: IndexAuthorityResolver,
    query_terms: Sequence[str] = (),
    source_id: str | None = None,
    language: str | None = None,
    period: str | None = None,
    medium: str | None = None,
    selection_state: str | None = None,
    duplicate_cluster_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return deterministic rights-filtered results and safe facets."""

    record = validate_corpus_index(index)
    evaluated = parse_index_timestamp(current_time, "current_time")
    terms = [str(item).casefold() for item in query_terms if str(item).strip()]
    if limit is not None and limit < 0:
        raise SearchServiceError("result limit cannot be negative")
    header = {
        "schema_version": 1,
        "record_type": "corpus_search_result",
        "corpus_index_id": record["corpus_index_id"],
        "index_sha256": record["index_sha256"],
        "policy_snapshot_sha256": record["policy_snapshot_sha256"],
        "operation": SEARCH_OPERATION,
        "audience": audience,
        "evaluated_at": current_time,
    }
    empty = dict(header, result_count=0, results=[], facets=_facets([]))
    if parse_index_timestamp(record["built_at"], "built_at") > evaluated:
        return empty
    snapshot = record["snapshot"]
    entries = {
        str(item["index_document_id"]): item for item in record["entries"]
    }
    edges = {
        str(item["provenance_edge_id"]): item
        for item in snapshot["provenance_edges"]
    }
    clusters = {
        str(item["duplicate_cluster_id"]): item
        for item in snapshot["duplicate_clusters"]
    }
    filters = {
        "source_id": source_id,
        "language": language,
        "period": period,
        "medium": medium,
        "selection_state": selection_state,
        "duplicate_cluster_id": duplicate_cluster_id,
    }
    grants = _prefetch_grants(
        [item for item in record["entries"] if _matches_filters(item, filters)],
        authority_resolver,
    )
    visible = query_index(
        snapshot,
        operation=SEARCH_OPERATION,
        audience=audience,
        current_time=current_time,
        authority_resolver=authority_resolver,
        source_id=source_id,
        language=language,
        period=period,
        medium=medium,
        selection_state=selection_state,
        duplicate_cluster_id=duplicate_cluster_id,
    )
    visible_ids = {str(item["index_document_id"]) for item in visible}
    results = []
    for document in visible:
        document_id = str(document["index_document_id"])
        entry = entries[document_id]
        fields = []
        matches = 0
        for item in document["fields"]:
            edge = edges[str(item["provenance_edge_id"])]
            granted = _granted_operations(
                grants,
                index_document_id=document_id,
                field_id=str(item["field_id"]),
                visibility_policy_id=str(item["visibility_policy_id"]),
            )
            text = str(item["value"])
            haystack = f"{item['name']} {text}".casefold()
            if terms and all(term in haystack for term in terms):
                matches += 1
            fields.append(
                {
                    "field_id": item["field_id"],
                    "name": item["name"],
                    "origin_class": item["origin_class"],
                    "provenance_edge_id": item["provenance_edge_id"],
                    "visibility_policy_id": item["visibility_policy_id"],
                    "value_sha256": field_value_sha256(text),
                    "value_length": len(text),
                    "evidence_at": edge["evidence_at"],
                    "evidence_expires_at": edge["evidence_expires_at"],
                    "snippet": (
                        _snippet(text) if SNIPPET_OPERATION in granted else None
                    ),
                }
            )
        if terms and matches == 0:
            continue
        cluster = clusters.get(str(entry["duplicate_cluster_id"]))
        members = (
            sorted(
                str(member["index_document_id"])
                for member in cluster["members"]
                if str(member["index_document_id"]) in visible_ids
            )
            if cluster is not None
            else []
        )
        results.append(
            {
                "index_document_id": document_id,
                "source_id": entry["source_id"],
                "asset_id": entry["asset_id"],
                "selection_state": entry["selection_state"],
                "coverage_state": entry["coverage_state"],
                "coverage_target_ids": list(entry["coverage_target_ids"]),
                "duplicate_cluster_id": entry["duplicate_cluster_id"],
                "duplicate_member_document_ids": members,
                "languages": list(entry["languages"]),
                "period": entry["period"],
                "mediums": list(entry["mediums"]),
                "evidence_scope": {
                    "evidence_at": max(item["evidence_at"] for item in fields),
                    "evidence_expires_at": min(
                        item["evidence_expires_at"] for item in fields
                    ),
                },
                "match_count": matches,
                "visible_field_ids": [item["field_id"] for item in fields],
                "fields": fields,
            }
        )
    results.sort(
        key=lambda item: (
            -item["match_count"],
            -len(item["fields"]),
            str(item["index_document_id"]),
        )
    )
    facets = _facets(results)
    for position, result in enumerate(results, start=1):
        result["rank"] = position
    limited = results if limit is None else results[:limit]
    return dict(
        header, result_count=len(results), results=limited, facets=facets
    )


def export_score_features(
    index: Mapping[str, Any],
    *,
    audience: str,
    current_time: str,
    authority_resolver: IndexAuthorityResolver,
    object_authority: DerivedObjectAuthority | None = None,
) -> dict[str, Any]:
    """Return one deterministic rights-safe score-generation feature export."""

    record = validate_corpus_index(index)
    if audience not in EXPORT_AUDIENCES:
        raise SearchServiceError(
            "score exports require an authorized non-public audience"
        )
    evaluated = parse_index_timestamp(current_time, "current_time")
    snapshot = record["snapshot"]
    entries = {
        str(item["index_document_id"]): item for item in record["entries"]
    }
    documents: list[dict[str, Any]] = []
    if parse_index_timestamp(record["built_at"], "built_at") <= evaluated:
        grants = _prefetch_grants(record["entries"], authority_resolver)
        visible = query_index(
            snapshot,
            operation=SCORE_OPERATION,
            audience=audience,
            current_time=current_time,
            authority_resolver=authority_resolver,
        )
        source_documents = {
            str(item["index_document_id"]): item for item in snapshot["documents"]
        }
        for document in visible:
            document_id = str(document["index_document_id"])
            entry = entries[document_id]
            indexed = source_documents[document_id]
            features = []
            for item in document["fields"]:
                field = next(
                    child
                    for child in indexed["fields"]
                    if str(child["field_id"]) == str(item["field_id"])
                )
                granted = _granted_operations(
                    grants,
                    index_document_id=document_id,
                    field_id=str(item["field_id"]),
                    visibility_policy_id=str(item["visibility_policy_id"]),
                )
                text = str(item["value"])
                exported_value = None
                if FEATURE_VALUE_OPERATION in granted:
                    if len(text) > MAX_FEATURE_VALUE_CHARS:
                        raise SearchServiceError(
                            "score exports cannot carry long-form field text"
                        )
                    exported_value = text
                features.append(
                    {
                        "field_id": item["field_id"],
                        "name": item["name"],
                        "origin_class": item["origin_class"],
                        "provenance_edge_id": item["provenance_edge_id"],
                        "visibility_policy_id": item["visibility_policy_id"],
                        "rights_snapshot_sha256": field["rights_snapshot_sha256"],
                        "consent_snapshot_sha256": field[
                            "consent_snapshot_sha256"
                        ],
                        "value_sha256": field_value_sha256(text),
                        "value_length": len(text),
                        "value": exported_value,
                    }
                )
            keys = []
            for binding in entry["derived_objects"]:
                if (
                    binding["retrieval_decision"] != "approved"
                    or object_authority is None
                ):
                    continue
                try:
                    _verified_receipt(binding, object_authority)
                except SearchIndexError:
                    continue
                keys.append(
                    {
                        "transformation_id": binding["transformation_id"],
                        "object_key": binding["object_key"],
                        "sha256": binding["sha256"],
                        "byte_size": binding["byte_size"],
                        "media_type": binding["media_type"],
                    }
                )
            documents.append(
                {
                    "index_document_id": document_id,
                    "source_id": entry["source_id"],
                    "asset_id": entry["asset_id"],
                    "selection_state": entry["selection_state"],
                    "coverage_state": entry["coverage_state"],
                    "duplicate_cluster_id": entry["duplicate_cluster_id"],
                    "features": sorted(
                        features, key=lambda item: str(item["field_id"])
                    ),
                    "derived_object_keys": sorted(
                        keys, key=lambda item: str(item["object_key"])
                    ),
                }
            )
    documents.sort(key=lambda item: str(item["index_document_id"]))
    payload = {
        "schema_version": 1,
        "record_type": "score_feature_export",
        "score_export_id": "score_export_" + "0" * 24,
        "corpus_index_id": record["corpus_index_id"],
        "index_sha256": record["index_sha256"],
        "policy_snapshot_sha256": record["policy_snapshot_sha256"],
        "exported_at": current_time,
        "audience": audience,
        "operation": SCORE_OPERATION,
        "documents": documents,
    }
    identity = {key: value for key, value in payload.items() if key != "score_export_id"}
    payload["score_export_id"] = "score_export_" + record_sha256(identity)[:24]
    export = validate_schema_record("score-feature-export", payload)
    _assert_export_is_rights_safe(export)
    return export


def _string_leaves(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _string_leaves(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _string_leaves(child)


def _assert_export_is_rights_safe(export: Mapping[str, Any]) -> None:
    if sanitize(export, environ={}) != export:
        raise SearchServiceError("score export contains private data")
    for text in _string_leaves(export):
        if _UNSAFE_EXPORT_TEXT.search(text) or contains_secret_like_text(text):
            raise SearchServiceError(
                "score export cannot carry locators, URLs, or credentials"
            )


class BundleAuthority:
    """Offline current-authority boundary backed by one reviewed bundle."""

    def __init__(
        self,
        *,
        documents: Sequence[Mapping[str, Any]] = (),
        visibility_policies: Sequence[Mapping[str, Any]] = (),
        provenance_edges: Sequence[Mapping[str, Any]] = (),
        deletion_events: Sequence[Mapping[str, Any]] = (),
        object_receipts: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._documents = {
            str(item["index_document_id"]): copy.deepcopy(dict(item))
            for item in documents
        }
        self._policies = {
            (str(item["index_document_id"]), str(item["field_id"])): copy.deepcopy(
                dict(item)
            )
            for item in visibility_policies
        }
        self._edges = {
            str(item["provenance_edge_id"]): copy.deepcopy(dict(item))
            for item in provenance_edges
        }
        self._events = {
            str(item["deletion_event_id"]): copy.deepcopy(dict(item))
            for item in deletion_events
        }
        self._receipts = {
            str(item["object_key"]): copy.deepcopy(dict(item))
            for item in object_receipts
        }

    def resolve_index_document(
        self, *, index_document_id: str
    ) -> dict[str, Any] | None:
        return copy.deepcopy(self._documents.get(index_document_id))

    def resolve_visibility_policy(
        self, *, index_document_id: str, field_id: str
    ) -> dict[str, Any] | None:
        return copy.deepcopy(self._policies.get((index_document_id, field_id)))

    def resolve_provenance_edge(
        self, *, provenance_edge_id: str
    ) -> dict[str, Any] | None:
        return copy.deepcopy(self._edges.get(provenance_edge_id))

    def resolve_deletion_event(
        self, *, deletion_event_id: str
    ) -> dict[str, Any] | None:
        return copy.deepcopy(self._events.get(deletion_event_id))

    def resolve_object_receipt(self, *, object_key: str) -> dict[str, Any] | None:
        return copy.deepcopy(self._receipts.get(object_key))


def read_json_artifact(path: str | Path) -> Any:
    """Read one bounded reviewed local JSON artifact."""

    candidate = Path(path)
    try:
        size = candidate.stat().st_size
    except OSError as error:
        raise SearchServiceError("local artifact is unreadable") from error
    if size > MAX_ARTIFACT_BYTES:
        raise SearchServiceError("local artifact exceeds the bounded size")
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SearchServiceError("local artifact is not valid JSON") from error


def write_json_artifact(path: str | Path, value: Mapping[str, Any]) -> None:
    """Write one canonical sanitized local JSON artifact."""

    if sanitize(value, environ={}) != value:
        raise SearchServiceError("local artifact contains private data")
    candidate = Path(path)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(canonical_json_bytes(value))


def load_authority_bundle(path: str | Path) -> BundleAuthority:
    """Load one reviewed offline authority bundle for the local surface."""

    bundle = read_json_artifact(path)
    if (
        not isinstance(bundle, Mapping)
        or bundle.get("schema_version") != 1
        or bundle.get("record_type") != "index_authority_bundle"
        or set(bundle) - {
            "schema_version",
            "record_type",
            "documents",
            "visibility_policies",
            "provenance_edges",
            "deletion_events",
            "object_receipts",
        }
    ):
        raise SearchServiceError("authority bundle shape is not canonical")
    return BundleAuthority(
        documents=bundle.get("documents", ()),
        visibility_policies=bundle.get("visibility_policies", ()),
        provenance_edges=bundle.get("provenance_edges", ()),
        deletion_events=bundle.get("deletion_events", ()),
        object_receipts=bundle.get("object_receipts", ()),
    )
