# NJP Center metadata adapters

The `njp-center-main` and `njp-center-video-archive` adapters are separate
provenance boundaries even though both use the reviewed public
`njp.ggcf.kr` host. Each adapter is bound to its own canonical endpoint and
completeness checkpoint.

The portable implementation is deliberately offline. Tests use invented
HTML-shaped fixtures whose factual fields are explicit data attributes:
stable public record identifier, record type, language, year, and a bounded
classification enum. Text nodes and display titles are ignored. A changed or
missing structural contract fails closed instead of widening the retained
projection.

Passing these tests is not a live-source approval. Before a network request,
the exact endpoint still needs current robots, terms, access, authentication,
copyright/lawful-basis, retention, rate, byte, page, retry, and elapsed-time
decisions through the shared governance and discovery engines.

## Attachment boundary

An attachment locator is represented only as a non-acquirable candidate tied
to a stable source record. Candidate URLs must be unauthenticated HTTPS URLs
on the reviewed host, under the observed upload path, with no query,
fragment, credential, cookie, token, referer, or browser-state dependency.
The claimed MIME type is an untrusted source observation.

Candidates start with `pending` rights, `acquisition_eligible = false`, and
`retry_allowed = false`. An exact `403` observation produces an
`access_forbidden` blocker for that exact locator. It never authorizes a
retry, alternate route, token reuse, referer change, login, or byte request.
No attachment bytes are requested by either metadata adapter.

## Offline evidence

Both adapters inherit the standard conformance matrix. It covers zero
budgets, robots denial, `401`/`403`/`429`, login and subscription signals,
redirects, MIME and size bounds, shape drift, pagination and ordinal loops,
retry/resume integrity, duplicates, stable-ID collisions, deterministic
manifests, forbidden fields and values, and automatic network denial.

The adapter-specific tests additionally verify that:

- main-site and Video Archive records never share a source identity;
- titles do not define identity, and equal titles do not merge distinct IDs;
- missing years remain explicit unknown observations;
- attachment candidates cannot become acquisition-eligible;
- credentialed, signed, or off-host attachment locators fail closed.
