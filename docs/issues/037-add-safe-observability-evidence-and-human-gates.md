# Add safe observability, evidence, and actionable human gates

depends-on: 013,014,027,029,030

## Goal

Apply one cross-cutting safety contract to discovery, object storage, trusted workers, search, and project-native lifecycle: privacy-safe observability, rate and retry state, dynamic-transit and log redaction, secret scanning, exact-head evidence, and human blockers with an exact safe action and resumable state.

## Acceptance tests

- Define versioned sanitized event, metric, run manifest, evidence reference, blocker, human decision, and resume-token contracts. Every record identifies operation, stable IDs, lane, policy version, attempt, bound consumption, outcome code, and evidence time.
- Prohibit response bodies, source prose, media, captions, transcripts, prompts, private proposal or meeting content, personal names or duties, contacts, comments, credentials, account identifiers, endpoints, signed URLs, cookies, headers, provider error bodies, and machine-local paths from logs, metrics, traces, transit envelopes, evidence, and issues.
- Add centralized structured redaction and allowlisted serialization before output. Unknown fields, bytes, exception objects, and nested provider payloads fail closed rather than being stringified.
- Emit per-source and per-worker request, byte, page, retry, rate, lease, checkpoint, queue-age, storage, transformation, deletion, and blocker metrics without content or high-cardinality secrets.
- Make every human gate actionable: missing authority class, privacy-safe question, recommended response, exact next safe action, unblocking command class, expiry, and durable resumable checkpoint. One blocked job does not hold unrelated work.
- Extend secret scanning and repository-content tests across specs, docs, fixtures, logs selected for evidence, and generated sanitized manifests using invented canaries.
- Tie evidence to exact commit and exact-head review. Document red/green/refactor TDD, small focused PRs, required local evidence, guarded review, and no merge bypass.
- Record private GitHub Actions held by billing or spending limits as `held`, never `passed` or `failed`; local or trusted-VM evidence may satisfy only the lanes it actually ran.
- Route any discovered Rucksack product defect or improvement to a separate privacy-safe issue in `erniesg/rucksack`; do not implement Rucksack product changes in this corpus repository.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

No secret values are allowed in observability or evidence. Workers may know separately authorized secret names, but serializers expose only the name and `present` or `missing`.

## Artifact outputs

- New observability, blocker, decision, and evidence schemas under `schemas/`
- New safe serializer, metric, and gate modules under `src/performing_fire_corpus/`
- New canary redaction, secret-scan, blocker, resume, and held-CI tests
- New evidence and operator-gate documentation under `docs/`

## Stop conditions

- Stop if any logger, metric, trace, issue, or evidence path can stringify arbitrary inputs or provider exceptions.
- Stop if a human gate omits the exact safe action, required authority, expiry or review trigger, and resumable state.
- Stop if held CI is represented as run evidence or if exact-head state cannot be established.
- Stop if a Rucksack defect is mixed into corpus feature implementation.

## Human clarification protocol

Ask only when a real blocker lacks the authority needed for its exact next safe action. Use stable IDs and sanitized codes, recommend the least permissive response, include the resumable checkpoint, and never request secret values or protected material.

## Recommended response

Adopt allowlisted structured events and content-free metrics, preserve blockers as first-class state, and accept local or trusted-VM evidence only for the lanes actually executed while hosted Actions remain held.

## Trade-offs

Strict allowlists reduce diagnostic detail and require explicit schema updates, but sharply limit leakage. Fine-grained blockers add ledger volume while making parallel progress and human intervention recoverable.

## Free-form response

Optional maintainer notes or alternate privacy-safe evidence rule:
