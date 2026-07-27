# Product readiness matrix

This is the falsifiable status record for `performing-fire-corpus`. Every claim
in `README.md`, `docs/PROJECT_BRIEF.md`, and the runbooks under `docs/` must be
reconcilable with a row here, and every row must name a passing test, a
sanitized live proof, a durable blocker, or an explicitly labeled future issue.

The repository today is a tested rights-aware corpus pipeline with one bounded
source proof and explicit held gates. It is not a hosted operator product.

## Readiness states

These states are deliberately separate. An implemented contract is never
promoted to a live-proven service, and a passing offline test never stands in
for a live observation.

| State | Meaning |
|---|---|
| `contract-only` | A reviewed contract exists in `docs/`. No module implements it. |
| `implemented-offline` | A module and passing tests exist. Every input is a checked-in synthetic fixture or a fake client. No live system was contacted. |
| `live-proven` | A real run against a real source or service produced sanitized evidence recorded in this repository. |
| `held` | Implemented or contracted, but a named human gate must be resolved before it may run. |
| `planned` | Not implemented. A numbered issue under `docs/issues/` owns it. |
| `absent` | Does not exist in this repository in any form. |

Two rules keep the states honest:

- Held is not passed. A refused or skipped GitHub Actions job is evidence that
  the gate held, never evidence that the capability works.
- A live proof covers exactly the source, endpoint, bound, and moment it
  observed. It does not generalize to another adapter, another source, or a
  later date. See `docs/safe-observability-and-evidence.md`.

## Matrix

| Capability | Current CLI or surface | Implementation path | Passing test | Evidence lane | Live proof or durable blocker | Readiness | Next issue |
|---|---|---|---|---|---|---|---|
| Source-universe inventory (bounded public metadata) | `inventory-public --source antiegg-fluxus`; `discover-fixture` | `src/performing_fire_corpus/acquisition.py`, `discovery.py`, `bounded_discovery.py`, `registry.py`, `governance.py` | `tests/test_network_acquisition.py`, `tests/test_fixture_discovery.py`, `tests/test_bounded_discovery.py`, `tests/test_source_registry.py`, `tests/test_governance.py` | `network-acquisition` on the trusted VM | Live proof: `docs/metadata-readiness-proof.md` (issue 7, checkout `900e63b`, two bounded public `GET`s, ended on a durable `response_oversized` blocker). Marked historical; issue 11 has produced no current observation. | `live-proven` for the one antiegg article endpoint only; `implemented-offline` elsewhere | 011 revalidation |
| ANTIEGG catalogue expansion beyond the one article | none | `src/performing_fire_corpus/antiegg_metadata_adapters.py` | `tests/test_antiegg_metadata_adapters.py`; `tests/fixtures/antiegg/` | `network-acquisition` | Governance and REST v2 shape reviewed 2026-07-26 (robots grants blanket crawl; `/wp-json/wp/v2/posts` returns 200 unauthenticated with `x-wp-total: 1463`). The portable fixture audit reaches two stable, prose-free records, terminates on `x-wp-totalpages`, and reports no unvisited remainder. Prior blocked proof: `docs/antiegg-inventory-proof.md`. | `implemented-offline` (shape-bound fixture) | run a separately authorized bounded live inventory |
| NJP Art Center site and video-archive inventory | `inventory-njp-sites [--source <id>]` | `src/performing_fire_corpus/njp_center_adapters.py`, `njp_site_inventory.py` | `tests/test_njp_center_adapters.py`, `tests/test_njp_site_inventory.py` | `network-acquisition` on the trusted VM | On 2026-07-26 the shape-bound `/mediaObjects/more` endpoint completed with 29 reachable records. On 2026-07-27 the separate Video Archive inventory completed from exact commit `bda79f0` with two requests, one page, eight unique PDF-link metadata records, an exact live/reviewed structure-digest match, and no blockers or linked-object request. See `docs/njp-center-site-inventory-report.json`, `docs/njp-center-video-archive-shape.md`, and `docs/njp-center-video-archive-inventory-report.json`. | `live-proven` for both endpoint-specific inventories | linked objects remain outside this inventory proof |
| NJP Video Library inventory | none | `src/performing_fire_corpus/njp_video_library_adapter.py` | `tests/test_njp_video_library_adapter.py` | `network-acquisition` | The bounded adapter run remains held on `robots_ambiguous`. A separate operator-requested out-of-band run acquired and exact-key verified one public primary asset for each of 678 records, plus 38 human SRT attachments. This is corpus coverage, not proof of the adapter or production worker. See `docs/njp-public-raw-archive-acquisition-20260727.md`. | `held` for the product adapter; public raw set acquired out of band | resolve the ambiguous robots response before claiming adapter readiness |
| Official YouTube metadata proof | none | `src/performing_fire_corpus/youtube_metadata_adapter.py` | `tests/test_youtube_metadata_adapter.py` | `network-acquisition` | The formal API-key metadata proof remains held. A separate operator-requested trusted-laptop run acquired and exact-key verified one public combined MP4 representation for each of 156 observed official uploads. This is not proof of the metadata adapter or trusted-laptop worker. See `docs/njp-public-raw-archive-acquisition-20260727.md`. | `held` for the product adapter; public raw set acquired out of band | 023 |
| Offline source-adapter conformance harness | none | `src/performing_fire_corpus/adapter_conformance.py` | `tests/test_adapter_conformance.py` | `portable` | Offline by design; `docs/adapter-conformance.md` names the evidence required before any live proof. | `implemented-offline` | — |
| Operation-specific rights qualification | none | `src/performing_fire_corpus/qualification.py`, `policy.py` | `tests/test_asset_qualification.py`, `tests/test_acquisition_policy.py` | `portable` | Synthetic records only. `docs/rights-qualification.md`: an approval for one operation never implies another. | `implemented-offline` | — |
| Selected rich corpus | none | `src/performing_fire_corpus/selection.py` | `tests/test_selection_policy.py` | `portable` | Deterministic and fixture-only. No corpus has been selected. See `docs/rich-corpus-selection.md`. | `implemented-offline` | 039 |
| One-object R2 proof | `r2 readiness`, `r2 transfer-approved`, `trusted-vm acquire-one-to-r2` | `src/performing_fire_corpus/r2.py`, `storage.py`, `transfer.py`, `trusted_vm.py` | `tests/test_object_storage.py`, `tests/test_r2_adapter.py`, `tests/test_trusted_vm_acquisition.py` | `object-storage` on the trusted VM | The formal proof remains blocked and its fake-client tests do not establish a live product path. The bucket now contains an operator-requested out-of-band public corpus acquisition; that run did not execute this CLI proof. | `held` | 025, then 026 |
| Production ingestion, namespaces, manifests, retention | none | `src/performing_fire_corpus/corpus_objects.py`, `ledger.py` | `tests/test_corpus_object_contract.py`, `tests/test_ledger.py` | `object-storage` | The product contract remains fake-storage-only and authorizes no production operation. Operator-requested public assets were written through separate resumable streaming tools under legacy source prefixes, so they still require reconciliation into the production namespace and ledger. | `implemented-offline`; live corpus exists outside the product contract | 039 |
| Derived processing (OCR, transcription, video understanding) | none | `src/performing_fire_corpus/derived_media.py`, `trusted_laptop_worker.py`, `trusted_vm_worker.py` | `tests/test_derived_media.py`, `tests/test_trusted_laptop_worker.py`, `tests/test_trusted_vm_worker.py` | `trusted-laptop`, `trusted-vm` | No worker has ever run against real media. The contract never executes a tool; a missing decision is a denial. | `implemented-offline` | 039 |
| Provenance-aware search index and query | `search build`, `search query` | `src/performing_fire_corpus/search_index.py`, `search_service.py` | `tests/test_search_index_contract.py`, `tests/test_rights_filtered_search.py` | `portable` | Offline over validated synthetic snapshots. `docs/provenance-aware-search-index.md`: "not a deployed search service". | `implemented-offline` | — |
| Score-generation export | `search export-scores` | `src/performing_fire_corpus/search_service.py` | `tests/test_rights_filtered_search.py` | `portable` | Offline. Emits rights-safe features and exact object keys, never a signed URL, and is restricted to the `operator` and `researcher` audiences. | `implemented-offline` | — |
| Project-native lifecycle (consent, access, deletion) | none | `src/performing_fire_corpus/project_native_lifecycle.py` | `tests/test_project_native_lifecycle.py` | `portable` | Durable blocker: `docs/issues/036-approve-first-project-native-intake-pilot.md` carries `rucksack-blocked`. Invented records only; no intake surface is deployed. | `held` | 036 |
| Safe observability, evidence, and operator gates | none | `src/performing_fire_corpus/observability.py`, `operator_gates.py`, `redaction.py` | `tests/test_safe_observability.py`, `tests/test_operator_gates.py`, `tests/test_redaction.py`, `tests/test_secret_canaries.py` | `portable` | Allowlisted and content-free by construction. | `implemented-offline` | — |
| Hosted operator UI | none | none | — | — | No HTTP server, web form, or loopback API exists in `src/`. `docs/rights-filtered-search-surface.md` states a loopback interface "is intentionally not implemented here". | `absent` | none scheduled |
| Deployment | none | none | — | `deploy` | `.agent/deploy.yaml` sets `hosted_execution: held`; `.github/workflows/deploy.yml` is named "Rucksack VM Deploy (Held)", grants `permissions: {}`, checks out nothing, and exits `2`. Nothing has been deployed. | `held` | none scheduled |
| PRD or demo documentation | none | none | — | — | No PRD, demo, or pitch document exists anywhere in `docs/`. Recorded here rather than invented. | `absent` | — |

If a PRD or demo document is added later, it must reference these same
readiness states and must not introduce a status word that is not defined
above.

## Live proof register

The repository contains one historical live source proof, one current bounded
endpoint proof, and dated blocked preflight proofs. A blocked preflight is live
evidence that its gates held at that moment, not evidence that inventory
succeeded.

| Proof | Scope | Record |
|---|---|---|
| Issue 7 metadata-only readiness proof | Two unauthenticated public `GET`s against the `antiegg-fluxus` adapter at documented bounds, from checkout `900e63b`. No body, credential, media, or object transfer. | `docs/metadata-readiness-proof.md` |
| Issue 27 bounded ANTIEGG inventory | One bounded unauthenticated robots-policy request from exact commit `fcda3289f261687bd84a43e9bcf5f7bb26d5d8f6`; both source endpoints stopped on missing policy decisions before request. Superseded 2026-07-26: those decisions are now recorded, and a later bounded run reached the network and stopped on `response_structure_changed`. | `docs/antiegg-inventory-proof.md` |
| Issue 29 NJP Center site preflight | Independent bounded robots and registered-page access checks for `njp-center-main` and `njp-center-video-archive`. Both robots checks allowed; both bounded `HEAD` checks ended in transport errors. No catalogue or attachment body was requested. | `docs/njp-center-site-inventory-report.json` |
| Issue 84 NJP Center mediaObjects proof | One bounded unauthenticated inventory of the reviewed `/mediaObjects/more?page=<n>` fragments. It reached the first zero-item page with 29 unique factual records, no blocker, and no attachment request. The Video Archive result remains independent and blocked. | `docs/njp-center-site-inventory-report.json`, `docs/njp-center-mediaobjects-shape.md` |
| Issue 95 NJP Center Video Archive shape proof | One exact-clean-head trusted-VM request pair: allowed `robots.txt`, then one 53,358-byte public page. The content-neutral report had 98 categorical signature shapes, no JSON, no capacity truncation, and structure digest `e6f9a291…`; no laptop fallback or linked-object request occurred. | `docs/njp-center-video-archive-shape.md` |

The issue 7 proof is explicitly recorded there as historical evidence and as
expired hypotheses, not a current source fact. Neither the issue 27 nor the
issue 29 record establishes catalogue completeness; each shows only what its
gates did at its own commit. Issue 84 establishes completeness only for the
observed mediaObjects endpoint and bounds. No R2 object exists, no worker has
processed media, no index has been built from a real corpus, and no CI job
success is claimed as capability evidence.

## Source boundary

The approved public source universe is fixed in `docs/PROJECT_BRIEF.md` and
`src/performing_fire_corpus/registry.py`. Adding any source outside it is a
human decision owned by `docs/issues/040-decide-whether-to-add-a-later-source.md`,
which carries `rucksack-blocked`. Public readability is not ingestion approval.

Counts are unverified. The sole scoped exception is the dated, bounded
mediaObjects endpoint proof above. Corpus-size and API observations in the
brief are otherwise hypotheses that bounded public requests must verify; this
repository does not generalize the 29-record endpoint count to another source
or the whole NJP Center universe.

The repository does not mirror source sites in bulk. It records public URLs and
factual metadata only, and commits no source prose, HTML bodies, media,
captions, transcripts, embeddings, or private material.

## Automation and CI status

| Workflow | Trigger | Gate |
|---|---|---|
| `.github/workflows/ci.yml` | `pull_request`, `push` to `staging`/`main`, `workflow_dispatch` | Reads no repository or environment secrets. |
| `.github/workflows/agent-evidence.yml` | `pull_request`, `workflow_dispatch` | Fork-safe; reads no secrets; pins Python `3.11`. |
| `.github/workflows/deploy.yml` | `workflow_dispatch` | Held. `permissions: {}`, no checkout, refuses and exits `2`. |
| `.github/workflows/rucksack-build.yml` | `workflow_dispatch` | Held. `permissions: {}`, refuses hosted agent execution. |
| `.github/workflows/rucksack-ledger.yml` | `workflow_dispatch` | Held. Refuses hosted generative planning without a checkout or credentials. |
| `.github/workflows/rucksack-autopilot.yml` | `issues`, `issue_comment`, `pull_request` closed, `schedule`, `workflow_dispatch` | Jobs stay skipped unless the `RUCKSACK_AUTOPILOT_ENABLED` repository variable is exactly `true`; `.agent/autopilot.yaml` records `default_state: held`. |

Model and effort racing is disabled for this project, so no capability here
depends on comparing candidate model outputs.

## Runtime preflight

Validation results are only comparable on a supported runtime. `pyproject.toml`
declares `requires-python = ">=3.11"`, so select the interpreter before running
anything:

```bash
sh scripts/preflight-python
sh scripts/preflight-python -m unittest discover -s tests
```

With no arguments the preflight prints the selected interpreter. With
arguments it replaces itself with that interpreter. If no interpreter `>= 3.11`
is available it writes the exact versions it found and exits `2`
(`blocked by missing dependency or environment setup` in `.agent/verify.md`)
rather than running a partial suite on an unsupported runtime. Pin one
interpreter with the `PERFORMING_FIRE_PYTHON` environment variable; a pinned
interpreter is never silently replaced.

## Command surface

Every command below exists today, uses repository-relative paths, and is
covered by the readiness rows above. Secrets are named, never valued. Commands
for `held` capabilities are marked so; there are no future or
post-implementation commands documented in this repository.

| Command | Lane | Secrets by name | Live side effects | Stop condition |
|---|---|---|---|---|
| `sh scripts/preflight-python` | `portable` | none | none | Exits `2` when no interpreter `>= 3.11` is found. |
| `python3 -m unittest discover -s tests` | `portable` | none | none | Standard non-zero exit on any failing test. |
| `scripts/agent-evidence` | `portable` | none | Writes `.agent/evidence/<stamp>/manifest.json` and lane logs. | Exit `1` on required-lane failure, `2` when blocked; see `.agent/verify.md`. |
| `performing-fire-corpus progress --database <path>` | `portable` | none | Reads a local ledger. | Fails closed on a missing or unreadable ledger. |
| `performing-fire-corpus discover-fixture --fixture <path> --database <path> --output <path>` | `portable` | none | Writes a local ledger and sanitized manifest. | Offline only; rejects any non-fixture input. |
| `performing-fire-corpus inventory-public --source antiegg-fluxus --max-requests 2 --ledger <path> --sanitized-manifest <path>` | `network-acquisition`, trusted VM | none; runs unauthenticated | Makes bounded public `GET` requests. | Stops on the request, timeout, rate, elapsed, or response-byte bound, on a robots restriction, or on any durable blocker. Bounds must not be raised to bypass a result. See `docs/network-acquisition-smoke.md`. |
| `performing-fire-corpus inventory-njp-sites --commit-sha <sha> --run-label <id> --state-root <path> --aggregate-report <path> [--source <id>]` | `network-acquisition`, trusted VM | none; runs unauthenticated | Requires the named exact clean head and current full-horizon governance before network access; writes independent ignored ledgers and sanitized, commit/shape-bound reports; requests bounded robots metadata and only the selected shape-bound metadata page(s). The Video Archive path is one page and issues no linked-PDF request. | Stops each source independently on governance, robots, access, shape, retry, request, page, elapsed, or byte bounds; one same-host rate limiter spans selected sources. Attachment bodies remain held. See `docs/njp-center-site-inventory.md`. |
| `performing-fire-corpus review-njp-video-archive-shape --commit-sha <sha> --governance config/source-governance.v1.json --output <path>` | `network-acquisition`, trusted VM first | none; runs unauthenticated | Validates current endpoint governance, requests `robots.txt` and at most one 128 KiB archive-page body, then writes an ignored report containing only categorical shapes, counts, and digests. | Requires the named exact clean Git head; follows no redirect, retains no raw HTML, prose, source strings, or hostile error text, and stops on governance, robots, access, MIME, size, elapsed, transport, malformed JSON, or structure-summary bounds. See `docs/issues/095-bind-run-njp-video-archive-scraper.md`. |
| `performing-fire-corpus r2 readiness --config .agent/storage.yaml --output <path>` | `trusted-vm` | `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT` | Reports secret presence only; may probe configured storage scope. | Reports `missing` and fails closed rather than guessing; never records a secret value. |
| `performing-fire-corpus r2 transfer-approved --plan <path> --ledger <path> --config <path> --cache-directory <path> --output <path>` | `object-storage`, trusted VM | same four names | Would write one immutable R2 object. | **Held.** A reviewed plan does not authorize a live transfer; see `docs/r2-object-storage.md`. |
| `performing-fire-corpus trusted-vm acquire-one-to-r2 --approval <path> --database <path> --storage-config .agent/storage.yaml --cache-directory <path> --sanitized-output <path>` | `trusted-vm` | same four names | Would acquire, verify, and delete exactly one approved object. | **Held** behind issues 010 and 025. Delete-after-verification only; retention is unsupported. |
| `performing-fire-corpus search build --index-id <id> --snapshot <path> --authority <path> --built-at <timestamp> --output <path>` | `portable` | none | Writes a local index artifact. | Fails closed on unknown rights or authority; never defaults to visible. |
| `performing-fire-corpus search query --index <path> --authority <path> --audience <operator\|researcher\|public> --current-time <timestamp> --output <path>` | `portable` | none | Writes a local answer artifact. | Empty facets and empty results rather than a leak; no signed URL is ever emitted. |
| `performing-fire-corpus search export-scores --index <path> --authority <path> --audience <operator\|researcher> --current-time <timestamp> --output <path>` | `portable` | none | Writes a local feature export. | Refuses the `public` audience; emits exact keys, never content or a grant. |
