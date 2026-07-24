# Add the NJP Video Library metadata adapter

depends-on: 015

## Goal

Implement a dedicated bounded metadata adapter for the Nam June Paik Art Center Video Library host. Preserve its distinct provenance, inventory factual records only, and keep video, captions, thumbnails, documents, and attachment bytes blocked until asset-specific rights and platform decisions permit them.

## Acceptance tests

- Start with a current bounded endpoint review and encode no prior inventory count, API behavior, catalogue structure, or media availability as fact.
- Use the canonical `njp-video-library` source ID and derive stable asset IDs from reviewed public record identifiers or canonical URLs, never from titles, page positions, or machine paths.
- Parse only approved factual metadata such as public record ID, canonical URL, record class, language, date, duration when explicitly published as a fact, and content-neutral classifications.
- Represent playable or downloadable media, captions, thumbnails, documents, and attachment URLs only as candidate relationships with separate pending or blocked rights records. The adapter makes no content requests.
- Support bounded pagination and resumable checkpoints through the shared discovery engine, and detect repeated pages, mutable ordering, duplicate aliases, and changed shape.
- Record current robots, API or page mechanism, terms, copyright, access-control, and retention decisions before requests. Any denial or ambiguity is durable and source-specific.
- Treat `401`, `403`, login, rate exhaustion, unexpected MIME, browser-only behavior, or an expiring or signed locator as blockers without attempting alternate tokens, cookies, referers, or browser automation.
- Pass the offline conformance suite using invented bilingual and pagination fixtures with all network entry points disabled.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. The adapter is public and metadata-only; required authentication blocks the endpoint.

## Artifact outputs

- New Video Library adapter under `src/performing_fire_corpus/`
- New source policy and endpoint entries
- New synthetic bilingual, pagination, duplicate, and blocker tests
- Updated adapter documentation under `docs/`

## Stop conditions

- Stop on robots denial or ambiguity, access control, login, rate exhaustion, terms prohibition, unclear retention, changed structure, signed locators, or configured bounds.
- Stop before requesting or retaining video, audio, image, document, caption, transcript, thumbnail, or source prose.
- Stop if stable identity cannot be established without unreviewed platform internals.

## Human clarification protocol

Ask only if a media or attachment request is the next executable task and the exact asset rights and access decision is missing. Recommend keeping it blocked and completing metadata inventory first.

## Recommended response

Treat the Video Library as its own catalogue with metadata-only eligibility. Preserve media locators as blocked candidates until reviewed rights and access records explicitly authorize one bounded operation.

## Trade-offs

Metadata-only discovery cannot validate playable media or content quality, but it establishes the known-source universe without becoming a bulk mirror. Strict identity rules may leave some records unresolved rather than incorrectly merged.

## Free-form response

Optional maintainer notes or alternate factual metadata scope:
