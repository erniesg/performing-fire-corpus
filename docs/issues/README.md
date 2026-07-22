# Rucksack Issue Ledger

This directory stores reviewable markdown issue specs for `erniesg/performing-fire-corpus`.

Generate or update specs with:

```bash
rucksack github issues plan erniesg/performing-fire-corpus --repo-root . --issue-dir docs/issues --execute
```

After reviewing the generated specs, seed or update held ledger issues without
making them runnable:

```bash
rucksack github issues seed erniesg/performing-fire-corpus --issue-dir docs/issues --label rucksack-ledger --execute
```

Seeding preserves GitHub issue state and never reopens a closed marker-matched
issue. Reopen only a reviewed unfinished issue explicitly before seeding:

```bash
gh issue reopen ISSUE_NUMBER --repo erniesg/performing-fire-corpus
```

The GitHub queue workflow is skipped by default. Only after the candidate and
publisher credential boundary is reviewed and live-proven may an authorized maintainer
activate GitHub queue orchestration explicitly:

```bash
gh workflow enable rucksack-autopilot.yml --repo erniesg/performing-fire-corpus
gh variable set RUCKSACK_AUTOPILOT_ENABLED --repo erniesg/performing-fire-corpus --body true
rucksack autopilot reconcile erniesg/performing-fire-corpus --issue-dir docs/issues --queue-after-activation --execute
gh workflow run rucksack-autopilot.yml --repo erniesg/performing-fire-corpus -f action=queue
```

The reconcile command verifies that the variable is exactly `true`, the
workflow is active, and its default-branch content exactly matches the safe
generated contract before it adds any queue label. Each synchronized issue also
records a versioned source marker with the repo-relative spec path, spec ID,
source commit, and observed default branch. A spec is queueable only while that
commit remains reachable from a freshly fetched current default branch; otherwise
it stays held until the source is committed/merged and reconciliation runs again.

That variable activates GitHub issue/label orchestration only. It does not
enable VM drains, hosted agent builds, deploys, or automatic merge.

After activation, GitHub issues are the live queue. Use `/rucksack run #123`, `/rucksack queue`,
or labels such as `rucksack-queued` and `rucksack-run` to dispatch work. When
Rucksack asks for a decision, reply `/rucksack accept`, `/rucksack approve`, or
`/rucksack resolve` on the issue to clear decision/blocker labels and queue it.
For human-unlocked gates that need trusted local or VM proof first, reply
`/rucksack accept-proof #123`; GitHub will hold the issue for
`rucksack autopilot resolve erniesg/performing-fire-corpus --issue 123 --decision accept --repo-root <checkout> --run-post-unlock --execute`
instead of dispatching cloud work immediately.
When review evidence is accepted, use `/rucksack done #123` or
`rucksack autopilot resolve erniesg/performing-fire-corpus --issue 123 --decision done --execute` to
close the reviewed issue without dispatching more implementation work.

Issue specs may include an optional `## Provider` section to route one issue
away from the repo default:

```markdown
## Provider

claude
```

`provider` accepts `codex-action`, `vm-codex`, or `claude` while unmarked issues
use the repo default. `vm-codex` and `claude` route to supervised trusted-VM
invocation; `codex-action` is held fail-closed until hosted build and
publication have separate token boundaries. Specs may also include top-level
`depends-on: 001,002` metadata immediately under `# Title`, or a `## Depends on`
section with comma-separated or bullet-listed spec ids:

```markdown
## Depends on

- 001
- 002
```

Both dependency forms keep an issue queued until each dependency is closed or
labeled `rucksack-awaiting-review`; dependency cycles are rejected when specs
are seeded.

For local-first overnight checks before a remote queue exists, select the next
ready checked-in spec without requiring GitHub:

```bash
rucksack autopilot next erniesg/performing-fire-corpus --repo-root . --issue-dir docs/issues --provider vm-codex
```

Local-only ledgers can mark reviewed or held specs with top-level `state: done`
or `labels: rucksack-blocked`; the GitHub-backed queue remains the live source
once issues are seeded. After a local work slice validates, prepare PR-ready
text without requiring an `origin` remote:

```bash
rucksack autopilot submit erniesg/performing-fire-corpus --issue 123 --repo-root . --execute
```

## Label state machine

These labels are Rucksack's GitHub Issues state machine. They are not topic
tags; they let GitHub Actions, a trusted VM, and humans recover queue state from
the repository without a separate database.

| Label | Meaning |
|---|---|
| `rucksack-ledger` | Generated or synced from markdown specs. |
| `rucksack-queued` | Ready for the queue drain. |
| `rucksack-run` | Manual run-this-now trigger. |
| `rucksack-running` | Build workflow is running or leased. |
| `rucksack-needs-clarification` | Definition of done is unclear; ask/ping before building. |
| `rucksack-needs-decision` | Rucksack recommended a path and needs human approval. |
| `rucksack-needs-human` | Human login/secret/action is required. |
| `rucksack-provider-limited` | Provider quota or subscription limit paused this issue. |
| `rucksack-out-of-work` | No ready implementation work remains; recommend or seed more. |
| `rucksack-blocked` | Failed, held, or waiting on external action. |
| `rucksack-awaiting-review` | PR/evidence is ready for review. |
| `rucksack-review-feedback` | Actionable review feedback was found and repair was requeued. |
| `rucksack-waiting-checks` | Fresh PR checks are still pending or missing. |
| `rucksack-waiting-rereview` | PR is waiting for reviewer re-review. |
| `rucksack-merge-ready` | PR is eligible for merge-policy handling. |
| `rucksack-merge-blocked` | Guarded merge policy does not authorize this PR head. |

The installed `.github/workflows/rucksack-ledger.yml` workflow intentionally
refuses hosted generative planning without checking out the repository or
receiving GitHub/OpenAI credentials. Run the local plan command on the trusted
VM, review the generated specs, and seed only the approved files.
