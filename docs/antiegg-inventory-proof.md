# Bounded ANTIEGG source-universe inventory

> **Superseded 2026-07-26.** The governance decisions this run stopped on are
> now recorded in `config/source-governance.v1.json`. A later bounded run
> reached the network (robots 200, article fetched at 200) and stopped on
> `response_structure_changed` instead. This document remains the accurate
> record of the earlier run; it is not the current source state.

This is the sanitized aggregate result of issue 27's trusted-VM run on
2026-07-26. The outcome is `blocked`. It is current evidence that the policy
gate held, not evidence of endpoint or site completeness.

The run started from clean exact commit
`fcda3289f261687bd84a43e9bcf5f7bb26d5d8f6`. Its reviewed plan allowed at most
6 requests, 2 pages per endpoint, 262,144 bytes per response, 786,432 aggregate
response bytes, no retries, a 2-second per-host interval, a 10-second request
timeout, and 90 seconds elapsed. Live run plans, request facts, manifests,
provider details, and command output remain under ignored local state.

## Current gate observations

One unauthenticated, bounded request revalidated `robots.txt`: status 200,
`text/plain`, 168 bytes, safe SHA-256
`9e9d7afdc935dd5b9234e1b8ee11f004873e01b0a59df821da6dde2df42ed58f`.
The rules allowed both registered paths for the inventory user agent.

Robots allowance did not authorize either endpoint request. The current
endpoint-specific governance records still classify API availability, access
control, authentication, platform terms, and copyright or lawful basis as
`unknown`; metadata inventory and retention remain `pending`. The production
adapters also remain held until their real live response and pagination shapes
are reviewed. The run therefore stopped before requesting either source
endpoint.

| Endpoint | Observed source content types | Unique stable IDs | Duplicate or alias | Rejected unsafe fields | Blockers | Unvisited remainder |
|---|---:|---:|---:|---:|---:|---:|
| `antiegg-posts-api` | 0 | 0 | 0 | 0 | 1 | unknown |
| `antiegg-sitemap` | 0 | 0 | 0 | 0 | 1 | unknown |

Both blockers have code `endpoint_decisions_missing`. The next safe action for
each endpoint is to review and record its current availability, access,
authentication, applicable terms, lawful basis, metadata-inventory, and
retention decisions, then bind the production adapter to a reviewed live
metadata and pagination shape. Approval for one endpoint must not authorize the
other.

No source page was allowed, so no page checkpoint could exist and the
multi-page resume condition was not applicable. Re-evaluating the terminal
state made no additional request, created no duplicate, and returned the same
aggregate result.

No R2, media, prose, caption, transcript, embedding, browser-session,
credential, personal-data, or object-storage operation occurred. No response
body, source prose, machine-local path, live ledger, provider detail, or command
log is committed here.
