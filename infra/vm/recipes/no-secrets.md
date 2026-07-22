# No-Secrets Recipe

Do not store secrets in repo files, templates, generated manifests, logs, issues,
pull requests, or chat.

Use names and paths only in tracked files:

- secret names such as `APP_IMAGE`, `HEALTHCHECK_URL`, or `TAILSCALE_AUTHKEY_FILE`;
- local root-owned files such as `/etc/rucksack-app/app.env`;
- platform secret stores or human vault workflows.

When running commands, avoid echoing env files and avoid URLs with tokens in
`HEALTHCHECK_URL`. If output contains a secret, stop and rotate it before
publishing artifacts.
