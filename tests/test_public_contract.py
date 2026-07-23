from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", "evidence", "harness-backups", "harness-runs", "vm-runs"}
FORBIDDEN_SUFFIXES = {
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".wav",
}
PRIVATE_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")


def public_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.parts)
    ]


class PublicRepositoryContractTests(unittest.TestCase):
    def test_repository_contains_no_source_documents_or_media(self) -> None:
        forbidden = [
            path.relative_to(ROOT).as_posix()
            for path in public_files()
            if path.suffix.lower() in FORBIDDEN_SUFFIXES
        ]
        self.assertEqual([], forbidden)

    def test_text_files_do_not_contain_machine_local_home_paths(self) -> None:
        offenders: list[str] = []
        for path in public_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if PRIVATE_PATH.search(text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], offenders)

    def test_public_brief_pins_source_and_privacy_boundaries(self) -> None:
        brief = (ROOT / "docs" / "PROJECT_BRIEF.md").read_text(encoding="utf-8")
        for value in (
            "https://njpvideo.ggcf.kr/",
            "https://njp.ggcf.kr/",
            "https://antiegg.kr/25502/",
            "Forbidden in Git, GitHub, logs, screenshots, fixtures, and evidence",
            "Model/effort racing is disabled",
            "trusted-laptop",
        ):
            self.assertIn(value, brief)

    def test_network_smoke_run_is_documented_as_opt_in_and_metadata_only(
        self,
    ) -> None:
        smoke = (ROOT / "docs" / "network-acquisition-smoke.md").read_text(
            encoding="utf-8"
        )
        for value in (
            "opt-in",
            "trusted VM",
            "inventory-public",
            "--max-requests 2",
            "--ledger",
            "--sanitized-manifest",
            "unauthenticated",
            "must not be added to portable CI",
        ):
            self.assertIn(value, smoke)


if __name__ == "__main__":
    unittest.main()
