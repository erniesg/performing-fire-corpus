# Approve and run the official YouTube metadata proof

depends-on: 022
labels: rucksack-blocked

## Goal

Resolve the minimum platform and credential gate, then run one bounded trusted-VM inventory of the official channel’s metadata. This issue authorizes no caption, transcript, thumbnail, audio, or video acquisition.

## Acceptance tests

- A maintainer confirms the reviewed official metadata mechanism, applicable platform-terms decision, metadata retention decision, quota budget, and presence or absence of the single allowed secret name without pasting a value.
- The run plan fixes the canonical channel locator, expected stable channel identifier when already verified, request, page, byte, quota, retry, rate, and elapsed limits before client construction.
- The trusted VM loads only `YOUTUBE_DATA_API_KEY` from its secret store. Output records the name as `present` or `missing`, never the value, request authorization, project, account, or provider identifiers.
- A channel mismatch, missing key, quota exhaustion, `401`, `403`, unavailable item, terms conflict, changed shape, or pagination ambiguity becomes a durable blocker with an exact next safe action.
- Demonstrate resumable pagination and idempotent rerun when the run reaches multiple pages. Report observed unique videos, duplicates, unavailable states, bounded remainder, and evidence scope without claiming channel completeness beyond the proof.
- Make no captions, transcript, thumbnail, audio, video, browser, login, subscription, or object-storage request.
- Keep live ledger, request and quota ledger, tokens, cache, manifest, and provider details in ignored local state. Commit only sanitized aggregate evidence.
- While this issue is blocked, ANTIEGG and NJP metadata branches remain independently executable.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

After approval, run the newly documented bounded YouTube metadata command on a trusted VM only.

## Allowed secrets

- `YOUTUBE_DATA_API_KEY`

The trusted VM may read its value only for the approved metadata run. No output, issue response, log, fixture, evidence, or commit may expose the value or associated account or project details.

## Artifact outputs

- Reviewed platform, quota, and retention decision in the issue
- Ignored YouTube metadata ledger, quota ledger, checkpoints, and manifest
- Sanitized aggregate official-channel completeness report
- Durable blocked record when approval or any live gate is incomplete

## Stop conditions

- Stop before client construction if the human response, platform decision, quota, channel identity, run bounds, or secret presence is incomplete.
- Stop on `401`, `403`, quota exhaustion, terms conflict, channel mismatch, pagination ambiguity, changed shape, or unsafe output.
- Stop before captions, transcripts, thumbnails, audio, video, browser authentication, subscription access, or object storage.

## Human clarification protocol

Reply with approve or decline and the privacy-safe fields below. Never paste the API key, project or account identifiers, provider endpoint details, screenshots, response payloads, personal information, or local paths. Keep the issue blocked if any field is unknown.

## Recommended response

```text
Decision: approve or decline
Official metadata mechanism reviewed:
Canonical public channel locator:
Stable channel identifier verified: yes, no, or pending bounded proof
Platform terms permit bounded metadata inventory: yes or no
Metadata retention decision:
Maximum requests:
Maximum pages:
Maximum quota units:
Maximum response and aggregate bytes:
Maximum elapsed time:
YOUTUBE_DATA_API_KEY: present or missing
Additional privacy-safe notes:
```

Approve only read-only public metadata with the smallest useful quota and keep every caption and media operation blocked.

## Trade-offs

A live official API proof requires a scoped credential and quota governance, but avoids scraping. Declining or delaying the key leaves YouTube coverage incomplete without blocking the other source inventories.

## Free-form response

Optional privacy-safe maintainer response using different wording:
