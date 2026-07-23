from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "v1"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "records" / "v1"
RECORD_TYPES = ("source", "asset", "rights", "job", "lease", "object", "evidence")


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validator_for(record_type: str) -> Draft202012Validator:
    schema = load_json(SCHEMA_DIR / f"{record_type}.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def fixture_for(record_type: str) -> dict[str, object]:
    return load_json(FIXTURE_DIR / f"{record_type}.json")


class SchemaContractTests(unittest.TestCase):
    def assert_invalid(self, record_type: str, record: dict[str, object]) -> None:
        with self.assertRaises(ValidationError):
            validator_for(record_type).validate(record)

    def test_every_schema_is_versioned_strict_and_accepts_its_fixture(self) -> None:
        for record_type in RECORD_TYPES:
            with self.subTest(record_type=record_type):
                schema = load_json(SCHEMA_DIR / f"{record_type}.json")
                self.assertEqual(
                    f"https://performing-fire-corpus.invalid/schemas/v1/{record_type}.json",
                    schema["$id"],
                )
                self.assertEqual(False, schema["additionalProperties"])
                self.assertEqual(
                    {"const": 1},
                    schema["properties"]["schema_version"],
                )

                validator = validator_for(record_type)
                fixture = fixture_for(record_type)
                validator.validate(fixture)
                validator.validate(copy.deepcopy(fixture))

    def test_missing_required_field_is_rejected(self) -> None:
        record = fixture_for("source")
        del record["source_id"]
        self.assert_invalid("source", record)

    def test_unknown_field_is_rejected_by_every_record_schema(self) -> None:
        for record_type in RECORD_TYPES:
            with self.subTest(record_type=record_type):
                record = fixture_for(record_type)
                record["unexpected"] = "not part of the public contract"
                self.assert_invalid(record_type, record)

    def test_rights_state_is_bounded_and_decisions_are_complete(self) -> None:
        invalid_state = fixture_for("rights")
        invalid_state["state"] = "allowed"
        self.assert_invalid("rights", invalid_state)

        incomplete_decision = fixture_for("rights")
        del incomplete_decision["decision_at"]
        self.assert_invalid("rights", incomplete_decision)

        pending_with_decision = fixture_for("rights")
        pending_with_decision["state"] = "pending"
        self.assert_invalid("rights", pending_with_decision)

        pending = fixture_for("rights")
        pending["state"] = "pending"
        del pending["decision_reason"]
        del pending["decision_at"]
        validator_for("rights").validate(pending)

    def test_malformed_lowercase_sha256_is_rejected(self) -> None:
        uppercase_hash = fixture_for("asset")
        uppercase_hash["sha256"] = "A" * 64
        self.assert_invalid("asset", uppercase_hash)

        short_hash = fixture_for("object")
        short_hash["sha256"] = "a" * 63
        self.assert_invalid("object", short_hash)

    def test_machine_local_paths_are_rejected(self) -> None:
        cases = (
            ("job", "input_object_key", "/tmp/download.bin"),
            ("object", "object_key", "file:///tmp/download.bin"),
            ("rights", "decision_reason", "/tmp/private/decision.txt"),
        )
        for record_type, field, value in cases:
            with self.subTest(record_type=record_type, field=field):
                record = fixture_for(record_type)
                record[field] = value
                self.assert_invalid(record_type, record)

    def test_cli_help_is_offline_and_deterministic(self) -> None:
        environment = {
            "LC_ALL": "C",
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "1",
            "PYTHONPATH": str(ROOT / "src"),
        }
        command = [sys.executable, "-m", "performing_fire_corpus", "--help"]
        first = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        second = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual("", first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("usage: performing-fire-corpus", first.stdout)

    def test_pyproject_declares_python_and_bounded_runtime_dependency(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        self.assertEqual(">=3.11", project["requires-python"])
        self.assertEqual(
            {"performing-fire-corpus": "performing_fire_corpus.cli:main"},
            project["scripts"],
        )
        self.assertEqual(
            ["boto3>=1.37.32,<2", "jsonschema>=4.10,<5"],
            project["dependencies"],
        )


if __name__ == "__main__":
    unittest.main()
