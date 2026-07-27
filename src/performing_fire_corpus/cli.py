from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from performing_fire_corpus.acquisition import (
    AcquisitionConfig,
    HTTPTransport,
    UrllibGETTransport,
    inventory_public_source,
)
from performing_fire_corpus.discovery import discover_fixture
from performing_fire_corpus.ledger import Ledger
from performing_fire_corpus.njp_site_inventory import (
    InventoryLimits,
    run_njp_site_inventories,
)
from performing_fire_corpus.njp_video_archive_shape import (
    review_video_archive_shape,
)
from performing_fire_corpus.search_index import SearchIndexError
from performing_fire_corpus.search_service import (
    build_corpus_index,
    export_score_features,
    load_authority_bundle,
    read_json_artifact,
    search_corpus_index,
    write_json_artifact,
)
from performing_fire_corpus.r2 import (
    ApprovalError,
    UrllibHTTPClient,
    build_r2_client,
    load_transfer_approval,
)
from performing_fire_corpus.storage import (
    REQUIRED_SECRET_NAMES,
    StorageClient,
    StorageError,
    dedicated_staging_prefix,
    load_r2_config,
    r2_readiness,
    write_readiness_result,
)
from performing_fire_corpus.transfer import HTTPClient, TransferError, transfer_approved_asset
from performing_fire_corpus.trusted_vm import (
    TrustedVMRunError,
    acquire_one_to_r2,
    load_trusted_vm_approval,
    persist_blocked_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="performing-fire-corpus",
        description="Privacy-safe, rights-aware corpus tooling.",
    )
    subparsers = parser.add_subparsers(dest="command")
    progress = subparsers.add_parser(
        "progress", help="reconstruct durable corpus progress"
    )
    progress.add_argument(
        "--database", required=True, help="explicit path to the SQLite ledger"
    )
    discover = subparsers.add_parser(
        "discover-fixture",
        help="ingest checked-in synthetic metadata without network access",
    )
    discover.add_argument(
        "--fixture", required=True, help="checked-in synthetic JSON fixture"
    )
    discover.add_argument(
        "--database", required=True, help="explicit path to the SQLite ledger"
    )
    discover.add_argument(
        "--output", required=True, help="explicit path for the sanitized manifest"
    )
    inventory = subparsers.add_parser(
        "inventory-public",
        help="inventory one reviewed public source with bounded metadata requests",
    )
    inventory.add_argument(
        "--source", choices=("antiegg-fluxus",), default="antiegg-fluxus"
    )
    inventory.add_argument("--max-requests", type=int, default=2)
    inventory.add_argument("--timeout", type=float, default=10.0)
    inventory.add_argument("--rate-limit", type=float, default=2.0)
    inventory.add_argument("--retries", type=int, default=1)
    inventory.add_argument("--max-elapsed", type=float, default=30.0)
    inventory.add_argument("--max-response-bytes", type=int, default=262144)
    inventory.add_argument("--ledger", required=True)
    inventory.add_argument("--sanitized-manifest", required=True)
    njp_inventory = subparsers.add_parser(
        "inventory-njp-sites",
        help="run independent bounded NJP site preflights on a trusted VM",
    )
    njp_inventory.add_argument("--run-label", required=True)
    njp_inventory.add_argument("--commit-sha", required=True)
    njp_inventory.add_argument(
        "--source",
        choices=(
            "all",
            "njp-center-main",
            "njp-center-video-archive",
        ),
        default="all",
    )
    njp_inventory.add_argument("--state-root", required=True)
    njp_inventory.add_argument("--aggregate-report", required=True)
    njp_inventory.add_argument(
        "--governance", default="config/source-governance.v1.json"
    )
    njp_inventory.add_argument("--max-requests", type=int, default=6)
    njp_inventory.add_argument("--max-pages", type=int, default=5)
    njp_inventory.add_argument("--max-response-bytes", type=int, default=65536)
    njp_inventory.add_argument("--aggregate-bytes", type=int, default=131072)
    njp_inventory.add_argument("--retries", type=int, default=1)
    njp_inventory.add_argument("--max-retry-after", type=float, default=2.0)
    njp_inventory.add_argument("--rate-limit", type=float, default=1.0)
    njp_inventory.add_argument("--timeout", type=float, default=10.0)
    njp_inventory.add_argument("--max-elapsed", type=float, default=30.0)
    archive_shape = subparsers.add_parser(
        "review-njp-video-archive-shape",
        help="run one content-neutral Video Archive shape review",
    )
    archive_shape.add_argument("--commit-sha", required=True)
    archive_shape.add_argument("--output", required=True)
    archive_shape.add_argument(
        "--governance", default="config/source-governance.v1.json"
    )
    archive_shape.add_argument("--max-response-bytes", type=int, default=131072)
    archive_shape.add_argument("--rate-limit", type=float, default=1.0)
    archive_shape.add_argument("--timeout", type=float, default=10.0)
    archive_shape.add_argument("--max-elapsed", type=float, default=30.0)
    r2 = subparsers.add_parser("r2", help="R2 object-storage boundary commands")
    r2_subparsers = r2.add_subparsers(dest="r2_command", required=True)
    readiness = r2_subparsers.add_parser(
        "readiness", help="report redacted R2 configuration readiness"
    )
    readiness.add_argument(
        "--config", default=".agent/storage.yaml", help="agent storage contract"
    )
    readiness.add_argument(
        "--output", required=True, help="durable sanitized readiness result"
    )
    transfer = r2_subparsers.add_parser(
        "transfer-approved",
        help="transfer one reviewed bounded asset to immutable R2 storage",
    )
    transfer.add_argument("--plan", required=True, help="reviewed local approval plan")
    transfer.add_argument("--ledger", required=True, help="explicit SQLite ledger")
    transfer.add_argument("--config", required=True, help="agent storage contract")
    transfer.add_argument(
        "--cache-directory", required=True, help="disposable bounded cache directory"
    )
    transfer.add_argument(
        "--output", required=True, help="sanitized immutable object receipt"
    )
    trusted_vm = subparsers.add_parser(
        "trusted-vm", help="held trusted-VM operator workflows"
    )
    trusted_vm_subparsers = trusted_vm.add_subparsers(
        dest="trusted_vm_command", required=True
    )
    acquire_one = trusted_vm_subparsers.add_parser(
        "acquire-one-to-r2",
        help="acquire, verify, and delete one explicitly approved R2 object",
    )
    acquire_one.add_argument("--approval", required=True)
    acquire_one.add_argument("--database", required=True)
    acquire_one.add_argument("--storage-config", required=True)
    acquire_one.add_argument("--cache-directory", required=True)
    acquire_one.add_argument("--sanitized-output", required=True)
    search = subparsers.add_parser(
        "search",
        help="offline rights-filtered corpus index and local search surface",
    )
    search_subparsers = search.add_subparsers(dest="search_command", required=True)
    build_index = search_subparsers.add_parser(
        "build",
        help="build one deterministic corpus index from a validated snapshot",
    )
    build_index.add_argument("--index-id", required=True)
    build_index.add_argument("--snapshot", required=True)
    build_index.add_argument("--authority", required=True)
    build_index.add_argument("--built-at", required=True)
    build_index.add_argument("--derived-objects")
    build_index.add_argument("--coverage-targets")
    build_index.add_argument("--previous-index")
    build_index.add_argument("--output", required=True)
    query = search_subparsers.add_parser(
        "query", help="run one rights-filtered local query with safe facets"
    )
    query.add_argument("--index", required=True)
    query.add_argument("--authority", required=True)
    query.add_argument(
        "--audience", required=True, choices=("operator", "researcher", "public")
    )
    query.add_argument("--current-time", required=True)
    query.add_argument("--term", action="append", default=[])
    query.add_argument("--source-id")
    query.add_argument("--language")
    query.add_argument("--period")
    query.add_argument("--medium")
    query.add_argument("--selection-state")
    query.add_argument("--duplicate-cluster-id")
    query.add_argument("--limit", type=int)
    query.add_argument("--output", required=True)
    export_scores = search_subparsers.add_parser(
        "export-scores",
        help="export rights-safe score-generation features and exact keys",
    )
    export_scores.add_argument("--index", required=True)
    export_scores.add_argument("--authority", required=True)
    export_scores.add_argument(
        "--audience", required=True, choices=("operator", "researcher")
    )
    export_scores.add_argument("--current-time", required=True)
    export_scores.add_argument("--output", required=True)
    return parser


def _run_search_command(arguments: argparse.Namespace) -> int:
    authority = load_authority_bundle(arguments.authority)
    if arguments.search_command == "build":
        index = build_corpus_index(
            index_id=arguments.index_id,
            snapshot=read_json_artifact(arguments.snapshot),
            built_at=arguments.built_at,
            authority_resolver=authority,
            derived_objects=(
                read_json_artifact(arguments.derived_objects)
                if arguments.derived_objects
                else ()
            ),
            object_authority=authority,
            coverage_targets=(
                read_json_artifact(arguments.coverage_targets)
                if arguments.coverage_targets
                else ()
            ),
            previous_index=(
                read_json_artifact(arguments.previous_index)
                if arguments.previous_index
                else None
            ),
        )
        write_json_artifact(arguments.output, index)
        print(
            json.dumps(
                {
                    "status": "complete",
                    "corpus_index_id": index["corpus_index_id"],
                    "index_sha256": index["index_sha256"],
                    "indexed_documents": len(index["entries"]),
                    "superseded_fields": len(index["superseded_fields"]),
                },
                sort_keys=True,
            )
        )
        return 0
    if arguments.search_command == "query":
        result = search_corpus_index(
            read_json_artifact(arguments.index),
            audience=arguments.audience,
            current_time=arguments.current_time,
            authority_resolver=authority,
            query_terms=arguments.term,
            source_id=arguments.source_id,
            language=arguments.language,
            period=arguments.period,
            medium=arguments.medium,
            selection_state=arguments.selection_state,
            duplicate_cluster_id=arguments.duplicate_cluster_id,
            limit=arguments.limit,
        )
        write_json_artifact(arguments.output, result)
        print(
            json.dumps(
                {"status": "complete", "result_count": result["result_count"]},
                sort_keys=True,
            )
        )
        return 0
    export = export_score_features(
        read_json_artifact(arguments.index),
        audience=arguments.audience,
        current_time=arguments.current_time,
        authority_resolver=authority,
        object_authority=authority,
    )
    write_json_artifact(arguments.output, export)
    print(
        json.dumps(
            {
                "status": "complete",
                "score_export_id": export["score_export_id"],
                "exported_documents": len(export["documents"]),
            },
            sort_keys=True,
        )
    )
    return 0


def _trusted_vm_paths(arguments: argparse.Namespace) -> dict[str, Path]:
    raw_paths = {
        "approval": arguments.approval,
        "database": arguments.database,
        "storage_config": arguments.storage_config,
        "cache_directory": arguments.cache_directory,
        "sanitized_output": arguments.sanitized_output,
    }
    root = Path.cwd().resolve()
    selected: dict[str, Path] = {}
    for name, raw_path in raw_paths.items():
        candidate = Path(raw_path)
        if (
            candidate.is_absolute()
            or not candidate.parts
            or any(part in ("", ".", "..") for part in candidate.parts)
        ):
            raise TrustedVMRunError(
                "unsafe_path",
                "Use explicit repository-relative paths in the held proof scope.",
            )
        resolved = (root / candidate).resolve()
        if not resolved.is_relative_to(root):
            raise TrustedVMRunError(
                "unsafe_path",
                "Use explicit repository-relative paths in the held proof scope.",
            )
        selected[name] = resolved
    if Path(raw_paths["storage_config"]).as_posix() != ".agent/storage.yaml":
        raise TrustedVMRunError(
            "unsafe_path",
            "Use the reviewed .agent storage contract for this held proof.",
        )
    proof_root = (root / ".local" / "r2-proof").resolve()
    if any(
        not selected[name].is_relative_to(proof_root)
        for name in (
            "approval",
            "database",
            "cache_directory",
            "sanitized_output",
        )
    ):
        raise TrustedVMRunError(
            "unsafe_path",
            "Keep approval, ledger, cache, and receipts in the held proof scope.",
        )
    if (
        selected["approval"].suffix != ".json"
        or selected["database"].suffix != ".sqlite3"
        or selected["cache_directory"] in (proof_root, selected["sanitized_output"])
        or selected["sanitized_output"] == proof_root
    ):
        raise TrustedVMRunError(
            "unsafe_path",
            "Use one approval file, one ledger, and separate bounded cache and receipt directories.",
        )
    return selected


def _njp_inventory_paths(arguments: argparse.Namespace) -> dict[str, Path]:
    root = Path.cwd().resolve()
    raw = {
        "state_root": Path(arguments.state_root),
        "aggregate_report": Path(arguments.aggregate_report),
        "governance": Path(arguments.governance),
    }
    if any(
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
        for path in raw.values()
    ):
        raise ValueError("NJP inventory paths must be repository-relative")
    selected = {name: (root / path).resolve() for name, path in raw.items()}
    local_root = (root / ".local" / "njp-center-inventory").resolve()
    docs_root = (root / "docs").resolve()
    if (
        selected["state_root"] == local_root
        or not selected["state_root"].is_relative_to(local_root)
        or not selected["aggregate_report"].is_relative_to(docs_root)
        or selected["aggregate_report"].suffix != ".json"
        or raw["governance"].as_posix() != "config/source-governance.v1.json"
    ):
        raise ValueError(
            "keep NJP live state under .local/njp-center-inventory and "
            "the sanitized aggregate under docs"
        )
    return selected


