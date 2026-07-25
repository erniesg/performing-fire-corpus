# Rights-aware derived-media workflows

This contract governs OCR, transcription, and video understanding for the
deliberately selected corpus. It does not authorize any live transformation,
grant a source right, or approve a tool. The portable implementation in
`performing_fire_corpus.derived_media` plans and validates records only; it
never executes a tool, opens a network connection, or touches media bytes.

## Separate profiles, never one opaque extraction job

Each operation has its own versioned transformation profile, its own output
record type, and its own output schema:

| Operation | Profile output | Output schema |
| --- | --- | --- |
| `ocr` | `ocr_result` | `schemas/v1/ocr-result.json` |
| `transcription` | `transcription_result` | `schemas/v1/transcription-result.json` |
| `video_understanding` | `video_understanding_result` | `schemas/v1/video-understanding-result.json` |

A profile binds its operation, allowed tool classes and tool IDs, an inclusive
tool-version range, allowed input media types, allowed languages, resource
bounds, retention class, redaction state, the most permissive retrieval
decision it may produce, and a minimum confidence. `external_service_policy` is
always `local_offline_only` and `model_trace_retention` is always `none`. The
profile hash binds every one of those facts, so changing a bound without
re-binding the hash fails validation.

## Admission before a job is queued

`plan_derived_media_job` queues nothing until every one of these is currently
true for the exact input:

- the exact input object key, SHA-256, byte size, media type, and verified raw
  receipt, revalidated through `corpus_objects.validate_object_receipt` so a
  tampered receipt fact cannot assert its own rights;
- a current operation-specific qualification whose decision for this exact
  operation is approved and eligible, revalidated through
  `qualification.validate_asset_qualification`;
- `derivative_policy` of `operation_specific`;
- the selected tool ID, tool class, and tool version inside the profile;
- an explicit language hint the profile allows — a profile always names at
  least one language, and a missing hint is a denial;
- input bytes within the profile resource bounds;
- a current retention authority for the same source and asset, rebound through
  `corpus_objects.build_retention_authority` so an edited expiry or a cleared
  legal hold fails its own hash, with no active legal hold, an unexpired
  retention window, and an unexpired authority window;
- no exact-key tombstone for the input, resolved through an injected
  `DerivedMediaDeletionAuthority` that is always consulted — an unavailable
  authority or a tombstone for a different key is a denial, never permission;
- granted consent for any `project-native-` source. `consent_state` is a closed
  label — `granted`, `not_applicable`, or `withdrawn` — and anything else is
  refused outright rather than echoed into a reason.

Any unmet gate is returned as a stable `dimension:state` reason by
`evaluate_derived_media_admission`, and `plan_derived_media_job` refuses.
Every reason is a fixed literal, so a denial never carries caller text into a
log or an issue. Nothing is inferred: a missing decision is a denial, not a
default.

The queued job inherits the **most restrictive** retrieval decision of the
input receipt and the profile ceiling, and a `blocked` decision can never queue
a transformation.

## Derived outputs are separated and content-free

Every result record separates the derived facts from the source object: the
output key and hash must differ from the input key and hash. The facts
themselves carry no content.

- **OCR** records per-page layout and token facts — page dimensions, block,
  line, and word counts, and per-page confidence. No recognized text.
- **Transcription** records timed segments — start, end, word count, and
  confidence per segment, plus media duration and detected language. No
  transcript text and no waveform.
- **Video understanding** records bounded shot or event observations from a
  closed label vocabulary, each with a time range and confidence. No frames.

Every result asserts `interpretation: model_output_not_ground_truth`,
`source_excerpt_retention: none`, and `model_trace_retention: none`;
transcription adds `waveform_retention: none` and video understanding adds
`frame_retention: none`. No prompt, chain-of-thought, provider response,
temporary frame, waveform, or source excerpt is representable in these
schemas, and every record is rejected if the central redaction module would
change it. Derived content itself remains in R2 under its rights class.

Result records also bind tool ID, tool class, tool version, contract version,
the deterministic parameters hash, input and output hashes, observed and
minimum confidence, redaction state, retention class, retrieval decision, the
rights snapshot, and a sanitized evidence reference. Counts, indices, and
confidence aggregates must agree with the facts they summarize, and identity
and record hashes are self-binding.

## Conflicts and quality states

A job hash binds only itself, so a job that never passed admission can still
look internally consistent. `evaluate_derived_media_conflicts` is the one
review that holds both the job and its exact profile, and it re-checks the tool
identity, tool-version range, input media type, operation, resource bounds, and
retrieval ceiling against that profile. It reports stable codes rather than
guessing:

- `tool_not_allowed`, `media_type_not_allowed`, `profile_operation_mismatch`,
  `resource_bounds_drift`, `retrieval_decision_too_permissive` — a job that
  contradicts the profile it claims;
- `duplicate_transformation` — a prior job with the same operation, profile ID,
  profile version, and input hash;
- `conflicting_output_receipt` — results for one job disagreeing on output key
  or output hash;
- `low_confidence` — an observed confidence below the profile minimum;
- `unsupported_language` — a detected language the profile does not allow;
- `tool_version_drift` — a result tool version that differs from the admitted
  job or falls outside the profile range.

## Deletion, revocation, withdrawal, and expiry propagation

`propagate_derived_media_deletion` carries one obligation to every descendant.
Scope follows the record that decided it:

- `consent_withdrawn`, `retention_expired`, and `rights_revoked` are decided
  for a whole **asset** — the qualification, consent, and retention authority
  are all keyed by source and asset — so they reach every derivative of that
  asset, not only the one object named in the trigger;
- `exact_key_deleted`, `source_corrected`, and `transformation_replaced` name
  one exact **object**, and match on source, asset, input object key, and input
  hash together.

Neither scope may leak into the other. Whichever scope applies, the sweep then
closes transitively: a derivative whose input is a swept output inherits the
same obligation.

Every job in this contract takes a verified **raw** receipt as its only input,
so in normal operation the descendant graph is exactly one level deep and that
closure converges immediately. Chained derivation is out of scope here and
would need its own admission review before it is allowed — but the propagation
never assumes it, because silently leaving a chained record behind would be
indistinguishable from a finished sweep.

Index and export entries are identifiers only. Anything that is not a bounded
identifier is refused rather than copied into the plan, so a deletion plan is
always safe to log.

The plan lists every affected result ID and derived object key, every index
document and field to remove with `remove_exact_field`, and every downstream
score-generation export. `derived_data_treatment` selects `delete` or
`review`. Any index or export entry naming a result the caller did not supply
is reported in `unresolved_result_ids` and clears `complete`, so a partial
inventory can never present itself as a finished propagation.

## Human authority

Approve each operation independently. Prefer local offline tools. Retain only
structured outputs and provenance in R2, and apply the most restrictive input
rights to every derivative and index entry. Ask for an operation-specific
decision when a selected asset has no allowed tool class or transformation
right, and keep the job blocked meanwhile — never attach content to that
request.

## Offline verification

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```
