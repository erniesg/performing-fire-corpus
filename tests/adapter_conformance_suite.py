from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from performing_fire_corpus.adapter_conformance import (
    MetadataResponse,
    OfflineConformanceHarness,
    assert_stable_identity,
    deny_live_network,
    validate_adapter_declaration,
)


class StandardAdapterConformanceMixin:
    """Reusable admission matrix for every source-specific metadata adapter."""

    adapter_factory: Callable[[], Any]
    registry: Mapping[str, Any]
    make_item: Callable[..., Mapping[str, Any]]
    make_page: Callable[..., bytes]
    identity_variants: Callable[
        [Mapping[str, Any]], Sequence[Mapping[str, Any]]
    ]
    additional_network_entry_points: Sequence[tuple[object, str]] = ()
    expected_mime_type = "application/json"
    unexpected_mime_type = "text/html"
    next_cursor = "page-002"
    alternate_cursor = "page-003"
    server_supplies_ordinal = True

    def setUp(self) -> None:
        super().setUp()
        guard = deny_live_network(
            additional_entry_points=self.additional_network_entry_points
        )
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)

    def _harness(self, **bounds: Any) -> OfflineConformanceHarness:
        return OfflineConformanceHarness(
            self.adapter_factory(),
            self.registry,
            additional_network_entry_points=(
                self.additional_network_entry_points
            ),
            **bounds,
        )

    def _response(
        self,
        body: bytes,
        request_url: str,
        *,
        status: int = 200,
        mime_type: str | None = None,
        final_url: str | None = None,
    ) -> MetadataResponse:
        return MetadataResponse(
            status=status,
            mime_type=(
                self.expected_mime_type
                if mime_type is None
                else mime_type
            ),
            body=body,
            final_url=request_url if final_url is None else final_url,
        )

    def _colliding_adapter(self) -> Any:
        adapter = self.adapter_factory()
        adapter.stable_record_id = lambda item: "synthetic-collision"
        return adapter

    def test_standard_adapter_admission_matrix(self) -> None:
        validate_adapter_declaration(self.adapter_factory(), self.registry)

        zero = self._harness(request_budget=0)
        self.assertIsNone(zero.next_request())
        self.assertEqual("zero_request_budget", zero.manifest()["stop_reason"])

        robots = self._harness(robots_allowed=False)
        if self.adapter_factory().robots_applicability == "required":
            self.assertIsNone(robots.next_request())
            self.assertEqual("robots_denied", robots.manifest()["stop_reason"])
        else:
            self.assertIsNotNone(robots.next_request())
            self.assertNotEqual("robots_denied", robots.manifest()["stop_reason"])

        for status, reason in (
            (401, "login_required"),
            (403, "access_forbidden"),
            (429, "rate_limited"),
        ):
            harness = self._harness()
            request = harness.next_request()
            result = harness.ingest(
                self._response(b"{}", request.url, status=status)
            )
            self.assertEqual(("blocked", reason), (
                result["state"], result["stop_reason"]
            ))

        for reason in ("login_required", "subscription_required"):
            harness = self._harness()
            request = harness.next_request()
            result = harness.ingest(
                self._response(
                    self.make_page([], access_state=reason),
                    request.url,
                )
            )
            self.assertEqual(("blocked", reason), (
                result["state"], result["stop_reason"]
            ))

        request_failures = (
            (
                {"final_url": "https://unreviewed.invalid/moved"},
                "redirect_mismatch",
            ),
            ({"mime_type": self.unexpected_mime_type}, "mime_mismatch"),
        )
        for response_options, reason in request_failures:
            harness = self._harness()
            request = harness.next_request()
            result = harness.ingest(
                self._response(
                    self.make_page([]),
                    request.url,
                    **response_options,
                )
            )
            self.assertEqual(reason, result["stop_reason"])

        oversized = self._harness(max_response_bytes=32)
        request = oversized.next_request()
        result = oversized.ingest(
            self._response(b"x" * 33, request.url)
        )
        self.assertEqual("response_oversized", result["stop_reason"])

        drift = self._harness()
        request = drift.next_request()
        result = drift.ingest(self._response(b"{}", request.url))
        self.assertEqual("shape_drift", result["stop_reason"])

        pagination = self._harness()
        request = pagination.next_request()
        first = pagination.ingest(
            self._response(
                self.make_page(
                    [self.make_item("001")],
                    next_cursor=self.next_cursor,
                    next_ordinal=1,
                    terminal=False,
                ),
                request.url,
            )
        )
        self.assertEqual("ready", first["state"])
        request = pagination.next_request()
        loop = pagination.ingest(
            self._response(
                self.make_page(
                    [self.make_item("002")],
                    next_cursor=self.next_cursor,
                    next_ordinal=2,
                    terminal=False,
                ),
                request.url,
            )
        )
        self.assertEqual("pagination_loop", loop["stop_reason"])

        if self.server_supplies_ordinal:
            ordinal = self._harness()
            request = ordinal.next_request()
            ordinal.ingest(
                self._response(
                    self.make_page(
                        [self.make_item("001")],
                        next_cursor=self.next_cursor,
                        next_ordinal=1,
                        terminal=False,
                    ),
                    request.url,
                )
            )
            request = ordinal.next_request()
            ordinal_result = ordinal.ingest(
                self._response(
                    self.make_page(
                        [self.make_item("002")],
                        next_cursor=self.alternate_cursor,
                        next_ordinal=3,
                        terminal=False,
                    ),
                    request.url,
                )
            )
            self.assertEqual(
                "pagination_loop",
                ordinal_result["stop_reason"],
            )

        changing_total = self._harness()
        request = changing_total.next_request()
        changing_total.ingest(
            self._response(
                self.make_page(
                    [self.make_item("001")],
                    next_cursor=self.next_cursor,
                    next_ordinal=1,
                    terminal=False,
                    expected_total=2,
                ),
                request.url,
            )
        )
        request = changing_total.next_request()
        total_result = changing_total.ingest(
            self._response(
                self.make_page(
                    [self.make_item("002")],
                    terminal=True,
                    expected_total=3,
                ),
                request.url,
            )
        )
        self.assertEqual(
            "expected_total_changed",
            total_result["stop_reason"],
        )

        retry = self._harness(max_retries=2)
        original_request = retry.next_request()
        retry.record_retry("temporary_unavailable")
        expected_bounds = copy.deepcopy(retry.bounds)
        checkpoint = retry.checkpoint()
        resumed = OfflineConformanceHarness.resume(
            self.adapter_factory(),
            self.registry,
            checkpoint,
            expected_bounds=expected_bounds,
            expected_checkpoint_sha256=checkpoint["checkpoint_sha256"],
            additional_network_entry_points=(
                self.additional_network_entry_points
            ),
        )
        self.assertEqual(original_request, resumed.next_request())

        duplicate_item = self.make_item("001")
        duplicate = self._harness()
        request = duplicate.next_request()
        result = duplicate.ingest(
            self._response(
                self.make_page(
                    [duplicate_item, copy.deepcopy(duplicate_item)]
                ),
                request.url,
            )
        )
        self.assertEqual(1, result["duplicate_records"])

        colliding_adapter = self._colliding_adapter()
        collision = OfflineConformanceHarness(
            colliding_adapter,
            self.registry,
            additional_network_entry_points=(
                self.additional_network_entry_points
            ),
        )
        request = collision.next_request()
        result = collision.ingest(
            self._response(
                self.make_page(
                    [self.make_item("001"), self.make_item("002")]
                ),
                request.url,
            )
        )
        self.assertEqual("stable_id_collision", result["stop_reason"])

        stable_item = self.make_item("001")
        assert_stable_identity(
            self.adapter_factory(),
            self.identity_variants(stable_item),
        )

        manifests = []
        ordered_items = [self.make_item("002"), self.make_item("001")]
        for order in (ordered_items, list(reversed(ordered_items))):
            harness = self._harness()
            request = harness.next_request()
            manifests.append(
                harness.ingest(
                    self._response(
                        self.make_page(order, expected_total=2),
                        request.url,
                    )
                )
            )
        self.assertEqual(manifests[0], manifests[1])

        seed_adapter = self.adapter_factory()
        seed_page = seed_adapter.parse_page(
            self.make_page([self.make_item("001")]),
            cursor=None,
        )
        seed_record = seed_page["records"][0]
        approved_field = sorted(seed_record["metadata"])[0]
        forbidden_records = []
        for field in ("caption", "html", "prose", "transcript"):
            record = copy.deepcopy(seed_record)
            record["metadata"][field] = "Invented forbidden source value"
            forbidden_records.append(record)
        for value in (
            "https://media.invalid/file?signature=private",
            "person@example.invalid",
            "/" + "Users/example/private",
        ):
            record = copy.deepcopy(seed_record)
            record["metadata"][approved_field] = value
            forbidden_records.append(record)

        for forbidden_record in forbidden_records:
            adapter = self.adapter_factory()
            adapter.parse_page = (
                lambda body, cursor, record=forbidden_record: {
                    "records": [record],
                    "next_cursor": None,
                    "next_ordinal": None,
                    "terminal": True,
                    "expected_total": 1,
                    "rejected_count": 0,
                }
            )
            forbidden = OfflineConformanceHarness(
                adapter,
                self.registry,
                additional_network_entry_points=(
                    self.additional_network_entry_points
                ),
            )
            request = forbidden.next_request()
            result = forbidden.ingest(
                self._response(self.make_page([]), request.url)
            )
            self.assertEqual("shape_drift", result["stop_reason"])
            self.assertEqual([], result["records"])
