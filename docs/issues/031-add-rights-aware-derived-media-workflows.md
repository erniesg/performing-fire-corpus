# Add rights-aware OCR, transcription, and video-understanding workflows

depends-on: 013,027,030

## Goal

Define bounded, operation-specific derived workflows for OCR, transcription, and video understanding. Produce structured, provenance-rich derived objects only where rights, consent, privacy, and retention records explicitly allow the transformation.

## Acceptance tests

- Define separate versioned transformation profiles for OCR, transcription, and video understanding rather than one opaque extraction job.
- Require exact input object key and hash, current transformation-specific rights, allowed tool or model class, language or media hints, resource bounds, output schema, retention, redaction, and retrieval eligibility before a job is queued.
- OCR outputs separate layout or token facts from source-page images; transcription separates timed text from audio; video understanding separates bounded shot or event observations from source frames and raw model traces.
- Store no unrestricted prompt, chain-of-thought, provider response, temporary frame, waveform, or source excerpt in manifests, logs, issues, or Git. Derived content remains in R2 under its rights class.
- Record tool or model version, deterministic parameters where available, input and output hashes, confidence or uncertainty, redaction status, provenance, and evidence without claiming model output is factual ground truth.
- Propagate rights revocation, consent withdrawal, retention expiry, and exact-key deletion from raw input to every derived object, index entry, and downstream score-generation export.
- Detect duplicate transformations by profile and input hash, conflicting output receipts, low-confidence or unsupported language states, and tool-version drift.
- Add synthetic content-free tests and small invented byte fixtures for each workflow, including denial, resource exhaustion, interruption, resume, deletion propagation, and sanitized manifests.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None for portable workflows and tests. Any proprietary local tool, model credential, or subscription must be approved in a separate trusted-laptop issue and may never enter Git or evidence.

## Artifact outputs

- New OCR, transcription, and video-understanding profile and result schemas
- New workflow planners and validators under `src/performing_fire_corpus/`
- New synthetic transformation, provenance, and deletion tests
- New derived-data safety documentation under `docs/`

## Stop conditions

- Stop if the exact transformation is not explicitly allowed by current rights and consent records.
- Stop if a tool requires uploading protected content to an unapproved external service or retaining raw prompts, traces, frames, or source excerpts.
- Stop on resource exhaustion, unsupported media or language, output conflict, low-confidence policy threshold, or deletion obligation.
- Stop if derived content could be committed, placed in issue text, or exposed through public evidence.

## Human clarification protocol

Ask only when a specific selected asset is ready for derivation but its allowed tool class or transformation right is absent. Recommend local offline processing or keeping the job blocked, and request an operation-specific decision without attaching content.

## Recommended response

Approve transformations independently, prefer local offline tools, retain only structured outputs and provenance in R2, and apply the most restrictive input rights to every derivative and index entry.

## Trade-offs

Separate profiles add orchestration work but make rights and quality evaluation precise. Avoiding raw model traces reduces debugging detail while protecting copyrighted and private material.

## Free-form response

Optional maintainer notes or alternate transformation boundary:
