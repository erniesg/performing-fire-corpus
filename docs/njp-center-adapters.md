# NJP Center metadata adapters

The `njp-center-main` and `njp-center-video-archive` adapters are separate
provenance boundaries even though both use the reviewed public
`njp.ggcf.kr` host. Each adapter is bound to its own canonical endpoint and
completeness checkpoint.

The `njp-center-main` adapter is bound only to the reviewed
`/mediaObjects/more?page=<n>` fragment. It retains the stable positive-integer
identifier, canonical detail URL, and decoded title, and terminates on the
first structurally valid zero-item fragment. See
`docs/njp-center-mediaobjects-shape.md`.

The separate `njp-center-video-archive` adapter remains held:
constructing it without a reviewed source-shape binding raises
`SourceShapeUnreviewed` before it can build a request.

Tests alone enable a private invented-fixture contract. Those fixtures use
HTML-shaped factual fields as explicit data attributes:
stable public record identifier, record type, language, year, and a bounded
classification enum. Text nodes and display titles are ignored. A changed or
missing structural contract fails closed instead of widening the retained
projection.

## Bilingual observations

Korean and English variants are kept as language-specific observations on the
per-record `language` enum (`language_ko`, `language_en`,
`language_bilingual`, `language_unknown`), never as a merge key. Identity comes
only from the stable public record identifier, so two records that publish the
same Korean/English display label stay two records, and a record whose label
changes keeps one identity. Display labels themselves are prose and are not
retained, so no title-derived alias string enters the projection.

Passing these tests is not a live-source approval and does not make the
invented fixture parser source-useful. Before a network request, the exact
endpoint needs a bounded reviewed observation that defines the real factual
projection, plus current robots, terms, access, authentication,
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

A candidate is bound to the source that observed it. One adapter cannot record
an access outcome for the other source's candidate, so a main-site blocker
neither blocks nor authorizes the Video Archive, and the reverse holds too.

## Offline evidence

Both adapters inherit the standard conformance matrix. It covers zero
budgets, robots denial, `401`/`403`/`429`, login and subscription signals,
redirects, MIME and size bounds, shape drift, pagination and ordinal loops,
retry/resume integrity, duplicates, stable-ID collisions, deterministic
manifests, forbidden fields and values, and automatic network denial.

The adapter-specific tests additionally verify that:

- main-site and Video Archive records never share a source identity;
- titles do not define identity, and equal titles do not merge distinct IDs;
- Korean, English, and bilingual records on one page stay separate records
  with their own language observation;
- one source's `403` attachment blocker leaves the other source untouched;
- missing years remain explicit unknown observations;
- attachment candidates cannot become acquisition-eligible;
- credentialed, signed, or off-host attachment locators fail closed.
