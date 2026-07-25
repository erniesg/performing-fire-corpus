# First metadata-only readiness proof

This records the sanitized result of the issue 7 trusted-VM proof. The clean
checkout was `900e63b` on `codex/issue-7-rucksack`. Generated manifests, the
SQLite ledger, command output, and response bodies remain outside Git.

## Revalidation status

Issue 11 rechecks this proof. Everything recorded below is historical evidence
from the issue 7 run, not a current source fact. Robots rules, response sizes,
and metadata structure may have changed since, so these rows are expired
hypotheses until a current bounded run confirms them.

The issue 11 revalidation has not yet produced a current observation. The
bounded run is a `trusted-vm` `network-acquisition` lane command, and the
headless agent sandbox permits only the portable validation lanes, so no
request was made and no live ledger, manifest, or blocker was created. That is
an environment gate rather than a source observation: it neither supersedes nor
reconfirms the table below. No interruption-and-resume proof was applicable,
because a durable nonterminal checkpoint exists only once a bounded run has
made its first request.

Next safe action: an operator runs the unchanged command in
`docs/network-acquisition-smoke.md` on the trusted VM at the documented bounds,
writing a new ledger and sanitized manifest under the now-ignored
`.local/network-smoke/` live-state root, then updates only sanitized aggregate
rows here when the current observations differ. Do not raise a bound, switch
endpoints mid-run, or reuse an earlier ledger, cookie, token, cache, or
response to obtain a result.

## Bounded run

The selected source was the public `antiegg-fluxus` adapter. The documented
`inventory-public` command ran without credentials with these explicit bounds:

- 2 total requests;
- 10-second request timeout;
- 2-second per-host rate limit;
- 1 retry;
- 30-second elapsed-time limit;
- 262,144 response bytes;
- an explicit VM-local durable ledger and sanitized manifest destination.

The interrupted restart check used the same bounds except for a longer
20-second rate-limit interval and 45-second elapsed limit so the process could
be terminated after its first durable checkpoint. Its sanitized robots
observation was present before termination. Resuming against the same ledger
made only the remaining article request. Repeating the completed command made
no request and emitted a byte-identical manifest. Resume authorization now
requires that robots observation to be no more than 24 hours old and linked to
its matching sanitized request evidence; otherwise robots is fetched again.

## Sanitized observations (issue 7, historical)

| Request | Robots observation | Status | MIME type | Bytes | Safe SHA-256 | Retry outcome |
|---|---|---:|---|---:|---|---|
| `https://antiegg.kr/robots.txt` | catalogue allowed | 200 | `text/plain` | 168 | `9e9d7afdc935dd5b9234e1b8ee11f004873e01b0a59df821da6dde2df42ed58f` | not retried |
| `https://antiegg.kr/25502/` | applicable check passed | 200 | `text/html` | 445,594 declared | not retained because oversized | not retried |

The run discovered one stable source and one blocked asset. It stopped with
`response_oversized` because the declared article response exceeded the byte
cap. The next safe action is to keep the metadata response within the configured
bound, which requires a reviewed smaller metadata endpoint or adapter change;
the bound must not be raised merely to bypass this result.

Immediately after interruption, record counts were 0 assets, 0 jobs, 1 request,
and 0 blockers. After resume they were 1 asset, 1 job, 2 requests, and 1
blocker. A terminal rerun preserved those exact counts, demonstrating that
assets, jobs, requests, and blockers were not duplicated.

The command used unauthenticated public `GET` requests only. It performed no
media, document, caption, transcript, embedding, object-storage download, or R2
upload. No credential was configured or required. No response body was written
to the ledger, manifest, logs, evidence, or repository.

## First-usable-slice gap matrix

| Promise | Passing test, proof, blocker, or follow-up |
|---|---|
| Minimal Python package and CLI | `test_cli_help_is_offline_and_deterministic` and the live `inventory-public` invocation pass. |
| Versioned source, asset, rights, job, lease, object, and evidence schemas | `test_every_schema_is_versioned_strict_and_accepts_its_fixture` passes. |
| State transitions | `test_every_forward_transition_and_failure_branch_is_enforced` passes. |
| Idempotency | `test_records_jobs_checkpoints_and_completion_are_idempotent` and the restart comparison above pass. |
| Redaction | `test_sensitive_fields_bodies_accounts_and_paths_are_redacted` and the sanitized live artifact inspection pass. |
| URL allowlist | `test_checked_in_public_hosts_are_allowed_and_normalized` passes. |
| Rate limiting | `test_rate_limiter_spaces_each_normalized_host_deterministically` passes; the live command used an explicit host interval. |
| Retry policy | `test_retry_after_is_bounded_and_attempts_are_resume_safe` passes; neither live response required a retry. |
| Restart/resume | `test_resume_after_robots_checkpoint_does_not_duplicate_records` and the interrupted live proof above pass. |
| Deterministic fixture discovery and sanitized manifest | `test_cli_is_offline_deterministic_and_idempotent` passes. |
| Bounded public metadata discovery without stored bodies | The live proof above passed fail-closed with a durable oversized-response blocker. |
| R2 readiness reports secret names and presence only and fails closed | Explicit gap: follow-up issue 6 implements and tests this boundary; no R2 action was attempted here. |
| Progress reconstruction after restart | `test_progress_reconstructs_after_restart_and_cli_reports_it` passes, and progress was reconstructed from the interrupted live ledger. |

The live proof is intentionally one-source evidence. It does not establish
full-corpus size, validate other adapters, grant ingestion rights, or authorize
an object transfer.
