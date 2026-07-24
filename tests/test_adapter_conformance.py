from __future__ import annotations

import copy
import json
import socket
import unittest
import urllib.request
import webbrowser
from pathlib import Path

from performing_fire_corpus.adapter_conformance import (
    AdapterConformanceError,
    MetadataResponse,
    OfflineConformanceHarness,
    assert_stable_identity,
    deny_live_network,
    validate_adapter_declaration,
)
from performing_fire_corpus.registry import load_registry
from synthetic_adapter_builders import (
    SyntheticMetadataAdapter,
    synthetic_item,
    synthetic_page,
    varied_identity_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_registry(ROOT / "config" / "source-registry.v1.json")


def response(
    body: bytes,
    *,
    status: int = 200,
    mime_type: str = "application/json",
    final_url: str = "https://antiegg.kr/wp-json/wp/v2/posts",
) -> MetadataResponse:
    return MetadataResponse(
        status=status,
        mime_type=mime_type,
        body=body,
        final_url=final_url,
    )


class AdapterDeclarationTests(unittest.TestCase):
    def test_synthetic_adapter_binds_to_canonical_registry_endpoint(self) -> None:
        declaration = validate_adapter_declaration(
            SyntheticMetadataAdapter(), REGISTRY
        )
        self.assertEqual("antiegg-fluxus", declaration["source_id"])
        self.assertEqual("antiegg-posts-api", declaration["endpoint_id"])
        self.assertEqual(["application/json"], declaration["expected_mime_types"])

    def test_declaration_rejects_unknown_or_unsafe_capabilities(self) -> None:
        for field, value in (
            ("source_id", "later-unreviewed-source"),
            ("endpoint_id", "njp-center-main-home"),
            ("allowed_methods", ("GET", "POST")),
            ("allowed_hosts", ("unreviewed.invalid",)),
            ("allowed_query_parameters", ("access_token",)),
            ("expected_mime_types", ("text/html; charset=utf-8",)),
            ("approved_metadata_fields", ("kind", "prose", "year")),
            ("approved_metadata_fields", ("kind", "title", "year")),
            ("required_metadata_fields", ("missing",)),
        ):
            adapter = SyntheticMetadataAdapter()
            setattr(adapter, field, value)
            with self.subTest(field=field), self.assertRaises(
                AdapterConformanceError
            ):
                validate_adapter_declaration(adapter, REGISTRY)

        unsafe_contract = SyntheticMetadataAdapter()
        unsafe_contract.metadata_field_contracts = copy.deepcopy(
            unsafe_contract.metadata_field_contracts
        )
        unsafe_contract.metadata_field_contracts["kind"]["allowed_values"] = [
            "https://media.invalid/invented?signature=private"
        ]
        with self.assertRaises(AdapterConformanceError):
            validate_adapter_declaration(unsafe_contract, REGISTRY)

    def test_request_must_remain_inside_declared_endpoint_boundary(self) -> None:
        adapter = SyntheticMetadataAdapter()
        harness = OfflineConformanceHarness(adapter, REGISTRY)
        request = harness.next_request()
        self.assertEqual(
            "https://antiegg.kr/wp-json/wp/v2/posts",
            request.url,
        )

        adapter.build_request = lambda cursor: copy.copy(request).__class__(
            endpoint_id=adapter.endpoint_id,
            method="GET",
            url="https://unreviewed.invalid/metadata",
        )
        with self.assertRaises(AdapterConformanceError):
            OfflineConformanceHarness(adapter, REGISTRY).next_request()

        adapter = SyntheticMetadataAdapter()
        adapter.build_request = lambda cursor: copy.copy(request).__class__(
            endpoint_id=adapter.endpoint_id,
            method="GET",
            url="https://antiegg.kr:invalid/wp-json/wp/v2/posts",
        )
        with self.assertRaises(AdapterConformanceError):
            OfflineConformanceHarness(adapter, REGISTRY).next_request()


class OfflineConformanceTests(unittest.TestCase):
    def test_zero_budget_and_robots_denial_refuse_before_request(self) -> None:
        zero = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY, request_budget=0
        )
        self.assertIsNone(zero.next_request())
        self.assertEqual(
            ("bounded_partial", "zero_request_budget"),
            (zero.manifest()["state"], zero.manifest()["stop_reason"]),
        )

        denied = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY, robots_allowed=False
        )
        self.assertIsNone(denied.next_request())
        self.assertEqual(
            ("blocked", "robots_denied"),
            (denied.manifest()["state"], denied.manifest()["stop_reason"]),
        )

    def test_http_and_source_access_signals_block_with_specific_reasons(self) -> None:
        cases = (
            (response(b"{}", status=401), "login_required"),
            (response(b"{}", status=403), "access_forbidden"),
            (response(b"{}", status=429), "rate_limited"),
            (
                response(synthetic_page([], access_state="login_required")),
                "login_required",
            ),
            (
                response(
                    synthetic_page([], access_state="subscription_required")
                ),
                "subscription_required",
            ),
        )
        for supplied_response, reason in cases:
            harness = OfflineConformanceHarness(
                SyntheticMetadataAdapter(), REGISTRY
            )
            harness.next_request()
            manifest = harness.ingest(supplied_response)
            with self.subTest(reason=reason):
                self.assertEqual(("blocked", reason), (
                    manifest["state"], manifest["stop_reason"]
                ))
                self.assertEqual([], manifest["records"])

    def test_redirect_mime_oversize_and_shape_drift_fail_closed(self) -> None:
        cases = (
            (
                response(
                    synthetic_page([]),
                    final_url="https://antiegg.kr/wp-json/wp/v2/media",
                ),
                "redirect_mismatch",
            ),
            (
                response(synthetic_page([]), mime_type="text/html"),
                "mime_mismatch",
            ),
            (
                response(b"x" * 33),
                "response_oversized",
            ),
            (
                response(json.dumps({"invented": True}).encode()),
                "shape_drift",
            ),
        )
        for supplied_response, reason in cases:
            harness = OfflineConformanceHarness(
                SyntheticMetadataAdapter(), REGISTRY, max_response_bytes=32
            )
            harness.next_request()
            manifest = harness.ingest(supplied_response)
            with self.subTest(reason=reason):
                self.assertEqual(("changed", reason), (
                    manifest["state"], manifest["stop_reason"]
                ))

    def test_pagination_loop_blocks_without_committing_loop_page(self) -> None:
        harness = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY
        )
        harness.next_request()
        first = harness.ingest(
            response(
                synthetic_page(
                    [synthetic_item("001")],
                    next_cursor="page-002",
                    next_ordinal=1,
                    terminal=False,
                )
            )
        )
        self.assertEqual("ready", first["state"])
        request = harness.next_request()
        loop = harness.ingest(
            response(
                synthetic_page(
                    [synthetic_item("002")],
                    next_cursor="page-002",
                    next_ordinal=2,
                    terminal=False,
                ),
                final_url=request.url,
            )
        )
        self.assertEqual(("changed", "pagination_loop"), (
            loop["state"], loop["stop_reason"]
        ))
        self.assertEqual(["synthetic-001"], [
            item["record_id"] for item in loop["records"]
        ])

    def test_retry_checkpoint_resumes_same_request_and_stable_manifest(self) -> None:
        adapter = SyntheticMetadataAdapter()
        harness = OfflineConformanceHarness(adapter, REGISTRY, max_retries=2)
        first_request = harness.next_request()
        retry = harness.record_retry("temporary_unavailable")
        self.assertEqual(("ready", "retry_pending"), (
            retry["state"], retry["stop_reason"]
        ))

        resumed = OfflineConformanceHarness.resume(
            adapter, REGISTRY, harness.checkpoint()
        )
        self.assertEqual(first_request, resumed.next_request())
        completed = resumed.ingest(
            response(
                synthetic_page(
                    [synthetic_item("001")],
                    expected_total=1,
                )
            )
        )
        self.assertEqual("complete_for_observed_endpoint", completed["state"])
        self.assertEqual(1, completed["observed_unique_records"])
        self.assertEqual(0, completed["unvisited_remainder"])
        self.assertEqual(completed, resumed.manifest())

    def test_tampered_resume_checkpoint_fails_closed(self) -> None:
        harness = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY
        )
        checkpoint = harness.checkpoint()
        tampered_values = []

        extra_bound = copy.deepcopy(checkpoint)
        extra_bound["bounds"]["credential"] = "invented"
        tampered_values.append(extra_bound)

        unsafe_record = copy.deepcopy(checkpoint)
        unsafe_record["state"]["records"] = {
            "synthetic-001": {
                "kind": "Invented unapproved source prose",
                "year": "2026",
            }
        }
        tampered_values.append(unsafe_record)

        bad_cursor = copy.deepcopy(checkpoint)
        bad_cursor["state"]["next_cursor"] = "signed-token-value"
        tampered_values.append(bad_cursor)

        for tampered in tampered_values:
            with self.subTest(tampered=tampered), self.assertRaises(
                AdapterConformanceError
            ):
                OfflineConformanceHarness.resume(
                    SyntheticMetadataAdapter(), REGISTRY, tampered
                )

    def test_duplicate_items_dedupe_but_identity_collisions_block(self) -> None:
        duplicate = synthetic_item("001")
        harness = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY
        )
        harness.next_request()
        completed = harness.ingest(
            response(synthetic_page([duplicate, copy.deepcopy(duplicate)]))
        )
        self.assertEqual(1, completed["duplicate_records"])
        self.assertEqual(1, len(completed["records"]))

        collision = copy.deepcopy(duplicate)
        collision["year"] = "2025"
        blocked = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY
        )
        blocked.next_request()
        changed = blocked.ingest(
            response(synthetic_page([duplicate, collision]))
        )
        self.assertEqual(("changed", "stable_id_collision"), (
            changed["state"], changed["stop_reason"]
        ))
        self.assertEqual([], changed["records"])

    def test_manifest_is_deterministic_when_source_order_changes(self) -> None:
        items = [synthetic_item("002"), synthetic_item("001")]
        manifests = []
        for ordered in (items, list(reversed(items))):
            harness = OfflineConformanceHarness(
                SyntheticMetadataAdapter(), REGISTRY
            )
            harness.next_request()
            manifests.append(
                harness.ingest(
                    response(
                        synthetic_page(ordered, expected_total=2)
                    )
                )
            )
        self.assertEqual(manifests[0], manifests[1])

    def test_stable_identity_ignores_mutable_presentation_values(self) -> None:
        item = synthetic_item("001")
        stable_id = assert_stable_identity(
            SyntheticMetadataAdapter(),
            varied_identity_inputs(item),
        )
        self.assertEqual("synthetic-001", stable_id)

    def test_forbidden_content_and_private_values_never_enter_manifest(self) -> None:
        forbidden_records = (
            {"record_id": "synthetic-001", "metadata": {
                "kind": "synthetic_catalogue_record",
                "year": "2026",
                "prose": "Invented source prose",
            }},
            {"record_id": "synthetic-001", "metadata": {
                "kind": "https://media.invalid/file.mp4?signature=secret",
                "year": "2026",
            }},
            {"record_id": "synthetic-001", "metadata": {
                "kind": "person@example.invalid",
                "year": "2026",
            }},
            {"record_id": "synthetic-001", "metadata": {
                "kind": "/" + "Users/example/private",
                "year": "2026",
            }},
        )
        for record in forbidden_records:
            adapter = SyntheticMetadataAdapter()
            adapter.parse_page = lambda body, cursor, record=record: {
                "records": [record],
                "next_cursor": None,
                "next_ordinal": None,
                "terminal": True,
                "expected_total": 1,
                "rejected_count": 0,
            }
            harness = OfflineConformanceHarness(adapter, REGISTRY)
            harness.next_request()
            manifest = harness.ingest(response(synthetic_page([])))
            with self.subTest(record=record):
                self.assertEqual("shape_drift", manifest["stop_reason"])
                self.assertEqual([], manifest["records"])

    def test_network_guard_denies_dns_socket_http_browser_and_sdk_targets(self) -> None:
        calls: list[str] = []

        class SyntheticSdk:
            def call(self) -> None:
                calls.append("called")

        sdk = SyntheticSdk()
        with deny_live_network(
            additional_entry_points=[(SyntheticSdk, "call")]
        ):
            for operation in (
                lambda: socket.getaddrinfo("example.invalid", 443),
                lambda: socket.create_connection(("example.invalid", 443)),
                lambda: urllib.request.urlopen("https://example.invalid/"),
                lambda: webbrowser.open("https://example.invalid/"),
                sdk.call,
            ):
                with self.subTest(operation=operation), self.assertRaises(
                    AdapterConformanceError
                ):
                    operation()
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
