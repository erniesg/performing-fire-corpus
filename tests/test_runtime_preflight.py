from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "preflight-python"
# Resolved here so a test can shrink PATH without losing the shell itself.
SHELL = shutil.which("sh") or "/bin/sh"
REQUIRES_PYTHON = re.compile(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)"')


def run_preflight(
    *arguments: str, path: str | None = None, pinned: str | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PERFORMING_FIRE_PYTHON"}
    }
    if path is not None:
        environment["PATH"] = path
    if pinned is not None:
        environment["PERFORMING_FIRE_PYTHON"] = pinned
    return subprocess.run(
        [SHELL, str(PREFLIGHT), *arguments],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def write_shim(directory: Path, name: str, reported_version: str) -> Path:
    """Write a PATH stub that reports one interpreter version and nothing else."""
    shim = directory / name
    shim.write_text(f'#!/bin/sh\necho "{reported_version}"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim


class RuntimePreflightTests(unittest.TestCase):
    def test_preflight_selects_a_supported_interpreter_on_this_runtime(self) -> None:
        result = run_preflight()
        self.assertEqual(0, result.returncode, result.stderr)
        selected = result.stdout.strip()
        self.assertNotEqual("", selected)

        reported = subprocess.run(
            [selected, "-c", 'import sys; print("%d.%d" % sys.version_info[:2])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        major, minor = (int(part) for part in reported.split("."))
        self.assertGreaterEqual((major, minor), (3, 11))

    def test_unsupported_python3_fails_closed_with_an_exact_version_message(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            write_shim(directory, "python3", "3.10")
            result = run_preflight(path=str(directory))

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("no Python >= 3.11 was found", result.stderr)
        self.assertIn("requires-python >= 3.11", result.stderr)
        self.assertIn("python3: 3.10", result.stderr)

    def test_missing_interpreter_reports_the_blocked_environment_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            result = run_preflight(path=raw_directory)

        self.assertEqual(2, result.returncode)
        self.assertIn("no candidate interpreter was found on PATH", result.stderr)

    def test_supported_versioned_interpreter_wins_over_unsupported_python3(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            write_shim(directory, "python3", "3.10")
            write_shim(directory, "python3.11", "3.11")
            result = run_preflight(path=str(directory))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("python3.11", result.stdout.strip())

    def test_pinned_interpreter_is_never_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            write_shim(directory, "python3", "3.10")
            write_shim(directory, "python3.11", "3.11")
            result = run_preflight(path=str(directory), pinned="python3")

        self.assertEqual(2, result.returncode)
        self.assertIn("python3: 3.10", result.stderr)
        self.assertNotIn("python3.11", result.stdout)

    def test_unreportable_interpreter_is_treated_as_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            write_shim(directory, "python3", "not-a-version")
            result = run_preflight(path=str(directory))

        self.assertEqual(2, result.returncode)
        self.assertIn("python3: not-a-version", result.stderr)

    def test_preflight_forwards_arguments_to_the_selected_interpreter(self) -> None:
        result = run_preflight(
            "-c",
            'import sys; print("%d.%d" % sys.version_info[:2])',
            pinned=sys.executable,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "%d.%d" % sys.version_info[:2],
            result.stdout.strip(),
        )

    def test_preflight_floor_matches_the_declared_requires_python(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = REQUIRES_PYTHON.search(pyproject)
        self.assertIsNotNone(declared)
        assert declared is not None

        script = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn(f"REQUIRED_MAJOR={declared.group(1)}", script)
        self.assertIn(f"REQUIRED_MINOR={declared.group(2)}", script)

    def test_preflight_is_offline_and_free_of_network_or_secret_use(self) -> None:
        script = PREFLIGHT.read_text(encoding="utf-8")
        for forbidden in ("curl", "wget", "http://", "https://", "pip install"):
            self.assertNotIn(forbidden, script)

    def test_preflight_is_documented_with_a_repository_relative_command(self) -> None:
        for document in (
            ROOT / "README.md",
            ROOT / "docs" / "product-readiness-matrix.md",
            ROOT / ".agent" / "verify.md",
        ):
            self.assertIn(
                "sh scripts/preflight-python",
                document.read_text(encoding="utf-8"),
                document.name,
            )


if __name__ == "__main__":
    unittest.main()
