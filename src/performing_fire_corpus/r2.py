"""Concrete, prefix-bound Cloudflare R2 S3 storage adapter."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.parse import urlsplit

from botocore.config import Config as SDKConfig
from jsonschema import Draft202012Validator

from performing_fire_corpus.storage import (
    REQUIRED_SECRET_NAMES,
    R2Config,
    StorageError,
    dedicated_staging_prefix,
)
from performing_fire_corpus.transfer import TransferPlan, plan_transfer


_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)
_CONFIG_NEXT_ACTION = (
    "Provide the reviewed R2 configuration and all required secret names."
)
_SCOPE_NEXT_ACTION = "Verify access to the dedicated R2 staging scope."
_OBJECT_NEXT_ACTION = "Verify the exact immutable R2 object and retry safely."
_APPROVAL_NEXT_ACTION = "Provide one complete reviewed transfer approval plan."


class ApprovalError(RuntimeError):
    """A stable approval-plan failure that never includes plan values."""

    def __init__(self) -> None:
        self.code = "approval_invalid"
        self.next_action = _APPROVAL_NEXT_ACTION
        super().__init__(f"{self.code}: {self.next_action}")


def _storage_error(code: str, next_action: str) -> StorageError:
    return StorageError(code, next_action)


def _normalized_media_type(value: object) -> str:
    normalized = str(value).partition(";")[0].strip().lower()
    return normalized if _MEDIA_TYPE.fullmatch(normalized) else ""


def _status(response: object) -> int | None:
    if not isinstance(response, Mapping):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    status = metadata.get("HTTPStatusCode")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _error_facts(error: Exception) -> tuple[str, int | None]:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return "", None
    details = response.get("Error")
    code = details.get("Code") if isinstance(details, Mapping) else ""
    return (code if isinstance(code, str) else "", _status(response))


def _valid_exact_key(prefix: str, key: object) -> bool:
    return (
        isinstance(key, str)
        and key.startswith(prefix)
        and key != prefix
        and not any(character in key for character in "*?[]{}")
        and not any(part in {".", ".."} for part in key.split("/"))
    )


class R2StorageClient:
    """One bucket and prefix capability over the S3-compatible R2 API."""

    def __init__(self, config: R2Config, sdk_client: Any) -> None:
        if not config.bucket.strip() or not dedicated_staging_prefix(
            config.staging_prefix
        ):
            raise _storage_error("r2_configuration_invalid", _CONFIG_NEXT_ACTION)
        self._bucket = config.bucket
        self._prefix = config.staging_prefix
        self._client = sdk_client

    def _require_scope(self, bucket: str, prefix: str) -> None:
        if bucket != self._bucket or prefix != self._prefix:
            raise _storage_error("r2_scope_invalid", _SCOPE_NEXT_ACTION)

    def _require_key(self, key: object) -> str:
        if not _valid_exact_key(self._prefix, key):
            raise _storage_error("r2_key_invalid", _OBJECT_NEXT_ACTION)
        return str(key)

    def probe_scope(self, bucket: str, staging_prefix: str) -> bool:
        self._require_scope(bucket, staging_prefix)
        try:
            response = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=self._prefix,
                MaxKeys=1,
            )
        except Exception:
            raise _storage_error("r2_scope_failed", _SCOPE_NEXT_ACTION) from None
        if not isinstance(response, Mapping) or _status(response) != 200:
            raise _storage_error("r2_scope_failed", _SCOPE_NEXT_ACTION)
        key_count = response.get("KeyCount")
        contents = response.get("Contents", [])
        if (
            not isinstance(key_count, int)
            or isinstance(key_count, bool)
            or key_count < 0
            or key_count > 1
            or not isinstance(contents, list)
            or key_count != len(contents)
            or len(contents) > 1
        ):
            raise _storage_error("r2_scope_failed", _SCOPE_NEXT_ACTION)
        for item in contents:
            if not isinstance(item, Mapping) or not _valid_exact_key(
                self._prefix, item.get("Key")
            ):
                raise _storage_error("r2_scope_failed", _SCOPE_NEXT_ACTION)
        return True

    def head_object(self, key: str) -> Mapping[str, object] | None:
        exact_key = self._require_key(key)
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=exact_key,
            )
        except Exception as error:
            code, status = _error_facts(error)
            if status == 404 and code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise _storage_error("r2_head_failed", _OBJECT_NEXT_ACTION) from None
        if not isinstance(response, Mapping) or _status(response) != 200:
            raise _storage_error("r2_head_failed", _OBJECT_NEXT_ACTION)
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping):
            raise _storage_error("r2_metadata_invalid", _OBJECT_NEXT_ACTION)
        try:
            byte_size = int(metadata.get("byte-size", ""))
        except (TypeError, ValueError):
            raise _storage_error("r2_metadata_invalid", _OBJECT_NEXT_ACTION) from None
        media_type = _normalized_media_type(metadata.get("media-type"))
        sha256 = metadata.get("sha256")
        content_length = response.get("ContentLength")
        content_type = _normalized_media_type(response.get("ContentType"))
        if (
            byte_size < 0
            or isinstance(content_length, bool)
            or content_length != byte_size
            or not media_type
            or content_type != media_type
            or not isinstance(sha256, str)
            or not _SHA256.fullmatch(sha256)
        ):
            raise _storage_error("r2_metadata_invalid", _OBJECT_NEXT_ACTION)
        return {
            "byte_size": byte_size,
            "media_type": media_type,
            "sha256": sha256,
        }

    def create_file_if_absent(
        self,
        key: str,
        path: Path,
        *,
        byte_size: int,
        media_type: str,
        sha256: str,
    ) -> bool:
        exact_key = self._require_key(key)
        normalized_media_type = _normalized_media_type(media_type)
        try:
            valid_size = (
                isinstance(byte_size, int)
                and not isinstance(byte_size, bool)
                and byte_size >= 0
                and Path(path).stat().st_size == byte_size
            )
        except OSError:
            valid_size = False
        if (
            not valid_size
            or not normalized_media_type
            or not isinstance(sha256, str)
            or not _SHA256.fullmatch(sha256)
        ):
            raise _storage_error("r2_create_invalid", _OBJECT_NEXT_ACTION)
        try:
            with Path(path).open("rb") as body:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=exact_key,
                    Body=body,
                    ContentLength=byte_size,
                    ContentType=normalized_media_type,
                    Metadata={
                        "byte-size": str(byte_size),
                        "media-type": normalized_media_type,
                        "sha256": sha256,
                    },
                    IfNoneMatch="*",
                )
        except Exception as error:
            code, status = _error_facts(error)
            if status == 412 and code in {"412", "PreconditionFailed"}:
                return False
            raise _storage_error("r2_create_failed", _OBJECT_NEXT_ACTION) from None
        if (
            not isinstance(response, Mapping)
            or _status(response) != 200
            or not isinstance(response.get("ETag"), str)
            or not response["ETag"]
        ):
            raise _storage_error("r2_create_failed", _OBJECT_NEXT_ACTION)
        return True

    def delete_exact_object(self, key: str) -> bool:
        """Delete only one validated key within the configured capability."""

        exact_key = self._require_key(key)
        try:
            response = self._client.delete_object(
                Bucket=self._bucket,
                Key=exact_key,
            )
        except Exception:
            raise _storage_error("r2_delete_failed", _OBJECT_NEXT_ACTION) from None
        if not isinstance(response, Mapping) or _status(response) != 204:
            raise _storage_error("r2_delete_failed", _OBJECT_NEXT_ACTION)
        return True


def _validate_endpoint(account_id: str, endpoint: str) -> str:
    if not _ACCOUNT_ID.fullmatch(account_id):
        raise _storage_error("r2_configuration_invalid", _CONFIG_NEXT_ACTION)
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise _storage_error("r2_configuration_invalid", _CONFIG_NEXT_ACTION) from None
    expected_host = f"{account_id}.r2.cloudflarestorage.com"
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise _storage_error("r2_configuration_invalid", _CONFIG_NEXT_ACTION)
    return f"https://{expected_host}"


def _isolated_botocore_session() -> Any:
    """Return a session with ambient profiles and credential providers disabled."""

    from botocore.session import Session as BotocoreSession

    session = BotocoreSession(
        session_vars={
            "profile": (None, None, None, None),
            "config_file": (None, None, os.devnull, None),
            "credentials_file": (None, None, os.devnull, None),
            "ca_bundle": (None, None, None, None),
        }
    )
    session.get_component("credential_provider").providers.clear()
    return session


def build_r2_client(
    config: R2Config,
    *,
    environ: Mapping[str, str],
    session_factory: Any | None = None,
) -> R2StorageClient:
    """Build an explicit-credential R2 client without ambient credential lookup."""

    if not config.bucket.strip() or not dedicated_staging_prefix(config.staging_prefix):
        raise _storage_error("r2_configuration_invalid", _CONFIG_NEXT_ACTION)
    values = {name: environ.get(name, "") for name in REQUIRED_SECRET_NAMES}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise _storage_error("r2_configuration_invalid", _CONFIG_NEXT_ACTION)
    endpoint = _validate_endpoint(
        values["CLOUDFLARE_ACCOUNT_ID"],
        values["R2_ENDPOINT"],
    )
    require_conditional_model = session_factory is None
    if require_conditional_model:
        from boto3.session import Session

        session_factory = Session
    try:
        session_arguments = {
            "aws_access_key_id": values["R2_ACCESS_KEY_ID"],
            "aws_secret_access_key": values["R2_SECRET_ACCESS_KEY"],
            "aws_session_token": None,
            "region_name": "auto",
            "profile_name": None,
        }
        if require_conditional_model:
            session_arguments["botocore_session"] = _isolated_botocore_session()
        session = session_factory(
            **session_arguments,
        )
        sdk_client = session.client(
            "s3",
            endpoint_url=endpoint,
            config=SDKConfig(
                signature_version="s3v4",
                retries={"max_attempts": 0},
                s3={"addressing_style": "path"},
            ),
        )
        if require_conditional_model:
            put_shape = (
                sdk_client.meta.service_model.operation_model("PutObject").input_shape
            )
            if put_shape is None or "IfNoneMatch" not in put_shape.members:
                raise RuntimeError("conditional put is unavailable")
    except Exception:
        raise _storage_error("r2_client_failed", _CONFIG_NEXT_ACTION) from None
    return R2StorageClient(config, sdk_client)


_APPROVAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "record_type",
        "asset_id",
        "source_id",
        "public_url",
        "rights",
        "allowed_media_types",
        "maximum_bytes",
        "staging_prefix",
        "retention_decision",
        "evidence_ref",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "record_type": {"const": "transfer_approval"},
        "asset_id": {"type": "string"},
        "source_id": {"type": "string"},
        "public_url": {"type": "string"},
        "rights": {"type": "object"},
        "allowed_media_types": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "maximum_bytes": {"type": "integer", "minimum": 1},
        "staging_prefix": {"type": "string"},
        "retention_decision": {"type": "string", "minLength": 1},
        "evidence_ref": {"type": "string", "minLength": 1},
    },
}


def load_transfer_approval(path: str | Path) -> TransferPlan:
    """Load and validate exactly one strict, reviewed local approval plan."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        Draft202012Validator(_APPROVAL_SCHEMA).validate(value)
        return plan_transfer(
            asset_id=value["asset_id"],
            source_id=value["source_id"],
            public_url=value["public_url"],
            rights=value["rights"],
            allowed_media_types=value["allowed_media_types"],
            maximum_bytes=value["maximum_bytes"],
            staging_prefix=value["staging_prefix"],
            retention_decision=value["retention_decision"],
            evidence_ref=value["evidence_ref"],
        )
    except Exception:
        raise ApprovalError() from None


class UrllibHTTPResponse:
    """Small streaming wrapper that closes its urllib response deterministically."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.final_url = response.geturl()
        status = getattr(response, "status", None)
        if not isinstance(status, int) or isinstance(status, bool):
            try:
                status = response.getcode()
            except Exception:
                status = None
        self.status = status
        self.media_type = response.headers.get("Content-Type", "")
        raw_length = response.headers.get("Content-Length")
        try:
            self.content_length = None if raw_length is None else int(raw_length)
        except (TypeError, ValueError):
            self.content_length = -1

    def iter_bytes(self, chunk_size: int) -> Iterable[bytes]:
        try:
            while True:
                chunk = self._response.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            self._response.close()


class _NoRedirectHandler(urlrequest.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class UrllibHTTPClient:
    """Production source stream used only by the explicitly invoked transfer CLI."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._opener = urlrequest.build_opener(_NoRedirectHandler())

    def open(
        self,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> UrllibHTTPResponse:
        selected_timeout = self._timeout_seconds
        if timeout_seconds is not None:
            if (
                not isinstance(timeout_seconds, (int, float))
                or isinstance(timeout_seconds, bool)
                or not math.isfinite(float(timeout_seconds))
                or timeout_seconds <= 0
            ):
                raise ValueError("timeout_seconds must be finite and positive")
            selected_timeout = min(selected_timeout, float(timeout_seconds))
        request = urlrequest.Request(
            url,
            headers={"User-Agent": "performing-fire-corpus/0.1"},
            method="GET",
        )
        return UrllibHTTPResponse(
            self._opener.open(request, timeout=selected_timeout)
        )
