# Build the Python CLI and versioned record schemas

## Goal

Create the minimal Python 3.11 package and command-line entry point, plus checked-in versioned schemas for source, asset, rights, job, lease, object, and evidence records. The schema layer is the single public contract for later ledger, discovery, storage, and worker tasks.

## Acceptance tests

- Add `pyproject.toml`, a `src/performing_fire_corpus` package, and a deterministic `performing-fire-corpus --help` entry point that succeeds without network access or secrets.
- Check in versioned schemas under `schemas/v1/` for source, asset, rights, job, lease, object, and evidence records; each schema rejects unknown fields and identifies its schema version.
- Represent ingest rights only as `pending`, `approved`, or `blocked`, with a required sanitized reason and decision timestamp for non-pending decisions.
- Define stable identifiers independently of machine-local paths. Asset and object records use public source identifiers, public URLs, object keys, byte sizes, media types, and lowercase SHA-256 values where applicable.
- Job and lease schemas support capability names, retry state, checkpoints, lease expiry, and R2 object keys without embedding media, credentials, or local paths.
- Add synthetic unit fixtures and red/green tests covering one valid record of every type plus missing-required-field, unknown-field, invalid-enum, malformed-hash, and local-path rejection cases.
- Keep runtime dependencies minimal and version-bounded; schema validation behaves identically across repeated runs.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

None. Schema and CLI tests must run offline and must not read the environment for credentials.

## Artifact outputs

- `pyproject.toml`
- `src/performing_fire_corpus/`
- `schemas/v1/`
- Synthetic schema fixtures and unit tests under `tests/`

## Stop conditions

- Stop if a proposed field requires personal information, private-source prose, source content, credentials, signed URLs, account identifiers, or machine-local paths.
- Stop if a schema change would make `pending`, `approved`, and `blocked` ambiguous or permit ingest without an explicit rights state.
- Stop if implementation requires network access or a secret.

## Human clarification protocol

Ask only if two required public record fields cannot be represented without storing forbidden material, or if a schema-versioning decision would irreversibly constrain the next issue. Describe the conflicting fields, recommend the smallest privacy-safe representation, and leave room for a different response.

## Recommended response

Use version `1` JSON Schemas with strict objects and privacy-safe scalar metadata. Defer optional source-specific fields to a bounded metadata map whose keys and values are validated and sanitized.

## Trade-offs

Strict versioned schemas require explicit migrations later, but they prevent silent contract drift. A small validation dependency adds packaging work, but is safer and more reviewable than maintaining a partial custom validator.

## Free-form response

Optional maintainer notes or an alternate schema decision:

