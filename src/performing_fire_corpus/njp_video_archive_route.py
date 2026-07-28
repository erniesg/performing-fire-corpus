"""Fail-closed VM-first routing for the NJP Center Video Archive inventory.

The route contract is deliberately separate from the network transport.  It
consumes only sanitized, exact-plan inventory reports and emits content-free
attempt receipts and route decisions.  A generic transport error is not enough
to authorize a trusted-laptop fallback: the VM receipt must also bind a
separate sanitized host-capability diagnostic digest from a closed mismatch
taxonomy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from performing_fire_corpus.njp_center_adapters import (
    NJPCenterVideoArchiveAdapter,
)
from performing_fire_corpus.redaction import sanitize


ROUTE_VERSION = "1.0.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,191}$")
_BLOCKER_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROBOTS_URL = "https://njp.ggcf.kr/robots.txt"
_CAPABILITY_MISMATCH_CODES = frozenset(
    {
        "runner_dns_capability_unavailable",
        "runner_ip_route_capability_unavailable",
        "runner_outbound_https_capability_unavailable",
        "runner_tls_capability_unavailable",
    }
)
CAPABILITY_MISMATCH_CODES = tuple(sorted(_CAPABILITY_MISMATCH_CODES))
_REVIEWED_LIMITS = {
    "aggregate_bytes": 65536,
    "elapsed_seconds": 30.0,
    "max_pages": 1,
    "max_requests": 2,
    "max_response_bytes": 65536,
    "max_retries": 0,
    "per_host_interval_seconds": 1.0,
    "retry_after_seconds": 2.0,
    "timeout_seconds": 10.0,
}
_PLAN_KEYS = frozenset(
    {
        "adapter_id",
        "adapter_version",
        "allowed_methods",
        "attachment_requests_allowed",
        "catalogue_body_requests_allowed",
        "commit_sha",
        "endpoint_id",
        "endpoint_url",
        "exact_head_verified",
        "limits",
        "live_shape_digest_comparison_required",
        "record_type",
        "reviewed_shape_sha256",
        "robots_url",
        "run_id",
        "schema_version",
        "source_id",
    }
)
_ATTEMPT_KEYS = frozenset(
    {
        "attachment_bytes_requested",
        "blocker_codes",
        "capability_evidence_sha256",
        "capability_mismatch_code",
        "commit_sha",
        "endpoint_id",
        "fallback_eligible",
        "lane",
        "observed_at",
        "observed_shape_sha256",
        "observed_unique_records",
        "pages_committed",
        "parent_vm_receipt_id",
        "plan_sha256",
        "receipt_id",
        "record_type",
        "report_sha256",
        "requests_attempted",
        "reviewed_shape_sha256",
        "route_version",
        "schema_version",
        "source_id",
        "state",
    }
)
_DECISION_KEYS = frozenset(
    {
        "action",
        "attachment_bytes_requested",
        "commit_sha",
        "decision_id",
        "fallback_authorized",
        "laptop_receipt_id",
        "next_lane",
        "plan_sha256",
        "record_type",
        "route_version",
        "schema_version",
        "source_id",
        "state",
        "vm_receipt_id",
    }
)


class VideoArchiveRouteError(RuntimeError):
    """Content-free failure at the VM/laptop route boundary."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _integer(
    value: object,
    *,
    minimum: int = 0,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise VideoArchiveRouteError("invalid_attempt_fact")
    return value


def _blocker_codes(report: Mapping[str, Any]) -> list[str]:
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        raise VideoArchiveRouteError("invalid_inventory_report")
    codes: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, Mapping):
            raise VideoArchiveRouteError("invalid_inventory_report")
        code = blocker.get("code")
        if not isinstance(code, str) or _BLOCKER_CODE.fullmatch(code) is None:
            raise VideoArchiveRouteError("invalid_inventory_report")
        codes.append(code)
    if len(codes) != len(set(codes)):
        raise VideoArchiveRouteError("invalid_inventory_report")
    return sorted(codes)


def _validate_plan(
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    limits = plan.get("limits")
    if (
        set(plan) != _PLAN_KEYS
        or plan.get("record_type") != "njp_inventory_run_plan"
        or plan.get("schema_version") != 1
        or not isinstance(plan.get("run_id"), str)
        or _RUN_ID.fullmatch(str(plan["run_id"])) is None
        or plan.get("run_id") != report.get("run_id")
        or plan.get("source_id") != NJPCenterVideoArchiveAdapter.source_id
        or plan.get("endpoint_id") != NJPCenterVideoArchiveAdapter.endpoint_id
        or plan.get("adapter_id") != NJPCenterVideoArchiveAdapter.adapter_id
        or plan.get("adapter_version")
        != NJPCenterVideoArchiveAdapter.adapter_version
        or plan.get("commit_sha") != report.get("commit_sha")
        or plan.get("exact_head_verified") is not True
        or plan.get("attachment_requests_allowed") is not False
        or plan.get("catalogue_body_requests_allowed") is not True
        or plan.get("live_shape_digest_comparison_required") is not True
        or plan.get("reviewed_shape_sha256")
        != NJPCenterVideoArchiveAdapter.reviewed_shape_sha256
        or plan.get("robots_url") != _ROBOTS_URL
        or plan.get("endpoint_url")
        != NJPCenterVideoArchiveAdapter.public_url
        or plan.get("allowed_methods")
        != ["GET robots.txt", "GET Video Archive page"]
        or not isinstance(limits, Mapping)
        or set(limits)
        != {
            "aggregate_bytes",
            "elapsed_seconds",
            "max_pages",
            "max_requests",
            "max_response_bytes",
            "max_retries",
            "per_host_interval_seconds",
            "retry_after_seconds",
            "timeout_seconds",
        }
        or dict(limits) != _REVIEWED_LIMITS
    ):
        raise VideoArchiveRouteError("invalid_inventory_plan")
    return _sha256(plan)


def _validate_report(
    report: Mapping[str, Any],
) -> tuple[str, list[str], int, int, int]:
    state = report.get("state")
    blockers = _blocker_codes(report)
    requests = _integer(
        report.get("requests_attempted"),
        maximum=2,
    )
    pages = _integer(report.get("pages_committed"), maximum=1)
    records = _integer(report.get("observed_unique_records"), maximum=8)
    retained_records = report.get("records")
    reviewed = report.get("reviewed_shape_sha256")
    observed = report.get("observed_shape_sha256")
    if (
        report.get("record_type")
        != "njp_inventory_completeness_report"
        or report.get("schema_version") != 1
        or report.get("source_id") != NJPCenterVideoArchiveAdapter.source_id
        or report.get("endpoint_id") != NJPCenterVideoArchiveAdapter.endpoint_id
        or not isinstance(report.get("run_id"), str)
        or _RUN_ID.fullmatch(str(report["run_id"])) is None
        or not isinstance(report.get("commit_sha"), str)
        or _COMMIT_SHA.fullmatch(str(report["commit_sha"])) is None
        or report.get("exact_head_verified") is not True
        or reviewed != NJPCenterVideoArchiveAdapter.reviewed_shape_sha256
        or (
            observed is not None
            and (
                not isinstance(observed, str)
                or _SHA256.fullmatch(observed) is None
            )
        )
        or state not in {"blocked", "complete_for_observed_endpoint"}
        or not isinstance(report.get("generated_at"), str)
        or _TIMESTAMP.fullmatch(str(report["generated_at"])) is None
        or report.get("attachment_candidates") != 0
        or report.get("duplicate_records") != 0
        or report.get("alias_records") != 0
        or not isinstance(retained_records, list)
        or len(retained_records) != records
    ):
        raise VideoArchiveRouteError("invalid_inventory_report")
    if state == "complete_for_observed_endpoint" and (
        blockers
        or requests != 2
        or pages != 1
        or records != 8
        or observed != reviewed
        or report.get("robots_state") != "robots_allowed"
        or report.get("access_state") != "public_get_available"
        or report.get("unvisited_remainder") is not None
    ):
        raise VideoArchiveRouteError("invalid_complete_inventory_report")
    if state == "blocked" and not blockers:
        raise VideoArchiveRouteError("blocked_inventory_without_blocker")
    return str(state), blockers, requests, pages, records


def _attempt_receipt_id(value: Mapping[str, Any]) -> str:
    payload = {key: child for key, child in value.items() if key != "receipt_id"}
    return f"njpva_attempt_{_sha256(payload)}"


def validate_video_archive_attempt_receipt(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and return one strict content-free attempt receipt."""

    if not isinstance(value, Mapping) or set(value) != _ATTEMPT_KEYS:
        raise VideoArchiveRouteError("invalid_attempt_receipt")
    receipt = dict(value)
    blockers = receipt.get("blocker_codes")
    mismatch = receipt.get("capability_mismatch_code")
    evidence = receipt.get("capability_evidence_sha256")
    lane = receipt.get("lane")
    parent = receipt.get("parent_vm_receipt_id")
    state = receipt.get("state")
    fallback_eligible = receipt.get("fallback_eligible")
    if (
        receipt.get("record_type")
        != "njp_video_archive_attempt_receipt"
        or receipt.get("schema_version") != 1
        or receipt.get("route_version") != ROUTE_VERSION
        or receipt.get("source_id")
        != NJPCenterVideoArchiveAdapter.source_id
        or receipt.get("endpoint_id")
        != NJPCenterVideoArchiveAdapter.endpoint_id
        or lane not in {"trusted-vm", "trusted-laptop"}
        or state not in {"blocked", "complete"}
        or not isinstance(receipt.get("commit_sha"), str)
        or _COMMIT_SHA.fullmatch(str(receipt["commit_sha"])) is None
        or not isinstance(receipt.get("plan_sha256"), str)
        or _SHA256.fullmatch(str(receipt["plan_sha256"])) is None
        or not isinstance(receipt.get("report_sha256"), str)
        or _SHA256.fullmatch(str(receipt["report_sha256"])) is None
        or receipt.get("reviewed_shape_sha256")
        != NJPCenterVideoArchiveAdapter.reviewed_shape_sha256
        or (
            receipt.get("observed_shape_sha256") is not None
            and (
                not isinstance(receipt["observed_shape_sha256"], str)
                or _SHA256.fullmatch(receipt["observed_shape_sha256"]) is None
            )
        )
        or not isinstance(receipt.get("observed_at"), str)
        or _TIMESTAMP.fullmatch(str(receipt["observed_at"])) is None
        or not isinstance(blockers, list)
        or blockers != sorted(set(blockers))
        or any(
            not isinstance(code, str)
            or _BLOCKER_CODE.fullmatch(code) is None
            for code in blockers
        )
        or receipt.get("attachment_bytes_requested") is not False
        or not isinstance(fallback_eligible, bool)
    ):
        raise VideoArchiveRouteError("invalid_attempt_receipt")
    requests = _integer(receipt.get("requests_attempted"), maximum=2)
    pages = _integer(receipt.get("pages_committed"), maximum=1)
    records = _integer(receipt.get("observed_unique_records"), maximum=8)
    if state == "complete" and blockers:
        raise VideoArchiveRouteError("invalid_attempt_receipt")
    if state == "complete" and (
        requests != 2
        or pages != 1
        or records != 8
        or receipt.get("observed_shape_sha256")
        != receipt.get("reviewed_shape_sha256")
    ):
        raise VideoArchiveRouteError("invalid_attempt_receipt")
    if state == "blocked" and not blockers:
        raise VideoArchiveRouteError("invalid_attempt_receipt")
    if fallback_eligible != (
        lane == "trusted-vm"
        and blockers == ["transport_error"]
        and mismatch in _CAPABILITY_MISMATCH_CODES
        and isinstance(evidence, str)
        and _SHA256.fullmatch(evidence) is not None
    ):
        raise VideoArchiveRouteError("invalid_fallback_authority")
    if mismatch is None:
        if evidence is not None:
            raise VideoArchiveRouteError("invalid_fallback_authority")
    elif (
        lane != "trusted-vm"
        or mismatch not in _CAPABILITY_MISMATCH_CODES
        or not isinstance(evidence, str)
        or _SHA256.fullmatch(evidence) is None
        or blockers != ["transport_error"]
    ):
        raise VideoArchiveRouteError("invalid_fallback_authority")
    if lane == "trusted-vm":
        if parent is not None:
            raise VideoArchiveRouteError("invalid_attempt_parent")
    elif (
        not isinstance(parent, str)
        or re.fullmatch(r"njpva_attempt_[0-9a-f]{64}", parent) is None
    ):
        raise VideoArchiveRouteError("invalid_attempt_parent")
    if receipt.get("receipt_id") != _attempt_receipt_id(receipt):
        raise VideoArchiveRouteError("invalid_attempt_receipt_id")
    if sanitize(receipt, environ={}) != receipt:
        raise VideoArchiveRouteError("unsafe_attempt_receipt")
    return receipt


def build_video_archive_attempt_receipt(
    report: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    lane: str,
    capability_mismatch_code: str | None = None,
    capability_evidence_sha256: str | None = None,
    parent_vm_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one exact-plan inventory attempt to a sanitized route receipt."""

    state, blockers, requests, pages, records = _validate_report(report)
    plan_sha256 = _validate_plan(plan, report)
    if lane not in {"trusted-vm", "trusted-laptop"}:
        raise VideoArchiveRouteError("invalid_attempt_lane")
    parent_receipt_id: str | None = None
    if lane == "trusted-laptop":
        if parent_vm_receipt is None:
            raise VideoArchiveRouteError("vm_receipt_required")
        vm_receipt = validate_video_archive_attempt_receipt(parent_vm_receipt)
        pending = route_video_archive_attempt(vm_receipt)
        if (
            pending["action"] != "queue_trusted_laptop_exact_plan"
            or vm_receipt["commit_sha"] != report.get("commit_sha")
            or vm_receipt["plan_sha256"] != plan_sha256
        ):
            raise VideoArchiveRouteError("laptop_fallback_not_authorized")
        parent_receipt_id = vm_receipt["receipt_id"]
    elif parent_vm_receipt is not None:
        raise VideoArchiveRouteError("unexpected_vm_receipt")
    if capability_mismatch_code is None:
        if capability_evidence_sha256 is not None:
            raise VideoArchiveRouteError("capability_evidence_without_mismatch")
    elif (
        lane != "trusted-vm"
        or blockers != ["transport_error"]
        or capability_mismatch_code not in _CAPABILITY_MISMATCH_CODES
        or not isinstance(capability_evidence_sha256, str)
        or _SHA256.fullmatch(capability_evidence_sha256) is None
    ):
        raise VideoArchiveRouteError("invalid_capability_mismatch")
    fallback_eligible = capability_mismatch_code is not None
    receipt: dict[str, Any] = {
        "record_type": "njp_video_archive_attempt_receipt",
        "schema_version": 1,
        "route_version": ROUTE_VERSION,
        "receipt_id": "",
        "source_id": NJPCenterVideoArchiveAdapter.source_id,
        "endpoint_id": NJPCenterVideoArchiveAdapter.endpoint_id,
        "lane": lane,
        "commit_sha": report["commit_sha"],
        "plan_sha256": plan_sha256,
        "report_sha256": _sha256(report),
        "reviewed_shape_sha256": report["reviewed_shape_sha256"],
        "observed_shape_sha256": report["observed_shape_sha256"],
        "observed_at": report["generated_at"],
        "state": (
            "complete"
            if state == "complete_for_observed_endpoint"
            else "blocked"
        ),
        "blocker_codes": blockers,
        "capability_mismatch_code": capability_mismatch_code,
        "capability_evidence_sha256": capability_evidence_sha256,
        "fallback_eligible": fallback_eligible,
        "parent_vm_receipt_id": parent_receipt_id,
        "requests_attempted": requests,
        "pages_committed": pages,
        "observed_unique_records": records,
        "attachment_bytes_requested": False,
    }
    receipt["receipt_id"] = _attempt_receipt_id(receipt)
    return validate_video_archive_attempt_receipt(receipt)


def _decision_id(value: Mapping[str, Any]) -> str:
    payload = {key: child for key, child in value.items() if key != "decision_id"}
    return f"njpva_route_{_sha256(payload)}"


def validate_video_archive_route_decision(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and return one strict content-free route decision."""

    if not isinstance(value, Mapping) or set(value) != _DECISION_KEYS:
        raise VideoArchiveRouteError("invalid_route_decision")
    decision = dict(value)
    state = decision.get("state")
    action = decision.get("action")
    next_lane = decision.get("next_lane")
    fallback = decision.get("fallback_authorized")
    laptop_receipt_id = decision.get("laptop_receipt_id")
    allowed_states = {
        (
            "complete",
            "complete_on_vm",
            None,
            False,
            False,
        ),
        (
            "held",
            "hold_vm_blocker",
            None,
            False,
            False,
        ),
        (
            "queued",
            "queue_trusted_laptop_exact_plan",
            "trusted-laptop",
            True,
            False,
        ),
        (
            "complete",
            "resume_vm_from_laptop_receipt",
            "trusted-vm",
            True,
            True,
        ),
        (
            "held",
            "hold_laptop_blocker",
            None,
            True,
            True,
        ),
    }
    if (
        decision.get("record_type")
        != "njp_video_archive_route_decision"
        or decision.get("schema_version") != 1
        or decision.get("route_version") != ROUTE_VERSION
        or decision.get("source_id")
        != NJPCenterVideoArchiveAdapter.source_id
        or not isinstance(decision.get("commit_sha"), str)
        or _COMMIT_SHA.fullmatch(str(decision["commit_sha"])) is None
        or not isinstance(decision.get("plan_sha256"), str)
        or _SHA256.fullmatch(str(decision["plan_sha256"])) is None
        or not isinstance(decision.get("vm_receipt_id"), str)
        or re.fullmatch(
            r"njpva_attempt_[0-9a-f]{64}",
            str(decision["vm_receipt_id"]),
        )
        is None
        or (
            laptop_receipt_id is not None
            and (
                not isinstance(laptop_receipt_id, str)
                or re.fullmatch(
                    r"njpva_attempt_[0-9a-f]{64}",
                    laptop_receipt_id,
                )
                is None
            )
        )
        or (
            state,
            action,
            next_lane,
            fallback,
            laptop_receipt_id is not None,
        )
        not in allowed_states
        or decision.get("attachment_bytes_requested") is not False
        or decision.get("decision_id") != _decision_id(decision)
        or sanitize(decision, environ={}) != decision
    ):
        raise VideoArchiveRouteError("invalid_route_decision")
    return decision


def route_video_archive_attempt(
    vm_receipt: Mapping[str, Any],
    laptop_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the only permitted next lane for one VM-first attempt."""

    vm = validate_video_archive_attempt_receipt(vm_receipt)
    if vm["lane"] != "trusted-vm":
        raise VideoArchiveRouteError("vm_receipt_required")
    laptop: dict[str, Any] | None = None
    if laptop_receipt is not None:
        laptop = validate_video_archive_attempt_receipt(laptop_receipt)
        if (
            laptop["lane"] != "trusted-laptop"
            or laptop["parent_vm_receipt_id"] != vm["receipt_id"]
            or laptop["commit_sha"] != vm["commit_sha"]
            or laptop["plan_sha256"] != vm["plan_sha256"]
            or laptop["reviewed_shape_sha256"]
            != vm["reviewed_shape_sha256"]
        ):
            raise VideoArchiveRouteError("laptop_receipt_contract_changed")
    if vm["state"] == "complete":
        if laptop is not None:
            raise VideoArchiveRouteError("laptop_attempt_not_authorized")
        state = "complete"
        action = "complete_on_vm"
        next_lane = None
        fallback_authorized = False
    elif not vm["fallback_eligible"]:
        if laptop is not None:
            raise VideoArchiveRouteError("laptop_attempt_not_authorized")
        state = "held"
        action = "hold_vm_blocker"
        next_lane = None
        fallback_authorized = False
    elif laptop is None:
        state = "queued"
        action = "queue_trusted_laptop_exact_plan"
        next_lane = "trusted-laptop"
        fallback_authorized = True
    elif laptop["state"] == "complete":
        state = "complete"
        action = "resume_vm_from_laptop_receipt"
        next_lane = "trusted-vm"
        fallback_authorized = True
    else:
        state = "held"
        action = "hold_laptop_blocker"
        next_lane = None
        fallback_authorized = True
    decision: dict[str, Any] = {
        "record_type": "njp_video_archive_route_decision",
        "schema_version": 1,
        "route_version": ROUTE_VERSION,
        "decision_id": "",
        "source_id": NJPCenterVideoArchiveAdapter.source_id,
        "commit_sha": vm["commit_sha"],
        "plan_sha256": vm["plan_sha256"],
        "vm_receipt_id": vm["receipt_id"],
        "laptop_receipt_id": (
            None if laptop is None else laptop["receipt_id"]
        ),
        "state": state,
        "action": action,
        "next_lane": next_lane,
        "fallback_authorized": fallback_authorized,
        "attachment_bytes_requested": False,
    }
    decision["decision_id"] = _decision_id(decision)
    return validate_video_archive_route_decision(decision)


def write_video_archive_route_artifact(
    path: str | Path,
    value: Mapping[str, Any],
) -> None:
    """Atomically persist a validated receipt or route decision."""

    artifact = dict(value)
    if artifact.get("record_type") == "njp_video_archive_attempt_receipt":
        artifact = validate_video_archive_attempt_receipt(artifact)
    elif artifact.get("record_type") == "njp_video_archive_route_decision":
        artifact = validate_video_archive_route_decision(artifact)
    else:
        raise VideoArchiveRouteError("invalid_route_artifact")
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    create_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    temporary_name = f"{selected.name}.tmp"
    directory_fd: int | None = None
    temporary_fd: int | None = None
    created_temporary = False
    try:
        directory_fd = os.open(selected.parent, directory_flags)
        temporary_fd = os.open(
            temporary_name,
            create_flags,
            0o600,
            dir_fd=directory_fd,
        )
        created_temporary = True
        view = memoryview(payload)
        while view:
            written = os.write(temporary_fd, view)
            if written < 1:
                raise OSError("short artifact write")
            view = view[written:]
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.link(
            temporary_name,
            selected.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.fsync(directory_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        created_temporary = False
    except OSError:
        raise VideoArchiveRouteError(
            "route_artifact_write_blocked"
        ) from None
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if created_temporary and directory_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        if directory_fd is not None:
            os.close(directory_fd)
