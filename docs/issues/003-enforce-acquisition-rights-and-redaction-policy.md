# Enforce acquisition, rights, retry, and redaction policy

depends-on: 001

## Goal

Build the fail-closed policy layer used by fixture and live discovery: exact public-host allowlisting, rights gates, per-host rate limits, bounded retries with backoff, and centralized sanitization of logs, manifests, and evidence.

## Acceptance tests

- Allow only `https` URLs on the distinct public source hosts represented by the checked-in brief, after normalized hostname and port validation; reject redirects to any unlisted host or non-public credential-bearing URL.
- Reject URL userinfo, fragments used as data, loopback or private-network targets, unsupported ports, signed-query credentials, and ambiguous hostnames before any request is attempted.
- Require `approved` rights before any content transfer operation; `pending`, `blocked`, missing, or malformed rights records fail closed with a durable sanitized reason.
- Implement a deterministic injectable-clock rate limiter with explicit per-host request intervals and no shared-host bypass through aliases.
- Classify `403`, robots denial, login requirements, changed response structure, unclear rights, and retry exhaustion as durable blocked or failed results rather than evasion opportunities.
- Retry only configured transient outcomes, honor bounded `Retry-After` values, cap attempts and elapsed backoff, and persist retry state needed for resume.
- Redact secret-like environment values, cookies, authorization headers, signed query values, account identifiers, local absolute paths, and response bodies from exceptions, logs, manifests, fixtures, and evidence.
- Add red/green tests for URL confusion cases, redirect policy, rights states, rate spacing, retry bounds, `Retry-After`, redaction, and resume-safe retry accounting without making network calls.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

None. Policy tests use synthetic inputs and fake clocks/transports.

## Artifact outputs

- Policy, redaction, rate-limit, and retry modules under `src/performing_fire_corpus/`
- Synthetic policy tests under `tests/`
- Sanitized error and blocker record contracts shared with the ledger

## Stop conditions

- Stop if a test or fixture would contain a real credential, signed URL, response body, source prose, media, or personal information.
- Stop if a source requires bypassing robots controls, access controls, rate limits, login, or a `403`.
- Stop if safe URL classification depends only on string prefixes or DNS results captured in a portable test.

## Human clarification protocol

Ask only if a required public host is absent from the checked-in brief or an ingest request lacks a rights decision. State the blocked host or asset identifier, recommend keeping it blocked, and leave room for a reviewed allowlist or rights decision.

## Recommended response

Keep the host or asset blocked until the public brief and rights record are explicitly updated through review. Do not add bypasses or permissive fallbacks.

## Trade-offs

Exact allowlisting and centralized redaction may reject some harmless URL variants, but false negatives are preferable to leaking material or crossing a rights boundary. Injectable time adds an interface seam that makes rate and retry behavior deterministic in tests.

## Free-form response

Optional maintainer notes or an alternate policy decision:
