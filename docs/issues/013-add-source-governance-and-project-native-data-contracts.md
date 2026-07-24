# Add source governance and project-native data contracts

depends-on: 012

## Goal

Define fail-closed, reviewable contracts for per-source robots, API, platform-terms, copyright and rights, access-control, retention, and lawful-use decisions. Define privacy, consent, access, deletion, and retention rules for future project-native material now, without fabricating or ingesting private data.

## Acceptance tests

- Add strict versioned governance records keyed by canonical source ID and, where needed, endpoint or asset ID. Separate observed facts, reviewed decisions, evidence timestamps, reviewer authority class, expiry, and next safe action.
- Require explicit states for robots, API availability, platform terms, copyright or lawful basis, authentication, acquisition eligibility, derivative eligibility, search visibility, retention, and deletion. Unknown, stale, conflicting, or incomplete states fail closed.
- Populate one record for every source in the canonical registry. Initial values may be `pending` or `blocked`; no unverified website or platform claim is promoted to fact.
- Make robots denial, `401`, `403`, rate exhaustion, login or subscription requirement, platform prohibition, unclear rights, and expired evidence durable blockers that cannot be bypassed by cookies, tokens, old URLs, or alternate clients.
- Require separate permissions for metadata inventory, prose or caption retention, media acquisition, derived processing, indexing, and public retrieval. Metadata permission never implies content permission.
- Define future project-native contracts for purpose, consent version, confidentiality class, allowed viewers, allowed uses, redaction, withdrawal, export, deletion SLA, legal hold, retention expiry, derived-data treatment, and audit events.
- Prohibit personal names, contact details, contributor duties, proposal or meeting material, raw comments, credentials, and private content from checked-in fixtures or evidence. Tests use invented principals and content-free IDs.
- Add transition and expiry tests proving that revoked consent or rights removes future eligibility, creates reindex or deletion work, and preserves only the minimum sanitized audit fact.
- Document that no project-native ingestion can start until a later reviewed intake issue supplies an approved notice, authority, and deletion owner.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None for contract implementation and tests. Future private-source credentials and private content are explicitly outside this issue.

## Artifact outputs

- New governance, consent, retention, and deletion schemas under `schemas/`
- New source-policy registry linked to canonical source IDs
- New policy evaluation and expiry logic under `src/performing_fire_corpus/`
- New synthetic fail-closed tests and public governance documentation under `docs/`

## Stop conditions

- Stop if a decision lacks authority, evidence time, expiry or review trigger, affected operation, and exact next safe action.
- Stop if metadata access is treated as acquisition or publication permission.
- Stop if a project-native contract would retain withdrawn content or derivatives without a reviewed legal-hold basis.
- Stop if implementation requires real private material, personal details, credentials, account identifiers, or confidential proposal content.

## Human clarification protocol

Ask only when a specific acquisition or future project-native operation is the next executable task and its required authority is absent. Name the stable source or data class, the missing decision field, the safe blocked state, and the least permissive recommended answer. Never request private content or secret values.

## Recommended response

Adopt operation-specific, expiring decisions and keep every unreviewed content operation blocked. For project-native data, default to explicit opt-in consent, least-privilege access, user-accessible withdrawal, prompt deletion of content and derivatives, and retention only for sanitized non-identifying audit events.

## Trade-offs

Fine-grained decisions create more review work but prevent metadata access from silently authorizing content use. Deletion propagation can reduce reproducibility; immutable, content-free tombstone events preserve accountability without retaining withdrawn material.

## Free-form response

Optional maintainer notes or alternate privacy-safe governance rule:
