"""Deterministic, content-free enrichment-pilot candidate freezing."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import os
import re
import stat
import unicodedata
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.redaction import sanitize


VIDEO_COUNT = 30
DOCUMENT_COUNT = 30
PAGE_SLOT_COUNT = 30
IMAGE_COUNT = 30
LINKAGE_COUNT = 30

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SAFE_LABEL = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SOURCE_IDS = (
    "njp-center-main",
    "njp-center-video-archive",
    "njp-video-library",
    "njp-youtube-official",
)
_SOURCE_PAIR_ORDER = tuple(itertools.combinations(_SOURCE_IDS, 2))


class PilotFreezeError(ValueError):
    """Raised when a content-free pilot freeze cannot be compiled safely."""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _schema_resource() -> Any:
    packaged = files("performing_fire_corpus").joinpath(
        "schemas", "v1", "pilot-freeze-manifest.json"
    )
    if packaged.is_file():
        return packaged
    return (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "v1"
        / "pilot-freeze-manifest.json"
    )


def _load_json(path: Path) -> tuple[Any, str]:
    try:
        raw = path.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PilotFreezeError("pilot input is not readable JSON") from error


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotFreezeError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PilotFreezeError(f"{label} must be an array")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PilotFreezeError(f"{label} must be non-empty text")
    return value.strip()


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PilotFreezeError(f"{label} must be a positive integer")
    return value


def _valid_hash(value: Any) -> str | None:
    if isinstance(value, str) and _HASH.fullmatch(value):
        return value
    return None


def _asset_id(source_id: str, source_record_id: str) -> str:
    identity = {"source_id": source_id, "source_record_id": source_record_id}
    return f"asset_{source_id.replace('-', '_')}_{_digest(identity)[:24]}"


def _bind_child(
    value: Mapping[str, Any],
    *,
    prefix: str,
    id_field: str,
    digest_field: str,
) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    record[id_field] = f"{prefix}_{_digest(record)[:24]}"
    record[digest_field] = _digest(record)
    return record


def _selection_rank(value: Mapping[str, Any]) -> str:
    return _digest(
        {
            "source_id": value["source_id"],
            "source_record_id": value["source_record_id"],
            "object_key": value["object_key"],
        }
    )


def _sorted_blockers(*values: str | None) -> list[str]:
    return sorted({value for value in values if value is not None})


def _asset_candidate(
    *,
    candidate_kind: str,
    source_id: str,
    source_record_id: str,
    storage_record_id: str | None,
    object_key: str,
    object_sha256: str | None,
    object_receipt_sha256: str | None,
    size_bytes: int,
    mime_type: str | None,
    duration_seconds: int | None,
    page_count: int | None,
    width_pixels: int | None,
    height_pixels: int | None,
    control_role: str,
    selection_rationale_code: str,
    blockers: Sequence[str],
) -> dict[str, Any]:
    return {
        "candidate_kind": candidate_kind,
        "ordinal": 0,
        "source_id": source_id,
        "asset_id": _asset_id(source_id, source_record_id),
        "source_record_id": source_record_id,
        "storage_record_id": storage_record_id,
        "object_key": object_key,
        "object_sha256": object_sha256,
        "object_receipt_sha256": object_receipt_sha256,
        "size_bytes": size_bytes,
        "mime_type": mime_type,
        "duration_seconds": duration_seconds,
        "page_count": page_count,
        "width_pixels": width_pixels,
        "height_pixels": height_pixels,
        "control_role": control_role,
        "selection_rationale_code": selection_rationale_code,
        "execution_state": "held",
        "blockers": sorted(set(blockers)),
    }


def _bind_asset_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    expected_count: int,
) -> list[dict[str, Any]]:
    if len(candidates) != expected_count:
        raise PilotFreezeError("candidate quota cannot be satisfied")
    output: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    keys: set[str] = set()
    for ordinal, candidate in enumerate(candidates, start=1):
        record = copy.deepcopy(dict(candidate))
        identity = (str(record["source_id"]), str(record["source_record_id"]))
        if identity in identities or record["object_key"] in keys:
            raise PilotFreezeError("pilot candidates must have unique identities and keys")
        identities.add(identity)
        keys.add(str(record["object_key"]))
        record["ordinal"] = ordinal
        output.append(
            _bind_child(
                record,
                prefix=f"{record['candidate_kind']}_candidate",
                id_field="candidate_id",
                digest_field="candidate_sha256",
            )
        )
    return output


def _select_by_rank(
    candidates: Sequence[Mapping[str, Any]], count: int
) -> list[Mapping[str, Any]]:
    if len(candidates) < count:
        raise PilotFreezeError("candidate universe is smaller than its frozen quota")
    return sorted(candidates, key=lambda item: (_selection_rank(item), item["asset_id"]))[
        :count
    ]


def _njp_video_candidates(raw_manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for value in _require_list(raw_manifest.get("records"), "NJP raw records"):
        record = _require_mapping(value, "NJP raw record")
        if record.get("kind") != "video" or record.get("status") != "present":
            continue
        objects = _require_list(record.get("r2_objects"), "NJP R2 objects")
        if len(objects) != 1:
            raise PilotFreezeError("NJP video record must bind exactly one R2 object")
        object_record = _require_mapping(objects[0], "NJP R2 object")
        source_record_id = str(record.get("catalogue_record_id", ""))
        storage_record_id = _require_text(
            str(record.get("storage_record_id", "")), "NJP storage record ID"
        )
        output.append(
            _asset_candidate(
                candidate_kind="video",
                source_id="njp-video-library",
                source_record_id=source_record_id,
                storage_record_id=storage_record_id,
                object_key=_require_text(object_record.get("key"), "NJP video key"),
                object_sha256=None,
                object_receipt_sha256=_digest(record),
                size_bytes=_positive_integer(
                    object_record.get("size"), "NJP video size"
                ),
                mime_type=None,
                duration_seconds=None,
                page_count=None,
                width_pixels=None,
                height_pixels=None,
                control_role="primary",
                selection_rationale_code="balanced_source_hash_rank",
                blockers=_sorted_blockers(
                    "duration_required",
                    "exact_sha256_required",
                    "mime_type_required",
                    "operation_authority_required",
                    "stratification_metadata_required",
                ),
            )
        )
    return output


def _youtube_video_candidates(
    youtube_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for value in _require_list(
        youtube_manifest.get("results"), "YouTube media results"
    ):
        record = _require_mapping(value, "YouTube media result")
        if record.get("status") != "uploaded_verified":
            continue
        source_record_id = _require_text(record.get("video_id"), "YouTube video ID")
        sha256 = _valid_hash(record.get("sha256"))
        duration = record.get("duration_seconds")
        duration_seconds = (
            duration
            if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0
            else None
        )
        output.append(
            _asset_candidate(
                candidate_kind="video",
                source_id="njp-youtube-official",
                source_record_id=source_record_id,
                storage_record_id=None,
                object_key=_require_text(
                    record.get("object_key"), "YouTube object key"
                ),
                object_sha256=sha256,
                object_receipt_sha256=_digest(record),
                size_bytes=_positive_integer(
                    record.get("r2_size"), "YouTube object size"
                ),
                mime_type=None,
                duration_seconds=duration_seconds,
                page_count=None,
                width_pixels=None,
                height_pixels=None,
                control_role="primary",
                selection_rationale_code="balanced_source_hash_rank",
                blockers=_sorted_blockers(
                    None if sha256 is not None else "exact_sha256_required",
                    None if duration_seconds is not None else "duration_required",
                    "mime_type_required",
                    "operation_authority_required",
                    "stratification_metadata_required",
                ),
            )
        )
    return output


def _document_candidates(
    raw_manifest: Mapping[str, Any],
    pdf_manifest: Mapping[str, Any],
    archive_pdf_manifest: Mapping[str, Any],
    page_counts: Mapping[str, int] | None,
) -> list[dict[str, Any]]:
    raw_pdf_records: dict[str, Mapping[str, Any]] = {}
    for value in _require_list(raw_manifest.get("records"), "NJP raw records"):
        record = _require_mapping(value, "NJP raw record")
        if record.get("kind") == "pdf" and record.get("status") == "present":
            raw_pdf_records[str(record.get("storage_record_id", ""))] = record

    primary: list[dict[str, Any]] = []
    for value in _require_list(pdf_manifest.get("files"), "NJP PDF files"):
        record = _require_mapping(value, "NJP PDF file")
        source_record_id = str(record.get("record_id", ""))
        raw_record = raw_pdf_records.get(source_record_id)
        if raw_record is None:
            raise PilotFreezeError("NJP PDF is not bound to its R2 presence record")
        objects = _require_list(raw_record.get("r2_objects"), "NJP PDF R2 objects")
        if len(objects) != 1:
            raise PilotFreezeError("NJP PDF must bind exactly one R2 object")
        object_record = _require_mapping(objects[0], "NJP PDF R2 object")
        size = _positive_integer(record.get("bytes"), "NJP PDF size")
        if object_record.get("size") != size:
            raise PilotFreezeError("NJP PDF manifest and R2 receipt size differ")
        sha256 = _valid_hash(record.get("sha256"))
        page_count = None if sha256 is None or page_counts is None else page_counts.get(sha256)
        primary.append(
            _asset_candidate(
                candidate_kind="document",
                source_id="njp-video-library",
                source_record_id=source_record_id,
                storage_record_id=source_record_id,
                object_key=_require_text(object_record.get("key"), "NJP PDF key"),
                object_sha256=sha256,
                object_receipt_sha256=_digest(raw_record),
                size_bytes=size,
                mime_type="application/pdf" if record.get("is_pdf") is True else None,
                duration_seconds=None,
                page_count=page_count,
                width_pixels=None,
                height_pixels=None,
                control_role="primary",
                selection_rationale_code="stable_hash_rank",
                blockers=_sorted_blockers(
                    None if sha256 is not None else "exact_sha256_required",
                    None if record.get("is_pdf") is True else "mime_type_required",
                    "operation_authority_required",
                    None if page_count is not None else "page_count_required",
                ),
            )
        )

    controls: list[dict[str, Any]] = []
    for value in _require_list(
        archive_pdf_manifest.get("files"), "archive PDF files"
    ):
        record = _require_mapping(value, "archive PDF file")
        filename = _require_text(record.get("file"), "archive PDF file name")
        source_record_id = f"archive_pdf_{hashlib.sha256(filename.encode()).hexdigest()[:16]}"
        sha256 = _valid_hash(record.get("sha256"))
        page_count = None if sha256 is None or page_counts is None else page_counts.get(sha256)
        controls.append(
            _asset_candidate(
                candidate_kind="document",
                source_id="njp-center-video-archive",
                source_record_id=source_record_id,
                storage_record_id=None,
                object_key=f"njp-center/videoarchive/{filename}",
                object_sha256=sha256,
                object_receipt_sha256=None,
                size_bytes=_positive_integer(
                    record.get("bytes"), "archive PDF size"
                ),
                mime_type="application/pdf" if record.get("is_pdf") is True else None,
                duration_seconds=None,
                page_count=page_count,
                width_pixels=None,
                height_pixels=None,
                control_role="negative_control",
                selection_rationale_code="archive_control_hash_rank",
                blockers=_sorted_blockers(
                    "exact_object_receipt_required",
                    None if sha256 is not None else "exact_sha256_required",
                    None if record.get("is_pdf") is True else "mime_type_required",
                    "operation_authority_required",
                    None if page_count is not None else "page_count_required",
                ),
            )
        )
    if len(controls) != 8:
        raise PilotFreezeError("all eight archive PDFs are required as controls")
    selected = [
        *_select_by_rank(primary, DOCUMENT_COUNT - len(controls)),
        *sorted(controls, key=lambda item: (_selection_rank(item), item["asset_id"])),
    ]
    return selected


def _image_candidates(image_manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for value in _require_list(image_manifest.get("results"), "image results"):
        record = _require_mapping(value, "image result")
        if record.get("status") != "uploaded_verified":
            continue
        sha256 = _valid_hash(record.get("sha256"))
        source_record_id = str(record.get("catalogue_record_id", ""))
        storage_record_id = _require_text(
            str(record.get("storage_record_id", "")), "image storage record ID"
        )
        output.append(
            _asset_candidate(
                candidate_kind="image",
                source_id="njp-video-library",
                source_record_id=source_record_id,
                storage_record_id=storage_record_id,
                object_key=_require_text(record.get("object_key"), "image object key"),
                object_sha256=sha256,
                object_receipt_sha256=_digest(record),
                size_bytes=_positive_integer(record.get("r2_size"), "image size"),
                mime_type=None,
                duration_seconds=None,
                page_count=None,
                width_pixels=None,
                height_pixels=None,
                control_role="primary",
                selection_rationale_code="stable_hash_rank",
                blockers=_sorted_blockers(
                    "dimensions_required",
                    None if sha256 is not None else "exact_sha256_required",
                    "mime_type_required",
                    "operation_authority_required",
                    "stratification_metadata_required",
                ),
            )
        )
    return output


def _normalized_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _link_record(
    *,
    source_id: str,
    source_record_id: str,
    metadata: Mapping[str, Any],
    titles: Sequence[Any],
    object_receipt_sha256: str | None,
    object_sha256: str | None,
) -> dict[str, Any] | None:
    variants = sorted(
        {
            normalized
            for title in titles
            if (normalized := _normalized_title(title))
        }
    )
    if not variants:
        return None
    return {
        "source_id": source_id,
        "source_record_id": source_record_id,
        "asset_id": _asset_id(source_id, source_record_id),
        "metadata_snapshot_sha256": _digest(metadata),
        "title_fingerprint_sha256": _digest(variants),
        "object_receipt_sha256": object_receipt_sha256,
        "object_sha256": object_sha256,
        "_variants": tuple(frozenset(value.split()) for value in variants),
    }


def _link_records(
    *,
    raw_manifest: Mapping[str, Any],
    njp_catalogue: Sequence[Any],
    youtube_manifest: Mapping[str, Any],
    center_catalogue: Sequence[Any],
    archive_list: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw_videos: dict[str, Mapping[str, Any]] = {}
    for value in _require_list(raw_manifest.get("records"), "NJP raw records"):
        record = _require_mapping(value, "NJP raw record")
        if record.get("kind") == "video" and record.get("status") == "present":
            raw_videos[str(record.get("catalogue_record_id", ""))] = record

    records: list[dict[str, Any]] = []
    for value in njp_catalogue:
        record = _require_mapping(value, "NJP catalogue record")
        source_record_id = str(record.get("id", ""))
        raw_record = raw_videos.get(source_record_id)
        if raw_record is None:
            continue
        linked = _link_record(
            source_id="njp-video-library",
            source_record_id=source_record_id,
            metadata=record,
            titles=(record.get("title"), record.get("titleEn")),
            object_receipt_sha256=_digest(raw_record),
            object_sha256=None,
        )
        if linked is not None:
            records.append(linked)

    for value in _require_list(
        youtube_manifest.get("results"), "YouTube media results"
    ):
        record = _require_mapping(value, "YouTube media result")
        if record.get("status") != "uploaded_verified":
            continue
        source_record_id = _require_text(record.get("video_id"), "YouTube video ID")
        linked = _link_record(
            source_id="njp-youtube-official",
            source_record_id=source_record_id,
            metadata=record,
            titles=(record.get("catalogue_title"),),
            object_receipt_sha256=_digest(record),
            object_sha256=_valid_hash(record.get("sha256")),
        )
        if linked is not None:
            records.append(linked)

    for value in center_catalogue:
        record = _require_mapping(value, "NJP Center catalogue record")
        source_record_id = str(record.get("id", ""))
        linked = _link_record(
            source_id="njp-center-main",
            source_record_id=source_record_id,
            metadata=record,
            titles=(record.get("title"),),
            object_receipt_sha256=None,
            object_sha256=None,
        )
        if linked is not None:
            records.append(linked)

    for value in _require_list(archive_list.get("records"), "archive rows"):
        record = _require_mapping(value, "archive row")
        source_record_id = f"archive_row_{record.get('index')}"
        linked = _link_record(
            source_id="njp-center-video-archive",
            source_record_id=source_record_id,
            metadata=record,
            titles=(record.get("title"),),
            object_receipt_sha256=None,
            object_sha256=None,
        )
        if linked is not None:
            records.append(linked)
    return records


def _pair_key(first: Mapping[str, Any], second: Mapping[str, Any]) -> str:
    members = sorted(
        (
            f"{first['source_id']}:{first['source_record_id']}",
            f"{second['source_id']}:{second['source_record_id']}",
        )
    )
    return _digest(members)


def _similarity(first: Mapping[str, Any], second: Mapping[str, Any]) -> int:
    return round(
        1000
        * max(
            len(left & right) / len(left | right)
            for left in first["_variants"]
            for right in second["_variants"]
            if left | right
        )
    )


def _positive_link_pairs(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    inverted: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        tokens = set().union(*record["_variants"])
        for token in tokens:
            if len(token) > 1:
                inverted.setdefault(token, []).append(index)

    pair_indexes: set[tuple[int, int]] = set()
    for indexes in inverted.values():
        unique = sorted(set(indexes))
        if len(unique) > 100:
            continue
        for left, right in itertools.combinations(unique, 2):
            if records[left]["source_id"] != records[right]["source_id"]:
                pair_indexes.add((left, right))

    output: list[dict[str, Any]] = []
    for left, right in pair_indexes:
        first = records[left]
        second = records[right]
        similarity = _similarity(first, second)
        if similarity < 200:
            continue
        output.append(
            {
                "first": first,
                "second": second,
                "similarity_milli": similarity,
                "pair_key": _pair_key(first, second),
            }
        )
    return output


def _source_pair(pair: Mapping[str, Any]) -> tuple[str, str]:
    return tuple(
        sorted((pair["first"]["source_id"], pair["second"]["source_id"]))
    )  # type: ignore[return-value]


def _select_balanced_pairs(
    pairs: Sequence[Mapping[str, Any]],
    *,
    count: int,
    used_members: set[tuple[str, str]],
) -> list[Mapping[str, Any]]:
    ordered = sorted(
        pairs,
        key=lambda value: (
            -int(value["similarity_milli"]),
            str(value["pair_key"]),
        ),
    )
    selected: list[Mapping[str, Any]] = []
    selected_keys: set[str] = set()

    def accept(pair: Mapping[str, Any]) -> bool:
        members = {
            (pair["first"]["source_id"], pair["first"]["source_record_id"]),
            (pair["second"]["source_id"], pair["second"]["source_record_id"]),
        }
        if members & used_members or str(pair["pair_key"]) in selected_keys:
            return False
        selected.append(pair)
        selected_keys.add(str(pair["pair_key"]))
        used_members.update(members)
        return True

    for source_pair in _SOURCE_PAIR_ORDER:
        for pair in ordered:
            if _source_pair(pair) == source_pair and accept(pair):
                break
        if len(selected) == count:
            return selected

    for pair in ordered:
        if len(selected) == count:
            break
        accept(pair)
    if len(selected) != count:
        raise PilotFreezeError("linkage quota cannot be satisfied without member reuse")
    return selected


def _negative_link_pairs(
    records: Sequence[Mapping[str, Any]],
    used_members: set[tuple[str, str]],
) -> list[Mapping[str, Any]]:
    by_source = {
        source_id: sorted(
            (record for record in records if record["source_id"] == source_id),
            key=lambda value: _digest(
                {
                    "source_id": value["source_id"],
                    "source_record_id": value["source_record_id"],
                }
            ),
        )
        for source_id in _SOURCE_IDS
    }
    pool: list[dict[str, Any]] = []
    for source_pair in _SOURCE_PAIR_ORDER:
        found = 0
        pair_members: set[tuple[str, str]] = set()
        for first in by_source[source_pair[0]]:
            first_identity = (first["source_id"], first["source_record_id"])
            if first_identity in used_members or first_identity in pair_members:
                continue
            first_tokens = set().union(*first["_variants"])
            for second in by_source[source_pair[1]]:
                second_identity = (second["source_id"], second["source_record_id"])
                if second_identity in used_members or second_identity in pair_members:
                    continue
                second_tokens = set().union(*second["_variants"])
                if first_tokens & second_tokens:
                    continue
                pool.append(
                    {
                        "first": first,
                        "second": second,
                        "similarity_milli": 0,
                        "pair_key": _pair_key(first, second),
                    }
                )
                pair_members.update((first_identity, second_identity))
                found += 1
                if found == LINKAGE_COUNT:
                    break
                break
            if found == LINKAGE_COUNT:
                break
    return pool


def _public_link_member(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "source_id",
            "asset_id",
            "source_record_id",
            "metadata_snapshot_sha256",
            "title_fingerprint_sha256",
            "object_receipt_sha256",
            "object_sha256",
        )
    }


def _bind_linkage_clusters(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    positives = _positive_link_pairs(records)
    strong = [pair for pair in positives if pair["similarity_milli"] >= 500]
    ambiguous = [pair for pair in positives if pair["similarity_milli"] < 500]
    used_members: set[tuple[str, str]] = set()
    selected = [
        *(
            ("strong_metadata_match", pair)
            for pair in _select_balanced_pairs(
                strong, count=10, used_members=used_members
            )
        ),
        *(
            ("ambiguous_metadata_match", pair)
            for pair in _select_balanced_pairs(
                ambiguous, count=10, used_members=used_members
            )
        ),
    ]
    negatives = _negative_link_pairs(records, used_members)
    selected.extend(
        (
            ("negative_control", pair)
            for pair in _select_balanced_pairs(
                negatives, count=10, used_members=used_members
            )
        )
    )

    output: list[dict[str, Any]] = []
    for ordinal, (candidate_class, pair) in enumerate(selected, start=1):
        members = sorted(
            (
                _public_link_member(pair["first"]),
                _public_link_member(pair["second"]),
            ),
            key=lambda value: (value["source_id"], value["source_record_id"]),
        )
        blockers = {"human_review_required"}
        if any(member["object_receipt_sha256"] is None for member in members):
            blockers.add("exact_object_receipt_required")
        if not (
            members[0]["object_sha256"]
            and members[0]["object_sha256"] == members[1]["object_sha256"]
        ):
            blockers.add("exact_content_equivalence_required")
        relationship = (
            "probable_match"
            if candidate_class == "strong_metadata_match"
            else "needs_review"
            if candidate_class == "ambiguous_metadata_match"
            else "not_same_control"
        )
        record = {
            "ordinal": ordinal,
            "candidate_class": candidate_class,
            "relationship_candidate": relationship,
            "similarity_milli": pair["similarity_milli"],
            "evidence_classes": ["normalized_title_similarity"],
            "members": members,
            "review_state": "unreviewed",
            "merge_authorized": False,
            "blockers": sorted(blockers),
        }
        output.append(
            _bind_child(
                record,
                prefix="linkage_candidate",
                id_field="cluster_candidate_id",
                digest_field="cluster_candidate_sha256",
            )
        )
    if len(output) != LINKAGE_COUNT:
        raise PilotFreezeError("linkage candidate output count is invalid")
    return output


def _page_slots(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    page_inventory_complete = (
        len(documents) == PAGE_SLOT_COUNT
        and all(
            isinstance(document.get("page_count"), int)
            and not isinstance(document.get("page_count"), bool)
            and document["page_count"] > 0
            for document in documents
        )
    )
    for ordinal in range(1, PAGE_SLOT_COUNT + 1):
        document = documents[ordinal - 1] if page_inventory_complete else None
        page_number = (
            1
            + int(_digest(document["asset_id"]), 16)
            % int(document["page_count"])
            if document is not None
            else None
        )
        record = {
            "ordinal": ordinal,
            "document_candidate_id": (
                document["candidate_id"] if document is not None else None
            ),
            "page_number": page_number,
            "source_page_sha256": None,
            "selection_state": (
                "frozen_metadata_only"
                if document is not None
                else "pending_page_inventory"
            ),
            "blockers": (
                [
                    "operation_authority_required",
                    "source_page_digest_required",
                    "visual_stratification_required",
                ]
                if document is not None
                else [
                    "operation_authority_required",
                    "page_inventory_required",
                    "visual_stratification_required",
                ]
            ),
        }
        output.append(
            _bind_child(
                record,
                prefix="page_slot",
                id_field="page_slot_id",
                digest_field="page_slot_sha256",
            )
        )
    return output


def compile_pilot_freeze(
    *,
    freeze_label: str,
    njp_raw_manifest_path: Path,
    njp_catalogue_path: Path,
    youtube_media_manifest_path: Path,
    njp_pdf_manifest_path: Path,
    archive_pdf_manifest_path: Path,
    image_manifest_path: Path,
    center_catalogue_path: Path,
    archive_list_path: Path,
    pdf_page_counts_path: Path | None = None,
) -> dict[str, Any]:
    """Compile exact candidate sets without reading or transforming source bytes."""

    if _SAFE_LABEL.fullmatch(freeze_label) is None:
        raise PilotFreezeError("freeze label is invalid")
    inputs = {
        "njp_raw_completeness": _load_json(njp_raw_manifest_path),
        "njp_catalogue": _load_json(njp_catalogue_path),
        "youtube_media_completeness": _load_json(youtube_media_manifest_path),
        "njp_pdf_acquisition": _load_json(njp_pdf_manifest_path),
        "archive_pdf_acquisition": _load_json(archive_pdf_manifest_path),
        "njp_image_acquisition": _load_json(image_manifest_path),
        "njp_center_catalogue": _load_json(center_catalogue_path),
        "njp_archive_rows": _load_json(archive_list_path),
    }
    if pdf_page_counts_path is not None:
        inputs["pdf_page_counts"] = _load_json(pdf_page_counts_path)
    raw_manifest = _require_mapping(inputs["njp_raw_completeness"][0], "NJP raw")
    njp_catalogue = _require_list(inputs["njp_catalogue"][0], "NJP catalogue")
    youtube_manifest = _require_mapping(
        inputs["youtube_media_completeness"][0], "YouTube manifest"
    )
    pdf_manifest = _require_mapping(
        inputs["njp_pdf_acquisition"][0], "NJP PDF manifest"
    )
    archive_pdf_manifest = _require_mapping(
        inputs["archive_pdf_acquisition"][0], "archive PDF manifest"
    )
    image_manifest = _require_mapping(
        inputs["njp_image_acquisition"][0], "image manifest"
    )
    center_catalogue = _require_list(
        inputs["njp_center_catalogue"][0], "NJP Center catalogue"
    )
    archive_list = _require_mapping(
        inputs["njp_archive_rows"][0], "archive row list"
    )
    page_counts: dict[str, int] | None = None
    if "pdf_page_counts" in inputs:
        page_count_manifest = _require_mapping(
            inputs["pdf_page_counts"][0], "PDF page counts"
        )
        if (
            page_count_manifest.get("schema_version") != 1
            or page_count_manifest.get("record_type") != "pdf_page_counts"
        ):
            raise PilotFreezeError("PDF page counts have an unsupported contract")
        page_counts = {}
        for value in _require_list(
            page_count_manifest.get("records"), "PDF page count records"
        ):
            page_count_record = _require_mapping(value, "PDF page count record")
            sha256 = _valid_hash(page_count_record.get("object_sha256"))
            if sha256 is None:
                raise PilotFreezeError("PDF page count hash is invalid")
            page_count = _positive_integer(
                page_count_record.get("page_count"), "PDF page count"
            )
            if sha256 in page_counts and page_counts[sha256] != page_count:
                raise PilotFreezeError(
                    "exact duplicate PDFs have conflicting page counts"
                )
            page_counts[sha256] = page_count

    videos = _bind_asset_candidates(
        [
            *_select_by_rank(_njp_video_candidates(raw_manifest), 15),
            *_select_by_rank(_youtube_video_candidates(youtube_manifest), 15),
        ],
        expected_count=VIDEO_COUNT,
    )
    documents = _bind_asset_candidates(
        _document_candidates(
            raw_manifest,
            pdf_manifest,
            archive_pdf_manifest,
            page_counts,
        ),
        expected_count=DOCUMENT_COUNT,
    )
    images = _bind_asset_candidates(
        _select_by_rank(_image_candidates(image_manifest), IMAGE_COUNT),
        expected_count=IMAGE_COUNT,
    )
    linkage_records = _link_records(
        raw_manifest=raw_manifest,
        njp_catalogue=njp_catalogue,
        youtube_manifest=youtube_manifest,
        center_catalogue=center_catalogue,
        archive_list=archive_list,
    )
    linkage_clusters = _bind_linkage_clusters(linkage_records)

    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "enrichment_pilot_freeze",
        "freeze_label": freeze_label,
        "selection_policy_version": "metadata_only_pilot_freeze_v1",
        "candidate_set_state": "frozen",
        "execution_state": "held",
        "source_snapshots": [
            {
                "source_manifest_id": source_manifest_id,
                "source_manifest_sha256": digest,
            }
            for source_manifest_id, (_, digest) in sorted(inputs.items())
        ],
        "quotas": {
            "videos": VIDEO_COUNT,
            "video_sources": {
                "njp-video-library": 15,
                "njp-youtube-official": 15,
            },
            "documents": DOCUMENT_COUNT,
            "document_sources": {
                "njp-center-video-archive": 8,
                "njp-video-library": 22,
            },
            "page_slots": PAGE_SLOT_COUNT,
            "images": IMAGE_COUNT,
            "linkage_clusters": LINKAGE_COUNT,
            "linkage_classes": {
                "ambiguous_metadata_match": 10,
                "negative_control": 10,
                "strong_metadata_match": 10,
            },
        },
        "execution_contract": {
            "authority_issue": 92,
            "tool_boundary": "local_offline_only",
            "concurrency_limit": 1,
            "review_mode": "exact_hash_source_beside_output",
            "transformation_authorized": False,
            "candidate_kinds_separate": [
                "human_caption",
                "image_text_triage",
                "local_machine_asr",
                "machine_image_ocr",
                "machine_pdf_ocr",
                "native_pdf_text",
                "platform_machine_asr",
            ],
            "configuration_state": "pending_version_pin",
            "quality_threshold_state": "pending_human_review",
            "stop_conditions": [
                "cleanup_failure",
                "input_digest_mismatch",
                "missing_current_authority",
                "output_conflict",
                "provenance_drift",
                "review_surface_mismatch",
                "unbounded_cache_or_output",
            ],
        },
        "videos": videos,
        "documents": documents,
        "page_slots": _page_slots(documents),
        "images": images,
        "linkage_clusters": linkage_clusters,
        "blockers": [
            "configuration_versions_required",
            "image_dimensions_required",
            "image_mime_type_required",
            "operation_authority_required",
            "page_visual_selection_required",
            "quality_thresholds_required",
            "video_exact_sha256_required",
            "video_mime_type_required",
            "video_stratification_metadata_required",
        ],
    }
    record["freeze_id"] = f"enrichment_pilot_freeze_{_digest(record)[:24]}"
    record["manifest_sha256"] = _digest(record)
    return validate_pilot_freeze_manifest(record)


def validate_pilot_freeze_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate schema, bindings, counts, and the fail-closed execution state."""

    if not isinstance(value, Mapping):
        raise PilotFreezeError("pilot freeze manifest must be an object")
    record = copy.deepcopy(dict(value))
    try:
        schema = json.loads(_schema_resource().read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(record)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
    ) as error:
        raise PilotFreezeError("pilot freeze does not match its strict schema") from error
    if sanitize(record, environ={}) != record:
        raise PilotFreezeError("pilot freeze contains private or secret-like data")
    if record["candidate_set_state"] != "frozen" or record["execution_state"] != "held":
        raise PilotFreezeError("metadata-only freezes must remain execution-held")
    if record["execution_contract"]["transformation_authorized"] is not False:
        raise PilotFreezeError("pilot freeze cannot authorize transformation")

    for collection, id_field, digest_field in (
        ("videos", "candidate_id", "candidate_sha256"),
        ("documents", "candidate_id", "candidate_sha256"),
        ("images", "candidate_id", "candidate_sha256"),
        ("page_slots", "page_slot_id", "page_slot_sha256"),
        (
            "linkage_clusters",
            "cluster_candidate_id",
            "cluster_candidate_sha256",
        ),
    ):
        identifiers: set[str] = set()
        for child in record[collection]:
            identifier = child[id_field]
            if identifier in identifiers:
                raise PilotFreezeError("pilot child identifiers must be unique")
            identifiers.add(identifier)
            payload = {
                key: nested
                for key, nested in child.items()
                if key not in {id_field, digest_field}
            }
            prefix = identifier.rsplit("_", 1)[0]
            if identifier != f"{prefix}_{_digest(payload)[:24]}":
                raise PilotFreezeError("pilot child identifier binding is invalid")
            child_without_digest = {
                key: nested for key, nested in child.items() if key != digest_field
            }
            if child[digest_field] != _digest(child_without_digest):
                raise PilotFreezeError("pilot child digest binding is invalid")

    payload = {
        key: child
        for key, child in record.items()
        if key not in {"freeze_id", "manifest_sha256"}
    }
    if record["freeze_id"] != f"enrichment_pilot_freeze_{_digest(payload)[:24]}":
        raise PilotFreezeError("pilot freeze identifier binding is invalid")
    without_digest = {
        key: child for key, child in record.items() if key != "manifest_sha256"
    }
    if record["manifest_sha256"] != _digest(without_digest):
        raise PilotFreezeError("pilot freeze manifest digest is invalid")
    return record


