from __future__ import annotations

import copy
import hashlib
import json
import socket
import unittest
import urllib.request
import webbrowser
from pathlib import Path

from performing_fire_corpus.adapter_conformance import (
    AdapterConformanceError,
    MetadataRequest,
    MetadataResponse,
    OfflineConformanceHarness,
    assert_stable_identity,
    deny_live_network,
    validate_adapter_declaration,
)
from performing_fire_corpus.registry import load_registry
from adapter_conformance_suite import StandardAdapterConformanceMixin
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


class SyntheticAdapterAdmissionMatrixTests(
    StandardAdapterConformanceMixin,
    unittest.TestCase,
):
    adapter_factory = SyntheticMetadataAdapter
    registry = REGISTRY
    make_item = staticmethod(synthetic_item)
    make_page = staticmethod(synthetic_page)
    identity_variants = staticmethod(varied_identity_inputs)


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

        unsafe_query = SyntheticMetadataAdapter()
        unsafe_query.allowed_query_parameters = ("auth",)
        unsafe_query.query_parameter_contracts = {
            "auth": {
                "allowed_values": ["opaque_value"],
                "value_type": "enum",
            }
        }
        with self.assertRaises(AdapterConformanceError):
            validate_adapter_declaration(unsafe_query, REGISTRY)

        acquisition_query = SyntheticMetadataAdapter()
        acquisition_query.allowed_query_parameters = ("fields",)
        acquisition_query.query_parameter_contracts = {
            "fields": {
                "allowed_values": ["content"],
                "value_type": "enum",
            }
        }
        with self.assertRaises(AdapterConformanceError):
            validate_adapter_declaration(acquisition_query, REGISTRY)

        for parameter, allowed_value in (
            ("download", "yes"),
            ("fields", "description"),
        ):
            adapter = SyntheticMetadataAdapter()
            adapter.allowed_query_parameters = (parameter,)
            adapter.query_parameter_contracts = {
                parameter: {
                    "allowed_values": [allowed_value],
                    "value_type": "enum",
                }
            }
            with self.subTest(
                parameter=parameter,
                allowed_value=allowed_value,
            ), self.assertRaises(AdapterConformanceError):
                validate_adapter_declaration(adapter, REGISTRY)

        for credential_parameter in (
            "accessToken",
            "refreshToken",
            "idToken",
        ):
            adapter = SyntheticMetadataAdapter()
            adapter.allowed_query_parameters = (credential_parameter,)
            adapter.query_parameter_contracts = {
                credential_parameter: {
                    "cursor_prefix": "opaque-",
                    "value_type": "cursor_opaque",
                }
            }
            with self.subTest(
                credential_parameter=credential_parameter,
            ), self.assertRaises(AdapterConformanceError):
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
        adapter.build_request = lambda cursor: MetadataRequest(
            endpoint_id=adapter.endpoint_id,
            method="GET",
            url="https://antiegg.kr:invalid/wp-json/wp/v2/posts",
        )
        with self.assertRaises(AdapterConformanceError):
            OfflineConformanceHarness(adapter, REGISTRY).next_request()

    def test_pagination_query_is_bound_to_the_exact_checkpoint_cursor(self) -> None:
        adapter = SyntheticMetadataAdapter()
        harness = OfflineConformanceHarness(adapter, REGISTRY)
        harness.next_request()
        harness.ingest(
            response(
                synthetic_page(
                    [synthetic_item("001")],
                    next_cursor="page-002",
                    next_ordinal=1,
                    terminal=False,
                )
            )
        )
        adapter.build_request = lambda cursor: copy.copy(
            MetadataRequest(
                endpoint_id=adapter.endpoint_id,
                method="GET",
                url="https://antiegg.kr/wp-json/wp/v2/posts?page=99",
            )
        )
        with self.assertRaises(AdapterConformanceError):
            harness.next_request()

    def test_adapter_callbacks_are_automatically_network_denied(self) -> None:
        adapter = SyntheticMetadataAdapter()

        def networked_request(cursor: str | None) -> MetadataRequest:
            del cursor
            socket.gethostbyname("localhost")
            raise AssertionError("network denial did not run")

        adapter.build_request = networked_request
        with self.assertRaises(AdapterConformanceError):
            OfflineConformanceHarness(adapter, REGISTRY).next_request()

    def test_opaque_case_sensitive_pagination_is_bound_but_not_reported(
        self,
    ) -> None:
        class OpaqueCursorAdapter(SyntheticMetadataAdapter):
            allowed_query_parameters = ("pageToken",)
            query_parameter_contracts = {
                "pageToken": {
                    "cursor_prefix": "opaque-",
                    "value_type": "cursor_opaque",
                }
            }

            def build_request(self, cursor: str | None) -> MetadataRequest:
                url = "https://antiegg.kr/wp-json/wp/v2/posts"
                if cursor is not None:
                    url = f"{url}?pageToken={cursor.removeprefix('opaque-')}"
                return MetadataRequest(
                    endpoint_id=self.endpoint_id,
                    method="GET",
                    url=url,
                )

        opaque_cursor = "opaque-InventedPage_002"
        harness = OfflineConformanceHarness(OpaqueCursorAdapter(), REGISTRY)
        request = harness.next_request()
        result = harness.ingest(
            response(
                synthetic_page(
                    [synthetic_item("001")],
                    next_cursor=opaque_cursor,
                    next_ordinal=1,
                    terminal=False,
                ),
                final_url=request.url,
            )
        )
        self.assertEqual("ready", result["state"])
        self.assertNotIn(opaque_cursor, json.dumps(result, sort_keys=True))
        self.assertIn("next_cursor_sha256", result)
        self.assertIn(
            "pageToken=InventedPage_002",
            harness.next_request().url,
        )


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

        expected_bounds = copy.deepcopy(harness.bounds)
        checkpoint = harness.checkpoint()
        resumed = OfflineConformanceHarness.resume(
            adapter,
            REGISTRY,
            checkpoint,
            expected_bounds=expected_bounds,
            expected_checkpoint_sha256=checkpoint["checkpoint_sha256"],
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
        self.assertNotIn(
            "synthetic-source-001",
            json.dumps(completed, sort_keys=True),
        )
        self.assertIn(
            "source_identity_sha256",
            completed["records"][0],
        )
        self.assertEqual(completed, resumed.manifest())

    def test_tampered_resume_checkpoint_fails_closed(self) -> None:
        harness = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY
        )
        checkpoint = harness.checkpoint()
        expected_bounds = copy.deepcopy(harness.bounds)
        expected_digest = checkpoint["checkpoint_sha256"]
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
                    SyntheticMetadataAdapter(),
                    REGISTRY,
                    tampered,
                    expected_bounds=expected_bounds,
                    expected_checkpoint_sha256=expected_digest,
                )

    def test_checkpoint_cannot_self_authorize_reset_or_wider_bounds(self) -> None:
        harness = OfflineConformanceHarness(
            SyntheticMetadataAdapter(), REGISTRY, request_budget=1
        )
        harness.next_request()
        harness.record_retry("temporary_unavailable")
        checkpoint = harness.checkpoint()
        expected_bounds = copy.deepcopy(checkpoint["bounds"])
        expected_digest = checkpoint["checkpoint_sha256"]

        checkpoint["bounds"]["request_budget"] = 4
        checkpoint["state"]["requests_attempted"] = 0
        checkpoint["state"]["current_retries"] = 0
        unsigned = {
            key: checkpoint[key]
            for key in (
                "adapter_runtime_checkpoint",
                "bounds",
                "declaration_sha256",
                "state",
            )
        }
        checkpoint["checkpoint_sha256"] = hashlib.sha256(
            json.dumps(
                unsigned,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaises(AdapterConformanceError):
            OfflineConformanceHarness.resume(
                SyntheticMetadataAdapter(),
                REGISTRY,
                checkpoint,
                expected_bounds=expected_bounds,
                expected_checkpoint_sha256=expected_digest,
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

        class CollidingAdapter(SyntheticMetadataAdapter):
            def stable_record_id(self, item: dict[str, str]) -> str:
                del item
                return "synthetic-collision"

        collision_harness = OfflineConformanceHarness(
            CollidingAdapter(), REGISTRY
        )
        collision_harness.next_request()
        same_metadata_collision = collision_harness.ingest(
            response(
                synthetic_page(
                    [synthetic_item("001"), synthetic_item("002")]
                )
            )
        )
        self.assertEqual(("changed", "stable_id_collision"), (
            same_metadata_collision["state"],
            same_metadata_collision["stop_reason"],
        ))

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
            {"record_id": "synthetic-001", "source_identity": "synthetic-source-001", "metadata": {
                "kind": "synthetic_catalogue_record",
                "year": "2026",
                "prose": "Invented source prose",
            }},
            {"record_id": "synthetic-001", "source_identity": "synthetic-source-001", "metadata": {
                "kind": "https://media.invalid/file.mp4?signature=secret",
                "year": "2026",
            }},
            {"record_id": "synthetic-001", "source_identity": "synthetic-source-001", "metadata": {
                "kind": "person@example.invalid",
                "year": "2026",
            }},
            {"record_id": "synthetic-001", "source_identity": "synthetic-source-001", "metadata": {
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

        adapter = SyntheticMetadataAdapter()
        adapter.parse_page = lambda body, cursor: {
            "records": [
                {
                    "record_id": "synthetic-001",
                    "source_identity": "user_123456",
                    "metadata": {
                        "kind": "kind_synthetic_catalogue_record",
                        "year": "2026",
                    },
                }
            ],
            "next_cursor": None,
            "next_ordinal": None,
            "terminal": True,
            "expected_total": 1,
            "rejected_count": 0,
        }
        harness = OfflineConformanceHarness(adapter, REGISTRY)
        harness.next_request()
        manifest = harness.ingest(response(synthetic_page([])))
        self.assertEqual("shape_drift", manifest["stop_reason"])
        self.assertNotIn("user_123456", json.dumps(manifest, sort_keys=True))

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
                lambda: socket.gethostbyname("localhost"),
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
