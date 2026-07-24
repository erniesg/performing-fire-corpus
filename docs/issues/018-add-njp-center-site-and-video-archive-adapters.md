# Add NJP Center main-site and Video Archive metadata adapters

depends-on: 015

## Goal

Implement bounded, source-distinct metadata adapters for the Nam June Paik Art Center main site and its Video Archive page. Inventory factual public metadata only and keep every attachment or downloadable asset ineligible until an explicit rights and access decision approves it.

## Acceptance tests

- Implement separate adapters for `njp-center-main` and `njp-center-video-archive` while sharing only reviewed host, robots, transport, and parsing helpers.
- Begin from current bounded metadata observations; do not encode earlier catalogue counts, HTML structure, endpoint behavior, or downloadable-asset assumptions as facts.
- Parse only approved factual fields such as stable canonical URL, public record ID when present, record type, language, date, and content-neutral classification. Exclude descriptions or prose unless a governance record explicitly classifies a narrow field as retainable metadata.
- Normalize Korean and English variants as aliases or language-specific observations without merging distinct records solely by title.
- Discover attachment locators only as blocked candidates with public URL, claimed MIME type, and source relationship. Do not request attachment bytes during metadata discovery.
- Treat any attachment URL that returns `403` as a durable exact-URL blocker. Never retry it with an old token, cookies, referer manipulation, browser state, or alternate download route.
- Use the shared budgets, checkpoints, sanitized request facts, completeness accounting, and conformance suite. One source’s blocker does not authorize or silently block the other source.
- Add invented fixtures for navigation, pagination, bilingual aliases, missing fields, changed structure, access denial, and attachment candidates. Portable tests make zero network requests.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. These adapters are public and unauthenticated. Authentication requirements are blockers.

## Artifact outputs

- New NJP main-site and Video Archive adapters under `src/performing_fire_corpus/`
- New source-specific policy entries and synthetic tests
- New attachment-candidate blocker representation
- Updated source-adapter documentation under `docs/`

## Stop conditions

- Stop on robots denial or ambiguity, `401`, `403`, login, rate exhaustion, disallowed redirect, unexpected MIME type, changed structure, or an unreviewed terms or retention state.
- Stop before requesting or retaining an attachment, source description, raw HTML, image, audio, video, caption, or transcript.
- Stop if bilingual matching would merge records without stable evidence.

## Human clarification protocol

Ask only if an attachment acquisition is the next executable task and its exact rights, URL, MIME, byte bound, and retention decision are absent. Recommend keeping it blocked and continuing independent metadata inventory.

## Recommended response

Keep the main site and Video Archive as separate provenance sources, retain only narrowly factual metadata, and represent every attachment as `pending` or `blocked` until a reviewed asset-specific decision exists.

## Trade-offs

Separate adapters duplicate some host logic but preserve source-specific completeness and rights. Excluding descriptive text may reduce search recall while keeping the initial inventory defensible.

## Free-form response

Optional maintainer notes or alternate factual metadata fields:
