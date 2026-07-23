from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.redaction import REDACTED, sanitize


class RedactionTests(unittest.TestCase):
    def test_sensitive_fields_bodies_accounts_and_paths_are_redacted(self) -> None:
        local_path = "/" + "home/synthetic-user/private/file.txt"
        signed_url = "https://njp.ggcf.kr/?" + "sig=synthetic-signature&item=1"
        redacted_signed_url = "https://njp.ggcf.kr/?" + "sig=%5BREDACTED%5D&item=1"
        value = {
            "Authorization": "Bearer synthetic-auth",
            "Cookie": "session=synthetic-cookie",
            "response_body": "synthetic body",
            "account_id": "synthetic-account",
            "message": f"failed at {local_path}",
            "public": "safe status",
            "url": signed_url,
        }
        cleaned = sanitize(value)
        rendered = repr(cleaned)

        for forbidden in (
            "synthetic-auth",
            "synthetic-cookie",
            "synthetic body",
            "synthetic-account",
            "synthetic-user",
            "synthetic-signature",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual("safe status", cleaned["public"])
        self.assertEqual(REDACTED, cleaned["Authorization"])
        self.assertEqual(redacted_signed_url, cleaned["url"])

    def test_known_environment_values_are_removed_from_nested_exceptions(self) -> None:
        environment = {
            "SYNTHETIC_API_TOKEN": "synthetic-env-secret",
            "PATH": os.environ.get("PATH", ""),
        }
        error = RuntimeError(
            "request failed with synthetic-env-secret at /tmp/private/cache"
        )
        cleaned = sanitize({"error": error}, environ=environment)
        rendered = repr(cleaned)
        self.assertNotIn("synthetic-env-secret", rendered)
        self.assertNotIn("/tmp/private/cache", rendered)
        self.assertNotIn(os.environ.get("PATH", ""), rendered)

    def test_redaction_is_recursive_and_does_not_store_binary_bodies(self) -> None:
        cleaned = sanitize(
            {
                "events": [
                    {"headers": {"set-cookie": "synthetic-cookie"}},
                    {"body": b"synthetic bytes"},
                ]
            }
        )
        self.assertEqual(REDACTED, cleaned["events"][0]["headers"]["set-cookie"])
        self.assertEqual(REDACTED, cleaned["events"][1]["body"])


if __name__ == "__main__":
    unittest.main()
