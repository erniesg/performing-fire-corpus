# Offline metadata-adapter conformance

This harness is the portable admission gate for every ANTIEGG, Nam June Paik
Art Center, and official YouTube metadata adapter. Passing it shows that an
adapter obeys the shared structural and privacy bounds. It does not approve a
live request, establish a lawful basis, or authorize retention of source
content.

The implementation is in
`src/performing_fire_corpus/adapter_conformance.py`. The shared synthetic matrix
is the reusable mixin in `tests/adapter_conformance_suite.py`; the protocol
tests are in `tests/test_adapter_conformance.py`, and the current invented
response builders are in `tests/synthetic_adapter_builders.py`.

## Adapter contract

An adapter declares one canonical `source_id` and `endpoint_id` from the source
registry, a semantic adapter version, whether robots applies, and sorted exact
allowlists for:

- request methods, the canonical endpoint host, and pagination query names
  plus exact per-parameter value contracts;
- accepted MIME types;
- approved and minimum-required factual metadata fields;
- bounded terminal states and content-free blocker states.

The adapter also supplies pure functions to build a content-free request,
detect a declared login or subscription blocker, derive a stable record ID,
and parse one bounded page. `MetadataRequest` intentionally has no headers,
cookies, request body, credentials, or browser-state surface. A pagination
value must be derived exactly from the current content-free checkpoint cursor.
Optional constant query values may be exact reviewed literals or a sorted,
explicit metadata-part projection;
credential, signed, content, media, transcript, caption, prose, raw, or
download-expanding names and values fail closed. Query-key matching is
case-sensitive. Numeric `page-`/`offset-` cursors and the exact reviewed
`pageToken` query role for sanitized `opaque-` platform pagination cursors have
separate contracts. Credential roles such as `accessToken`, `refreshToken`,
and `idToken` cannot use the pagination exception. An opaque cursor is kept
only in the local checkpoint; public manifests expose its SHA-256 digest.
Platforms that do not provide a numeric page ordinal bind a locally derived
ordinal to the opaque token. Loop detection compares the underlying token,
while resume validates both the token and monotonic ordinal.

The approved metadata projection uses exact value contracts. The current
shared types are field-prefixed, identifier-like bounded enums, four-digit
years, UTC timestamps, and ISO 8601 durations. An enum
cannot bless a sentence, person name with spaces, URL, signed value, or local
path. Adding a new value type is a reviewed common-harness change, not a
source-adapter escape hatch.
UTC timestamps must also parse as real calendar instants; range-shaped but
impossible dates fail closed.

## Synthetic fixture rule

Fixtures contain only invented identifiers, labels, URLs, dates, and counts.
Do not copy source HTML, JSON payloads, article text, captions, transcripts,
media URLs, signed values, account data, or private project material into a
fixture. A source-specific parser should receive a response assembled by a
synthetic builder rather than a saved live response.

Each normalized record carries both its stable record ID and a SHA-256 digest
of a separate, sanitized, content-free synthetic source identity. Raw source
identity is never emitted in the manifest. Stable-ID variants must change the
invented title, result ordering, page
position, pagination value, tracking query, and presentation metadata while
keeping the stable source identifier fixed. The adapter must return the same
record ID for every variant. Two distinct source identities mapping to one
record ID are a collision even if their approved metadata is identical.

## Required shared matrix

Every adapter test module subclasses `StandardAdapterConformanceMixin` and
provides only its adapter factory plus invented item, page, and identity-variant
builders. The inherited matrix instantiates `OfflineConformanceHarness` and
exercises the same cases:

- zero request budget and, when applicable, robots denial before request
  construction;
- `401`, `403`, `429`, login-required, and subscription-required blockers;
- redirect target, MIME, response-size, and parser-shape mismatch;
- pagination loops, non-monotonic page ordinals, and changed expected totals
  for paginated sources;
- retry checkpoint/resume at the same cursor;
- duplicate records, stable-ID collisions, and expected-total changes;
- deterministic final manifests under reordered source results;
- rejection of prose, HTML, captions, transcripts, unapproved URLs, signed
  values, personal data, and machine-local paths.

Network denial is automatic around every adapter request builder, blocker
detector, parser, identity check, and the complete inherited matrix. It rejects
DNS lookups, raw socket connect/send entry points, standard-library HTTP
open/request methods, URL retrieval, and browser open methods. Pass every
source-specific SDK request method through `additional_network_entry_points`;
an adapter is non-conformant if its parser or tests require a live SDK,
browser, credential, cache, or remote fixture.

The offline checkpoint is integrity checked and declaration bound. Resume also
requires the operator to provide the expected bounds and checkpoint digest
from a separately trusted run-plan or receipt; never derive those expected
values from the checkpoint being resumed. Counter, retry, page, cursor, and
seen-page relationships must remain monotonic. Only a non-terminal `ready` or
`retry_pending` checkpoint can resume. A page is committed only after
pagination, metadata, source-identity collision, and completeness checks all
pass.
Adapters with run-time safety state, such as a quota ledger, must expose it to
the harness. The outer checkpoint binds that state and restores it before the
next request. A content-free adapter-lineage digest may also be included in a
manifest to bind dependent stages without exposing identifiers or raw
responses. The same lineage digest is part of the outer checkpoint and must
match exactly on resume. A typed pre-request blocker stops the harness without
returning a request or incrementing request-attempt counters.

## Evidence required before a live proof

Attach only sanitized, aggregate evidence to the source issue or PR. The
minimum checklist is:

1. Exact adapter commit and semantic adapter version.
2. Canonical source and endpoint IDs, with the endpoint still present in the
   reviewed registry.
3. Passing shared conformance matrix in a Python 3.11+ isolated environment.
4. Confirmation that all fixture data is invented and no live response,
   source prose, media, caption, transcript, private input, or credential is
   checked in.
5. Network-denial coverage naming the adapter's HTTP, browser, and SDK entry
   points.
6. Stable-ID invariant and collision-block evidence.
7. Exact approved metadata fields, value contracts, MIME types, methods,
   hosts, pagination parameters, terminal states, and blockers.
8. A current endpoint-specific governance decision and unexpired robots
   evidence for the proposed metadata operation.
9. Reviewed run-plan bounds for requests, pages, elapsed time, response and
   aggregate bytes, retries, Retry-After, timeouts, and host interval.
10. A stop/resume statement naming the durable checkpoint and completeness
    outputs without including local paths, response bodies, signed URLs, or
    secret values.

If any item is absent, keep the source adapter metadata-only and blocked from a
live proof. Conformance never widens acquisition, prose, caption, media, or
retention rights.

## Validation

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```
