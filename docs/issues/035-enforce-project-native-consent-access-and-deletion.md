# Enforce project-native consent, access, retention, and deletion

depends-on: 013,027,032

## Goal

Implement the future project-native data lifecycle for artist submissions, visitor inputs, generated scores, performer annotations or choices, and visual-system state or history. Build schemas and enforcement with synthetic data only; do not fabricate, solicit, or ingest real private material.

## Acceptance tests

- Implement strict versioned records for pseudonymous subject or contribution ID, data class, purpose, consent version and state, confidentiality, allowed audiences and uses, provenance, retention expiry, withdrawal, export, deletion, legal hold, and derived-object relationships.
- Minimize collection by data class. Personal names, contact details, contributor duties, private proposal or meeting text, and free-form comments are excluded unless a later approved product requirement and privacy review explicitly add them.
- Require affirmative current consent and purpose compatibility before intake, transformation, indexing, score generation, or retrieval. Silence, prechecked defaults, or broad future-use language fail closed.
- Implement access checks, subject export by stable pseudonymous ID, withdrawal, exact raw and derived deletion work, index removal, cache invalidation, and content-free audit tombstones.
- Make generated scores and visual-system history inherit the most restrictive input consent and rights when they can reveal or reproduce an input; record independent system provenance.
- Define retention defaults and deletion SLAs by data class, with no indefinite retention by default. Legal holds require separate authority, scope, and expiry review.
- Add synthetic tests for consent grant, incompatible purpose, expiry, withdrawal during processing, export, derived deletion, index deletion, duplicate submissions, and unauthorized audience queries.
- Ensure Git, issues, fixtures, evidence, logs, and dynamic transit contain no real private material or identifiable person data.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Authentication, encryption keys, and real private records belong to a later approved deployment and intake issue.

## Artifact outputs

- New project-native lifecycle schemas under `schemas/`
- New consent, access, export, retention, and deletion enforcement under `src/performing_fire_corpus/`
- New synthetic lifecycle and privacy tests
- New public project-native data contract under `docs/`

## Stop conditions

- Stop if implementation requires real names, contact details, proposal material, comments, private content, credentials, or authentication data.
- Stop if consent is not affirmative, specific, current, withdrawable, and linked to a deletion path.
- Stop if a derived object, index entry, score export, or cache can survive withdrawal without reviewed legal-hold authority.
- Stop if a hosted collection surface or production access control is implied but not implemented and tested.

## Human clarification protocol

Ask only if one data class cannot have a safe retention or deletion default and that choice blocks the contract. Recommend the shortest practical retention and full derivative deletion, and leave room for a reviewed lawful alternative.

## Recommended response

Use synthetic records now, explicit opt-in consent later, pseudonymous IDs, least-privilege audiences, short class-specific retention, user export and withdrawal, and deletion propagation through raw, derived, index, and score artifacts.

## Trade-offs

Strong withdrawal and deletion reduce long-term reproducibility and analytical continuity, but are appropriate for participant-originated material. Pseudonymous IDs limit personalization while reducing unnecessary exposure.

## Free-form response

Optional maintainer notes or alternate project-native privacy default:
