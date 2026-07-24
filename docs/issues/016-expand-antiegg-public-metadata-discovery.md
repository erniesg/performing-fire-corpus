# Expand ANTIEGG public metadata discovery without ingesting prose

depends-on: 011,015

## Goal

Replace the article-body-shaped ANTIEGG discovery assumption with a bounded adapter for currently allowed public sitemap and WordPress metadata endpoints. Inventory broad secondary Korean editorial and Fluxus context while keeping prose and media blocked absent permission or a reviewed lawful basis.

## Acceptance tests

- Write failing synthetic adapter tests before implementation and use only endpoint shapes that a current bounded review identifies as public, robots-allowed, and permitted by applicable terms.
- Discover the ANTIEGG Fluxus article and related catalogue entries through metadata fields such as stable public URL, public platform ID when available, publication or modification time, content type, language, and factual taxonomy labels.
- Do not retain excerpts, rendered or raw prose, HTML, embedded media, author contact information, comments, captions, or response bodies. Treat fields that may contain prose as forbidden even when an API labels them metadata.
- Use stable source and asset IDs derived from canonical source ID plus immutable public identifiers or canonical URLs; title changes do not change IDs.
- Support bounded sitemap and WordPress pagination through the shared checkpoint engine, including declared totals only as timestamped endpoint observations.
- Record endpoint-specific robots, API, terms, copyright, access, and retention decisions before requests. A missing or stale decision blocks that endpoint without blocking other sources.
- Fail closed on `401`, `403`, `429`, login or subscription signals, disallowed redirects, unexpected MIME types, shape drift, response oversize, pagination loops, or a response that cannot be parsed without retaining prose.
- Add only invented source-shaped fixtures and pass the full adapter conformance harness with network entry points disabled.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. The adapter is limited to unauthenticated public metadata. Authentication requests become durable blockers.

## Artifact outputs

- New ANTIEGG metadata adapter under `src/performing_fire_corpus/`
- New synthetic sitemap and WordPress metadata fixtures and tests
- New endpoint policy entries linked to `antiegg-fluxus`
- Updated bounded smoke documentation under `docs/`

## Stop conditions

- Stop if current robots or terms prohibit the endpoint, or if the endpoint requires login, cookies, tokens, or browser state.
- Stop if a response field contains source prose, comments, personal details, HTML bodies, or media that cannot be excluded before persistence.
- Stop on `401`, `403`, repeated `429`, changed shape, oversize, pagination ambiguity, or unclear rights or retention state.

## Human clarification protocol

Ask only if prose retention is required for the next executable research task. Identify the stable asset and intended operation, recommend keeping prose blocked while retaining factual metadata, and request a reviewed permission or lawful-basis decision without copying the prose.

## Recommended response

Use public sitemap and WordPress metadata endpoints only where current robots and platform policy allow them. Keep all ANTIEGG prose and media `blocked` or `pending` and treat the site as secondary context rather than the primary corpus.

## Trade-offs

Excluding prose limits semantic richness but avoids converting public readability into ingestion permission. Sitemap and API metadata may be incomplete or change shape; explicit partial-completeness reporting preserves honesty.

## Free-form response

Optional maintainer notes or alternate metadata-only scope:
