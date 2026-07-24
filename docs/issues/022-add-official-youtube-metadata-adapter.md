# Add the official YouTube metadata adapter

depends-on: 015

## Goal

Implement an offline-tested adapter for official Nam June Paik Art Center YouTube metadata, using the reviewed official platform mechanism before considering captions or media. Keep the live credential, quota, terms, caption, and media decisions outside the portable implementation.

## Acceptance tests

- Resolve the public handle to a stable channel identifier only through a reviewed official metadata response; persist the handle as a locator and fail closed on channel ambiguity.
- Inventory channel and video factual metadata such as stable platform IDs, canonical watch URLs, publish times, duration, language indicators, and public status only when the current governance record allows each field.
- Use the platform’s official metadata API or another explicitly reviewed official mechanism. Do not scrape rendered pages, invoke downloaders, use browser cookies, or imitate a logged-in client.
- Implement stable resumable pagination and quota-unit accounting within the shared request, page, retry, byte, and elapsed budgets. Checkpoint opaque page tokens only in sanitized form when platform terms permit retention.
- Record deleted, private, members-only, region-blocked, age-gated, live, or otherwise unavailable items as sanitized status observations or blockers without attempting access.
- Treat captions, transcripts, thumbnails, audio, and video as separate assets with `pending` or `blocked` rights. The adapter makes no caption-track or media request.
- Fail closed on quota exhaustion, `401`, `403`, terms conflict, channel mismatch, pagination-token ambiguity, changed response shape, or a credential request outside the trusted VM.
- Pass the shared conformance suite with invented API payload shapes and explicit tests that patch network, browser, and media-downloader entry points.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

`YOUTUBE_DATA_API_KEY` may be read only during a separately approved trusted-VM metadata proof. Portable implementation and tests use no secret and never inspect the environment for it.

## Artifact outputs

- New official YouTube metadata adapter under `src/performing_fire_corpus/`
- New quota-aware request and pagination tests with invented payloads
- New YouTube platform policy entry and held live-proof documentation
- New candidate caption and media relationship records that default closed

## Stop conditions

- Stop if implementation would scrape a rendered page, use cookies or account sessions, call a media downloader, or access captions or media.
- Stop if the official mechanism, quota terms, metadata retention, or credential scope is unreviewed.
- Stop if a page token, error, or artifact could expose credentials, account identifiers, provider response bodies, or local paths.

## Human clarification protocol

No human input is required for the offline adapter. If no reviewed official public metadata mechanism can be tested synthetically, keep the live path blocked and ask for a platform-policy decision rather than choosing a scraper.

## Recommended response

Build against the official YouTube Data API contract with synthetic tests, a minimal read-only API-key boundary, explicit quota accounting, and no caption or media methods.

## Trade-offs

The official API adds credential and quota setup for live proof, but offers a clearer platform contract than page scraping. Metadata coverage may omit unavailable videos, which should remain explicit gaps.

## Free-form response

Optional maintainer notes or alternate reviewed official mechanism:
