from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from performing_fire_corpus.bounded_discovery import (
    DiscoveryError,
    PageResponse,
    RetryableDiscoveryError,
    run_bounded_discovery as _run_bounded_discovery,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "v1"
T0 = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
LIMIT_KEYS = {
    "aggregate_bytes",
    "elapsed_seconds",
    "max_pages",
    "max_requests",
    "max_response_bytes",
    "max_retries",
    "max_retry_after_seconds",
    "per_host_interval_seconds",
    "timeout_seconds",
}


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.elapsed

    def wall(self) -> datetime:
        return T0 + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


class FakeTransport:
    def __init__(self, outcomes: list[PageResponse | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def fetch(
        self,
        endpoint_id: str,
        cursor: str | None,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> PageResponse:
        self.calls.append(
            {
                "endpoint_id": endpoint_id,
                "cursor": cursor,
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        if not self.outcomes:
            raise AssertionError("unexpected discovery request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class SyntheticAdapter:
    adapter_id = "synthetic-json"
    adapter_version = "1.0.0"

    def __init__(self, limits: dict[str, int | float]) -> None:
        self.limit_contract = dict(limits)
        self.parsed_bodies = 0

    def parse_page(
        self, body: bytes, *, cursor: str | None
    ) -> dict[str, Any]:
        self.parsed_bodies += 1
        return json.loads(body.decode("utf-8"))


class ExplodingAdapter(SyntheticAdapter):
    def parse_page(
        self, body: bytes, *, cursor: str | None
    ) -> dict[str, Any]:
        raise RuntimeError("synthetic parser detail that must not become durable")


def governance() -> dict[str, Any]:
    fact_states = {
        "access_control": "allowed",
        "api_availability": "available",
        "authentication": "not_required",
        "copyright_lawful_basis": "permitted",
        "platform_terms": "permitted",
        "robots": "allowed",
    }
    return {
        "schema_version": 1,
        "record_type": "source_governance",
        "source_governance_id": "source_governance_discovery_synthetic",
        "source_id": "antiegg-fluxus",
        "endpoint_id": "antiegg-posts-api",
        "fact_states": fact_states,
        "observations": [
            {
                "dimension": dimension,
                "state": state,
                "observed_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-25T00:00:00Z",
                "evidence_id": f"evidence_synthetic_{dimension}",
                "next_safe_action": "Revalidate before the synthetic expiry.",
            }
            for dimension, state in fact_states.items()
        ],
        "operation_states": {
            "acquisition_eligibility": "pending",
            "caption_retention": "pending",
            "deletion": "pending",
            "derivative_eligibility": "pending",
            "derived_processing": "pending",
            "indexing": "pending",
            "media_acquisition": "pending",
            "metadata_inventory": "approved",
            "prose_retention": "pending",
            "public_retrieval": "pending",
            "retention": "pending",
            "search_visibility": "pending",
        },
        "decisions": [
            {
                "affected_operation": "metadata_inventory",
                "state": "approved",
                "authority_class": "source_policy_reviewer",
                "basis_code": "synthetic_public_metadata",
                "decided_at": "2026-07-23T00:00:00Z",
                "expires_at": "2026-07-25T00:00:00Z",
                "review_trigger": "Recheck after any source policy change.",
                "next_safe_action": "Run only bounded synthetic metadata discovery.",
            }
        ],
        "blockers": [],
        "evaluated_at": "2026-07-23T00:00:00Z",
    }


def governance_snapshot_id(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return f"policy_snapshot_{hashlib.sha256(encoded).hexdigest()[:32]}"


def run_bounded_discovery(
    run_plan: dict[str, Any], database: Path, **kwargs: Any
) -> dict[str, Any]:
    governance_record = kwargs.pop("governance_record", governance())
    return _run_bounded_discovery(
        run_plan,
        database,
        governance_record=governance_record,
        **kwargs,
    )


def plan(**overrides: Any) -> dict[str, Any]:
    governance_record = governance()
    value = {
        "schema_version": 1,
        "record_type": "discovery_run_plan",
        "run_id": "discovery_run_synthetic_001",
        "source_id": "antiegg-fluxus",
        "endpoint_id": "antiegg-posts-api",
        "adapter_id": "synthetic-json",
        "adapter_version": "1.0.0",
        "policy_snapshot_id": governance_snapshot_id(governance_record),
        "policy_state": "approved",
        "policy_expires_at": "2026-07-25T00:00:00Z",
        "robots_evidence_id": "evidence_synthetic_robots",
        "robots_state": "allowed",
        "robots_expires_at": "2026-07-25T00:00:00Z",
        "limits": {
            "aggregate_bytes": 32768,
            "elapsed_seconds": 30.0,
            "max_pages": 4,
            "max_requests": 8,
            "max_response_bytes": 8192,
            "max_retries": 2,
            "max_retry_after_seconds": 2.0,
            "per_host_interval_seconds": 1.0,
            "timeout_seconds": 3.0,
        },
    }
    for key, child in overrides.items():
        if key == "limits":
            value["limits"].update(child)
        else:
            value[key] = child
    return value


def page(
    records: list[dict[str, Any]],
    *,
    next_cursor: str | None,
    next_ordinal: int | None,
    terminal: bool,
    expected_total: int | None = None,
    rejected_count: int = 0,
    observed_at: datetime = T0,
) -> PageResponse:
    body = json.dumps(
        {
            "records": records,
            "next_cursor": next_cursor,
            "next_ordinal": next_ordinal,
            "terminal": terminal,
            "expected_total": expected_total,
            "rejected_count": rejected_count,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return PageResponse(
        status=200,
        mime_type="application/json",
        body=body,
        observed_at=observed_at,
    )


def record(record_id: str) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "metadata": {
            "date": "2026",
            "kind": "synthetic_catalogue_record",
        },
    }


class BoundedDiscoveryTests(unittest.TestCase):
    def test_strict_contract_schemas_and_positive_limits(self) -> None:
        names = (
            "discovery-run-plan",
            "page-checkpoint",
            "request-fact",
            "discovery-observation",
            "completeness-report",
        )
        for name in names:
            schema = json.loads(
                (SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            self.assertFalse(schema["additionalProperties"])

        invalid = plan(limits={"max_requests": 0})
        schema = json.loads(
            (SCHEMA_DIR / "discovery-run-plan.json").read_text(encoding="utf-8")
        )
        with self.assertRaises(ValidationError):
            Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).validate(invalid)
        for unsafe_code in ("token_private", "a" * 65):
            with self.subTest(unsafe_code=unsafe_code), self.assertRaises(ValueError):
                RetryableDiscoveryError(unsafe_code)
        for unsafe_retry_after in (True, float("nan"), float("inf"), -1.0):
            with (
                self.subTest(unsafe_retry_after=unsafe_retry_after),
                self.assertRaises(ValueError),
            ):
                RetryableDiscoveryError(
                    "synthetic_retry", retry_after_seconds=unsafe_retry_after
                )

    def test_two_pages_commit_body_free_facts_and_complete_deterministically(
        self,
    ) -> None:
        run_plan = plan()
        clock = FakeClock()
        transport = FakeTransport(
            [
                page(
                    [record("synthetic-001")],
                    next_cursor="page-002",
                    next_ordinal=1,
                    terminal=False,
                    expected_total=2,
                ),
                page(
                    [record("synthetic-002")],
                    next_cursor=None,
                    next_ordinal=None,
                    terminal=True,
                    expected_total=2,
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "discovery.sqlite3"
            report = run_bounded_discovery(
                run_plan,
                database,
                adapter=SyntheticAdapter(run_plan["limits"]),
                transport=transport,
                wall_clock=clock.wall,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )
            repeated = run_bounded_discovery(
                run_plan,
                database,
                adapter=SyntheticAdapter(run_plan["limits"]),
                transport=FakeTransport([]),
                wall_clock=clock.wall,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )

            self.assertEqual(report, repeated)
            self.assertEqual("complete_for_observed_endpoint", report["state"])
            self.assertEqual(2, report["observed_unique_records"])
            self.assertEqual(2, report["expected_total"])
            self.assertEqual(0, report["unvisited_remainder"])
            self.assertEqual([1.0], clock.sleeps)
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM discovery_request_facts"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    2,
                    connection.execute(
                        "SELECT COUNT(*) FROM discovery_observations"
                    ).fetchone()[0],
                )
                rendered = "\n".join(
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT body FROM discovery_request_facts
                        UNION ALL
                        SELECT body FROM discovery_observations
                        """
                    )
                ).lower()
                self.assertNotIn('"body"', rendered)
                self.assertNotIn("records", rendered)

    def test_interruptions_before_and_after_commit_resume_without_duplicates(
        self,
    ) -> None:
        for stage in ("before_commit", "after_commit"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                run_plan = plan()
                database = Path(temporary) / "discovery.sqlite3"
                interrupted = False

                def interrupt(event: str, page_sequence: int) -> None:
                    nonlocal interrupted
                    if not interrupted and event == stage and page_sequence == 1:
                        interrupted = True
                        raise KeyboardInterrupt

                first_page = page(
                    [record("synthetic-001")],
                    next_cursor="page-002",
                    next_ordinal=1,
                    terminal=False,
                    expected_total=2,
                )
                with self.assertRaises(KeyboardInterrupt):
                    run_bounded_discovery(
                        run_plan,
                        database,
                        adapter=SyntheticAdapter(run_plan["limits"]),
                        transport=FakeTransport([first_page]),
                        wall_clock=lambda: T0,
                        monotonic=lambda: 0.0,
                        sleeper=lambda _: None,
                        commit_hook=interrupt,
                    )

                outcomes = [
                    page(
                        [record("synthetic-002")],
                        next_cursor=None,
                        next_ordinal=None,
                        terminal=True,
                        expected_total=2,
                    )
                ]
                if stage == "before_commit":
                    outcomes.insert(0, first_page)
                report = run_bounded_discovery(
                    run_plan,
                    database,
                    adapter=SyntheticAdapter(run_plan["limits"]),
                    transport=FakeTransport(outcomes),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )
                self.assertEqual(2, report["observed_unique_records"])
                with sqlite3.connect(database) as connection:
                    self.assertEqual(
                        (2, 2),
                        (
                            connection.execute(
                                "SELECT COUNT(*) FROM discovery_request_facts"
                            ).fetchone()[0],
                            connection.execute(
                                "SELECT COUNT(*) FROM discovery_observations"
                            ).fetchone()[0],
                        ),
                    )

    def test_bounds_report_partial_without_claiming_whole_source_completeness(
        self,
    ) -> None:
        run_plan = plan(limits={"max_pages": 1})
        with tempfile.TemporaryDirectory() as temporary:
            report = run_bounded_discovery(
                run_plan,
                Path(temporary) / "discovery.sqlite3",
                adapter=SyntheticAdapter(run_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001"), record("synthetic-002")],
                            next_cursor="page-002",
                            next_ordinal=1,
                            terminal=False,
                            expected_total=5,
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
        self.assertEqual("bounded_partial", report["state"])
        self.assertEqual(3, report["unvisited_remainder"])
        self.assertEqual("page_budget_exhausted", report["stop_reason"])
        self.assertNotEqual("complete_for_observed_endpoint", report["state"])

    def test_retry_rate_limit_and_exhaustion_are_bounded_and_durable(self) -> None:
        run_plan = plan()
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            report = run_bounded_discovery(
                run_plan,
                Path(temporary) / "success.sqlite3",
                adapter=SyntheticAdapter(run_plan["limits"]),
                transport=FakeTransport(
                    [
                        RetryableDiscoveryError(
                            "synthetic_retry", retry_after_seconds=99.0
                        ),
                        page(
                            [record("synthetic-001")],
                            next_cursor=None,
                            next_ordinal=None,
                            terminal=True,
                            expected_total=1,
                        ),
                    ]
                ),
                wall_clock=clock.wall,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )
            self.assertEqual("complete_for_observed_endpoint", report["state"])
            self.assertEqual(2, report["requests_attempted"])
            self.assertIn(2.0, clock.sleeps)

            blocked = run_bounded_discovery(
                plan(run_id="discovery_run_synthetic_002", limits={"max_retries": 1}),
                Path(temporary) / "blocked.sqlite3",
                adapter=SyntheticAdapter(
                    plan(limits={"max_retries": 1})["limits"]
                ),
                transport=FakeTransport(
                    [
                        RetryableDiscoveryError("retry_one"),
                        RetryableDiscoveryError("retry_two"),
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual("blocked", blocked["state"])
            self.assertEqual("retry_exhausted", blocked["stop_reason"])
            self.assertEqual(2, blocked["requests_attempted"])

    def test_loops_policy_expiry_version_drift_and_unsafe_cursors_fail_closed(
        self,
    ) -> None:
        expired = plan(policy_expires_at="2026-07-24T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "expired.sqlite3"
            report = run_bounded_discovery(
                expired,
                database,
                adapter=SyntheticAdapter(expired["limits"]),
                transport=FakeTransport([]),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(("blocked", "policy_expired"), (report["state"], report["stop_reason"]))

            loop_plan = plan(run_id="discovery_run_synthetic_003")
            loop_report = run_bounded_discovery(
                loop_plan,
                Path(temporary) / "loop.sqlite3",
                adapter=SyntheticAdapter(loop_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor="page-002",
                            next_ordinal=1,
                            terminal=False,
                        ),
                        page(
                            [record("synthetic-002")],
                            next_cursor="page-002",
                            next_ordinal=1,
                            terminal=False,
                        ),
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(("changed", "pagination_loop"), (loop_report["state"], loop_report["stop_reason"]))

            unsafe_plan = plan(run_id="discovery_run_synthetic_004")
            unsafe_report = run_bounded_discovery(
                unsafe_plan,
                Path(temporary) / "unsafe.sqlite3",
                adapter=SyntheticAdapter(unsafe_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor="https://example.invalid/?sig=private",
                            next_ordinal=1,
                            terminal=False,
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(("blocked", "unsafe_cursor"), (unsafe_report["state"], unsafe_report["stop_reason"]))

            opaque_secret_plan = plan(run_id="discovery_run_synthetic_opaque")
            opaque_secret = run_bounded_discovery(
                opaque_secret_plan,
                Path(temporary) / "opaque.sqlite3",
                adapter=SyntheticAdapter(opaque_secret_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor="eyJhbGciOiJub25lIn0.abc.def",
                            next_ordinal=1,
                            terminal=False,
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                ("blocked", "unsafe_cursor"),
                (opaque_secret["state"], opaque_secret["stop_reason"]),
            )

            resumable = plan(run_id="discovery_run_synthetic_005")

            def interrupt_after(event: str, page_sequence: int) -> None:
                if event == "after_commit" and page_sequence == 1:
                    raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                run_bounded_discovery(
                    resumable,
                    Path(temporary) / "version.sqlite3",
                    adapter=SyntheticAdapter(resumable["limits"]),
                    transport=FakeTransport(
                        [
                            page(
                                [record("synthetic-001")],
                                next_cursor="page-002",
                                next_ordinal=1,
                                terminal=False,
                            )
                        ]
                    ),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                    commit_hook=interrupt_after,
                )
            changed = copy.deepcopy(resumable)
            changed["adapter_version"] = "2.0.0"
            changed_report = run_bounded_discovery(
                changed,
                Path(temporary) / "version.sqlite3",
                adapter=type(
                    "ChangedAdapter",
                    (SyntheticAdapter,),
                    {"adapter_version": "2.0.0"},
                )(changed["limits"]),
                transport=FakeTransport([]),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(("changed", "run_plan_changed"), (changed_report["state"], changed_report["stop_reason"]))

    def test_duplicates_rejections_and_shape_drift_are_counted_honestly(self) -> None:
        run_plan = plan(run_id="discovery_run_synthetic_006")
        with tempfile.TemporaryDirectory() as temporary:
            report = run_bounded_discovery(
                run_plan,
                Path(temporary) / "duplicates.sqlite3",
                adapter=SyntheticAdapter(run_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor="page-002",
                            next_ordinal=1,
                            terminal=False,
                            rejected_count=1,
                        ),
                        page(
                            [record("synthetic-001")],
                            next_cursor=None,
                            next_ordinal=None,
                            terminal=True,
                            rejected_count=2,
                        ),
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(1, report["observed_unique_records"])
            self.assertEqual(1, report["duplicate_records"])
            self.assertEqual(3, report["rejected_records"])

            same_page_plan = plan(run_id="discovery_run_synthetic_same_page")
            same_page = run_bounded_discovery(
                same_page_plan,
                Path(temporary) / "same-page.sqlite3",
                adapter=SyntheticAdapter(same_page_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [
                                record("synthetic-001"),
                                record("synthetic-001"),
                                record("synthetic-001"),
                            ],
                            next_cursor=None,
                            next_ordinal=None,
                            terminal=True,
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(1, same_page["observed_unique_records"])
            self.assertEqual(2, same_page["duplicate_records"])

            changed_plan = plan(run_id="discovery_run_synthetic_changed_record")
            changed_record = record("synthetic-001")
            changed_record["metadata"]["date"] = "2027"
            changed = run_bounded_discovery(
                changed_plan,
                Path(temporary) / "changed-record.sqlite3",
                adapter=SyntheticAdapter(changed_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor="page-002",
                            next_ordinal=1,
                            terminal=False,
                        ),
                        page(
                            [changed_record],
                            next_cursor=None,
                            next_ordinal=None,
                            terminal=True,
                        ),
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                ("changed", "record_changed"),
                (changed["state"], changed["stop_reason"]),
            )

            drift_plan = plan(run_id="discovery_run_synthetic_007")
            drift = run_bounded_discovery(
                drift_plan,
                Path(temporary) / "drift.sqlite3",
                adapter=SyntheticAdapter(drift_plan["limits"]),
                transport=FakeTransport(
                    [
                        PageResponse(
                            status=200,
                            mime_type="application/json",
                            body=b'{"unexpected":"shape"}',
                            observed_at=T0,
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(("changed", "shape_drift"), (drift["state"], drift["stop_reason"]))

    def test_adapter_cannot_omit_or_weaken_plan_limits(self) -> None:
        run_plan = plan()
        omitted = SyntheticAdapter(run_plan["limits"])
        del omitted.limit_contract["aggregate_bytes"]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DiscoveryError):
                run_bounded_discovery(
                    run_plan,
                    Path(temporary) / "omitted.sqlite3",
                    adapter=omitted,
                    transport=FakeTransport([]),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )

            weakened = SyntheticAdapter(run_plan["limits"])
            weakened.limit_contract["max_response_bytes"] += 1
            with self.assertRaises(DiscoveryError):
                run_bounded_discovery(
                    run_plan,
                    Path(temporary) / "weakened.sqlite3",
                    adapter=weakened,
                    transport=FakeTransport([]),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )
            silently_stricter = SyntheticAdapter(run_plan["limits"])
            silently_stricter.limit_contract["max_pages"] -= 1
            with self.assertRaises(DiscoveryError):
                run_bounded_discovery(
                    run_plan,
                    Path(temporary) / "silently-stricter.sqlite3",
                    adapter=silently_stricter,
                    transport=FakeTransport([]),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )
        self.assertEqual(LIMIT_KEYS, set(run_plan["limits"]))

    def test_policy_expiry_after_rate_wait_and_transport_errors_stop_safely(
        self,
    ) -> None:
        clock = FakeClock()
        expiring = plan(
            run_id="discovery_run_synthetic_expiring",
            policy_expires_at="2026-07-24T00:00:00.500000Z",
        )
        with tempfile.TemporaryDirectory() as temporary:
            transport = FakeTransport(
                [
                    page(
                        [record("synthetic-001")],
                        next_cursor="page-002",
                        next_ordinal=1,
                        terminal=False,
                    )
                ]
            )
            report = run_bounded_discovery(
                expiring,
                Path(temporary) / "expiring.sqlite3",
                adapter=SyntheticAdapter(expiring["limits"]),
                transport=transport,
                wall_clock=clock.wall,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )
            self.assertEqual(
                ("blocked", "policy_expired"),
                (report["state"], report["stop_reason"]),
            )
            self.assertEqual(1, len(transport.calls))

            transport_error_plan = plan(
                run_id="discovery_run_synthetic_transport_error"
            )
            database = Path(temporary) / "transport-error.sqlite3"
            blocked = run_bounded_discovery(
                transport_error_plan,
                database,
                adapter=SyntheticAdapter(transport_error_plan["limits"]),
                transport=FakeTransport([RuntimeError("synthetic failure")]),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                ("blocked", "transport_error"),
                (blocked["state"], blocked["stop_reason"]),
            )
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    1,
                    connection.execute(
                        "SELECT COUNT(*) FROM discovery_request_facts"
                    ).fetchone()[0],
                )

    def test_governance_snapshot_endpoint_and_eligibility_are_bound_before_request(
        self,
    ) -> None:
        approved = governance()
        run_plan = plan()
        with tempfile.TemporaryDirectory() as temporary:
            tampered = copy.deepcopy(approved)
            tampered["decisions"][0]["basis_code"] = "different_reviewed_basis"
            with self.assertRaises(DiscoveryError):
                run_bounded_discovery(
                    run_plan,
                    Path(temporary) / "tampered.sqlite3",
                    governance_record=tampered,
                    adapter=SyntheticAdapter(run_plan["limits"]),
                    transport=FakeTransport([]),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )

            wrong_endpoint = copy.deepcopy(approved)
            wrong_endpoint["endpoint_id"] = "antiegg-media-api"
            wrong_endpoint_plan = plan(
                run_id="discovery_run_synthetic_wrong_endpoint",
                policy_snapshot_id=governance_snapshot_id(wrong_endpoint),
            )
            with self.assertRaises(DiscoveryError):
                run_bounded_discovery(
                    wrong_endpoint_plan,
                    Path(temporary) / "wrong-endpoint.sqlite3",
                    governance_record=wrong_endpoint,
                    adapter=SyntheticAdapter(wrong_endpoint_plan["limits"]),
                    transport=FakeTransport([]),
                    wall_clock=lambda: T0,
                    monotonic=lambda: 0.0,
                    sleeper=lambda _: None,
                )

            pending = copy.deepcopy(approved)
            pending["operation_states"]["metadata_inventory"] = "pending"
            pending["decisions"][0]["state"] = "pending"
            pending_plan = plan(
                run_id="discovery_run_synthetic_pending_policy",
                policy_snapshot_id=governance_snapshot_id(pending),
                policy_state="pending",
            )
            transport = FakeTransport([])
            blocked = run_bounded_discovery(
                pending_plan,
                Path(temporary) / "pending.sqlite3",
                governance_record=pending,
                adapter=SyntheticAdapter(pending_plan["limits"]),
                transport=transport,
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                ("blocked", "policy_ineligible"),
                (blocked["state"], blocked["stop_reason"]),
            )
            self.assertEqual([], transport.calls)

    def test_future_response_time_and_unexpected_parser_errors_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            future_plan = plan(run_id="discovery_run_synthetic_future_response")
            future_report = run_bounded_discovery(
                future_plan,
                Path(temporary) / "future.sqlite3",
                adapter=SyntheticAdapter(future_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor=None,
                            next_ordinal=None,
                            terminal=True,
                            observed_at=T0 + timedelta(seconds=1),
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                ("blocked", "invalid_response_time"),
                (future_report["state"], future_report["stop_reason"]),
            )

            parser_plan = plan(run_id="discovery_run_synthetic_parser_exception")
            database = Path(temporary) / "parser.sqlite3"
            parser_report = run_bounded_discovery(
                parser_plan,
                database,
                adapter=ExplodingAdapter(parser_plan["limits"]),
                transport=FakeTransport(
                    [
                        page(
                            [record("synthetic-001")],
                            next_cursor=None,
                            next_ordinal=None,
                            terminal=True,
                        )
                    ]
                ),
                wall_clock=lambda: T0,
                monotonic=lambda: 0.0,
                sleeper=lambda _: None,
            )
            self.assertEqual(
                ("changed", "shape_drift"),
                (parser_report["state"], parser_report["stop_reason"]),
            )
            with sqlite3.connect(database) as connection:
                stored = connection.execute(
                    "SELECT body FROM discovery_request_facts"
                ).fetchone()[0]
            self.assertNotIn("synthetic parser detail", stored)


if __name__ == "__main__":
    unittest.main()
