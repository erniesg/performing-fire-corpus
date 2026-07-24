from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from performing_fire_corpus.registry import (
    RegistryError,
    UnknownSourceError,
    canonical_registry_bytes,
    canonicalize_public_url,
    load_registry,
    normalize_source_id,
    require_source,
    validate_registry,
    validate_registry_migration,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "source-registry.v1.json"
SCHEMA_PATH = ROOT / "schemas" / "v1" / "source-registry.json"


class SourceRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_registry(REGISTRY_PATH)

    def test_checked_in_registry_is_strict_valid_and_canonical(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(self.registry)
        self.assertEqual(
            REGISTRY_PATH.read_bytes(),
            canonical_registry_bytes(self.registry),
        )
        self.assertEqual(self.registry, load_registry(REGISTRY_PATH))

    def test_registry_covers_the_approved_source_universe_without_counts(self) -> None:
        source_ids = {item["source_id"] for item in self.registry["sources"]}
        self.assertEqual(
            {
                "antiegg-fluxus",
                "njp-center-main",
                "njp-center-video-archive",
                "njp-video-library",
                "njp-youtube-official",
                "project-native-artist-submissions",
                "project-native-generated-scores",
                "project-native-performer-annotations",
                "project-native-visitor-inputs",
                "project-native-visual-system-state",
            },
            source_ids,
        )
        self.assertNotIn("count", json.dumps(self.registry, sort_keys=True).lower())

    def test_njp_and_antiegg_endpoint_boundaries_are_preserved(self) -> None:
        main = require_source(self.registry, "njp-center-main")
        archive = require_source(self.registry, "njp-center-video-archive")
        library = require_source(self.registry, "njp-video-library")
        self.assertEqual(main["host_policy_id"], archive["host_policy_id"])
        self.assertNotEqual(main["host_policy_id"], library["host_policy_id"])

        antiegg = require_source(self.registry, "antiegg-fluxus")
        endpoint_kinds = {item["endpoint_kind"] for item in antiegg["endpoints"]}
        self.assertEqual({"article", "metadata_api", "sitemap"}, endpoint_kinds)
        self.assertGreaterEqual(
            sum(
                item["endpoint_kind"] == "metadata_api"
                for item in antiegg["endpoints"]
            ),
            2,
        )

    def test_youtube_channel_id_remains_unverified(self) -> None:
        youtube = require_source(self.registry, "njp-youtube-official")
        handle = next(
            item
            for item in youtube["endpoints"]
            if item["endpoint_id"] == "njp-youtube-handle"
        )
        self.assertEqual("channel_handle", handle["endpoint_kind"])
        self.assertEqual("unverified", handle["verification_state"])
        self.assertNotIn("platform_identifier", handle)

    def test_project_native_families_define_no_private_records(self) -> None:
        project_sources = [
            item
            for item in self.registry["sources"]
            if item["source_class"] == "project_native"
        ]
        self.assertEqual(5, len(project_sources))
        for item in project_sources:
            self.assertIsNone(item["canonical_url"])
            self.assertEqual([], item["aliases"])
            self.assertEqual([], item["endpoints"])

    def test_normalization_and_url_canonicalization_are_deterministic(self) -> None:
        self.assertEqual("njp-center-main", normalize_source_id("NJP Center Main"))
        self.assertEqual(
            "https://njp.ggcf.kr/pages/videoarchive",
            canonicalize_public_url(
                "HTTPS://NJP.GGCF.KR:443/pages/videoarchive/"
            ),
        )
        for unsafe in (
            "https://example.test/source",
            "../source",
            "source?token=value",
            "source-678",
            str(Path("/", "Users", "example", "source")),
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(RegistryError):
                normalize_source_id(unsafe)
        for unsafe_url in (
            "http://example.test/",
            "https://user:password@example.test/",
            "https://example.test/path?token=value",
            "https://example.test/path#fragment",
            "https://127.0.0.1/",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/",
            "https://example.com./path",
            "https://antiegg.kr/path%0d%0aheader",
            "https://antiegg.kr/path%09tab",
        ):
            with self.subTest(unsafe_url=unsafe_url), self.assertRaises(
                RegistryError
            ):
                canonicalize_public_url(unsafe_url)

    def test_alias_and_endpoint_collisions_fail_closed(self) -> None:
        alias_collision = copy.deepcopy(self.registry)
        alias_collision["sources"][1]["aliases"] = ["antiegg-fluxus"]
        with self.assertRaises(RegistryError):
            validate_registry(alias_collision)

        endpoint_collision = copy.deepcopy(self.registry)
        endpoint_collision["sources"][0]["endpoints"][1]["endpoint_id"] = (
            endpoint_collision["sources"][0]["endpoints"][0]["endpoint_id"]
        )
        with self.assertRaises(RegistryError):
            validate_registry(endpoint_collision)

    def test_unknown_sources_and_silent_id_migrations_fail_closed(self) -> None:
        with self.assertRaises(UnknownSourceError):
            require_source(self.registry, "later-unreviewed-source")
        with self.assertRaises(UnknownSourceError):
            require_source(self.registry, "NJP Center Main")

        removed = copy.deepcopy(self.registry)
        removed["sources"] = removed["sources"][1:]
        with self.assertRaises(RegistryError):
            validate_registry_migration(self.registry, removed)

        rewritten = copy.deepcopy(self.registry)
        rewritten["sources"][0]["source_id"] = "antiegg-editorial"
        with self.assertRaises(RegistryError):
            validate_registry_migration(self.registry, rewritten)

        alias_rebound = copy.deepcopy(self.registry)
        alias = alias_rebound["sources"][0]["aliases"].pop()
        alias_rebound["sources"][1]["aliases"].append(alias)
        alias_rebound["sources"][1]["aliases"].sort()
        with self.assertRaises(RegistryError):
            validate_registry_migration(self.registry, alias_rebound)

        endpoint_rebound = copy.deepcopy(self.registry)
        endpoint = endpoint_rebound["sources"][0]["endpoints"][0]
        endpoint["endpoint_kind"] = "homepage"
        endpoint["public_url"] = "https://njp.ggcf.kr/"
        with self.assertRaises(RegistryError):
            validate_registry_migration(self.registry, endpoint_rebound)


if __name__ == "__main__":
    unittest.main()
