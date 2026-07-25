# Safe observability and evidence contract

One cross-cutting safety contract covers discovery, object storage, trusted
workers, search, and the project-native lifecycle. Every diagnostic record this
repository writes is built by `performing_fire_corpus.observability`. Nothing
else may write a log line, metric, trace, transit envelope, issue body, or
evidence file.

## Versioned contracts

Seven strict v1 schemas carry every diagnostic fact:

| Record | Schema |
|---|---|
| sanitized event | `schemas/v1/observability-event.json` |
| content-free metric | `schemas/v1/observability-metric.json` |
| run manifest | `schemas/v1/run-manifest.json` |
| evidence reference | `schemas/v1/evidence-reference.json` |
| operator blocker | `schemas/v1/operator-blocker.json` |
| human decision | `schemas/v1/human-decision.json` |
| resume token | `schemas/v1/resume-token.json` |

Every one of them is `additionalProperties: false`, pins `schema_version: 1`,
and carries the same envelope: `operation`, stable `subject_ids`, `lane`,
`policy_version`, `attempt`, `bound_consumption`, `outcome_code`, and
`evidence_time`. `bound_consumption` reports exactly `requests`, `bytes`,
`pages`, `retries`, and `elapsed_seconds`, so any record can be read as bound
consumption against a declared budget.

## Prohibited content

The following are forbidden in logs, metrics, traces, transit envelopes,
evidence, and issues, with no exception and no configuration switch:

- response bodies, source prose, media, captions, transcripts, and prompts
- private proposal or meeting content
- personal names or duties, contacts, and comments
- credentials, account identifiers, endpoints, signed URLs, cookies, headers
- provider error bodies
- machine-local paths

## Fail closed, never stringify

`safe_serialize` is an allowlist, not a filter. It accepts mappings with
snake_case keys, sequences, booleans, finite numbers, `None`, and bounded
content-free text. Everything else raises `ObservabilityError`:

- bytes, `bytearray`, and `memoryview` are refused, not decoded
- exception objects are refused, not `str()`-ed
- nested provider payloads are refused by key shape and by the strict schema
- unknown fields are refused by `additionalProperties: false`
- sets, dates, and arbitrary objects are refused rather than coerced to text

Text is refused when it exceeds the bound, carries control characters, changes
under the central redaction module, embeds a signed or credential-bearing URL,
or resembles a credential. There is no code path that turns an unrecognized
input into a diagnostic string.

## Content-free metrics

`METRIC_DEFINITIONS` fixes the name, kind, and unit of every metric, covering
request, byte, page, retry, rate, lease, checkpoint, queue-age, storage,
transformation, deletion, and blocker signals. Dimensions are exactly
`source_id`, `worker_id`, `lane`, and `operation`, each pattern-bounded, so a
metric can never carry content or a high-cardinality identifier.

## Secret names only

Workers may know separately authorized secret names. `secret_presence` reports
only the name and `present` or `missing`. No serializer accepts a secret value,
and no record has a field that could hold one.

## Secret scanning with invented canaries

`tests/test_secret_canaries.py` assembles invented canaries at run time from
fragments, so the whole value never appears in the repository. Those same
constants then prove both directions: the detector catches them, and the specs,
docs, config, schemas, infrastructure recipes, fixtures, selected evidence logs,
and generated sanitized manifests do not contain them.

## Exact-head evidence

An evidence reference binds an artifact digest to one exact commit. Building one
requires the observed head to equal that commit; a drifted or unestablished head
raises instead of producing evidence. A run manifest repeats the same check and
refuses any evidence reference from another commit.

## Held is not passed

Private GitHub Actions withheld by billing or spending limits are recorded as
`held`, never `passed` and never `failed`. A held lane must state why it is
held and must carry no evidence reference, because held CI is not run evidence.
A lane that did not run is `skipped` and also carries no evidence. Local and
trusted-VM evidence may satisfy only the lane it actually ran: the evidence
reference must name the same lane and the same status as the lane result.

## Working agreement

- Red/green/refactor TDD: write the failing test that states the contract, make
  it pass with the smallest safe slice, then refactor with the suite green.
- Small focused PRs: one spec, one contract, one reviewable diff.
- Required local evidence: run `scripts/agent-evidence` and attach the manifest
  before claiming completion.
- Guarded review: `.agent/pr-policy.yaml` governs stewardship and
  `.agent/merge-policy.yaml` is the only merge authority.
- No merge bypass: branch protection is never bypassed from a VM, and resolving
  a human gate is not merge approval.

## Rucksack defects are routed, not implemented

A defect or improvement discovered in the Rucksack product itself is filed as a
separate privacy-safe issue in `erniesg/rucksack`. It is never fixed inside this
corpus repository and never mixed into a corpus feature change.

See `docs/operator-gates.md` for the human-gate half of this contract.
