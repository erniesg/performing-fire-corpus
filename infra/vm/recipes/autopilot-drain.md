# Rucksack Autopilot Drain Timer

This repo includes a held, future user-level systemd timer for a trusted VM to
check the queue every 30 minutes and drain VM-routed GitHub Issues into detached
Codex/Claude sessions. The installer keeps every drain held by default. One VM
account may own both provider login and publisher credentials only after the
fixed bubblewrap and system-manager network-isolation proofs pass.

Prerequisites on the VM:

```bash
command -v rucksack
command -v gh
command -v bwrap
sudo -n systemd-run --system --wait --collect --pipe /bin/true
rucksack github token erniesg/performing-fire-corpus --role developer --execute >/dev/null
rucksack github token erniesg/performing-fire-corpus --role publisher --execute >/dev/null
```

Install or update the held timer and credential-gated daily CLI updater from the repository root:

```bash
rucksack vm autopilot install-timer erniesg/performing-fire-corpus --repo-root . --profile trusted-worker --execute
```

Activate only this repository drain after the live isolation probes pass:

```bash
rucksack vm autopilot install-timer erniesg/performing-fire-corpus --repo-root . --profile trusted-worker --enable-drain --isolation bubblewrap --execute
```

Configure Discord notifications on the VM if you want human-gate pings outside
GitHub. The command opens an SSH prompt and stores the webhook only in the VM
user environment file:

```bash
rucksack vm autopilot discord erniesg/performing-fire-corpus --profile trusted-worker --execute
```

Manual equivalent for the repo-specific queue timer only:

```bash
mkdir -p ~/.config/systemd/user
cp infra/vm/systemd/rucksack-autopilot-v1-ZXJuaWVzZy9wZXJmb3JtaW5nLWZpcmUtY29ycHVz-drain.service ~/.config/systemd/user/
cp infra/vm/systemd/rucksack-autopilot-v1-ZXJuaWVzZy9wZXJmb3JtaW5nLWZpcmUtY29ycHVz-drain.timer ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user disable --now rucksack-autopilot-v1-ZXJuaWVzZy9wZXJmb3JtaW5nLWZpcmUtY29ycHVz-drain.timer
systemctl --user status rucksack-autopilot-v1-ZXJuaWVzZy9wZXJmb3JtaW5nLWZpcmUtY29ycHVz-drain.timer
```

`loginctl enable-linger "$USER"` keeps the user service manager available after
the SSH session disconnects. It does not enable the held drain timer.

The recommended `rucksack vm autopilot install-timer` command leaves the drain
timer disabled unless activation is explicit and both isolation probes pass. It
embeds the host-global updater from the trusted local Rucksack package only
when that account has no publisher credentials; otherwise it preserves both
updater definitions in the user systemd data directory behind higher-priority
masks. This updater hold is independent of drain activation: it prevents an
automatic package update from changing trusted publisher code underneath the
reviewed runtime.
It never executes an updater copied from the target project
checkout. When enabled, managed Codex and Claude Code CLIs update together once
daily with jitter. Busy workers and
transient npm failures retry at bounded intervals, while permanent local-path or
tooling errors wait for the next daily run after an operator repairs the VM.
The 30-minute queue drain no longer runs a package update on every poll.

Inspect runs:

```bash
journalctl --user -u rucksack-autopilot-v1-ZXJuaWVzZy9wZXJmb3JtaW5nLWZpcmUtY29ycHVz-drain.service -f
```

Manual equivalent:

```bash
rucksack autopilot review-repair erniesg/performing-fire-corpus --provider vm-codex --execute
rucksack autopilot self-heal erniesg/performing-fire-corpus --repo-root . --provider vm-codex --request-review codex --execute
rucksack autopilot work-queue erniesg/performing-fire-corpus --provider vm-codex --max-workers 1 --local --repo-root . --issue-dir docs/issues --reconcile-issues --check-provider-ready --notify-github-when-blocked --execute
```

The queue drain first inspects unresolved review threads and requeues safe
same-repository repair work. Only after that succeeds does self-heal classify
remaining awaiting-review PRs into waiting for checks, waiting for re-review,
merge-ready, or merge-blocked states. It then
checks Codex/Claude readiness after selecting runnable work but before acquiring
VM leases. If provider login is missing or expired, a supervised manual drain
leaves issues queued and refreshes the human-gate notification instead of
starting failing workers. The timer follows the same behavior after explicit
activation and successful isolation proof.

The held timer deliberately omits `--plan-when-idle`: a planner may write
partial or successful issue specs, and the durable base checkout must stay
clean. Generate and commit new ledger specs through an operator-owned
disposable worktree before the VM reconciles them.

Review repair fails closed on GraphQL errors, malformed or truncated GitHub
review data, and ambiguous App marker identity. It trusts the configured
Rucksack App slug for reads and updates only markers owned by the active
OAuth user. App-authored markers are append-only, and every new marker POST is
verified against the configured App or current owner before labels move. Repair pushes
compare the expected source SHA, hold the issue during post-push verification,
and return it to review only after the completion marker and final label
transition are both durable. Repeated 30-minute passes recognize the trusted
exact-head repair marker and leave queued or running repair workers unchanged.

Add `--notify-when-blocked` to the manual queue drain only after configuring the
VM Discord webhook with `rucksack vm autopilot discord`; GitHub notification
works without Discord.

When GitHub comments a `vm-codex` or `claude` provider handoff, run the VM
issue worker from the trusted machine:

```bash
rucksack autopilot work erniesg/performing-fire-corpus --issue ISSUE_NUMBER --provider vm-codex --execute
```

The worker uses the existing VM checkout and provider auth store. Candidate
code runs without GitHub credentials, seals a publication bundle, and hands
that data to fixed parent code. The fixed publisher independently verifies the
bundle and evidence, mints the narrow `publisher` role token, pushes the exact
result commit, and opens or reuses the exact PR.

The Codex CLI still receives `--sandbox danger-full-access`, but only inside
Rucksack's fixed bubblewrap boundary. That boundary exposes one writable
worktree, mounts Git metadata and the provider executable read-only, and copies
only a bounded provider login/config allowlist into bubblewrap's tmpfs-backed
home. Prior histories, databases, sessions, and caches are omitted; skills and
plugins remain read-only. The host provider store is never mounted writable.
The boundary hides the VM home, App PEM, privileged sockets, and host temporary
paths, drops capabilities, disables nested user namespaces, and runs as the
overflow UID/GID. The parent drain runs through a privileged transient system
service with `NoNewPrivileges` and outbound private/metadata address ranges
denied. Activation creates a disposable linked worktree and fails closed unless
that real worker shape passes both live isolation proofs.

The timer file contains only repo names and command flags. Trusted scheduler
commands mint short-lived `developer` tokens; publication separately mints the
exact `publisher` role (`contents:write`, `issues:read`, `metadata:read`, and
`pull_requests:write`) inside the fixed runtime; inherited developer or user
tokens are ignored. The publisher uses a disposable bare transport with
system/global config ignored, hooks disabled, a fixed GitHub HTTPS URL, and an
expected-old-SHA compare-and-swap. A transport interruption records a bounded
retryable attempt, not a consuming receipt, so recovery re-observes GitHub and
finishes only the remaining mutation. Provider, validation, repo-hook, and tmux
children never inherit `GH_TOKEN` or `GITHUB_TOKEN`, and the candidate sandbox
cannot read the App configuration or PEM. Keep App PEMs and provider auth
stores on the VM, not in this repository.
