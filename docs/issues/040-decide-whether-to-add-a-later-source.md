# Decide whether to add a later public source

depends-on: 012,013,034
labels: rucksack-blocked

## Goal

Provide the durable approval gate for any public source outside the currently named universe. Do not add, probe, scrape, ingest, or index a later source until its purpose, provenance value, terms, rights, access, retention, privacy, and bounded discovery plan are reviewed.

## Acceptance tests

- Identify one proposed source by canonical public locator and explain the specific coverage gap it addresses using the current sanitized evaluation, without copying source prose or content.
- Define a proposed stable source ID, source class, allowed public hosts and endpoints, robots-review method, official API or metadata mechanism, platform terms, copyright or lawful basis, access-control expectations, and metadata retention.
- Define initial request, page, per-response and aggregate byte, retry, rate, and elapsed limits plus checkpoint and completeness semantics.
- State explicitly whether prose, media, captions, documents, attachments, or private material remain blocked. Metadata approval never implies content acquisition.
- Assess duplicate and provenance overlap with existing sources and explain why the source is not merely a workaround for `401`, `403`, robots denial, rate limiting, login, platform prohibition, or unclear rights elsewhere.
- An approval authorizes only a registry and offline adapter issue. A separate current live-proof issue remains required before requests beyond a minimal reviewed probe.
- A decline or incomplete response remains a durable blocker and does not affect the existing source-universe inventory.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Any later official API secret name must be reviewed in a separate source-specific live-proof issue; values are never accepted here.

## Artifact outputs

- Reviewed approve or decline decision in this issue
- Proposed privacy-safe canonical registry entry only after approval
- Durable blocker with missing fields and exact safe next action
- No network request or source content

## Stop conditions

- Stop if the source is proposed to bypass an existing robots, access, platform, rate, login, or rights blocker.
- Stop if purpose, stable identity, terms, rights, access, retention, privacy, bounds, or completeness plan is incomplete.
- Stop if source prose, media, personal data, credentials, account identifiers, endpoints, signed URLs, screenshots, or local paths would enter the issue.
- Stop before any live probe or registry mutation without explicit approval.

## Human clarification protocol

Reply with approve or decline using the privacy-safe fields below. Keep the issue blocked when any field is unknown, and never paste source content, credentials, provider details, personal information, or private proposal material.

## Recommended response

```text
Decision: approve or decline
Canonical public locator:
Proposed stable source ID:
Coverage gap addressed:
Official metadata mechanism:
Robots, terms, rights, access, and retention review:
Initial request, page, byte, retry, rate, and time bounds:
Content classes that remain blocked:
Duplicate and provenance considerations:
Additional privacy-safe notes:
```

Decline unless the source adds a concrete coverage dimension and has a defensible bounded metadata path.

## Trade-offs

Adding sources can improve coverage but expands policy, adapter, duplicate, and maintenance work. A durable gate may slow opportunistic discovery while preventing scope creep and access-control workarounds.

## Free-form response

Optional privacy-safe maintainer response using different wording:
