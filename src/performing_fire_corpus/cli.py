from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence

from performing_fire_corpus.acquisition import AcquisitionConfig, inventory_public_source
from performing_fire_corpus.discovery import discover_fixture
from performing_fire_corpus.ledger import Ledger
from performing_fire_corpus.storage import (
    StorageClient,
    load_r2_config,
    r2_readiness,
    write_readiness_result,
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
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    storage_client: StorageClient | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
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
    elif arguments.command == "r2" and arguments.r2_command == "readiness":
        result = r2_readiness(
            load_r2_config(arguments.config),
            environ=os.environ if environ is None else environ,
            storage_client=storage_client,
        )
        write_readiness_result(arguments.output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ready"] else 2
    return 0