def _njp_archive_shape_path(raw_path: str) -> Path:
    root = Path.cwd().resolve()
    candidate = Path(raw_path)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in ("", ".", "..") for part in candidate.parts)
    ):
        raise ValueError("NJP Video Archive shape output must be repository-relative")
    lexical = root
    for part in candidate.parts:
        lexical /= part
        if lexical.is_symlink():
            raise ValueError(
                "NJP Video Archive shape output cannot traverse symlinks"
            )
    selected = lexical.resolve()
    shape_root = (root / ".local" / "njp-video-archive-shape").resolve()
    if (
        selected == shape_root
        or not selected.is_relative_to(shape_root)
        or selected.suffix != ".json"
    ):
        raise ValueError(
            "keep the NJP Video Archive shape report under "
            ".local/njp-video-archive-shape"
        )
    return selected


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    storage_client: StorageClient | None = None,
    http_client: HTTPClient | None = None,
    robots_transport: HTTPTransport | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    source = os.environ if environ is None else environ
    if arguments.command == "progress":
        with Ledger(arguments.database) as ledger:
            print(json.dumps(ledger.progress(), indent=2, sort_keys=True))
    elif arguments.command == "discover-fixture":
        discover_fixture(arguments.fixture, arguments.database, arguments.output)
    elif arguments.command == "inventory-public":
        inventory_public_source(
            AcquisitionConfig(
                source=arguments.source,
                max_requests=arguments.max_requests,
                timeout_seconds=arguments.timeout,
                rate_limit_seconds=arguments.rate_limit,
                max_retries=arguments.retries,
                max_elapsed_seconds=arguments.max_elapsed,
                max_response_bytes=arguments.max_response_bytes,
                ledger_path=arguments.ledger,
                manifest_path=arguments.sanitized_manifest,
            )
        )
    elif arguments.command == "inventory-njp-sites":
        selected_paths = _njp_inventory_paths(arguments)
        result = run_njp_site_inventories(
            run_label=arguments.run_label,
            commit_sha=arguments.commit_sha,
            repo_root=Path.cwd(),
            source_ids=(
                None
                if arguments.source == "all"
                else (arguments.source,)
            ),
            state_root=selected_paths["state_root"],
            aggregate_report=selected_paths["aggregate_report"],
            governance_path=selected_paths["governance"],
            limits=InventoryLimits(
                max_requests=arguments.max_requests,
                max_pages=arguments.max_pages,
                max_response_bytes=arguments.max_response_bytes,
                aggregate_bytes=arguments.aggregate_bytes,
                max_retries=arguments.retries,
                retry_after_seconds=arguments.max_retry_after,
                per_host_interval_seconds=arguments.rate_limit,
                timeout_seconds=arguments.timeout,
                elapsed_seconds=arguments.max_elapsed,
            ),
        )
        print(
            json.dumps(
                {
                    "status": "complete",
                    "source_states": {
                        item["source_id"]: item["state"]
                        for item in result["sources"]
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    elif arguments.command == "review-njp-video-archive-shape":
        result = review_video_archive_shape(
            commit_sha=arguments.commit_sha,
            repo_root=Path.cwd(),
            governance_path=arguments.governance,
            output_path=_njp_archive_shape_path(arguments.output),
            max_response_bytes=arguments.max_response_bytes,
            timeout_seconds=arguments.timeout,
            per_host_interval_seconds=arguments.rate_limit,
            elapsed_seconds=arguments.max_elapsed,
        )
        print(
            json.dumps(
                {
                    "status": result["state"],
                    "blocker_codes": result["blocker_codes"],
                    "output": arguments.output,
                },
                sort_keys=True,
            )
        )
        return 0 if result["state"] == "shape_observed" else 2
    elif arguments.command == "r2" and arguments.r2_command == "readiness":
        config = load_r2_config(arguments.config)
        selected_storage = storage_client
        if (
            selected_storage is None
            and config.bucket.strip()
            and dedicated_staging_prefix(config.staging_prefix)
            and all(bool(source.get(name)) for name in REQUIRED_SECRET_NAMES)
        ):
            try:
                selected_storage = build_r2_client(config, environ=source)
            except StorageError:
                selected_storage = None
        result = r2_readiness(
            config,
            environ=source,
            storage_client=selected_storage,
        )
        write_readiness_result(arguments.output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ready"] else 2
    elif arguments.command == "r2" and arguments.r2_command == "transfer-approved":
        try:
            plan = load_transfer_approval(arguments.plan)
            config = load_r2_config(arguments.config)
            if (
                not config.bucket.strip()
                or config.staging_prefix != plan.staging_prefix
            ):
                raise ApprovalError()
            selected_storage = storage_client or build_r2_client(
                config,
                environ=source,
            )
            selected_http = http_client or UrllibHTTPClient()
            with Ledger(arguments.ledger) as ledger:
                receipt = transfer_approved_asset(
                    plan,
                    http_client=selected_http,
                    storage_client=selected_storage,
                    ledger=ledger,
                    cache_directory=arguments.cache_directory,
                )
            write_readiness_result(arguments.output, receipt)
            print(json.dumps({"status": "complete"}, sort_keys=True))
            return 0
        except ApprovalError as error:
            print(
                json.dumps(
                    {"code": error.code, "next_action": error.next_action},
                    sort_keys=True,
                )
            )
            return 4
        except StorageError as error:
            print(
                json.dumps(
                    {"code": error.code, "next_action": error.next_action},
                    sort_keys=True,
                )
            )
            return 3 if error.code == "r2_configuration_invalid" else 1
        except TransferError as error:
            print(
                json.dumps(
                    {
                        "code": error.code,
                        "next_action": "Review the bounded transfer gates and retry safely.",
                    },
                    sort_keys=True,
                )
            )
            return 1
        except Exception:
            print(
                json.dumps(
                    {
                        "code": "transfer_failed",
                        "next_action": "Review the bounded transfer gates and retry safely.",
                    },
                    sort_keys=True,
                )
            )
            return 1
    elif arguments.command == "search":
        try:
            return _run_search_command(arguments)
        except SearchIndexError:
            print(
                json.dumps(
                    {
                        "code": "search_authority_unavailable",
                        "next_action": "Review the local snapshot, authority bundle, and rights gates.",
                    },
                    sort_keys=True,
                )
            )
            return 4
        except Exception:
            print(
                json.dumps(
                    {
                        "code": "search_surface_failed",
                        "next_action": "Review the local snapshot, authority bundle, and rights gates.",
                    },
                    sort_keys=True,
                )
            )
            return 1
    elif (
        arguments.command == "trusted-vm"
        and arguments.trusted_vm_command == "acquire-one-to-r2"
    ):
        paths: dict[str, Path] | None = None
        try:
            paths = _trusted_vm_paths(arguments)
            approval = load_trusted_vm_approval(paths["approval"])
            config = load_r2_config(paths["storage_config"])
            selected_storage = storage_client or build_r2_client(
                config,
                environ=source,
            )
            manifest = acquire_one_to_r2(
                approval,
                config=config,
                ledger_path=paths["database"],
                cache_directory=paths["cache_directory"],
                sanitized_output=paths["sanitized_output"],
                environ=source,
                storage_client=selected_storage,
                robots_transport=robots_transport or UrllibGETTransport(),
                asset_http_client=http_client or UrllibHTTPClient(),
            )
            print(json.dumps({"status": manifest["status"]}, sort_keys=True))
            return 0
        except TrustedVMRunError as error:
            if paths is not None:
                persist_blocked_run(
                    paths["sanitized_output"],
                    code=error.code,
                    next_action=error.next_action,
                    environ=source,
                )
            print(
                json.dumps(
                    {"code": error.code, "next_action": error.next_action},
                    sort_keys=True,
                )
            )
            return 4 if error.code in {"approval_invalid", "unsafe_path"} else 1
        except StorageError as error:
            if paths is not None:
                persist_blocked_run(
                    paths["sanitized_output"],
                    code=error.code,
                    next_action=error.next_action,
                    environ=source,
                )
            print(
                json.dumps(
                    {"code": error.code, "next_action": error.next_action},
                    sort_keys=True,
                )
            )
            return 3 if error.code == "r2_configuration_invalid" else 1
        except Exception:
            if paths is not None:
                persist_blocked_run(
                    paths["sanitized_output"],
                    code="trusted_vm_run_failed",
                    next_action="Review the held one-object gates and retry safely.",
                    environ=source,
                )
            print(
                json.dumps(
                    {
                        "code": "trusted_vm_run_failed",
                        "next_action": "Review the held one-object gates and retry safely.",
                    },
                    sort_keys=True,
                )
            )
            return 1
    return 0
