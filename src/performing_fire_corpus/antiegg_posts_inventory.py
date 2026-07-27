"""Bounded public inventory of the reviewed ANTIEGG WordPress posts endpoint.

The posts endpoint is the one ANTIEGG surface that yields the whole catalogue,
so it needs a ledger, a request trail, and a completeness statement rather than
a JSON file pulled by hand.

Two properties are deliberate:

* Every invocation re-attempts. This lane never short-circuits on a stored
  blocker, because replaying one cannot tell an operator whether the bound they
  just raised changed anything.
* A run reports ``complete`` only when the unique record ids retrieved equal the
  total the endpoint itself declared. Reaching the last page is not the same
  fact as having seen every record, and the manifest keeps them apart.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from performing_fire_corpus.acquisition import (
    ROBOTS_URL,
    USER_AGENT,
    AcquisitionError,
    BoundedRequestRunner,
    HTTPTransport,
    UrllibGETTransport,
    bounds_of,
    json_summary,
    write_manifest,
)
from performing_fire_corpus.adapter_conformance import (
    AdapterConformanceError,
    MetadataResponse,
    OfflineConformanceHarness,
)
from performing_fire_corpus.antiegg_metadata_adapters import (
    ANTIEGGPostsMetadataAdapter,
    POSTS_PER_PAGE,
)
from performing_fire_corpus.ledger import Ledger, LedgerError, utc_text
from performing_fire_corpus.redaction import sanitize
from performing_fire_corpus.registry import RegistryError, load_registry


SOURCE_NAME = "antiegg-posts"
SOURCE_ID = "source_antiegg_posts"
ASSET_ID = "asset_antiegg_posts_catalogue"
ENDPOINT_ID = "antiegg-posts-api"
POSTS_URL = "https://antiegg.kr/wp-json/wp/v2/posts"
REQUEST_EVIDENCE_PREFIX = "evidence_antiegg_posts_request"
_ROBOTS_OBSERVATION_PREFIX = "evidence_antiegg_posts_robots_observation"
_VERDICT_PREFIX = "evidence_antiegg_posts_verdict"
#: The one reviewed source registry. This lane takes no operator-supplied
#: registry path: the adapter boundary is a repository contract, not a flag.
REGISTRY_PATH = Path("config/source-registry.v1.json")
_ROBOTS_MIME_TYPES = frozenset({"text/plain", "text/plain;charset=utf-8"})


def _utc_wall_clock() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class PostsInventoryConfig:
    """Explicit bounds for one bounded public posts inventory."""

    source: str
    max_requests: int
    max_pages: int
    timeout_seconds: float
    rate_limit_seconds: float
    max_retries: int
    max_elapsed_seconds: float
    max_response_bytes: int
    ledger_path: str | Path
    manifest_path: str | Path

    def __post_init__(self) -> None:
        counters = (
            self.max_requests,
            self.max_pages,
            self.max_response_bytes,
        )
        durations = (
            self.timeout_seconds,
            self.rate_limit_seconds,
            self.max_elapsed_seconds,
        )
        if self.source != SOURCE_NAME:
            raise AcquisitionError("this adapter inventories the posts endpoint only")
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in counters
            )
            or isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in durations
            )
            or self.timeout_seconds <= 0
            or self.rate_limit_seconds < 0
            or self.max_elapsed_seconds <= 0
        ):
            raise AcquisitionError(
                "request, page, timeout, rate, retry, and size bounds are required"
            )
        if not str(self.ledger_path).strip() or not str(self.manifest_path).strip():
            raise AcquisitionError("explicit ledger and manifest paths are required")
        if Path(self.ledger_path).resolve() == Path(self.manifest_path).resolve():
            raise AcquisitionError("ledger and manifest paths must be different")


def _source_record() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "source",
        "source_id": SOURCE_ID,
        "public_url": POSTS_URL,
        "source_kind": "website",
        "metadata": {
            "adapter": ANTIEGGPostsMetadataAdapter.adapter_id,
            "endpoint_id": ENDPOINT_ID,
        },
    }


def _endpoint_record() -> dict[str, object]:
    """One stand-in asset for the endpoint itself.

    Individual posts never become assets here: the adapter keeps a hashed
    identity and a record-type fact, which is not enough to name a retrievable
    thing. This record exists so the endpoint has a stable ledger subject; its
    state is never transitioned, so no run can inherit an earlier verdict.
    """

    return {
        "schema_version": 1,
        "record_type": "asset",
        "asset_id": ASSET_ID,
        "source_id": SOURCE_ID,
        "public_url": POSTS_URL,
        "media_type": "application/json",
        "metadata": {"endpoint_id": ENDPOINT_ID},
    }


def _next_index(ledger: Ledger, prefix: str) -> int:
    index = 1
    while ledger.get_record("evidence", f"{prefix}_{index:03d}") is not None:
        index += 1
    return index


def _record_robots_observation(
    ledger: Ledger,
    *,
    outcome: str,
    status: int,
    recorded_at: str,
) -> dict[str, object]:
    observation: dict[str, object] = {
        "catalogue_allowed": outcome != "denied",
        "outcome": outcome,
        "status": status,
    }
    index = _next_index(ledger, _ROBOTS_OBSERVATION_PREFIX)
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": f"{_ROBOTS_OBSERVATION_PREFIX}_{index:03d}",
            "subject_id": SOURCE_ID,
            "evidence_kind": "sanitized_robots_observation",
            "recorded_at": recorded_at,
            "summary": json_summary(observation),
            "public_references": [ROBOTS_URL],
        }
    )
    return observation


def _check_robots(
    runner: BoundedRequestRunner, ledger: Ledger
) -> tuple[dict[str, object] | None, str | None]:
    """Re-observe robots on every run, and record what was observed."""

    robots, failure = runner.get(ROBOTS_URL)
    if failure is not None:
        return None, failure
    if robots is None:
        return None, "request_failed"
    if robots.status in {401, 403}:
        return None, "login_required" if robots.status == 401 else "access_forbidden"
    if robots.oversized:
        return None, "response_oversized"
    recorded_at = str(runner.requests[-1]["recorded_at"])
    if robots.status == 404:
        return (
            _record_robots_observation(
                ledger, outcome="not_found", status=robots.status, recorded_at=recorded_at
            ),
            None,
        )
    if robots.status != 200 or robots.mime_type not in _ROBOTS_MIME_TYPES:
        return None, "unexpected_mime_type"
    parser = RobotFileParser()
    try:
        parser.parse(robots.body.decode("utf-8").splitlines())
        allowed = parser.can_fetch(USER_AGENT, POSTS_URL)
    except UnicodeDecodeError:
        allowed = False
    observation = _record_robots_observation(
        ledger,
        outcome="allowed" if allowed else "denied",
        status=robots.status,
        recorded_at=recorded_at,
    )
    return (observation, None) if allowed else (observation, "robots_denied")


def _drive_pagination(
    harness: OfflineConformanceHarness, runner: BoundedRequestRunner
) -> str | None:
    """Page through the endpoint until the harness or a bound stops the run."""

    while True:
        request = harness.next_request()
        if request is None:
            return None
        response, failure = runner.get(request.url)
        if failure is not None:
            return failure
        if response is None:
            return "request_failed"
        if response.oversized:
            return "response_oversized"
        harness.ingest(
            MetadataResponse(
                status=response.status,
                mime_type=response.mime_type or "application/octet-stream",
                body=response.body,
                final_url=response.url,
                headers=dict(response.headers or {}),
            )
        )


#: Exhausting a configured budget is a bound, not a source blocker. Reporting
#: it as a blocker would tell an operator that the source stopped them, when in
#: fact their own budget did.
_BOUND_STOPS = frozenset(
    {
        "elapsed_time_exhausted",
        "page_budget_exhausted",
        "request_budget_exhausted",
        "zero_request_budget",
    }
)


def _verdict(
    page_state: Mapping[str, object], failure: str | None
) -> tuple[str, str]:
    """Decide one run's result, keeping "last page" and "all records" apart."""

    declared = page_state["expected_total"]
    unique = page_state["observed_unique_records"]
    state = page_state["state"]
    if failure is not None:
        if failure in _BOUND_STOPS:
            return "bounded_partial", failure
        return "blocked", failure
    if state == "complete_for_observed_endpoint":
        if not isinstance(declared, int):
            return "blocked", "declared_total_missing"
        # Reaching the last page is not the same fact as having seen every
        # record the endpoint claims to hold.
        if unique != declared:
            return "blocked", "declared_total_mismatch"
        return "complete", "terminal_page"
    reason = str(page_state["stop_reason"] or "pagination_stopped")
    if state == "bounded_partial" or reason in _BOUND_STOPS:
        return "bounded_partial", reason
    return "blocked", reason


_NEXT_SAFE_ACTIONS = {
    "declared_total_mismatch": (
        "the endpoint declared a different total than the unique ids "
        "retrieved; re-run before treating this as a catalogue"
    ),
    "declared_total_missing": (
        "the endpoint stopped without declaring a total; review the adapter "
        "against the current pagination headers"
    ),
    "robots_denied": "review robots policy or select another public endpoint",
    "response_oversized": (
        "keep the metadata response within the configured byte bound"
    ),
}
_DEFAULT_NEXT_SAFE_ACTION = (
    "review the bounded public request failure before retrying"
)


def inventory_antiegg_posts(
    config: PostsInventoryConfig,
    *,
    transport: HTTPTransport | None = None,
    registry: Mapping[str, object] | None = None,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], datetime] = _utc_wall_clock,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Inventory the public posts endpoint within explicit request bounds."""

    ledger_path = Path(config.ledger_path).resolve()
    manifest_path = Path(config.manifest_path).resolve()
    if not ledger_path.parent.is_dir():
        raise AcquisitionError("ledger parent directory must already exist")
    try:
        selected_registry = (
            load_registry(REGISTRY_PATH) if registry is None else registry
        )
    except RegistryError:
        raise AcquisitionError(
            f"run from the repository root; {REGISTRY_PATH} is the reviewed "
            "source registry and could not be loaded"
        ) from None
    adapter = ANTIEGGPostsMetadataAdapter()
    try:
        with Ledger(ledger_path) as ledger:
            ledger.upsert(_source_record())
            ledger.upsert(_endpoint_record())
            runner = BoundedRequestRunner(
                config,
                ledger,
                transport or UrllibGETTransport(accept="application/json"),
                clock=clock,
                wall_clock=wall_clock,
                sleep=sleep,
                source_id=SOURCE_ID,
                evidence_prefix=REQUEST_EVIDENCE_PREFIX,
            )
            # The ledger keeps every run's request facts. Everything this
            # manifest reports is scoped to the requests made after this point.
            prior_requests = len(runner.requests)
            observation, failure = _check_robots(runner, ledger)
            harness = OfflineConformanceHarness(
                adapter,
                selected_registry,
                request_budget=config.max_requests,
                max_pages=config.max_pages,
                max_response_bytes=config.max_response_bytes,
                max_retries=config.max_retries,
                robots_allowed=failure != "robots_denied",
            )
            if failure is None:
                failure = _drive_pagination(harness, runner)
            observed_at = utc_text(wall_clock())
            manifest = _manifest(
                config,
                harness.manifest(),
                runner=runner,
                prior_requests=prior_requests,
                observation=observation,
                failure=failure,
                observed_at=observed_at,
                source=ledger.get_record("source", SOURCE_ID),
            )
            # Refuse to write rather than quietly redacting: a manifest that
            # needed redacting is a manifest whose shape we no longer know.
            if sanitize(manifest, environ={}) != manifest:
                raise AcquisitionError("posts inventory manifest is not sanitized")
            _record_verdict(ledger, manifest, observed_at=observed_at)
            write_manifest(manifest_path, manifest)
            return manifest
    except AdapterConformanceError:
        raise AcquisitionError(
            "the posts adapter no longer matches its reviewed registry "
            "declaration; review the adapter before running it live"
        ) from None
    except (LedgerError, OSError) as error:
        raise AcquisitionError(
            f"public posts inventory failed: {type(error).__name__}"
        ) from None


def _manifest(
    config: PostsInventoryConfig,
    page_state: Mapping[str, object],
    *,
    runner: BoundedRequestRunner,
    prior_requests: int,
    observation: Mapping[str, object] | None,
    failure: str | None,
    observed_at: str,
    source: Mapping[str, object] | None,
) -> dict[str, object]:
    result, reason = _verdict(page_state, failure)
    declared = page_state["expected_total"]
    unique = page_state["observed_unique_records"]
    record_ids = [str(record["record_id"]) for record in page_state["records"]]
    requests = runner.requests[prior_requests:]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "manifest_type": "public_posts_metadata_inventory",
        "source": source,
        "endpoint": {
            "endpoint_id": ENDPOINT_ID,
            "public_url": POSTS_URL,
            "records_per_page": POSTS_PER_PAGE,
            "source_id": ANTIEGGPostsMetadataAdapter.source_id,
        },
        "result": result,
        "pagination": {
            "pages_committed": page_state["pages_committed"],
            # The requests this run actually put on the wire, robots included.
            # The harness counts a page request when it builds one, which is
            # one too many when a bound stops it before it is sent.
            "requests_dispatched": runner.run_request_count,
            "state": page_state["state"],
            "stop_reason": page_state["stop_reason"],
        },
        "completeness": {
            "declared_total": declared,
            "unique_records_retrieved": unique,
            "unvisited_remainder": page_state["unvisited_remainder"],
            "declared_total_matches_unique_records": (
                isinstance(declared, int) and unique == declared
            ),
            # A total is what one endpoint said at one time, never a guarantee.
            "is_completeness_guarantee": False,
            "observed_at": observed_at,
        },
        "record_ids": record_ids,
        "record_ids_sha256": hashlib.sha256(
            json.dumps(record_ids, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "requests": requests,
        "bounds": {**bounds_of(config), "max_pages": config.max_pages},
        "record_counts": {
            "blockers": 1 if result == "blocked" else 0,
            "record_ids": len(record_ids),
            "requests": len(requests),
        },
    }
    if observation is not None:
        manifest["robots_observation"] = dict(observation)
    if result == "blocked":
        manifest["blocker"] = {
            "code": reason,
            "next_safe_action": _NEXT_SAFE_ACTIONS.get(
                reason, _DEFAULT_NEXT_SAFE_ACTION
            ),
        }
    elif result == "bounded_partial":
        manifest["bounded_stop"] = {
            "code": reason,
            "next_safe_action": (
                "raise the request, page, or elapsed bound and re-run to "
                "continue the inventory"
            ),
        }
    return manifest


def _record_verdict(
    ledger: Ledger,
    manifest: Mapping[str, object],
    *,
    observed_at: str,
) -> None:
    """Append this run's verdict. Runs never overwrite each other's answers."""

    completeness = manifest["completeness"]
    index = _next_index(ledger, _VERDICT_PREFIX)
    ledger.upsert(
        {
            "schema_version": 1,
            "record_type": "evidence",
            "evidence_id": f"{_VERDICT_PREFIX}_{index:03d}",
            "subject_id": ASSET_ID,
            "evidence_kind": "sanitized_completeness_verdict",
            "recorded_at": observed_at,
            "summary": json_summary(
                {
                    "declared_total": completeness["declared_total"],
                    "result": manifest["result"],
                    "unique_records_retrieved": completeness[
                        "unique_records_retrieved"
                    ],
                }
            ),
            "public_references": [POSTS_URL],
        }
    )
