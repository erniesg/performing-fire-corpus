# Agent Environment

Names only. Do not write secret values in this file.

- Repository: `performing-fire-corpus`
- OS detected during scaffold: `macOS-15.6.1-arm64-arm-64bit`

Issue ledger:

- Run `rucksack github issues pack --list` to see reusable issue spec packs.
- Run `rucksack github issues pack --archetype python-library --issue-dir docs/issues` as a dry-run, then add `--execute` after selecting the right archetype.
- Run `rucksack github issues seed OWNER/REPO --issue-dir docs/issues` as a dry-run before creating or updating GitHub issues.

Storage policy:

- Run `rucksack storage init --repo-root . --provider r2 --bucket <bucket> --execute` to write `.agent/storage.yaml`.
- For R2-backed apps, run `rucksack ci resources setup OWNER/REPO --resource r2 --mode existing --bucket <bucket> --environment live --execute` to name storage env/secrets, fill a missing `.agent/storage.yaml` bucket, and create ignored `.env.live` resource recipes.
- Run `rucksack ci env collect --environment live --repo-root . --execute`, then `rucksack ci inspect OWNER/REPO --target workers --environment live --repo-root .` and `rucksack ci setup OWNER/REPO --target workers --environment live --repo-root . --execute --yes` after reviewing the redacted mapping.
- Store generated media, model outputs, traces, and ingest corpora in object storage or GitHub artifacts, not normal commits.
- Commit secret names and storage locations only; collect secret values with `rucksack ci secrets collect` or the provider secret store.

Common account bootstrap checks:

- `gh auth status` for GitHub write/read operations.
- `bw status` for human Bitwarden vault status, if used interactively.
- `bws project list` for Bitwarden Secrets Manager machine access, if configured.
- `wrangler whoami` for Cloudflare projects.
- `tailscale status` for trusted VM access.
- `infra/vm/verify.sh` for reusable VM service, port, disk, memory, and Tailscale checks.

Agents should request human setup instead of asking for secrets in chat.
