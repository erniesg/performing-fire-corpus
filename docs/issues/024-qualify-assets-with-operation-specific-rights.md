# Qualify assets with operation-specific rights and access decisions

depends-on: 013,014

## Goal

Turn the metadata-only source universe into an auditable candidate-asset ledger. Decide eligibility separately for metadata retention, download, raw storage, OCR, transcription, video understanding, indexing, score-generation use, and public retrieval; never infer content rights from public visibility.

## Acceptance tests

- Add a qualification workflow that joins stable asset IDs to current source policy, asset-specific copyright or permission evidence, access status, expected host and exact URL, MIME, byte bound, retention, deletion, derivative, and retrieval decisions.
- Require a reviewed factual basis, authority class, decision timestamp, expiry or review trigger, and sanitized evidence reference for each `approved` operation. Missing fields leave only that operation `pending` or `blocked`.
- Keep YouTube captions and media ineligible unless current platform terms and an explicit asset-specific rights record permit the exact operation. Official metadata approval alone is insufficient.
- Keep NJP attachment candidates ineligible after `403`, login, signed or expired URL, or unclear download permission. Never retry a blocked URL with old tokens, cookies, referer changes, or browser state.
- Keep ANTIEGG prose and media ineligible absent permission or a clearly reviewed lawful basis. Editorial metadata eligibility never implies prose ingestion.
- Detect conflicting decisions, expired rights, changed URLs, revoked permission, duplicate candidates, and retention-policy mismatch before creating transfer work.
- Emit only stable IDs and exact object keys into downstream jobs; never source bytes, signed locators, credentials, or machine-local paths.
- Add tests for every source class and operation, including expiry, revocation, `403`, platform prohibition, conflicting evidence, and an eligible synthetic public object.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Qualification consumes sanitized reviewed records, not credentials or private content.

## Artifact outputs

- New operation-specific qualification schema under `schemas/`
- New qualification evaluator and ledger queries under `src/performing_fire_corpus/`
- New synthetic per-source rights and blocker tests
- New sanitized candidate coverage report under `docs/`

## Stop conditions

- Stop if public availability is the only asserted rights basis.
- Stop if an approval omits its exact operation, authority, evidence time, expiry or review trigger, MIME, byte bound, and retention decision.
- Stop on `401`, `403`, login, subscription, signed or expired locator, platform prohibition, conflicting rights, or revocation.
- Stop if issue text or evidence would contain source prose, media, captions, private correspondence, personal details, or secret values.

## Human clarification protocol

Ask only when one identified candidate is the next executable acquisition and its exact operation-specific authority is missing. Provide the stable asset ID and sanitized gap, recommend leaving it blocked, and offer an explicit approve or decline field without requesting protected material.

## Recommended response

Approve only assets with a current, documented download and downstream-use basis plus narrow MIME, byte, retention, and retrieval limits. Keep all others discoverable as metadata with durable blocker reasons.

## Trade-offs

Operation-specific decisions are slower than one broad rights flag but prevent a download permission from silently authorizing derivatives or publication. A smaller eligible corpus is preferable to a legally ambiguous mirror.

## Free-form response

Optional maintainer notes or alternate qualification rule:
