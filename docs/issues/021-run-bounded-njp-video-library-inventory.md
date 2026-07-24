# Run the bounded NJP Video Library inventory

depends-on: 020

## Goal

Execute the dedicated Video Library adapter on a trusted VM with current policy checks and hard budgets. Produce a resumable metadata inventory, duplicate accounting, and honest coverage gaps without requesting video or attachment bytes.

## Acceptance tests

- Run from an exact clean commit with explicit limits for requests, pages, per-response and aggregate bytes, retries, rate, and elapsed time.
- Revalidate current robots, endpoint access, terms, copyright or rights, API or platform behavior, and metadata retention before catalogue requests.
- Demonstrate checkpointed resume when pagination is available and a terminal idempotent rerun; preserve adapter version and policy snapshot with the checkpoint.
- Record unique stable records, language aliases, duplicates, rejected unsafe fields, candidate media relationships, blockers, and the bounded unvisited remainder.
- Do not probe playback, stream manifests, captions, thumbnails, documents, attachment URLs, or signed locators. A catalogue-level `401`, `403`, login, browser-only signal, or denial is a durable blocker.
- Keep ledger, cache, request facts, manifest, and provider details in ignored local state. Commit only sanitized aggregate counts, coverage status, blocker categories, and evidence references.
- Do not combine Video Library counts with NJP main-site, Video Archive, or YouTube counts until the later deduplication and completeness issue defines cross-source semantics.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

After portable validation, run the newly documented bounded Video Library command on a trusted VM only.

## Allowed secrets

None. Any authentication requirement blocks the live proof.

## Artifact outputs

- Ignored Video Library ledger, checkpoints, request facts, and manifest
- Sanitized aggregate Video Library completeness report under `docs/`
- Durable source and candidate-asset blockers
- Evidence manifest tied to the exact commit

## Stop conditions

- Stop on robots denial or ambiguity, `401`, `403`, login, repeated `429`, browser-only access, terms prohibition, unexpected MIME or structure, or a configured bound.
- Stop before content, playback, caption, thumbnail, document, or attachment acquisition.
- Stop if output could expose signed locators, cookies, provider details, personal information, or local paths.

## Human clarification protocol

Ask only if all public catalogue metadata paths are blocked and a different endpoint or browser-authenticated lane is necessary. Recommend preserving the blocker and continuing independent NJP and YouTube work unless explicit trusted-VM authority is granted.

## Recommended response

Accept a bounded partial or blocked inventory as current evidence. Do not authorize media acquisition or reinterpret a public player as a download permission.

## Trade-offs

Avoiding playback requests leaves technical media facts unknown, but prevents metadata inventory from crossing into content acquisition. Separate completeness reporting keeps cross-source overlap unresolved until evidence supports deduplication.

## Free-form response

Optional maintainer notes about the aggregate Video Library outcome:
