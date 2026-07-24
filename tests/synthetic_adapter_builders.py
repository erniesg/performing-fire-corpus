from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from performing_fire_corpus.adapter_conformance import MetadataRequest


def synthetic_item(
    item_id: str,
    *,
    title: str = "Invented catalogue label",
    year: str = "2026",
    kind: str = "synthetic_catalogue_record",
    tracking: str = "campaign-a",
    presentation: str = "grid",
) -> dict[str, str]:
    return {
        "id": item_id,
        "title": title,
        "year": year,
        "kind": kind,
        "tracking": tracking,
        "presentation": presentation,
    }


def varied_identity_inputs(item: Mapping[str, str]) -> list[dict[str, str]]:
    variants: list[dict[str, str]] = []
    for field, value in (
        ("title", "Different invented label"),
        ("tracking", "campaign-b"),
        ("presentation", "list"),
        ("position", "99"),
        ("page", "7"),
        ("source_url", "https://antiegg.kr/invented?tracking=campaign-c"),
    ):
        changed = copy.deepcopy(dict(item))
        changed[field] = value
        variants.append(changed)
    return [dict(item), *variants]


def synthetic_page(
    items: list[Mapping[str, str]],
    *,
    next_cursor: str | None = None,
    next_ordinal: int | None = None,
    terminal: bool = True,
    expected_total: int | None = None,
    rejected_count: int = 0,
    access_state: str | None = None,
) -> bytes:
    value: dict[str, Any] = {
        "items": [dict(item) for item in items],
        "next_cursor": next_cursor,
        "next_ordinal": next_ordinal,
        "terminal": terminal,
        "expected_total": expected_total,
        "rejected_count": rejected_count,
    }
    if access_state is not None:
        value["access_state"] = access_state
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


class SyntheticMetadataAdapter:
    adapter_id = "synthetic-conformance-json"
    adapter_version = "1.0.0"
    source_id = "antiegg-fluxus"
    endpoint_id = "antiegg-posts-api"
    robots_applicability = "required"
    allowed_methods = ("GET",)
    allowed_hosts = ("antiegg.kr",)
    allowed_query_parameters = ("page",)
    query_parameter_contracts = {
        "page": {
            "cursor_prefix": "page-",
            "value_type": "cursor_integer",
        }
    }
    expected_mime_types = ("application/json",)
    approved_metadata_fields = ("kind", "year")
    required_metadata_fields = ("kind", "year")
    metadata_field_contracts = {
        "kind": {
            "allowed_values": ["synthetic_catalogue_record"],
            "value_type": "enum",
        },
        "year": {"value_type": "year"},
    }
    terminal_states = ("complete_for_observed_endpoint",)
    blocker_states = (
        "access_forbidden",
        "login_required",
        "rate_limited",
        "subscription_required",
    )

    def build_request(self, cursor: str | None) -> MetadataRequest:
        url = "https://antiegg.kr/wp-json/wp/v2/posts"
        if cursor is not None:
            page = cursor.removeprefix("page-")
            url = f"{url}?{urlencode({'page': int(page)})}"
        return MetadataRequest(
            endpoint_id=self.endpoint_id,
            method="GET",
            url=url,
        )

    def detect_access_blocker(self, body: bytes) -> str | None:
        value = json.loads(body.decode("utf-8"))
        return value.get("access_state")

    def stable_record_id(self, item: Mapping[str, Any]) -> str:
        return f"synthetic-{item['id']}"

    def parse_page(
        self, body: bytes, *, cursor: str | None
    ) -> dict[str, Any]:
        del cursor
        value = json.loads(body.decode("utf-8"))
        return {
            "records": [
                {
                    "record_id": self.stable_record_id(item),
                    "source_identity": f"synthetic-source-{item['id']}",
                    "metadata": {
                        "kind": item["kind"],
                        "year": item["year"],
                    },
                }
                for item in value["items"]
            ],
            "next_cursor": value["next_cursor"],
            "next_ordinal": value["next_ordinal"],
            "terminal": value["terminal"],
            "expected_total": value["expected_total"],
            "rejected_count": value["rejected_count"],
        }
