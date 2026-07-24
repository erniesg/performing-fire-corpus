# Revalidate the bounded public ANTIEGG metadata flow

depends-on: 005,007

## Goal

Run the smallest current `network-acquisition` proof against the existing `antiegg-fluxus` adapter, treating the earlier robots, response-size, and inventory observations as expired hypotheses. Confirm only bounded public metadata behavior, durable resume, and sanitized evidence; do not acquire prose, HTML bodies, media, or object-storage content.

## Acceptance tests

- Run from an exact clean checkout on a trusted VM with the existing documented limits: at most two requests, a ten-second request timeout, a two-second per-host interval, one retry, thirty seconds elapsed time, and 262,144 response bytes.
- Before any request, add and test an exact `.gitignore` rule for `.local/network-smoke/`; the current checkout does not ignore that documented live-state directory. Then use a new ledger and manifest there rather than any earlier live ledger, cookie, token, cache, or response.
- Recheck current robots rules before the metadata request and record only sanitized public URL, status class, MIME type, declared or observed bytes, safe hash when retained, retry outcome, timestamp, checkpoint, and next safe action.
- Treat robots denial or ambiguity, `401`, `403`, login, rate exhaustion, redirect mismatch, changed shape, unsupported MIME type, or oversize as a durable blocker. Do not raise bounds or switch endpoints during the run.
- Interrupt and resume only if the bounded run reaches a durable nonterminal checkpoint; otherwise document why an interruption proof was not applicable. A completed rerun makes no duplicate request or ledger record.
- Compare the current result with `docs/metadata-readiness-proof.md` as historical evidence, not as a current source fact, and update only sanitized aggregate evidence when the observations differ.
- Confirm that no source body, raw HTML, article prose, media, caption, transcript, provider detail, credential, account identifier, endpoint value, or machine-local path enters Git, logs selected for publication, or the aggregate proof.
- Keep this proof independent of the one-object R2 gate. Its success or failure neither defines corpus completeness nor authorizes acquisition.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

On a trusted VM only, after the portable checks pass, run the existing command in `docs/network-acquisition-smoke.md`.

## Allowed secrets

None. The flow is public, unauthenticated, and metadata-only. If a source requests authentication, persist a blocker and stop.

## Artifact outputs

- Ignored live ledger and sanitized manifest under `.local/network-smoke/`
- Reviewed `.gitignore` entry and repository-content test for the live-state root
- Sanitized evidence manifest outside normal commits
- Optional aggregate-only update to `docs/metadata-readiness-proof.md`
- Durable blocker with an exact next safe action when a current gate fails

## Stop conditions

- Stop on robots denial or ambiguity, access control, login, `401`, `403`, exhausted rate or retry budget, disallowed redirect, unexpected MIME type or structure, or configured byte or time exhaustion.
- Stop if any proposed workaround uses old tokens, cookies, signed URLs, browser state, alternate identities, or increased bounds.
- Stop before retaining or transferring source prose, raw HTML, media, captions, transcripts, or private material.
- Stop if the checkout or evidence cannot be tied to an exact commit, or if generated local state would be staged for Git.

## Human clarification protocol

No human decision is required to execute the bounded metadata-only run. Ask only if the run produces a blocker with two materially different safe adapter changes and the choice determines the next executable issue. Provide sanitized facts, recommend the narrower metadata endpoint, and leave room for a different privacy-safe response.

## Recommended response

Accept a current fail-closed blocker as valid pipeline evidence. If the article remains oversized, keep the bound and proceed with the separately specified public sitemap or WordPress metadata adapter rather than fetching the article body.

## Trade-offs

A two-request proof cannot establish ANTIEGG completeness, but it cheaply revalidates robots, bounds, resume, and evidence hygiene. Refusing to raise the response cap may delay discovery while preserving the metadata-only boundary.

## Free-form response

Optional maintainer notes about the sanitized outcome:
