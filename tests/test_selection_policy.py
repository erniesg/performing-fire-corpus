from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.selection import (
    SelectionPolicyError,
    bind_selection_candidate,
    build_proof_selection_override,
    evaluate_selection,
    validate_coverage_target,
    validate_selection_candidate,
    validate_selection_decision,
    validate_selection_exclusion,
    validate_selection_manifest,
    validate_selection_review_override,
)

SCHEMA_DIR = ROOT / "schemas" / "v1"
RIGHTS_SHA = "a" * 64
INVENTORY_SHA = "b" * 64
GOVERNANCE_SHA = "c" * 64
RETENTION_SHA = "d" * 64
PRIVACY_SHA = "e" * 64
TRANSFORMATION_SHA = "f" * 64
OBSERVATION_SHA = "1" * 64
NOW = "2026-07-24T00:00:00Z"
EXPIRES = "2026-08-24T00:00:00Z"


class SyntheticCandidateResolver:
    def __init__(self, resolved: dict[str, object]) -> None:
        self.resolved = copy.deepcopy(resolved)

    def resolve_selection_candidate(
        self, *, source_id: str, asset_id: str
    ) -> dict[str, object]:
        return copy.deepcopy(self.resolved)


class SyntheticCandidateRegistry:
    def __init__(self, candidates: list[dict[str, object]]) -> None:
        self.resolved = {}
        for value in candidates:
            resolved = {
                key: copy.deepcopy(child)
                for key, child in value.items()
                if key
                not in {
                    "schema_version",
                    "record_type",
                    "candidate_id",
                    "candidate_sha256",
                    "source_id",
                    "asset_id",
                }
            }
            self.resolved[(value["source_id"], value["asset_id"])] = resolved

    def resolve_selection_candidate(
        self, *, source_id: str, asset_id: str
    ) -> dict[str, object]:
        return copy.deepcopy(self.resolved[(source_id, asset_id)])


def candidate(
    suffix: str,
    *,
    source_id: str = "njp-video-library",
    period: str = "1980s",
    language: str = "ko",
    medium: str = "video",
    topic: str = "performance",
    performance_context: str = "broadcast",
    technical_quality: str = "medium",
    duplicate_cluster_id: str | None = None,
    inventory_state: str = "observed",
    retrieval_state: str = "available",
    source_governance_state: str = "approved",
    rights_state: str = "approved",
    retention_state: str = "approved",
    privacy_state: str = "approved",
    transformation_state: str = "approved",
    pipeline_proof: bool = False,
    authority_expires_at: str = EXPIRES,
) -> dict[str, object]:
    resolved = {
        "inventory_observation_id": f"observation_{suffix}",
        "inventory_observation_sha256": OBSERVATION_SHA,
        "inventory_snapshot_sha256": INVENTORY_SHA,
        "inventory_state": inventory_state,
        "retrieval_state": retrieval_state,
        "dimensions": {
            "source": source_id,
            "period": period,
            "languages": [language],
            "mediums": [medium],
            "topics": [topic],
            "performance_contexts": [performance_context],
        },
        "technical_quality": technical_quality,
        "duplicate_cluster_id": duplicate_cluster_id,
        "source_governance_state": source_governance_state,
        "source_governance_snapshot_sha256": GOVERNANCE_SHA,
        "source_governance_expires_at": authority_expires_at,
        "rights_state": rights_state,
        "rights_snapshot_sha256": RIGHTS_SHA,
        "rights_expires_at": authority_expires_at,
        "retention_state": retention_state,
        "retention_snapshot_sha256": RETENTION_SHA,
        "retention_expires_at": authority_expires_at,
        "privacy_state": privacy_state,
        "privacy_snapshot_sha256": PRIVACY_SHA,
        "privacy_expires_at": authority_expires_at,
        "transformation_state": transformation_state,
        "transformation_snapshot_sha256": TRANSFORMATION_SHA,
        "transformation_expires_at": authority_expires_at,
        "pipeline_proof": pipeline_proof,
        "evidence_scope": "Synthetic metadata fixture only.",
    }
    return bind_selection_candidate(
        candidate_id=f"candidate_{suffix}",
        source_id=source_id,
        asset_id=f"asset_{suffix}",
        authority_resolver=SyntheticCandidateResolver(resolved),
    )


def rebind_candidate(value: dict[str, object]) -> dict[str, object]:
    facts = copy.deepcopy(value)
    facts.pop("candidate_sha256", None)
    identity = {
        field: str(facts.pop(field))
        for field in ("candidate_id", "source_id", "asset_id")
    }
    facts.pop("schema_version")
    facts.pop("record_type")
    return bind_selection_candidate(
        **identity,
        authority_resolver=SyntheticCandidateResolver(facts),
    )


def rebind_record(
    value: dict[str, object], *, prefix: str, id_field: str
) -> dict[str, object]:
    rebound = copy.deepcopy(value)
    payload = {
        key: child for key, child in rebound.items() if key != id_field
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    rebound[id_field] = f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"
    return rebound


def target(
    suffix: str,
    *,
    dimension: str,
    value: str,
    minimum: int = 1,
    priority: int = 1,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "coverage_target",
        "coverage_target_id": f"coverage_{suffix}",
        "dimension": dimension,
        "value": value,
        "minimum_selected": minimum,
        "priority": priority,
        "rationale": "Exercise a declared synthetic coverage stratum.",
    }


def evaluate(
    candidates: list[dict[str, object]],
    targets: list[dict[str, object]],
    *,
    policy_version: str = "selection_policy_v1",
) -> dict[str, object]:
    return evaluate_selection(
        candidates,
        targets,
        inventory_snapshot_sha256=INVENTORY_SHA,
        policy_version=policy_version,
        decision_authority="reviewed_project_policy",
        decided_at=NOW,
        expires_at=EXPIRES,
        review_trigger="Re-evaluate when inventory, rights, or policy changes.",
        authority_resolver=SyntheticCandidateRegistry(candidates),
    )


def proof_override(value: dict[str, object]) -> dict[str, object]:
    return build_proof_selection_override(
        value,
        authority_class="reviewed_project_policy",
        decided_at=NOW,
        expires_at="2026-07-30T00:00:00.125000Z",
        rationale="Separately reviewed under the ordinary selection policy.",
        review_trigger="Re-review when the candidate or authority changes.",
        evidence_scope="Synthetic review fixture only.",
    )


class SelectionPolicyTests(unittest.TestCase):
    def test_published_schemas_are_strict_and_runtime_outputs_validate(self) -> None:
        candidates = [candidate("001")]
        targets = [target("source", dimension="source", value="njp-video-library")]
        manifest = evaluate(candidates, targets)

        records = {
            "selection-candidate": candidates,
            "coverage-target": targets,
            "selection-decision": manifest["decisions"],
            "selection-exclusion": manifest["exclusions"],
            "selection-manifest": [manifest],
        }
        for schema_name, values in records.items():
            schema = json.loads(
                (SCHEMA_DIR / f"{schema_name}.json").read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(
                schema, format_checker=FormatChecker()
            )
            for value in values:
                validator.validate(value)

        self.assertEqual(candidates[0], validate_selection_candidate(candidates[0]))
        self.assertEqual(targets[0], validate_coverage_target(targets[0]))
        self.assertEqual(
            manifest["decisions"][0],
            validate_selection_decision(manifest["decisions"][0]),
        )
        self.assertEqual(
            manifest,
            validate_selection_manifest(
                manifest,
                authority_resolver=SyntheticCandidateRegistry(candidates),
            ),
        )

    def test_every_known_candidate_is_counted_even_when_not_selectable(self) -> None:
        candidates = [
            candidate("approved"),
            candidate("blocked", rights_state="blocked"),
            candidate("unavailable", retrieval_state="unavailable"),
            candidate("out_scope", inventory_state="out_of_scope"),
        ]
        manifest = evaluate(
            candidates,
            [target("video", dimension="medium", value="video")],
        )

        self.assertEqual(4, manifest["universe_counts"]["known_candidates"])
        self.assertEqual(1, manifest["universe_counts"]["included"])
        self.assertEqual(3, manifest["universe_counts"]["excluded"])
        self.assertEqual(
            {item["candidate_id"] for item in candidates},
            {item["candidate_id"] for item in manifest["decisions"]},
        )
        self.assertEqual(
            {
                "inventory_out_of_scope",
                "retrieval_unavailable",
                "rights_not_approved",
            },
            {item["reason_code"] for item in manifest["exclusions"]},
        )

    def test_authority_gates_precede_quality_and_downloadability(self) -> None:
        approved = candidate("approved", technical_quality="low")
        blocked = candidate(
            "blocked",
            technical_quality="high",
            rights_state="blocked",
        )
        manifest = evaluate(
            [blocked, approved],
            [target("video", dimension="medium", value="video")],
        )

        included = [
            item["candidate_id"]
            for item in manifest["decisions"]
            if item["decision"] == "include"
        ]
        self.assertEqual(["candidate_approved"], included)
        blocked_decision = next(
            item
            for item in manifest["decisions"]
            if item["candidate_id"] == "candidate_blocked"
        )
        self.assertEqual("exclude", blocked_decision["decision"])
        self.assertEqual("rights_not_approved", blocked_decision["reason_code"])

    def test_expired_rights_never_enter_the_rich_corpus(self) -> None:
        expired = candidate("expired", technical_quality="high")
        expired["rights_expires_at"] = "2026-07-23T00:00:00Z"
        expired = rebind_candidate(expired)
        current = candidate("current", technical_quality="low")
        manifest = evaluate(
            [expired, current],
            [target("video", dimension="medium", value="video")],
        )
        decisions = {
            item["candidate_id"]: item for item in manifest["decisions"]
        }
        self.assertEqual("rights_expired", decisions["candidate_expired"]["reason_code"])
        self.assertEqual("include", decisions["candidate_current"]["decision"])

    def test_proof_asset_is_never_automatically_selected(self) -> None:
        proof = candidate("proof", technical_quality="high", pipeline_proof=True)
        ordinary = candidate("ordinary", technical_quality="low")
        manifest = evaluate(
            [proof, ordinary],
            [target("video", dimension="medium", value="video")],
        )
        decisions = {
            item["candidate_id"]: item for item in manifest["decisions"]
        }
        self.assertEqual("exclude", decisions["candidate_proof"]["decision"])
        self.assertEqual(
            "proof_requires_review",
            decisions["candidate_proof"]["reason_code"],
        )
        self.assertEqual("include", decisions["candidate_ordinary"]["decision"])

    def test_hash_bound_review_can_select_a_proof_asset(self) -> None:
        proof = candidate(
            "proof_reviewed", technical_quality="high", pipeline_proof=True
        )
        override = proof_override(proof)
        manifest = evaluate_selection(
            [proof],
            [target("video", dimension="medium", value="video")],
            inventory_snapshot_sha256=INVENTORY_SHA,
            policy_version="selection_policy_v1",
            decision_authority="reviewed_project_policy",
            decided_at=NOW,
            expires_at=EXPIRES,
            review_trigger=(
                "Re-evaluate when inventory, rights, or policy changes."
            ),
            authority_resolver=SyntheticCandidateRegistry([proof]),
            review_overrides=[override],
        )
        decision = manifest["decisions"][0]
        self.assertEqual("include", decision["decision"])
        self.assertEqual(
            override["selection_review_override_id"],
            decision["selection_review_override_id"],
        )
        self.assertEqual(override["expires_at"], decision["expires_at"])
        self.assertEqual(
            override, validate_selection_review_override(override)
        )

        changed = copy.deepcopy(proof)
        changed["technical_quality"] = "low"
        changed = rebind_candidate(changed)
        with self.assertRaises(SelectionPolicyError):
            evaluate_selection(
                [changed],
                [target("video", dimension="medium", value="video")],
                inventory_snapshot_sha256=INVENTORY_SHA,
                policy_version="selection_policy_v1",
                decision_authority="reviewed_project_policy",
                decided_at=NOW,
                expires_at=EXPIRES,
                review_trigger=(
                    "Re-evaluate when inventory, rights, or policy changes."
                ),
                authority_resolver=SyntheticCandidateRegistry([changed]),
                review_overrides=[override],
            )

    def test_duplicate_clusters_preserve_records_and_select_one_representative(self) -> None:
        candidates = [
            candidate(
                "cluster_low",
                duplicate_cluster_id="duplicate_cluster_001",
                technical_quality="low",
            ),
            candidate(
                "cluster_high",
                duplicate_cluster_id="duplicate_cluster_001",
                technical_quality="high",
            ),
        ]
        manifest = evaluate(
            candidates,
            [target("video", dimension="medium", value="video")],
        )
        decisions = {
            item["candidate_id"]: item for item in manifest["decisions"]
        }
        self.assertEqual("include", decisions["candidate_cluster_high"]["decision"])
        self.assertEqual("exclude", decisions["candidate_cluster_low"]["decision"])
        self.assertEqual(
            "duplicate_not_representative",
            decisions["candidate_cluster_low"]["reason_code"],
        )
        self.assertEqual(2, manifest["universe_counts"]["known_candidates"])

    def test_selection_and_ties_are_deterministic(self) -> None:
        first = candidate("a")
        second = candidate("b")
        targets = [target("video", dimension="medium", value="video")]
        one = evaluate([second, first], targets)
        two = evaluate([first, second], copy.deepcopy(targets))

        self.assertEqual(one, two)
        included = [
            item["candidate_id"]
            for item in one["decisions"]
            if item["decision"] == "include"
        ]
        self.assertEqual(["candidate_a"], included)

    def test_coverage_gaps_and_unresolved_candidates_are_explicit(self) -> None:
        missing_metadata = candidate("missing")
        missing_metadata["dimensions"]["period"] = "unknown"
        missing_metadata = rebind_candidate(missing_metadata)
        manifest = evaluate(
            [missing_metadata],
            [
                target("period", dimension="period", value="1970s"),
                target("video", dimension="medium", value="video"),
            ],
        )
        coverage = {
            item["coverage_target_id"]: item for item in manifest["coverage"]
        }
        self.assertEqual("underrepresented", coverage["coverage_period"]["state"])
        self.assertEqual(1, coverage["coverage_period"]["shortfall"])
        self.assertEqual("underrepresented", coverage["coverage_video"]["state"])
        self.assertEqual(
            ["candidate_missing"],
            manifest["unresolved_metadata_candidate_ids"],
        )
        self.assertEqual(1, manifest["universe_counts"]["unresolved"])
        self.assertEqual("unresolved", manifest["decisions"][0]["decision"])

    def test_policy_version_changes_bound_ids_but_not_selection_facts(self) -> None:
        candidates = [candidate("001")]
        targets = [target("video", dimension="medium", value="video")]
        one = evaluate(candidates, targets, policy_version="selection_policy_v1")
        two = evaluate(candidates, targets, policy_version="selection_policy_v2")

        self.assertNotEqual(one["selection_manifest_id"], two["selection_manifest_id"])
        self.assertNotEqual(
            one["decisions"][0]["selection_decision_id"],
            two["decisions"][0]["selection_decision_id"],
        )
        comparable_one = copy.deepcopy(one["decisions"][0])
        comparable_two = copy.deepcopy(two["decisions"][0])
        for value in (comparable_one, comparable_two):
            value.pop("selection_decision_id")
            value.pop("selection_policy_version")
        self.assertEqual(comparable_one, comparable_two)

    def test_private_or_unbound_decision_data_fails_closed(self) -> None:
        manifest = evaluate(
            [candidate("001")],
            [target("video", dimension="medium", value="video")],
        )
        private = copy.deepcopy(manifest["decisions"][0])
        private["rationale"] = "Contact operator@example.com for approval."
        with self.assertRaises(SelectionPolicyError):
            validate_selection_decision(private)

        mutation = copy.deepcopy(manifest["decisions"][0])
        mutation["decision"] = "exclude"
        with self.assertRaises(SelectionPolicyError):
            validate_selection_decision(mutation)

    def test_exclusion_is_hash_bound_and_validated(self) -> None:
        manifest = evaluate(
            [candidate("blocked", rights_state="blocked")],
            [target("video", dimension="medium", value="video")],
        )
        exclusion = manifest["exclusions"][0]
        self.assertEqual(exclusion, validate_selection_exclusion(exclusion))
        mutation = copy.deepcopy(exclusion)
        mutation["reason_code"] = "coverage_not_needed"
        with self.assertRaises(SelectionPolicyError):
            validate_selection_exclusion(mutation)

    def test_decision_and_manifest_expiry_clip_to_every_authority(self) -> None:
        authority_expiry = "2026-07-30T00:00:00Z"
        manifest = evaluate(
            [candidate("short", authority_expires_at=authority_expiry)],
            [target("video", dimension="medium", value="video")],
        )
        self.assertEqual(authority_expiry, manifest["expires_at"])
        self.assertEqual(authority_expiry, manifest["decisions"][0]["expires_at"])

    def test_noncanonical_source_and_inventory_snapshot_fail_closed(self) -> None:
        facts = candidate("source")
        facts["source_id"] = "unregistered-source"
        facts["dimensions"]["source"] = "unregistered-source"
        with self.assertRaises(SelectionPolicyError):
            rebind_candidate(facts)

        stale = candidate("stale")
        stale["inventory_snapshot_sha256"] = "9" * 64
        stale = rebind_candidate(stale)
        with self.assertRaises(SelectionPolicyError):
            evaluate(
                [stale],
                [target("video", dimension="medium", value="video")],
            )

    def test_contradictory_bound_decision_fails_closed(self) -> None:
        manifest = evaluate(
            [candidate("001")],
            [target("video", dimension="medium", value="video")],
        )
        contradictory = copy.deepcopy(manifest["decisions"][0])
        contradictory["decision"] = "exclude"
        contradictory = rebind_record(
            contradictory,
            prefix="selection_decision",
            id_field="selection_decision_id",
        )
        with self.assertRaises(SelectionPolicyError):
            validate_selection_decision(contradictory)

    def test_manifest_recomputes_coverage_despite_a_valid_hash(self) -> None:
        manifest = evaluate(
            [candidate("001")],
            [target("video", dimension="medium", value="video")],
        )
        forged = copy.deepcopy(manifest)
        forged["coverage"][0]["selected_candidates"] = 0
        forged["coverage"][0]["shortfall"] = 1
        forged["coverage"][0]["state"] = "underrepresented"
        forged = rebind_record(
            forged,
            prefix="selection_manifest",
            id_field="selection_manifest_id",
        )
        with self.assertRaises(SelectionPolicyError):
            validate_selection_manifest(
                forged,
                authority_resolver=SyntheticCandidateRegistry(
                    manifest["candidates"]
                ),
            )

    def test_target_priority_selects_duplicate_representative(self) -> None:
        priority = candidate(
            "priority",
            topic="priority_topic",
            duplicate_cluster_id="duplicate_cluster_priority",
            technical_quality="low",
        )
        broad = candidate(
            "broad",
            topic="secondary_topic",
            performance_context="secondary_context",
            duplicate_cluster_id="duplicate_cluster_priority",
            technical_quality="high",
        )
        manifest = evaluate(
            [broad, priority],
            [
                target(
                    "priority",
                    dimension="topic",
                    value="priority_topic",
                    priority=1,
                ),
                target(
                    "secondary_topic",
                    dimension="topic",
                    value="secondary_topic",
                    priority=2,
                ),
                target(
                    "secondary_context",
                    dimension="performance_context",
                    value="secondary_context",
                    priority=2,
                ),
            ],
        )
        decisions = {
            item["candidate_id"]: item for item in manifest["decisions"]
        }
        self.assertEqual("include", decisions["candidate_priority"]["decision"])
        self.assertEqual(
            "duplicate_not_representative",
            decisions["candidate_broad"]["reason_code"],
        )

    def test_manifest_schema_rejects_loose_nested_records_and_target_bounds(self) -> None:
        manifest = evaluate(
            [candidate("001"), candidate("blocked", rights_state="blocked")],
            [target("video", dimension="medium", value="video")],
        )
        schema = json.loads(
            (SCHEMA_DIR / "selection-manifest.json").read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        for field in ("candidates", "decisions", "exclusions"):
            malformed = copy.deepcopy(manifest)
            malformed[field] = [{}]
            with self.assertRaises(ValidationError):
                validator.validate(malformed)
        excessive = copy.deepcopy(manifest)
        excessive["coverage_targets"][0]["priority"] = 100
        with self.assertRaises(ValidationError):
            validator.validate(excessive)

    def test_candidate_binding_requires_the_authority_resolver_boundary(self) -> None:
        resolved = copy.deepcopy(
            SyntheticCandidateResolver(
                {
                    key: value
                    for key, value in candidate("resolver").items()
                    if key
                    not in {
                        "schema_version",
                        "record_type",
                        "candidate_id",
                        "candidate_sha256",
                        "source_id",
                        "asset_id",
                    }
                }
            ).resolved
        )
        resolved["candidate_sha256"] = "0" * 64
        with self.assertRaises(SelectionPolicyError):
            bind_selection_candidate(
                candidate_id="candidate_resolver",
                source_id="njp-video-library",
                asset_id="asset_resolver",
                authority_resolver=SyntheticCandidateResolver(resolved),
            )

        self_attested = candidate("self_attested")
        current = copy.deepcopy(self_attested)
        current["rights_state"] = "blocked"
        current = rebind_candidate(current)
        manifest = evaluate_selection(
            [self_attested],
            [target("video", dimension="medium", value="video")],
            inventory_snapshot_sha256=INVENTORY_SHA,
            policy_version="selection_policy_v1",
            decision_authority="reviewed_project_policy",
            decided_at=NOW,
            expires_at=EXPIRES,
            review_trigger=(
                "Re-evaluate when inventory, rights, or policy changes."
            ),
            authority_resolver=SyntheticCandidateRegistry([current]),
        )
        self.assertEqual(
            "rights_not_approved",
            manifest["decisions"][0]["reason_code"],
        )

    def test_manifest_rejects_duplicate_asset_and_target_identities(self) -> None:
        first = candidate("first")
        duplicate_asset = candidate("second")
        duplicate_asset["source_id"] = first["source_id"]
        duplicate_asset["asset_id"] = first["asset_id"]
        duplicate_asset["dimensions"]["source"] = first["source_id"]
        duplicate_asset = rebind_candidate(duplicate_asset)
        with self.assertRaises(SelectionPolicyError):
            evaluate(
                [first, duplicate_asset],
                [target("video", dimension="medium", value="video")],
            )

        duplicate_targets = [
            target("same", dimension="medium", value="video"),
            target("same", dimension="topic", value="performance"),
        ]
        with self.assertRaises(SelectionPolicyError):
            evaluate([first], duplicate_targets)

    def test_empty_universe_is_a_valid_complete_snapshot(self) -> None:
        manifest = evaluate([], [])
        self.assertEqual([], manifest["candidates"])
        self.assertEqual([], manifest["decisions"])
        self.assertEqual(
            {
                "known_candidates": 0,
                "included": 0,
                "excluded": 0,
                "unresolved": 0,
            },
            manifest["universe_counts"],
        )
        self.assertEqual(EXPIRES, manifest["expires_at"])

    def test_fractional_authority_expiry_is_not_extended(self) -> None:
        fractional_expiry = "2026-07-30T00:00:00.125000Z"
        manifest = evaluate_selection(
            [candidate("fraction", authority_expires_at=fractional_expiry)],
            [target("video", dimension="medium", value="video")],
            inventory_snapshot_sha256=INVENTORY_SHA,
            policy_version="selection_policy_v1",
            decision_authority="reviewed_project_policy",
            decided_at="2026-07-24T00:00:00.062500Z",
            expires_at="2026-08-24T00:00:00.500000Z",
            review_trigger=(
                "Re-evaluate when inventory, rights, or policy changes."
            ),
            authority_resolver=SyntheticCandidateRegistry(
                [candidate("fraction", authority_expires_at=fractional_expiry)]
            ),
        )
        self.assertEqual(fractional_expiry, manifest["expires_at"])
        self.assertEqual(
            fractional_expiry, manifest["decisions"][0]["expires_at"]
        )


if __name__ == "__main__":
    unittest.main()
