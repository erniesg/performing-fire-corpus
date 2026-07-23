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
    return parser


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
