# Performing Fire Corpus

A public, privacy-safe, rights-aware pipeline for inventorying research sources
about technology, performance, Nam June Paik, and Fluxus.

The repository starts metadata-first. It does not mirror source sites or commit
source documents, PDFs, article text, images, audio, video, captions,
transcripts, credentials, personal information, or private project notes.

The initial goal is a deterministic Python CLI and durable job ledger that can
discover public metadata, record rights decisions, resume interrupted work, and
upload only explicitly approved bounded assets to Cloudflare R2. Network
acquisition runs on a trusted VM. Later OCR, transcription, and video
understanding may run on an outbound-paired trusted laptop, with R2 object keys
as the handoff rather than machine-local paths.

Read [the public project brief](docs/PROJECT_BRIEF.md) for scope, source URLs,
transit lanes, and the first usable slice.

The shared [bounded discovery contract](docs/bounded-discovery.md) defines
governance binding, hard limits, atomic resume, and evidence-scoped
completeness for later source adapters.

The [safe observability and evidence contract](docs/safe-observability-and-evidence.md)
and the [operator gate contract](docs/operator-gates.md) apply across every lane:
allowlisted content-free records, exact-head evidence, held-not-passed CI, and
human blockers that always carry an exact next safe action and resumable state.

The [product readiness matrix](docs/product-readiness-matrix.md) is the
falsifiable status record. Every capability claim below is a row there, with its
current CLI surface, implementation path, passing test, evidence lane, and
either a sanitized live proof or a durable blocker.

## Status

This is a tested rights-aware corpus pipeline with one bounded source proof and
explicit held gates. It is not a hosted operator product.

Implemented and covered by passing offline tests: the Python CLI and durable
SQLite ledger, versioned record schemas, rights qualification, bounded discovery
and source governance, the ANTIEGG, NJP Art Center, NJP Video Library, and
official YouTube metadata adapters, the offline adapter-conformance harness,
R2 readiness and transfer boundaries, the full-corpus object contract,
rights-aware derived-media and worker contracts, the provenance-aware search
index, local rights-filtered query, score-generation export, the project-native
lifecycle contract, and safe observability, evidence, and operator gates. Those
tests run against checked-in synthetic fixtures and fake clients.

Live-proven: one bounded metadata-only run against the public ANTIEGG article
endpoint, recorded in [the readiness proof](docs/metadata-readiness-proof.md)
and explicitly marked historical. Nothing else in this repository has been run
against a live source or service.

Not yet real, and not claimed to be:

- No hosted operator UI exists. No HTTP server, web form, or loopback API is
  implemented. The search surface is a local reference over local artifacts.
- Nothing is deployed. The deploy workflow is held, grants no token, and exits
  non-zero by design.
- No object has been transferred to R2, no worker has processed real media, and
  no index has been built from a real corpus.
- Source counts are unverified hypotheses. The repository does not mirror
  source sites in bulk and claims no complete source count.
- Adding a source outside the reviewed public universe is a held human decision.
- A refused or skipped GitHub Actions job is evidence that a gate held, not
  evidence that a capability works.

There is no PRD or demo document in this repository; that absence is recorded
in the matrix rather than filled with an invented one.

## Validating

`pyproject.toml` declares `requires-python = ">=3.11"`. Select a supported
interpreter first so results are comparable and an unsupported `python3` fails
with an exact version message instead of a partial run:

```bash
sh scripts/preflight-python
sh scripts/preflight-python -m unittest discover -s tests
scripts/agent-evidence
```

See [`.agent/verify.md`](.agent/verify.md) for the exit taxonomy and
[the readiness matrix](docs/product-readiness-matrix.md#command-surface) for
every documented command with its lane, secret names, live side effects, and
stop conditions.
