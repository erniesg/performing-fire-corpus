# Offline metadata-adapter conformance

This harness is the portable admission gate for every ANTIEGG, Nam June Paik
Art Center, and official YouTube metadata adapter. Passing it shows that an
adapter obeys the shared structural and privacy bounds. It does not approve a
live request, establish a lawful basis, or authorize retention of source
content.

The implementation is in
`src/performing_fire_corpus/adapter_conformance.py`. The shared synthetic matrix
is in `tests/test_adapter_conformance.py`, and its invented response builders
are in `tests/synthetic_adapter_builders.py`.

## Adapter contract

An adapter declares one canonical `source_id` and `endpoint_id` from the source
registry, a semantic adapter version, whether robots applies, and sorted exact
allowlists for:

- request methods, the canonical endpoint host, and pagination query names;
- accepted MIME types;
- approved and minimum-required factual metadata fields;
- bounded terminal states and content-free blocker states.

The adapter also supplies pure functions to build a content-free request,
detect a declared login or subscription blocker, derive a stable record ID,
and parse one bounded page. `MetadataRequest` intentionally has no headers,
cookies, request body, credentials, or browser-state surface.

The approved metadata projection uses exact value contracts. The current
shared types are bounded enums and four-digit years. Adding a new value type is
a reviewed common-harness change, not a source-adapter escape hatch.

## Synthetic fixture rule

Fixtures contain only invented identifiers, labels, URLs, dates, and counts.
Do not copy source HTML, JSON payloads, article text, captions, transcripts,
media URLs, signed values, account data, or private project material into a
fixture. A source-specific parser should receive a response assembled by a
synthetic builder rather than a saved live response.

Stable-ID variants must change the invented title, result ordering, page
position, pagination value, tracking query, and presentation metadata while
keeping the stable source identifier fixed. The adapter must return the same
record ID for every variant.

## Required shared matrix

Every adapter test module must instantiate the common
`OfflineConformanceHarness` and exercise the same cases:

- zero request budget and robots denial before request construction;
- `401`, `403`, `429`, login-required, and subscription-required blockers;
- redirect target, MIME, response-size, and parser-shape mismatch;
- pagination loops and non-monotonic page ordinals;
- retry checkpoint/resume at the same cursor;
- duplicate records, stable-ID collisions, and expected-total changes;
- deterministic final manifests under reordered source results;
- rejection of prose, HTML, captions, transcripts, unapproved URLs, signed
  values, personal data, and machine-local paths.

Use `deny_live_network()` around the portable matrix. It rejects DNS, raw
socket, standard-library HTTP, and browser entry points. Pass every
source-specific SDK request method through `additional_entry_points`; an
adapter is non-conformant if its parser or tests require a live SDK, browser,
credential, cache, or remote fixture.

The offline checkpoint is integrity checked and declaration bound. Only a
non-terminal `ready` or `retry_pending` checkpoint can resume. A page is
committed only after pagination, metadata, collision, and completeness checks
all pass.

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
