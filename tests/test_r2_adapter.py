from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError
from botocore.session import Session as BotocoreSession


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.cli import main
from performing_fire_corpus.r2 import (
    R2StorageClient,
    UrllibHTTPClient,
    _isolated_botocore_session,
    build_r2_client,
)
from performing_fire_corpus.storage import R2Config, StorageError


ACCOUNT_ID = "a" * 32
ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"
ENVIRONMENT = {
    "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID,
    "R2_ACCESS_KEY_ID": "invented-access-key",
    "R2_SECRET_ACCESS_KEY": "invented-secret-key",
    "R2_ENDPOINT": ENDPOINT,
}
CONFIG = R2Config(bucket="synthetic-bucket", staging_prefix="proof/")
SDK_SUPPORTS_CONDITIONAL_PUT = (
    "IfNoneMatch"
    in BotocoreSession()
    .get_service_model("s3")
    .operation_model("PutObject")
    .input_shape.members
)


def client_error(code: str, status: int, operation: str = "HeadObject") -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "private provider detail"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class FakeSDK:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.list_result: object = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "KeyCount": 0,
            "Contents": [],
        }
        self.head_result: object = None
        self.put_result: object = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "ETag": '"synthetic"',
        }

    def list_objects_v2(self, **kwargs: object) -> object:
        self.calls.append(("list_objects_v2", kwargs))
        if isinstance(self.list_result, Exception):
            raise self.list_result
        return self.list_result

    def head_object(self, **kwargs: object) -> object:
        self.calls.append(("head_object", kwargs))
        if isinstance(self.head_result, Exception):
            raise self.head_result
        return self.head_result

    def put_object(self, **kwargs: object) -> object:
        self.calls.append(("put_object", kwargs))
        if isinstance(self.put_result, Exception):
            raise self.put_result
        return self.put_result


class FakeSession:
    def __init__(self, sdk: FakeSDK, capture: dict[str, object], **kwargs: object) -> None:
        self.sdk = sdk
        capture["session"] = kwargs
        self.capture = capture

    def client(self, service_name: str, **kwargs: object) -> FakeSDK:
        self.capture["client"] = {"service_name": service_name, **kwargs}
        return self.sdk


class OfflineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        guards = (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("live network access is forbidden"),
            ),
            patch.object(
                socket,
                "getaddrinfo",
                side_effect=AssertionError("live DNS access is forbidden"),
            ),
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("live socket access is forbidden"),
            ),
        )
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)


class R2AdapterTests(OfflineTestCase):
    def test_factory_rejects_missing_credentials_and_invalid_endpoint_before_sdk(self) -> None:
        for environment in (
            {},
            {**ENVIRONMENT, "R2_ENDPOINT": "http://example.invalid"},
            {
                **ENVIRONMENT,
                "R2_ENDPOINT": "https://example.invalid",
            },
            {
                **ENVIRONMENT,
                "R2_ENDPOINT": "https://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.r2.cloudflarestorage.com",
            },
        ):
            called = False

            def session_factory(**kwargs: object) -> object:
                nonlocal called
                del kwargs
                called = True
                raise AssertionError("SDK construction must not occur")

            with self.subTest(environment=environment), self.assertRaises(StorageError) as raised:
                build_r2_client(CONFIG, environ=environment, session_factory=session_factory)
            self.assertEqual("r2_configuration_invalid", raised.exception.code)
            self.assertFalse(called)
            rendered = str(raised.exception)
            for value in environment.values():
                self.assertNotIn(value, rendered)

    def test_factory_uses_explicit_credentials_and_no_profile(self) -> None:
        sdk = FakeSDK()
        capture: dict[str, object] = {}

        def session_factory(**kwargs: object) -> FakeSession:
            return FakeSession(sdk, capture, **kwargs)

        adapter = build_r2_client(
            CONFIG,
            environ=ENVIRONMENT,
            session_factory=session_factory,
        )

        self.assertIsInstance(adapter, R2StorageClient)
        self.assertEqual(
            {
                "aws_access_key_id": ENVIRONMENT["R2_ACCESS_KEY_ID"],
                "aws_secret_access_key": ENVIRONMENT["R2_SECRET_ACCESS_KEY"],
                "aws_session_token": None,
                "region_name": "auto",
                "profile_name": None,
            },
            capture["session"],
        )
        client = capture["client"]
        self.assertEqual("s3", client["service_name"])
        self.assertEqual(ENDPOINT, client["endpoint_url"])
        self.assertEqual("s3v4", client["config"].signature_version)

    def test_production_session_ignores_ambient_profiles_and_credential_providers(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "AWS_PROFILE": "ambient-profile-must-not-load",
                "AWS_CONFIG_FILE": "/private/ambient-config",
                "AWS_SHARED_CREDENTIALS_FILE": "/private/ambient-credentials",
            },
            clear=False,
        ):
            session = _isolated_botocore_session()

        self.assertIsNone(session.get_config_variable("profile"))
        self.assertEqual(os.devnull, session.get_config_variable("config_file"))
        self.assertEqual(
            os.devnull,
            session.get_config_variable("credentials_file"),
        )
        self.assertEqual(
            [],
            session.get_component("credential_provider").providers,
        )

    @unittest.skipUnless(
        SDK_SUPPORTS_CONDITIONAL_PUT,
        "installed boto3 is below the declared conditional-PUT floor",
    )
    def test_real_sdk_factory_builds_offline_despite_ambient_profile(self) -> None:
        with patch.dict(
            os.environ,
            {"AWS_PROFILE": "ambient-profile-must-not-load"},
            clear=False,
        ):
            adapter = build_r2_client(CONFIG, environ=ENVIRONMENT)

        self.assertIsInstance(adapter, R2StorageClient)

    def test_probe_is_one_bounded_request_and_rejects_wrong_scope(self) -> None:
        sdk = FakeSDK()
        adapter = R2StorageClient(CONFIG, sdk)

        self.assertTrue(adapter.probe_scope(CONFIG.bucket, CONFIG.staging_prefix))
        self.assertEqual(
            [
                (
                    "list_objects_v2",
                    {
                        "Bucket": CONFIG.bucket,
                        "Prefix": CONFIG.staging_prefix,
                        "MaxKeys": 1,
                    },
                )
            ],
            sdk.calls,
        )
        with self.assertRaises(StorageError):
            adapter.probe_scope("other-bucket", CONFIG.staging_prefix)
        with self.assertRaises(StorageError):
            adapter.probe_scope(CONFIG.bucket, "other/")
        self.assertEqual(1, len(sdk.calls))

    def test_probe_accepts_the_standard_empty_response_without_contents(self) -> None:
        sdk = FakeSDK()
        sdk.list_result = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "KeyCount": 0,
        }
        adapter = R2StorageClient(CONFIG, sdk)

        self.assertTrue(adapter.probe_scope(CONFIG.bucket, CONFIG.staging_prefix))

    def test_head_maps_only_verified_not_found_and_requires_complete_metadata(self) -> None:
        sdk = FakeSDK()
        adapter = R2StorageClient(CONFIG, sdk)
        key = "proof/v1/asset/digest"

        sdk.head_result = client_error("NoSuchKey", 404)
        self.assertIsNone(adapter.head_object(key))

        sdk.head_result = {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "ContentLength": 4,
            "ContentType": "Video/MP4; charset=binary",
            "Metadata": {
                "byte-size": "4",
                "media-type": "video/mp4",
                "sha256": "b" * 64,
            },
        }
        self.assertEqual(
            {"byte_size": 4, "media_type": "video/mp4", "sha256": "b" * 64},
            adapter.head_object(key),
        )

        for result in (
            client_error("AccessDenied", 403),
            client_error("NoSuchKey", 500),
            {"ResponseMetadata": {"HTTPStatusCode": 302}},
            {
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "ContentLength": 4,
                "ContentType": "video/mp4",
                "Metadata": {"sha256": "b" * 64},
            },
        ):
            sdk.head_result = result
            with self.subTest(result=result), self.assertRaises(StorageError) as raised:
                adapter.head_object(key)
            self.assertNotIn("private provider detail", str(raised.exception))

    def test_conditional_create_sets_metadata_and_maps_only_verified_race(self) -> None:
        sdk = FakeSDK()
        adapter = R2StorageClient(CONFIG, sdk)
        key = "proof/v1/asset/digest"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "payload.bin"
            path.write_bytes(b"data")
            created = adapter.create_file_if_absent(
                key,
                path,
                byte_size=4,
                media_type="Video/MP4; charset=binary",
                sha256="c" * 64,
            )
            self.assertTrue(created)
            call, kwargs = sdk.calls[-1]
            self.assertEqual("put_object", call)
            self.assertEqual("*", kwargs["IfNoneMatch"])
            self.assertEqual(
                {
                    "byte-size": "4",
                    "media-type": "video/mp4",
                    "sha256": "c" * 64,
                },
                kwargs["Metadata"],
            )
            self.assertIsInstance(kwargs["Body"], io.BufferedReader)

            sdk.put_result = client_error("PreconditionFailed", 412, "PutObject")
            self.assertFalse(
                adapter.create_file_if_absent(
                    key,
                    path,
                    byte_size=4,
                    media_type="video/mp4",
                    sha256="c" * 64,
                )
            )
            sdk.put_result = client_error("PreconditionFailed", 403, "PutObject")
            with self.assertRaises(StorageError):
                adapter.create_file_if_absent(
                    key,
                    path,
                    byte_size=4,
                    media_type="video/mp4",
                    sha256="c" * 64,
                )

    def test_production_http_client_installs_a_rejecting_redirect_handler(self) -> None:
        client = UrllibHTTPClient()
        handlers = [
            handler
            for handler in client._opener.handlers
            if type(handler).__name__ == "_NoRedirectHandler"
        ]

        self.assertEqual(1, len(handlers))
        self.assertIsNone(
            handlers[0].redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "http://127.0.0.1/private",
            )
        )


class FakeHTTPResponse:
    media_type = "video/mp4"
    content_length = 4
    final_url = "https://antiegg.kr/media/synthetic.mp4"

    def iter_bytes(self, chunk_size: int):
        del chunk_size
        yield b"data"


class FakeHTTP:
    def open(self, url: str) -> FakeHTTPResponse:
        del url
        return FakeHTTPResponse()


class CLITests(OfflineTestCase):
    def write_plan_and_config(self, root: Path) -> tuple[Path, Path]:
        records = ROOT / "tests" / "fixtures" / "records" / "v1"
        plan_path = root / "approval.json"
        plan_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_type": "transfer_approval",
                    "asset_id": "asset_synthetic_video_001",
                    "source_id": "source_synthetic_001",
                    "public_url": FakeHTTPResponse.final_url,
                    "rights": json.loads((records / "rights.json").read_text()),
                    "allowed_media_types": ["video/mp4"],
                    "maximum_bytes": 4,
                    "staging_prefix": "proof/",
                    "retention_decision": (
                        "Delete the exact reviewed key after verification."
                    ),
                    "evidence_ref": "evidence:issue-17",
                }
            ),
            encoding="utf-8",
        )
        config_path = root / "storage.yaml"
        config_path.write_text(
            "object_storage:\n"
            "  bucket: synthetic-bucket\n"
            "  prefix: proof/\n",
            encoding="utf-8",
        )
        return plan_path, config_path

    def test_readiness_constructs_production_adapter_only_when_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "storage.yaml"
            config.write_text(
                "object_storage:\n"
                "  bucket: synthetic-bucket\n"
                "  prefix: proof/\n",
                encoding="utf-8",
            )
            output = root / "readiness.json"
            adapter = R2StorageClient(CONFIG, FakeSDK())
            with patch(
                "performing_fire_corpus.cli.build_r2_client",
                return_value=adapter,
            ) as factory, redirect_stdout(io.StringIO()):
                status = main(
                    ["r2", "readiness", "--config", str(config), "--output", str(output)],
                    environ=ENVIRONMENT,
                )
            self.assertEqual(0, status)
            factory.assert_called_once()

            with patch(
                "performing_fire_corpus.cli.build_r2_client",
                side_effect=AssertionError("must remain offline"),
            ), redirect_stdout(io.StringIO()):
                status = main(
                    ["r2", "readiness", "--config", str(config), "--output", str(output)],
                    environ={},
                )
            self.assertEqual(2, status)

    def test_transfer_cli_loads_one_strict_plan_and_prints_no_plan_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, config_path = self.write_plan_and_config(root)
            receipt_path = root / "receipt.json"
            ledger_path = root / "ledger.sqlite3"
            cache_path = root / "cache"
            records = ROOT / "tests" / "fixtures" / "records" / "v1"

            from performing_fire_corpus.ledger import Ledger

            with Ledger(ledger_path) as ledger:
                for name in ("source", "asset", "rights"):
                    ledger.upsert(json.loads((records / f"{name}.json").read_text()))

            sdk = FakeSDK()
            digest = hashlib.sha256(b"data").hexdigest()
            sdk.head_result = client_error("NoSuchKey", 404)

            def head_after_put(**kwargs: object) -> object:
                sdk.calls.append(("head_object", kwargs))
                if any(call == "put_object" for call, _ in sdk.calls):
                    return {
                        "ResponseMetadata": {"HTTPStatusCode": 200},
                        "ContentLength": 4,
                        "ContentType": "video/mp4",
                        "Metadata": {
                            "byte-size": "4",
                            "media-type": "video/mp4",
                            "sha256": digest,
                        },
                    }
                raise client_error("NoSuchKey", 404)

            sdk.head_object = head_after_put
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "r2",
                        "transfer-approved",
                        "--plan",
                        str(plan_path),
                        "--ledger",
                        str(ledger_path),
                        "--config",
                        str(config_path),
                        "--cache-directory",
                        str(cache_path),
                        "--output",
                        str(receipt_path),
                    ],
                    environ=ENVIRONMENT,
                    storage_client=R2StorageClient(CONFIG, sdk),
                    http_client=FakeHTTP(),
                )

            self.assertEqual(0, status)
            self.assertEqual({"status": "complete"}, json.loads(output.getvalue()))
            rendered = output.getvalue()
            self.assertNotIn(FakeHTTPResponse.final_url, rendered)
            self.assertNotIn(str(plan_path), rendered)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(digest, receipt["sha256"])
            self.assertEqual("uploaded", receipt["attempt_state"])

    def test_transfer_cli_exit_taxonomy_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, config_path = self.write_plan_and_config(root)
            arguments = [
                "r2",
                "transfer-approved",
                "--plan",
                str(plan_path),
                "--ledger",
                str(root / "ledger.sqlite3"),
                "--config",
                str(config_path),
                "--cache-directory",
                str(root / "cache"),
                "--output",
                str(root / "receipt.json"),
            ]

            missing_output = io.StringIO()
            with redirect_stdout(missing_output):
                missing_status = main(arguments, environ={})
            self.assertEqual(3, missing_status)
            self.assertEqual(
                "r2_configuration_invalid",
                json.loads(missing_output.getvalue())["code"],
            )

            failed_output = io.StringIO()
            with patch(
                "performing_fire_corpus.cli.transfer_approved_asset",
                side_effect=StorageError(
                    "r2_head_failed",
                    "Verify the exact immutable R2 object and retry safely.",
                ),
            ), redirect_stdout(failed_output):
                failed_status = main(
                    arguments,
                    environ=ENVIRONMENT,
                    storage_client=object(),
                    http_client=object(),
                )
            self.assertEqual(1, failed_status)
            self.assertEqual(
                "r2_head_failed",
                json.loads(failed_output.getvalue())["code"],
            )
            for output in (missing_output.getvalue(), failed_output.getvalue()):
                self.assertNotIn(str(plan_path), output)
                self.assertNotIn(ENVIRONMENT["R2_SECRET_ACCESS_KEY"], output)

    def test_transfer_cli_rejects_invalid_plan_before_clients(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "approval.json"
            plan_path.write_text('{"schema_version": 1}', encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "r2",
                        "transfer-approved",
                        "--plan",
                        str(plan_path),
                        "--ledger",
                        str(root / "ledger.sqlite3"),
                        "--config",
                        str(root / "storage.yaml"),
                        "--cache-directory",
                        str(root / "cache"),
                        "--output",
                        str(root / "receipt.json"),
                    ],
                    environ=ENVIRONMENT,
                    storage_client=object(),
                    http_client=object(),
                )
            self.assertEqual(4, status)
            self.assertEqual("approval_invalid", json.loads(output.getvalue())["code"])
            self.assertNotIn(str(plan_path), output.getvalue())


if __name__ == "__main__":
    unittest.main()
