# Bounded metadata discovery

`performing_fire_corpus.bounded_discovery` is the shared, source-neutral
checkpoint engine for metadata inventory work. It does not fetch a site by
itself. A source adapter and bounded transport must be supplied by a later
source-specific issue.

The engine is metadata-only. Response bytes exist in memory only long enough
for the adapter to parse one bounded page. Durable state contains sanitized
request facts, approved factual metadata, hashes, counts, and normalized
pagination state—not response bodies, headers, URLs, cookies, credentials,
signed values, prose, captions, transcripts, or media.

The run plan names the exact canonically ordered factual metadata fields that
may become durable. The adapter must declare the identical projection, and a
record containing any other field is rejected before observations are written.
Content-bearing fields such as descriptions, excerpts, captions, transcripts,
and prose cannot be added to this metadata-only projection. Every approved
field also has an exact declarative value contract in both the plan and adapter.
The plan fingerprint binds those contracts to the run ID; values outside the
field-specific year or enumerated-value contract fail closed before persistence.

## Authority binding

Every run requires both:

- a strict `discovery_run_plan`; and
- the exact reviewed `source_governance` record whose canonical SHA-256 digest
  produces the plan's `policy_snapshot_id`.

The source ID and endpoint ID must match the canonical source registry. The
plan's metadata-inventory state, robots evidence ID and state, and policy and
robots expiry bounds must not exceed the supplied governance record. The
governance evaluator runs before the first request. Pending, blocked, revoked,
expired, conflicting, or incomplete authority results in a durable blocked
report and zero requests.

A plan may shorten an authority window, but it cannot extend one. Authority is
checked both before and immediately after each request; if policy or robots
evidence expires in flight, the response is not parsed. A changed plan, adapter
version, or policy snapshot cannot reuse an existing run ID or replace its
stored report.

## Bounds and pagination

The plan and adapter must agree exactly on every positive limit:

- total requests;
- bytes per response;
- aggregate response bytes;
- committed pages;
- elapsed time;
- request timeout;
- per-host interval;
- retries; and
- maximum honored `Retry-After`.

Adapters cannot omit, weaken, or silently strengthen a bound. Transports receive
the smaller of the configured request timeout and remaining elapsed budget,
plus the per-response byte ceiling, for every request. The common engine also
enforces the aggregate, request, page, retry, rate, and elapsed budgets. A
`Retry-After` greater than the reviewed ceiling blocks the page without sleeping
or retrying early.

Adapters normalize source pagination to a monotonic `page-N` or `offset-N`
cursor plus the next ordinal. Only the cursor hash is written to request facts.
The sanitized next cursor is stored in the checkpoint. Repeated cursors,
non-monotonic ordinals, unsafe token-like cursors, ambiguous terminal pages, and
changed shapes fail closed.

## Atomic resume

One SQLite transaction commits:

1. the body-free request fact;
2. new factual observations or explicit duplicate events; and
3. the checkpoint that points to the next page.

Before any network call, the engine durably reserves one request in the
checkpoint with an opaque runner ID and bounded lease expiry. The corresponding
body-free fact and page commit atomically resolve that reservation. A concurrent
runner that encounters a live lease returns busy without mutating the run. Only
after the lease is proven expired may resume record `request_interrupted`.
Stale-owner validation, interrupted-fact insertion, lease clearing, and the
blocked report/status commit occur in one transaction, so no runner can reissue
the uncertain request between those steps. Terminal reports use compare-and-set
semantics and cannot overwrite an already terminalized run. Other terminal
request facts and their reports are committed atomically for the same reason.
Retry attempts are likewise committed with the body-free request fact, so
restarting cannot reset the current page's retry budget. A crash after a page
commit resumes from the new checkpoint. Re-running a terminal run returns the
same stored completeness report without another request.

The versioned migration creates separate run, request-fact, observation, and
duplicate-event tables. Migration versions are applied once and packaged with
the project.

## Completeness language

Reports are evidence-scoped to one source endpoint, adapter version, policy
snapshot, and run plan:

- `complete_for_observed_endpoint` means the observed endpoint supplied an
  unambiguous terminal page within the active bounds.
- `bounded_partial` means a configured budget ended the run.
- `blocked` means policy, robots, transport, response, or retry safety stopped
  the run.
- `changed` means the plan, source shape, expected total, record facts, or
  pagination behavior changed.
- `unknown` is reserved for evidence that cannot support a stronger state.

An endpoint report never establishes whole-site or whole-corpus completeness.
Expected totals are recorded only when the active endpoint explicitly supplies
one. Unvisited remainder is derived only from that endpoint-supplied total and
the unique records observed by the same run.

## Validation

Portable conformance is synthetic and uses fake clocks and transports:

```bash
python -m unittest tests.test_bounded_discovery -v
python -m unittest discover -s tests -v
scripts/agent-evidence
```

No live source request or secret is needed for these tests.
