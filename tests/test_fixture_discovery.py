from __future__ import annotations

import copy
import json
import socket
import sqlite3
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.cli import main
from performing_fire_corpus.discovery import (
    FixtureError,
    build_records,
    discover_fixture,
    load_fixture,
)
from performing_fire_corpus.ledger import Ledger


FIXTURES = ROOT / "tests" / "fixtures" / "discovery"
FIXTURE = FIXTURES / "synthetic-source-v1.json"
EXPECTED_MANIFEST = FIXTURES / "expected-manifest-v1.json"


def fixture_data() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FixtureDiscoveryTests(unittest.TestCase):
    def test_cli_is_offline_deterministic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = directory / "ledger.sqlite3"
            output = directory / "manifest.json"
            arguments = [
                "discover-fixture",
                "--fixture",
                str(FIXTURE),
                "--database",
                str(database),
                "--output",
                str(output),
            ]

            with (
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("network call attempted"),
                ),
                patch.object(
                    urllib.request,
                    "urlopen",
                    side_effect=AssertionError("network call attempted"),
                ),
            ):
                self.assertEqual(0, main(arguments))
                first = output.read_bytes()
                self.assertEqual(0, main(arguments))
                second = output.read_bytes()

            self.assertEqual(EXPECTED_MANIFEST.read_bytes(), first)
            self.assertEqual(first, second)
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    5, connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                )
                self.assertEqual(
                    1, connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                )
                self.assertEqual(
                    0,
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM jobs
                        WHERE operation LIKE '%transfer%'
                           OR required_capabilities LIKE '%trusted-vm%'
                           OR required_capabilities LIKE '%object-storage%'
                        """
                    ).fetchone()[0],
                )

    def test_restart_from_partially_populated_ledger_finishes_without_duplicates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = directory / "ledger.sqlite3"
            output = directory / "manifest.json"
            records = build_records(fixture_data())
            with Ledger(database) as ledger:
                ledger.upsert(records["source"])

            manifest = discover_fixture(FIXTURE, database, output)

            self.assertEqual(
                json.loads(EXPECTED_MANIFEST.read_text(encoding="utf-8")), manifest
            )
            self.assertEqual(EXPECTED_MANIFEST.read_bytes(), output.read_bytes())
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    5, connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                )
                self.assertEqual(
                    1, connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                )

    def test_only_repository_discovery_fixtures_are_accepted(self) -> None:
        self.assertEqual("synthetic_metadata", load_fixture(FIXTURE)["fixture_type"])
        with tempfile.TemporaryDirectory() as temporary:
            unchecked = Path(temporary) / "unchecked.json"
            unchecked.write_text(json.dumps(fixture_data()), encoding="utf-8")
            with self.assertRaisesRegex(FixtureError, "checked-in"):
                load_fixture(unchecked)

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            dir=FIXTURES,
        ) as untracked:
            json.dump(fixture_data(), untracked)
            untracked.flush()
            with self.assertRaisesRegex(FixtureError, "checked-in"):
                load_fixture(untracked.name)

    def test_malformed_fixture_is_rejected(self) -> None:
        malformed = fixture_data()
        del malformed["source"]["source_kind"]
        with self.assertRaisesRegex(FixtureError, "source_kind"):
            build_records(malformed)

    def test_private_or_content_bearing_fields_are_rejected(self) -> None:
        forbidden_fields = (
            "response_body",
            "article_prose",
            "media_encoding",
            "caption",
            "transcript",
            "embedding",
            "credentials",
            "personal_information",
            "email",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                private = copy.deepcopy(fixture_data())
                private["source"]["metadata"][field] = "Synthetic forbidden value"
                with self.assertRaisesRegex(FixtureError, "forbidden"):
                    build_records(private)

    def test_local_absolute_paths_are_rejected(self) -> None:
        private = fixture_data()
        private["assets"][0]["metadata"]["location"] = "/tmp/synthetic-private.json"
        with self.assertRaisesRegex(FixtureError, "local absolute path"):
            build_records(private)

    def test_sensitive_values_cannot_reach_records_or_manifests(self) -> None:
        sensitive_values = (
            "synthetic-person" + "@example.invalid",
            "acct_" + "1234567890",
        )
        for value in sensitive_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                private = fixture_data()
                private["source"]["metadata"]["label"] = value
                directory = Path(temporary)
                output = directory / "manifest.json"
                with (
                    patch(
                        "performing_fire_corpus.discovery.load_fixture",
                        return_value=private,
                    ),
                    self.assertRaisesRegex(FixtureError, "sensitive value"),
                ):
                    discover_fixture(FIXTURE, directory / "ledger.sqlite3", output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
