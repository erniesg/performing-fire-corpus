# Align product-readiness docs, tests, and demo claims

depends-on: 034,035,037

## Goal

Reconcile `README.md`, `docs/PROJECT_BRIEF.md`, runbooks, any future PRD or demo documentation, current CLI commands, scripts, and tests. Make the repository’s readiness claims falsifiable and prevent a local or loopback reference surface from being described as a hosted operator product.

## Acceptance tests

- Build a checked-in readiness matrix mapping each claimed capability to its current CLI or surface, implementation path, passing test, evidence lane, live proof or durable blocker, and next issue.
- Distinguish source-universe inventory, selected rich corpus, object proof, production ingestion, derived processing, search, score-generation export, project-native lifecycle, hosted UI, and deployment. Do not collapse an implemented contract into a live-proven service.
- Update the README status from the stale “implementation ledger is being generated” wording to exact current facts supported by tests and evidence.
- State that no hosted operator UI exists unless a deployed, authenticated, evidence-backed surface actually exists. Label any form or API bound to loopback as a local reference only.
- Add a runtime preflight or documentation that consistently selects Python 3.11+ before validation. An unsupported `python3` must fail with a clear version message rather than producing misleading partial results.
- Ensure every documented command exists or is explicitly labeled future or post-implementation, uses repository-relative paths, and identifies its lane, secrets by name only, live side effects, and stop conditions.
- Add public-contract tests for source boundaries, no bulk mirror claim, unverified-count language, held GitHub Actions, no model racing, no private material, no hosted-UI overclaim, and later-source approval.
- If no PRD or demo doc exists, record that absence in the matrix rather than inventing one. If added later, require it to reference the same tested readiness states.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Product-readiness documentation and tests are public and offline.

## Artifact outputs

- Updated `README.md` and relevant existing runbooks under `docs/`
- New readiness matrix under `docs/`
- New runtime-preflight and public-contract tests
- Sanitized evidence references for every live-proven claim

## Stop conditions

- Stop if a capability has no observable test, evidence, blocker, or explicitly labeled future issue.
- Stop if documentation claims a hosted UI, production corpus, complete source count, live worker, or successful held CI job without evidence.
- Stop if validation depends on unsupported Python, private proposal material, local absolute paths, credentials, provider details, or generated live state.
- Stop before adding a demo that exposes source content or private records.

## Human clarification protocol

Ask only if two product-status labels would materially change what a user is invited to do next and repository evidence cannot distinguish them. Recommend the more conservative status and provide the exact missing proof for promotion.

## Recommended response

Describe the current product as a tested rights-aware corpus pipeline with bounded source proofs and explicit held gates. Use “local reference” for loopback surfaces and “planned” for unimplemented or unproven hosted behavior.

## Trade-offs

Conservative readiness language can make progress look slower, but prevents users from relying on absent services. Runtime preflight adds a small setup step and makes validation results comparable.

## Free-form response

Optional maintainer notes or alternate evidence-backed status wording:
