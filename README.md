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

## Status

Rucksack created this repository and installed its reversible agent harness.
The implementation ledger is being generated and reviewed before activation.
