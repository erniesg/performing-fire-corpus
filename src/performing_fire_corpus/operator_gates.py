"""Actionable human gates with durable, resumable state.

A blocker is first-class state, not a stalled process. Every blocker names
the missing authority class, asks one privacy-safe question, recommends the
least permissive response, states the exact next safe action and the command
class that unblocks it, expires, and carries a durable resume token. Blockers
are scoped, so one blocked job never holds unrelated work.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any, Mapping, Sequence

from performing_fire_corpus.observability import (
    ObservabilityError,
    build_envelope,
    format_instant,
    record_identifier,
    safe_serialize,
    safe_text,
    validate_record,
)


ISOLATION_SCOPES = (
    "single_job",
    "single_endpoint",
    "single_source",
    "single_worker",
)
DECISIONS = ("granted", "denied", "deferred")
CHECKPOINT_FIELDS = (
    "cursor",
    "next_ordinal",
    "processed_count",
    "last_stable_id",
    "attempt",
)
_ISOLATION_FIELD = {
    "single_job": "job_id",
    "single_endpoint": "endpoint_id",
    "single_source": "source_id",
    "single_worker": "worker_id",
}

# Every gate literal below is fixed text. Nothing here is interpolated from a
# provider response, a source page, or a person, so a blocker is always safe
# to write to a log, an issue, or an evidence manifest.
BLOCKER_CATALOG: dict[str, dict[str, str]] = {
    "source_governance_approval": {
        "question": (
            "May this source be inventoried under the recorded governance "
            "contract, or should it stay held?"
        ),
        "recommended_response": (
            "Hold the source until a reviewed governance decision exists for "
            "this exact source ID."
        ),
        "next_safe_action": (
            "Record a source-governance decision for the named source ID and "
            "re-run the portable conformance lane; make no request."
        ),
        "unblocking_command_class": "record_source_governance_decision",
        "review_trigger": "Governance decision expiry or a source contract change",
    },
    "rights_approval": {
        "question": (
            "Is this asset approved for the exact operation this job needs, or "
            "is it unresolved?"
        ),
        "recommended_response": (
            "Treat the missing decision as a denial and leave the asset "
            "unqualified."
        ),
        "next_safe_action": (
            "Record an operation-specific rights decision for the named asset "
            "ID; acquire nothing until it exists."
        ),
        "unblocking_command_class": "record_rights_decision",
        "review_trigger": "Rights decision expiry or a platform authority change",
    },
    "retention_approval": {
        "question": (
            "What reviewed retention period applies to the named object key?"
        ),
        "recommended_response": (
            "Keep the shortest recorded retention and schedule a mandatory "
            "review; never default to indefinite."
        ),
        "next_safe_action": (
            "Record a bounded retention authority for the named object key; "
            "delete nothing until it exists."
        ),
        "unblocking_command_class": "record_retention_decision",
        "review_trigger": "Retention authority expiry or a legal-hold change",
    },
    "privacy_approval": {
        "question": (
            "Is the recorded consent still specific enough for this use and "
            "audience?"
        ),
        "recommended_response": (
            "Restrict to the most confidential input class and withhold the "
            "wider audience."
        ),
        "next_safe_action": (
            "Record a specific, withdrawable consent decision for the named "
            "contribution ID; index nothing until it exists."
        ),
        "unblocking_command_class": "record_privacy_decision",
        "review_trigger": "Consent withdrawal or a stated-use change",
    },
    "transformation_approval": {
        "question": (
            "Is this exact transformation profile approved for the named input "
            "object key?"
        ),
        "recommended_response": (
            "Deny the transformation; a missing decision is never a default "
            "permission."
        ),
        "next_safe_action": (
            "Record a transformation decision naming the profile and the input "
            "object key; run no tool until it exists."
        ),
        "unblocking_command_class": "record_transformation_decision",
        "review_trigger": "Profile version drift or a rights class change",
    },
    "object_storage_authority": {
        "question": (
            "Are the separately authorized object-storage secret names "
            "provisioned on the trusted VM?"
        ),
        "recommended_response": (
            "Leave the transfer held and report only the secret names with "
            "present or missing state."
        ),
        "next_safe_action": (
            "Provision the named secrets in the trusted VM secret store and "
            "re-run the readiness check; transfer nothing."
        ),
        "unblocking_command_class": "provision_object_storage_authority",
        "review_trigger": "Secret rotation or a bucket policy change",
    },
    "actions_spending_authority": {
        "question": (
            "Should private GitHub Actions minutes be funded for this "
            "repository, or does local evidence suffice?"
        ),
        "recommended_response": (
            "Leave hosted checks held and accept local or trusted-VM evidence "
            "only for the lanes it actually ran."
        ),
        "next_safe_action": (
            "Record the hosted lane as held and attach the exact-head local "
            "evidence manifest; never mark a held lane passed."
        ),
        "unblocking_command_class": "raise_actions_spending_authority",
        "review_trigger": "Billing or spending limit change",
    },
    "network_acquisition_approval": {
        "question": (
            "Is one bounded public-metadata request approved for the named "
            "endpoint?"
        ),
        "recommended_response": (
            "Keep the run offline on fixtures until a bounded request budget is "
            "approved."
        ),
        "next_safe_action": (
            "Approve a bounded request budget for the named endpoint and run it "
            "from the trusted VM lane only."
        ),
        "unblocking_command_class": "approve_network_acquisition",
        "review_trigger": "Robots or terms change at the named endpoint",
    },
    "trusted_laptop_pairing": {
        "question": (
            "Is the outbound-paired trusted laptop approved for this derived "
            "media job?"
        ),
        "recommended_response": (
            "Leave the job queued; do not move media to an unpaired device."
        ),
        "next_safe_action": (
            "Approve the pairing for the named worker ID and re-lease the job; "
            "move no media until then."
        ),
        "unblocking_command_class": "approve_trusted_laptop_pairing",
        "review_trigger": "Pairing expiry or a device change",
    },
    "deploy_approval": {
        "question": "Is a human approving this deploy or infrastructure apply?",
        "recommended_response": (
            "Withhold approval; deploy and infrastructure lanes stay human-only."
        ),
        "next_safe_action": (
            "Obtain explicit human deploy approval and run the command outside "
            "the agent lane; apply nothing."
        ),
        "unblocking_command_class": "approve_deploy",
        "review_trigger": "Deploy contract change or a production gate change",
    },
}


class OperatorGateError(ValueError):
    """Raised when a human gate is unactionable or loses resumable state."""


def _checkpoint(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(CHECKPOINT_FIELDS):
        raise OperatorGateError(
            "checkpoint must report exactly the resumable checkpoint fields"
        )
    try:
        checkpoint = safe_serialize(dict(value), field="checkpoint")
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error
    processed = checkpoint["processed_count"]
    if isinstance(processed, bool) or not isinstance(processed, int) or processed < 0:
        raise OperatorGateError("checkpoint processed_count must be a count")
    attempt = checkpoint["attempt"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise OperatorGateError("checkpoint attempt must be a positive integer")
    return checkpoint


def _checkpoint_digest(checkpoint: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(checkpoint), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_resume_token(
    *,
    checkpoint: Mapping[str, Any],
    issued_at: datetime,
    expires_at: datetime,
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the durable checkpoint a blocked job resumes from."""

    if not isinstance(issued_at, datetime) or not isinstance(expires_at, datetime):
        raise OperatorGateError("resume token times must be datetimes")
    if expires_at <= issued_at:
        raise OperatorGateError("a resume token must expire after it is issued")
    committed = _checkpoint(checkpoint)
    try:
        payload = build_envelope(
            operation=operation,
            subject_ids=subject_ids,
            lane=lane,
            policy_version=policy_version,
            attempt=attempt,
            bound_consumption=bound_consumption,
            outcome_code="checkpoint_committed",
            evidence_time=issued_at,
        )
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error
    payload.update(
        {
            "checkpoint": committed,
            "checkpoint_sha256": _checkpoint_digest(committed),
            "issued_at": format_instant(issued_at, field="issued_at"),
            "expires_at": format_instant(expires_at, field="expires_at"),
        }
    )
    record = safe_serialize(
        {"schema_version": 1, "record_type": "resume_token", **payload},
        field="resume_token",
    )
    record["resume_token_id"] = record_identifier("resume_token", record)
    try:
        return validate_record("resume-token", record)
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error


def open_blocker(
    *,
    missing_authority_class: str,
    blocked_subject_id: str,
    isolation_scope: str,
    checkpoint: Mapping[str, Any],
    opened_at: datetime,
    expires_at: datetime,
    operation: str,
    subject_ids: Sequence[str],
    lane: str,
    policy_version: str,
    attempt: int,
    bound_consumption: Mapping[str, Any],
) -> dict[str, Any]:
    """Open one actionable blocker together with its resume token."""

    if missing_authority_class not in BLOCKER_CATALOG:
        raise OperatorGateError("missing_authority_class has no actionable gate")
    if isolation_scope not in ISOLATION_SCOPES:
        raise OperatorGateError("isolation_scope is not a declared scope")
    if not isinstance(opened_at, datetime) or not isinstance(expires_at, datetime):
        raise OperatorGateError("blocker times must be datetimes")
    if expires_at <= opened_at:
        raise OperatorGateError("a blocker must expire after it is opened")

    token = build_resume_token(
        checkpoint=checkpoint,
        issued_at=opened_at,
        expires_at=expires_at,
        operation=operation,
        subject_ids=subject_ids,
        lane=lane,
        policy_version=policy_version,
        attempt=attempt,
        bound_consumption=bound_consumption,
    )
    gate = BLOCKER_CATALOG[missing_authority_class]
    try:
        payload = build_envelope(
            operation=operation,
            subject_ids=subject_ids,
            lane=lane,
            policy_version=policy_version,
            attempt=attempt,
            bound_consumption=bound_consumption,
            outcome_code="blocked_on_human",
            evidence_time=opened_at,
        )
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error
    if blocked_subject_id not in payload["subject_ids"]:
        raise OperatorGateError("blocked_subject_id must be one of the subject IDs")
    payload.update(
        {
            "blocked_subject_id": blocked_subject_id,
            "isolation_scope": isolation_scope,
            "missing_authority_class": missing_authority_class,
            "question": safe_text(gate["question"], field="question"),
            "recommended_response": safe_text(
                gate["recommended_response"], field="recommended_response"
            ),
            "next_safe_action": safe_text(
                gate["next_safe_action"], field="next_safe_action"
            ),
            "unblocking_command_class": gate["unblocking_command_class"],
            "review_trigger": safe_text(gate["review_trigger"], field="review_trigger"),
            "opened_at": format_instant(opened_at, field="opened_at"),
            "expires_at": format_instant(expires_at, field="expires_at"),
            "resume_token_id": token["resume_token_id"],
        }
    )
    record = safe_serialize(
        {"schema_version": 1, "record_type": "operator_blocker", **payload},
        field="operator_blocker",
    )
    record["blocker_id"] = record_identifier("operator_blocker", record)
    try:
        blocker = validate_record("operator-blocker", record)
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error
    return {"blocker": blocker, "resume_token": token}


def record_human_decision(
    *,
    blocker: Mapping[str, Any],
    resume_token: Mapping[str, Any],
    decision: str,
    decided_at: datetime,
    granted_authority_class: str | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Record one human decision against an open blocker."""

    try:
        gate = validate_record("operator-blocker", blocker)
        token = validate_record("resume-token", resume_token)
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error
    if token["resume_token_id"] != gate["resume_token_id"]:
        raise OperatorGateError("resume token does not belong to this blocker")
    if decision not in DECISIONS:
        raise OperatorGateError("decision is not a declared decision")
    if not isinstance(decided_at, datetime):
        raise OperatorGateError("decided_at must be a datetime")
    decided = format_instant(decided_at, field="decided_at")
    expired = decided > gate["expires_at"]
    if expired and decision != "deferred":
        raise OperatorGateError(
            "an expired blocker must be re-opened before it can be decided"
        )

    if decision == "granted":
        if granted_authority_class != gate["missing_authority_class"]:
            raise OperatorGateError(
                "a grant must name exactly the missing authority class"
            )
        if not isinstance(expires_at, datetime):
            raise OperatorGateError("a granted authority must expire")
        if expires_at <= decided_at:
            raise OperatorGateError("a granted authority must expire in the future")
        reason_code = "authority_recorded"
        outcome_code = "succeeded"
        token_id: str | None = token["resume_token_id"]
        authority: str | None = granted_authority_class
        expiry: str | None = format_instant(expires_at, field="expires_at")
    else:
        if granted_authority_class is not None:
            raise OperatorGateError(
                "only a grant may name an authority class"
            )
        if decision == "denied":
            reason_code = "authority_withheld"
            outcome_code = "failed_closed"
            token_id = None
        else:
            reason_code = "blocker_expired" if expired else "awaiting_further_review"
            outcome_code = "blocked_on_human"
            token_id = token["resume_token_id"]
        authority = None
        expiry = (
            format_instant(expires_at, field="expires_at")
            if isinstance(expires_at, datetime)
            else None
        )

    payload = {
        "blocker_id": gate["blocker_id"],
        "decision": decision,
        "decision_reason_code": reason_code,
        "granted_authority_class": authority,
        "review_trigger": gate["review_trigger"],
        "decided_at": decided,
        "expires_at": expiry,
        "resume_token_id": token_id,
        "operation": gate["operation"],
        "subject_ids": list(gate["subject_ids"]),
        "lane": gate["lane"],
        "policy_version": gate["policy_version"],
        "attempt": gate["attempt"],
        "bound_consumption": dict(gate["bound_consumption"]),
        "outcome_code": outcome_code,
        "evidence_time": decided,
    }
    record = safe_serialize(
        {"schema_version": 1, "record_type": "human_decision", **payload},
        field="human_decision",
    )
    record["decision_id"] = record_identifier("human_decision", record)
    try:
        return validate_record("human-decision", record)
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error


def resume_checkpoint(
    *,
    resume_token: Mapping[str, Any],
    human_decision: Mapping[str, Any],
    resumed_at: datetime,
) -> dict[str, Any]:
    """Return the checkpoint a granted decision authorizes work to resume from."""

    try:
        token = validate_record("resume-token", resume_token)
        made = validate_record("human-decision", human_decision)
    except ObservabilityError as error:
        raise OperatorGateError(str(error)) from error
    if made["decision"] != "granted":
        raise OperatorGateError("only a granted decision authorizes a resume")
    if made["resume_token_id"] != token["resume_token_id"]:
        raise OperatorGateError("resume token does not belong to this decision")
    if _checkpoint_digest(token["checkpoint"]) != token["checkpoint_sha256"]:
        raise OperatorGateError("resume checkpoint does not match its digest")
    if not isinstance(resumed_at, datetime):
        raise OperatorGateError("resumed_at must be a datetime")
    at = format_instant(resumed_at, field="resumed_at")
    if at > token["expires_at"]:
        raise OperatorGateError("the resume token has expired")
    if made["expires_at"] is None or at > made["expires_at"]:
        raise OperatorGateError("the granted authority has expired")
    return copy.deepcopy(token["checkpoint"])


def partition_work(
    work_items: Sequence[Mapping[str, Any]], blockers: Sequence[Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Split queued work into what stays runnable and what one blocker holds."""

    scopes: list[tuple[str, str]] = []
    for blocker in blockers:
        try:
            gate = validate_record("operator-blocker", blocker)
        except ObservabilityError as error:
            raise OperatorGateError(str(error)) from error
        scopes.append(
            (_ISOLATION_FIELD[gate["isolation_scope"]], gate["blocked_subject_id"])
        )

    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in work_items:
        if not isinstance(item, Mapping) or set(item) != set(_ISOLATION_FIELD.values()):
            raise OperatorGateError(
                "work item must report exactly the declared isolation fields"
            )
        try:
            candidate = safe_serialize(dict(item), field="work_item")
        except ObservabilityError as error:
            raise OperatorGateError(str(error)) from error
        held = any(candidate[field] == value for field, value in scopes)
        (blocked if held else ready).append(candidate)
    return {"ready": ready, "blocked": blocked}
