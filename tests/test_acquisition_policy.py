from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from performing_fire_corpus.policy import (
    AcquisitionPolicyError,
    require_transfer_rights,
    validate_public_url,
    validate_redirect,
)


class AcquisitionPolicyTests(unittest.TestCase):
    def test_checked_in_public_hosts_are_allowed_and_normalized(self) -> None:
        cases = {
            "https://NJPVIDEO.GGCF.KR/item?id=1": "njpvideo.ggcf.kr",
            "https://njp.ggcf.kr:443/pages/videoarchive": "njp.ggcf.kr",
            "https://www.youtube.com/@NamJunePaikArtCenter/videos": "www.youtube.com",
            "https://antiegg.kr/25502/": "antiegg.kr",
        }
        for url, expected_host in cases.items():
            with self.subTest(url=url):
                validated = validate_public_url(url)
                self.assertEqual(expected_host, validated.hostname)
                self.assertEqual(443, validated.port)

    def test_ordinary_metadata_query_keys_remain_allowed(self) -> None:
        for url in (
            "https://antiegg.kr/wp-json/wp/v2/posts?author=1",
            "https://antiegg.kr/wp-json/wp/v2/posts?page=1&per_page=10",
            "https://njpvideo.ggcf.kr/item?id=synthetic",
        ):
            with self.subTest(url=url):
                self.assertEqual(url, validate_public_url(url).url)

    def test_url_confusion_and_credentials_fail_closed(self) -> None:
        userinfo_url = "https://" + "user:pass@" + "njp.ggcf.kr/"
        signed_query_url = "https://njp.ggcf.kr/?" + "signature=synthetic-secret"
        credential_query_url = (
            "https://njp.ggcf.kr/?" + "X-Amz-Credential=synthetic-account"
        )
        credential_alias_urls = (
            "https://njp.ggcf.kr/?session=synthetic",
            "https://njp.ggcf.kr/?auth=synthetic",
            "https://njp.ggcf.kr/?accessToken=synthetic",
        )
        rejected = (
            "http://njp.ggcf.kr/",
            userinfo_url,
            "https://njp.ggcf.kr/#payload",
            "https://njp.ggcf.kr:444/",
            "https://njp.ggcf.kr.evil.invalid/",
            "https://njp.ggcf.kr./",
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://njp.ggcf.kr\\@evil.invalid/",
            "https://njp.ggcf.kr/%0aheader",
            "https://www.googleapis.com/drive/v3/files/arbitrary",
            signed_query_url,
            credential_query_url,
            *credential_alias_urls,
        )
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(AcquisitionPolicyError):
                    validate_public_url(url)

    def test_explicit_allowlist_cannot_admit_non_public_ip_addresses(self) -> None:
        cases = (
            ("127.0.0.1", "https://127.0.0.1/"),
            ("10.0.0.1", "https://10.0.0.1/"),
            ("169.254.1.1", "https://169.254.1.1/"),
            ("192.0.2.1", "https://192.0.2.1/"),
            ("::1", "https://[::1]/"),
        )
        for hostname, url in cases:
            with self.subTest(hostname=hostname):
                with self.assertRaises(AcquisitionPolicyError) as caught:
                    validate_public_url(url, allowed_hosts={hostname})
                self.assertEqual("non_public_host", caught.exception.code)

    def test_redirects_are_revalidated_before_following(self) -> None:
        validated = validate_redirect(
            "https://njp.ggcf.kr/pages/videoarchive",
            "https://njpvideo.ggcf.kr/synthetic/item",
        )
        self.assertEqual("njpvideo.ggcf.kr", validated.hostname)
        with self.assertRaises(AcquisitionPolicyError):
            validate_redirect(
                "https://njp.ggcf.kr/pages/videoarchive",
                "https://login.invalid/session",
            )

    def test_transfer_requires_a_complete_matching_approval(self) -> None:
        approved = {
            "schema_version": 1,
            "record_type": "rights",
            "rights_id": "rights_synthetic",
            "asset_id": "asset_synthetic",
            "state": "approved",
            "decision_reason": "Synthetic approval.",
            "decision_at": "2026-01-01T00:00:00Z",
        }
        require_transfer_rights("asset_synthetic", approved)

        for rights in (
            None,
            {},
            {"asset_id": "asset_synthetic", "state": "pending"},
            {"asset_id": "asset_synthetic", "state": "blocked"},
            {"asset_id": "asset_other", "state": "approved"},
            {"asset_id": "asset_synthetic", "state": "approved"},
            {
                **approved,
                "decision_at": "not-a-timestamp",
            },
            {
                **approved,
                "unexpected": "field",
            },
        ):
            with self.subTest(rights=rights):
                with self.assertRaises(AcquisitionPolicyError) as caught:
                    require_transfer_rights("asset_synthetic", rights)
                self.assertEqual("rights_not_approved", caught.exception.code)
                self.assertNotIn("None", caught.exception.reason)

    def test_policy_errors_do_not_echo_rejected_inputs(self) -> None:
        synthetic_secret = "synthetic-query-value"
        token_query = "token"
        with self.assertRaises(AcquisitionPolicyError) as caught:
            validate_public_url(
                f"https://njp.ggcf.kr/?{token_query}={synthetic_secret}"
            )
        self.assertNotIn(synthetic_secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
