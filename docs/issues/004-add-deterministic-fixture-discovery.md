# Add deterministic fixture-only discovery and sanitized manifests

depends-on: 001,002,003

## Goal

Add the first end-to-end offline command: ingest a fully synthetic source fixture, validate and upsert its metadata, schedule policy-safe jobs, and emit a byte-for-byte deterministic sanitized manifest without downloading corpus content.

## Acceptance tests

- Add a `discover-fixture` CLI command that accepts only checked-in synthetic JSON metadata and an explicitly selected temporary ledger and output path.
- Produce stable source and asset identifiers, normalized public URLs, `pending` rights records, and metadata-only jobs without creating transfer jobs.
- Emit a versioned manifest containing source structure, factual synthetic metadata, record counts, state counts, and sanitized evidence references; exclude timestamps that vary between identical runs or inject them explicitly for tests.
- Running the same fixture twice yields identical manifest bytes and leaves record and job counts unchanged.
- Interrupted execution can restart from the durable ledger and finish without duplicating records or jobs.
- Reject fixture fields containing response bodies, article prose, media encodings, captions, transcripts, embeddings, credentials, personal information, or local absolute paths.
- Add red/green CLI and unit tests for determinism, idempotency, restart, malformed fixture data, privacy rejection, and zero network calls.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

None. The command and its tests are strictly offline.

## Artifact outputs

- Fixture discovery command and parser under `src/performing_fire_corpus/`
- Minimal synthetic fixtures under `tests/fixtures/`
- Deterministic sanitized manifest examples asserted by tests

## Stop conditions

- Stop if fixture realism requires copying any public or private source prose, HTML, media, caption, transcript, or document.
- Stop if an identical rerun changes manifest bytes or duplicates ledger rows.
- Stop if the command attempts network access.

## Human clarification protocol

Ask only if a proposed fixture field cannot be made synthetic while still testing a required schema behavior. Recommend replacing it with a minimal invented scalar value and leave room for a different fixture design.

## Recommended response

Use small invented records on the already-approved public hostnames, with clearly synthetic titles and hashes. Keep expected manifests small enough for direct review.

## Trade-offs

Synthetic fixtures cannot prove a live source shape, but they establish deterministic parsing, state, privacy, and resume behavior before any external request occurs.

## Free-form response

Optional maintainer notes or an alternate fixture decision:

