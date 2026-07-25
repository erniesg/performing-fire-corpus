# Verification

Select a supported runtime first. `pyproject.toml` declares
`requires-python = ">=3.11"`, and an unsupported interpreter produces a
misleading partial result rather than an honest failure.

```bash
sh scripts/preflight-python
sh scripts/preflight-python -m unittest discover -s tests
```

The preflight prints the selected interpreter, or exits `2` with the exact
versions it found when none satisfies the floor. Pin one interpreter with
`PERFORMING_FIRE_PYTHON`.

Before claiming completion, run the evidence command and attach the manifest.

```bash
scripts/agent-evidence
```

Optional lanes are opt-in:

```bash
scripts/agent-evidence --e2e
scripts/agent-evidence --only=lint,type-check
# If wrapped in npm, pass flags after `--`: npm run agent:evidence -- --e2e
```

Validation lanes discovered:

- `python-test` (required): `python3 -m unittest discover -s tests`.

Only `portable` lanes run here. `network-acquisition`, `trusted-vm`,
`trusted-laptop`, `object-storage`, and `deploy` commands are held and are
listed with their gates in `docs/product-readiness-matrix.md`.

Deploy contract:

- `.agent/deploy.yaml` records provider-neutral deploy and infrastructure gates.
- Deploy, rollback, and infrastructure apply lanes require trusted context and human approval.
- `scripts/agent-evidence` does not execute secret-bearing deploy commands.
- When `.agent/storage.yaml` exists, `scripts/agent-evidence` records large untracked files over `repo_limit_mb` as manifest caveats and artifact entries.
- `infra/vm/verify.sh` is the reusable trusted-VM health and hardening check.

Exit taxonomy:

- `0`: required validation passed
- `1`: required validation failed
- `2`: blocked by missing dependency or environment setup
- `3`: blocked by missing auth/secret or subscription/browser state
- `4`: blocked by required human decision
