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
| ANTIEGG catalogue expansion beyond the one article | none | `src/performing_fire_corpus/antiegg_metadata_adapters.py` | `tests/test_antiegg_metadata_adapters.py` | `network-acquisition` | Held: adapters raise `SourceShapeUnreviewed`; `docs/antiegg-metadata-adapters.md` states this is "not a live-source approval". | `held` | 017 |
| NJP Art Center site and video-archive inventory | none | `src/performing_fire_corpus/njp_center_adapters.py` | `tests/test_njp_center_adapters.py` | `network-acquisition` | Held: no reviewed live source shape. See `docs/njp-center-adapters.md`. | `held` | 019 |
| NJP Video Library inventory | none | `src/performing_fire_corpus/njp_video_library_adapter.py` | `tests/test_njp_video_library_adapter.py` | `network-acquisition` | Current trusted-VM proof stopped before catalogue access on `robots_ambiguous`; all catalogue pages remain unvisited. See `docs/njp-video-library-inventory.md`. | `held` | reviewed public metadata endpoint or explicit browser-lane decision |
| Official YouTube metadata proof | none | `src/performing_fire_corpus/youtube_metadata_adapter.py` | `tests/test_youtube_metadata_adapter.py` | `network-acquisition` | Durable blocker: `docs/issues/023-approve-and-run-official-youtube-metadata-proof.md` carries `rucksack-blocked` pending API-key approval. | `held` | 023 |
| Offline source-adapter conformance harness | none | `src/performing_fire_corpus/adapter_conformance.py` | `tests/test_adapter_conformance.py` | `portable` | Offline by design; `docs/adapter-conformance.md` names the evidence required before any live proof. | `implemented-offline` | — |
| Operation-specific rights qualification | none | `src/performing_fire_corpus/qualification.py`, `policy.py` | `tests/test_asset_qualification.py`, `tests/test_acquisition_policy.py` | `portable` | Synthetic records only. `docs/rights-qualification.md`: an approval for one operation never implies another. | `implemented-offline` | — |
| Selected rich corpus | none | `src/performing_fire_corpus/selection.py` | `tests/test_selection_policy.py` | `portable` | Deterministic and fixture-only. No corpus has been selected. See `docs/rich-corpus-selection.md`. | `implemented-offline` | 039 |
| One-object R2 proof | `r2 readiness`, `r2 transfer-approved`, `trusted-vm acquire-one-to-r2` | `src/performing_fire_corpus/r2.py`, `storage.py`, `transfer.py`, `trusted_vm.py` | `tests/test_object_storage.py`, `tests/test_r2_adapter.py`, `tests/test_trusted_vm_acquisition.py` | `object-storage` on the trusted VM | Durable blockers: `docs/issues/008`, `010`, and `025` carry `rucksack-blocked`; `026` waits on them. No object has ever been transferred. All tests use a fake storage client. | `held` | 025, then 026 |
| Production ingestion, namespaces, manifests, retention | none | `src/performing_fire_corpus/corpus_objects.py`, `ledger.py` | `tests/test_corpus_object_contract.py`, `tests/test_ledger.py` | `object-storage` | Fake storage only. `docs/full-corpus-object-storage.md`: "No production operation is authorized." | `implemented-offline` | 039 |
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

The repository contains exactly one live proof.

| Proof | Scope | Record |
|---|---|---|
| Issue 7 metadata-only readiness proof | Two unauthenticated public `GET`s against the `antiegg-fluxus` adapter at documented bounds, from checkout `900e63b`. No body, credential, media, or object transfer. | `docs/metadata-readiness-proof.md` |

That proof is explicitly recorded there as historical evidence and as expired
hypotheses, not a current source fact. Nothing else in this repository is
live-proven. In particular, no R2 object exists, no worker has processed media,
no index has been built from a real corpus, and no CI job success is claimed as
capability evidence.

## Source boundary

The approved public source universe is fixed in `docs/PROJECT_BRIEF.md` and
`src/performing_fire_corpus/registry.py`. Adding any source outside it is a
human decision owned by `docs/issues/040-decide-whether-to-add-a-later-source.md`,
which carries `rucksack-blocked`. Public readability is not ingestion approval.

Counts are unverified. Corpus-size and API observations in the brief are
hypotheses that bounded public requests must verify; this repository does not
claim a complete or verified source count for any source.

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
| `performing-fire-corpus r2 readiness --config .agent/storage.yaml --output <path>` | `trusted-vm` | `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT` | Reports secret presence only; may probe configured storage scope. | Reports `missing` and fails closed rather than guessing; never records a secret value. |
| `performing-fire-corpus r2 transfer-approved --plan <path> --ledger <path> --config <path> --cache-directory <path> --output <path>` | `object-storage`, trusted VM | same four names | Would write one immutable R2 object. | **Held.** A reviewed plan does not authorize a live transfer; see `docs/r2-object-storage.md`. |
| `performing-fire-corpus trusted-vm acquire-one-to-r2 --approval <path> --database <path> --storage-config .agent/storage.yaml --cache-directory <path> --sanitized-output <path>` | `trusted-vm` | same four names | Would acquire, verify, and delete exactly one approved object. | **Held** behind issues 010 and 025. Delete-after-verification only; retention is unsupported. |
| `performing-fire-corpus search build --index-id <id> --snapshot <path> --authority <path> --built-at <timestamp> --output <path>` | `portable` | none | Writes a local index artifact. | Fails closed on unknown rights or authority; never defaults to visible. |
| `performing-fire-corpus search query --index <path> --authority <path> --audience <operator\|researcher\|public> --current-time <timestamp> --output <path>` | `portable` | none | Writes a local answer artifact. | Empty facets and empty results rather than a leak; no signed URL is ever emitted. |
| `performing-fire-corpus search export-scores --index <path> --authority <path> --audience <operator\|researcher> --current-time <timestamp> --output <path>` | `portable` | none | Writes a local feature export. | Refuses the `public` audience; emits exact keys, never content or a grant. |
