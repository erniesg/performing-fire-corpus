from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.pilot_freeze import (
    PilotFreezeError,
    compile_pilot_freeze,
    validate_pilot_freeze_manifest,
    write_pilot_freeze,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class PilotFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = self.write_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def write_fixture(self) -> dict[str, Path]:
        raw_records = []
        njp_catalogue = []
        for index in range(30):
            source_id = str(1000 + index)
            storage_id = str(2000 + index)
            raw_records.append(
                {
                    "catalogue_record_id": source_id,
                    "storage_record_id": storage_id,
                    "kind": "video",
                    "status": "present",
                    "r2_objects": [
                        {
                            "key": f"njpvideo/media/{storage_id}/low.mp4",
                            "size": 10000 + index,
                            "etag": f"multipart-{index}",
                        }
                    ],
                }
            )
            njp_catalogue.append(
                {
                    "id": 1000 + index,
                    "title": f"strong shared work {index}",
                    "titleEn": f"njp alternate work {index}",
                }
            )

        pdf_files = []
        page_records = []
        for index in range(22):
            record_id = str(3000 + index)
            sha256 = digest(f"pdf-{index}")
            size = 5000 + index
            raw_records.append(
                {
                    "catalogue_record_id": str(4000 + index),
                    "storage_record_id": record_id,
                    "kind": "pdf",
                    "status": "present",
                    "r2_objects": [
                        {
                            "key": f"njpvideo/pdf/{record_id}.pdf",
                            "size": size,
                            "etag": f"pdf-etag-{index}",
                        }
                    ],
                }
            )
            pdf_files.append(
                {
                    "record_id": record_id,
                    "file": f"{record_id}.pdf",
                    "bytes": size,
                    "sha256": sha256,
                    "is_pdf": True,
                }
            )
            page_records.append({"object_sha256": sha256, "page_count": index + 1})

        archive_files = []
        archive_rows = []
        for index in range(30):
            archive_rows.append(
                {
                    "index": index + 1,
                    "title": (
                        f"strong shared work {index}"
                        if index < 10
                        else f"archive unrelated control {index}"
                    ),
                }
            )
            if index < 8:
                sha256 = digest(f"archive-pdf-{index}")
                archive_files.append(
                    {
                        "file": f"archive-{index}.pdf",
                        "bytes": 8000 + index,
                        "sha256": sha256,
                        "is_pdf": True,
                    }
                )
                page_records.append(
                    {"object_sha256": sha256, "page_count": index + 3}
                )

        youtube_results = []
        for index in range(30):
            video_id = f"Video_{index:02d}"
            youtube_results.append(
                {
                    "video_id": video_id,
                    "catalogue_title": (
                        f"ambig shared item {index} youtube first"
                        if 10 <= index < 20
                        else f"youtube distinct control {index}"
                    ),
                    "object_key": f"youtube/media/{video_id}/{video_id}.mp4",
                    "r2_size": 20000 + index,
                    "sha256": digest(f"youtube-{index}"),
                    "duration_seconds": 60 + index,
                    "status": "uploaded_verified",
                }
            )

        center_catalogue = [
            {
                "id": 5000 + index,
                "title": (
                    f"ambig shared item {index + 10} center second"
                    if index < 10
                    else f"center negative reference {index}"
                ),
            }
            for index in range(30)
        ]
        image_results = [
            {
                "catalogue_record_id": str(6000 + index),
                "storage_record_id": str(7000 + index),
                "object_key": f"njpvideo/image/{7000 + index}/image.jpg",
                "r2_size": 30000 + index,
                "sha256": digest(f"image-{index}"),
                "status": "uploaded_verified",
            }
            for index in range(30)
        ]
        return {
            "njp_raw_manifest_path": self.write_json(
                "raw.json", {"records": raw_records}
            ),
            "njp_catalogue_path": self.write_json("njp.json", njp_catalogue),
            "youtube_media_manifest_path": self.write_json(
                "youtube.json", {"results": youtube_results}
            ),
            "njp_pdf_manifest_path": self.write_json(
                "pdf.json", {"files": pdf_files}
            ),
            "archive_pdf_manifest_path": self.write_json(
                "archive-pdf.json", {"files": archive_files}
            ),
            "image_manifest_path": self.write_json(
                "images.json", {"results": image_results}
            ),
            "center_catalogue_path": self.write_json(
                "center.json", center_catalogue
            ),
            "archive_list_path": self.write_json(
                "archive-list.json", {"records": archive_rows}
            ),
            "pdf_page_counts_path": self.write_json(
                "page-counts.json",
                {
                    "schema_version": 1,
                    "record_type": "pdf_page_counts",
                    "records": page_records,
                },
            ),
        }

    def compile(self, *, page_counts: bool = True) -> dict[str, object]:
        arguments = dict(self.paths)
        if not page_counts:
            arguments.pop("pdf_page_counts_path")
        return compile_pilot_freeze(
            freeze_label="synthetic_pilot_v1",
            **arguments,
        )

    def test_compiles_exact_content_free_held_candidate_sets(self) -> None:
        manifest = self.compile()

        schema = json.loads(
            (ROOT / "schemas/v1/pilot-freeze-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(manifest)
        self.assertEqual("frozen", manifest["candidate_set_state"])
        self.assertEqual("held", manifest["execution_state"])
        self.assertFalse(
            manifest["execution_contract"]["transformation_authorized"]
        )
        for field in (
            "videos",
            "documents",
            "page_slots",
            "images",
            "linkage_clusters",
        ):
            self.assertEqual(30, len(manifest[field]))
        self.assertEqual(
            {"njp-video-library": 15, "njp-youtube-official": 15},
            {
                source: sum(
                    value["source_id"] == source for value in manifest["videos"]
                )
                for source in ("njp-video-library", "njp-youtube-official")
            },
        )
        self.assertTrue(
            all(
                "exact_sha256_required" in value["blockers"]
                and "duration_required" in value["blockers"]
                for value in manifest["videos"]
                if value["source_id"] == "njp-video-library"
            )
        )
        self.assertTrue(
            all(
                value["page_count"] is not None
                and "page_count_required" not in value["blockers"]
                for value in manifest["documents"]
            )
        )
        self.assertTrue(
            all(
                value["selection_state"] == "frozen_metadata_only"
                and value["document_candidate_id"] is not None
                and value["page_number"] is not None
                and "visual_stratification_required" in value["blockers"]
                for value in manifest["page_slots"]
            )
        )
        classes = {
            name: sum(
                value["candidate_class"] == name
                for value in manifest["linkage_clusters"]
            )
            for name in (
                "strong_metadata_match",
                "ambiguous_metadata_match",
                "negative_control",
            )
        }
        self.assertEqual(
            {
                "strong_metadata_match": 10,
                "ambiguous_metadata_match": 10,
                "negative_control": 10,
            },
            classes,
        )
        encoded = json.dumps(manifest, sort_keys=True)
        for forbidden in ("catalogue_title", '"title"', "source_url", "/Users/"):
            self.assertNotIn(forbidden, encoded)

    def test_is_deterministic_and_hash_bound(self) -> None:
        first = self.compile()
        second = self.compile()
        self.assertEqual(first, second)

        changed = copy.deepcopy(first)
        changed["videos"][0]["size_bytes"] += 1
        with self.assertRaisesRegex(PilotFreezeError, "binding"):
            validate_pilot_freeze_manifest(changed)

    def test_page_slots_fail_closed_without_page_inventory(self) -> None:
        manifest = self.compile(page_counts=False)

        self.assertEqual(8, len(manifest["source_snapshots"]))
        self.assertTrue(
            all(
                value["selection_state"] == "pending_page_inventory"
                and value["document_candidate_id"] is None
                and value["page_number"] is None
                and "page_inventory_required" in value["blockers"]
                for value in manifest["page_slots"]
            )
        )
        self.assertTrue(
            all(
                value["page_count"] is None
                and "page_count_required" in value["blockers"]
                for value in manifest["documents"]
            )
        )

    def test_refuses_an_underfilled_candidate_universe(self) -> None:
        image_manifest = json.loads(
            self.paths["image_manifest_path"].read_text(encoding="utf-8")
        )
        image_manifest["results"].pop()
        self.paths["image_manifest_path"] = self.write_json(
            "images-short.json", image_manifest
        )

        with self.assertRaisesRegex(PilotFreezeError, "smaller"):
            self.compile()

    def test_write_is_immutable_but_accepts_an_identical_rerun(self) -> None:
        manifest = self.compile()
        output = self.root / "freeze.json"

        write_pilot_freeze(output, manifest)
        original = output.read_bytes()
        write_pilot_freeze(output, manifest)
        self.assertEqual(original, output.read_bytes())

    def test_write_refuses_an_existing_different_regular_file(self) -> None:
        output = self.root / "freeze.json"
        output.write_bytes(b"different\n")

        with self.assertRaisesRegex(PilotFreezeError, "replace"):
            write_pilot_freeze(output, self.compile())
        self.assertEqual(b"different\n", output.read_bytes())

    def test_write_never_follows_an_existing_target_symlink(self) -> None:
        victim = self.root / "victim.json"
        victim.write_bytes(b"victim\n")
        output = self.root / "freeze.json"
        output.symlink_to(victim)

        with self.assertRaisesRegex(PilotFreezeError, "safe regular file"):
            write_pilot_freeze(output, self.compile())
        self.assertTrue(output.is_symlink())
        self.assertEqual(b"victim\n", victim.read_bytes())

    def test_write_never_follows_a_temporary_symlink(self) -> None:
        victim = self.root / "victim.json"
        victim.write_bytes(b"victim\n")
        output = self.root / "freeze.json"
        temporary = self.root / ".freeze.json.pilot-freeze.tmp"
        temporary.symlink_to(victim)

        with self.assertRaisesRegex(PilotFreezeError, "temporary"):
            write_pilot_freeze(output, self.compile())
        self.assertFalse(output.exists())
        self.assertTrue(temporary.is_symlink())
        self.assertEqual(b"victim\n", victim.read_bytes())

    def test_write_does_not_overwrite_a_target_created_during_publish(self) -> None:
        output = self.root / "freeze.json"

        def create_racing_target(*args: object, **kwargs: object) -> None:
            output.write_bytes(b"raced\n")
            raise FileExistsError()

        with mock.patch(
            "performing_fire_corpus.pilot_freeze.os.link",
            side_effect=create_racing_target,
        ):
            with self.assertRaisesRegex(PilotFreezeError, "changed"):
                write_pilot_freeze(output, self.compile())
        self.assertEqual(b"raced\n", output.read_bytes())
        self.assertFalse(
            (self.root / ".freeze.json.pilot-freeze.tmp").exists()
        )


if __name__ == "__main__":
    unittest.main()