def write_pilot_freeze(path: Path, value: Mapping[str, Any]) -> None:
    """Publish once without following or replacing any filesystem entry."""

    encoded = json.dumps(
        validate_pilot_freeze_manifest(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    if path.name in {"", ".", ".."}:
        raise PilotFreezeError("pilot freeze target name is invalid")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise PilotFreezeError("no-follow pilot publication is unavailable")

    parent = path.parent if str(path.parent) else Path(".")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(parent, directory_flags)
    except OSError as error:
        raise PilotFreezeError(
            "pilot freeze parent must be an existing real directory"
        ) from error

    temporary_name = f".{path.name}.pilot-freeze.tmp"
    temporary_fd: int | None = None
    temporary_created = False
    try:
        existing = _existing_file_matches(
            directory_fd,
            path.name,
            encoded,
        )
        if existing is True:
            return
        if existing is False:
            raise PilotFreezeError(
                "refusing to replace a different pilot freeze"
            )

        try:
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            temporary_created = True
        except OSError as error:
            raise PilotFreezeError(
                "exclusive pilot freeze temporary file is unavailable"
            ) from error
        _write_all(temporary_fd, encoded)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None

        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raced = _existing_file_matches(
                directory_fd,
                path.name,
                encoded,
            )
            if raced is not True:
                raise PilotFreezeError(
                    "pilot freeze target changed during atomic publication"
                )
        except OSError as error:
            raise PilotFreezeError(
                "atomic pilot freeze publication failed"
            ) from error
        os.fsync(directory_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise PilotFreezeError(
                    "pilot freeze temporary cleanup failed"
                ) from error
        os.close(directory_fd)


def _existing_file_matches(
    directory_fd: int,
    name: str,
    expected: bytes,
) -> bool | None:
    try:
        file_fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PilotFreezeError(
            "pilot freeze target is not a safe regular file"
        ) from error
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise PilotFreezeError(
                "pilot freeze target is not a safe regular file"
            )
        chunks: list[bytes] = []
        remaining = len(expected) + 1
        while remaining:
            chunk = os.read(file_fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(file_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise PilotFreezeError(
                "pilot freeze target changed during validation"
            )
        return b"".join(chunks) == expected
    finally:
        os.close(file_fd)


def _write_all(file_fd: int, value: bytes) -> None:
    written = 0
    while written < len(value):
        try:
            count = os.write(file_fd, value[written:])
        except OSError as error:
            raise PilotFreezeError("pilot freeze temporary write failed") from error
        if count <= 0:
            raise PilotFreezeError("pilot freeze temporary write failed")
        written += count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze content-free enrichment-pilot candidate identities."
    )
    parser.add_argument("--freeze-label", required=True)
    parser.add_argument("--njp-raw-manifest", type=Path, required=True)
    parser.add_argument("--njp-catalogue", type=Path, required=True)
    parser.add_argument("--youtube-media-manifest", type=Path, required=True)
    parser.add_argument("--njp-pdf-manifest", type=Path, required=True)
    parser.add_argument("--archive-pdf-manifest", type=Path, required=True)
    parser.add_argument("--image-manifest", type=Path, required=True)
    parser.add_argument("--center-catalogue", type=Path, required=True)
    parser.add_argument("--archive-list", type=Path, required=True)
    parser.add_argument("--pdf-page-counts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        manifest = compile_pilot_freeze(
            freeze_label=arguments.freeze_label,
            njp_raw_manifest_path=arguments.njp_raw_manifest,
            njp_catalogue_path=arguments.njp_catalogue,
            youtube_media_manifest_path=arguments.youtube_media_manifest,
            njp_pdf_manifest_path=arguments.njp_pdf_manifest,
            archive_pdf_manifest_path=arguments.archive_pdf_manifest,
            image_manifest_path=arguments.image_manifest,
            center_catalogue_path=arguments.center_catalogue,
            archive_list_path=arguments.archive_list,
            pdf_page_counts_path=arguments.pdf_page_counts,
        )
        write_pilot_freeze(arguments.output, manifest)
    except PilotFreezeError:
        print(json.dumps({"code": "pilot_freeze_invalid_input", "status": "blocked"}))
        return 4
    print(
        json.dumps(
            {
                "candidate_set_state": manifest["candidate_set_state"],
                "execution_state": manifest["execution_state"],
                "freeze_id": manifest["freeze_id"],
                "status": "complete",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
