"""Redacted R2 configuration readiness and storage-client contracts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


REQUIRED_SECRET_NAMES = (
    "CLOUDFLARE_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ENDPOINT",
)
NEXT_ACTION = (
    "Configure a dedicated R2 bucket and staging prefix, then provide every "
    "required secret through the trusted VM secret store and verify storage "
    "scope access."
)
_CONFIG_FIELDS = {
    "bucket": "bucket",
    "prefix": "staging_prefix",
}
_DEDICATED_PREFIX = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))[a-z0-9][a-z0-9._/-]{2,255}/$"
)


class StorageError(RuntimeError):
    """A storage-boundary failure safe to include in durable evidence."""

    def __init__(self, code: str, next_action: str) -> None:
        self.code = code
        self.next_action = next_action
        super().__init__(f"{code}: {next_action}")


@dataclass(frozen=True)
class R2Config:
    bucket: str
    staging_prefix: str


class StorageClient(Protocol):
    """Minimal client used by readiness and transfer boundaries."""

    def probe_scope(self, bucket: str, staging_prefix: str) -> bool:
        """Return whether the configured bucket and staging scope are accessible."""
        ...

    def head_object(self, key: str) -> Mapping[str, object] | None: ...

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        """Atomically create *key*; return false when it already exists."""
        ...

    def delete_exact_object(self, key: str) -> bool:
        """Delete one exact immutable key; broad selectors are forbidden."""
        ...


def dedicated_staging_prefix(prefix: str) -> bool:
    """Return whether *prefix* is a normalized, dedicated staging namespace."""

    return bool(isinstance(prefix, str) and _DEDICATED_PREFIX.fullmatch(prefix))


def load_r2_config(path: str | Path = ".agent/storage.yaml") -> R2Config:
    """Read only the non-secret R2 bucket and prefix from the agent contract."""

    config_path = Path(path)
    bucket = ""
    prefix = ""
    in_object_storage = False
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return R2Config(bucket="", staging_prefix="")
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        if indent == 0:
            in_object_storage = stripped == "object_storage:"
            continue
        if not in_object_storage or indent < 2 or ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        value = raw_value.strip().strip("'\"")
        if key == "bucket":
            bucket = value
        elif key == "prefix":
            prefix = value
    return R2Config(bucket=bucket, staging_prefix=prefix)


def r2_readiness(
    config: R2Config,
    *,
    environ: Mapping[str, str] | None = None,
    storage_client: StorageClient | None = None,
) -> dict[str, object]:
    """Return names and presence states only; secret/config values never escape."""

    source = os.environ if environ is None else environ
    configuration = {
        _CONFIG_FIELDS["bucket"]: "present" if config.bucket.strip() else "missing",
        _CONFIG_FIELDS["prefix"]: (
            "present"
            if dedicated_staging_prefix(config.staging_prefix)
            else "missing"
        ),
    }
    secrets = {
        name: "present" if bool(source.get(name)) else "missing"
        for name in REQUIRED_SECRET_NAMES
    }
    scope_present = False
    if (
        all(state == "present" for state in configuration.values())
        and all(state == "present" for state in secrets.values())
        and storage_client is not None
    ):
        try:
            scope_present = (
                storage_client.probe_scope(config.bucket, config.staging_prefix) is True
            )
        except Exception:
            scope_present = False
    checks = {
        "configuration": configuration,
        "secrets": secrets,
        "storage": {
            "staging_scope": "present" if scope_present else "missing",
        },
    }
    ready = all(
        state == "present"
        for group in checks.values()
        for state in group.values()
    )
    return {
        "ready": ready,
        "checks": checks,
        "next_action": None if ready else NEXT_ACTION,
    }


def write_readiness_result(
    path: str | Path, result: Mapping[str, object]
) -> None:
    """Atomically persist a sanitized readiness result at an explicit path."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        delete=False,
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
