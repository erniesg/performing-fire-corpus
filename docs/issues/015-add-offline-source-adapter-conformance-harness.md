# Add an offline source-adapter conformance harness

depends-on: 012,013,014

## Goal

Create a reusable red/green/refactor harness that every ANTIEGG, NJP Center, and YouTube metadata adapter must pass before a trusted-VM proof. Keep all source-shaped fixtures synthetic and all portable tests network-free.

## Acceptance tests

- Define one adapter protocol for source identity, endpoint selection, robots applicability, request construction, bounded parsing, pagination checkpoints, stable asset IDs, normalized metadata, and completeness observations.
- Require each adapter to declare the canonical source and endpoint IDs, allowed methods and hosts, expected MIME classes, minimum required metadata fields, and its source-specific terminal and blocker states.
- Provide synthetic response builders rather than copied HTML, JSON, article prose, captions, or platform payloads. Fixtures use invented titles, IDs, URLs, dates, and counts.
- Run shared tests against every adapter for zero-budget refusal, robots denial, `401`, `403`, `429`, login or subscription signals, redirect mismatch, MIME mismatch, oversize, shape drift, pagination loop, retry resume, duplicate items, and stable final manifests.
- Patch DNS, socket, HTTP, browser, and SDK entry points so portable tests fail on any live access.
- Assert that stable IDs remain unchanged when mutable title, ordering, pagination, tracking query, or presentation metadata changes, and that collisions block rather than overwrite.
- Assert that no adapter emits raw source prose, HTML, captions, transcripts, media URLs not approved as factual metadata, signed values, personal information, or machine-local paths.
- Document the minimum adapter evidence required before a source-specific live proof can claim a bounded result.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. All conformance tests are offline and synthetic.

## Artifact outputs

- New adapter protocol and shared validation helpers under `src/performing_fire_corpus/`
- New synthetic adapter fixture builders under `tests/`
- New shared conformance test suite under `tests/`
- New adapter evidence checklist under `docs/`

## Stop conditions

- Stop if conformance requires copied source or platform payloads.
- Stop if any adapter needs live network access, browser state, credentials, or a machine-local cache to pass portable tests.
- Stop if source-specific code can bypass the common bounds, policy, redaction, checkpoint, or completeness contracts.

## Human clarification protocol

No human input is required for the offline harness. Ask only if a source’s documented public metadata contract cannot be represented without retaining forbidden content; recommend keeping that adapter blocked and testing a narrower metadata shape.

## Recommended response

Use one small protocol with shared failure semantics and source-specific pure parsers. Require a passing synthetic conformance suite before any live request is approved.

## Trade-offs

Synthetic conformance does not prove a live source shape, but it catches policy drift cheaply. A strict common protocol may expose genuine source differences as explicit adapter extensions instead of hidden special cases.

## Free-form response

Optional maintainer notes or alternate conformance requirement:
