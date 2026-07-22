# AI Terminal Recipe

Use this when the VM is the trusted mobile coding terminal for Codex and Claude
Code. The setup is mostly automatable; identity approval is intentionally not.

## What Rucksack can automate

- Discover or save the VM profile and SSH alias.
- Bootstrap baseline guest tools, Rucksack CLI, and a workspace root.
- Install/check Tailscale on the VM and save the tailnet host.
- Authorize a phone-generated SSH public key.
- Install/check Codex CLI and open a `codex` tmux session.
- Install/check Claude Code and open a `claude` tmux session.

## Human approval steps

- Sign in to Tailscale on the VM and iPhone.
- Keep the phone SSH private key on the phone; only copy the public key to the
  trusted Mac/VM setup flow.
- Complete Codex subscription login interactively with `codex login`.
- Complete Claude Max/Pro subscription login interactively by running `claude`.

## Setup from the trusted Mac

```bash
rucksack vm ai-terminal --profile dev-vm --source /path/to/rucksack --device-name iphone --public-key-file ~/Downloads/phone.pub --execute

# Equivalent lower-level commands.
rucksack vm bootstrap --profile dev-vm --source /path/to/rucksack --execute
rucksack vm network tailscale --profile dev-vm --execute
rucksack vm mobile --profile dev-vm --device-name iphone --public-key-file ~/Downloads/phone.pub --execute
rucksack vm codex --profile dev-vm --install --execute --post-login-drain OWNER/REPO
rucksack vm claude --profile dev-vm --install --execute
```

When `--source` points at a local `code/OWNER/REPO` checkout and no explicit
remote root is provided, Rucksack syncs it to `~/code/OWNER/REPO` on the VM.
Saved profile roots and explicit `--remote-root` values are respected as written.

## Login inside the VM tmux session

```bash
codex login
codex login status
claude
```

If the VM cannot open a browser directly, try `codex login --device-auth` and
complete the device flow from a browser where you are signed in. If Codex says
device code authorization is disabled, enable it in ChatGPT Security Settings,
then rerun `codex login --device-auth`. Claude Code prints its own interactive
browser/device login steps when `claude` starts.

Subscription/OAuth login is user-local state on the VM. Do not write
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, access tokens, browser cookies, or
Tailscale auth keys into templates, manifests, logs, issues, pull requests, or
chat. API-key mode is separate from subscription login and should use a
secret-aware runner or vault.

Codex app-server can wrap this later as the natural-language control plane, but
Rucksack keeps setup as explicit, testable CLI primitives. An AI assistant
should parse requests such as "authorize this Termius public key" into
`rucksack vm ai-terminal` or `rucksack vm mobile` calls, then stop at human
login steps instead of copying tokens or browser state.

## Add repositories

```bash
rucksack github connect --owner OWNER --account-type org --execute
rucksack github doctor OWNER/REPO --execute
rucksack github repos add OWNER/REPO --execute
rucksack vm github install-app --profile dev-vm --execute
rucksack vm github doctor OWNER/REPO --profile dev-vm --execute
rucksack github repo clone OWNER/REPO --execute
rucksack vm repo add OWNER/REPO --profile dev-vm --role developer --execute
rucksack vm repo verify OWNER/REPO --profile dev-vm --mode local-write --execute
rucksack vm repo verify OWNER/REPO --profile dev-vm --mode pr --cleanup --execute
rucksack github provisioner connect --owner OWNER --account-type org --execute
rucksack github provisioner repo create OWNER/NEW_REPO --template OWNER/blank-template --execute

# Use local human gh auth or a separate provisioner for new repos.
gh repo create OWNER/REPO --private --template OWNER/blank-template
rucksack github provisioner repo create OWNER/REPO --visibility private --template OWNER/blank-template --execute

# After GitHub App setup, daily repo use is short:

# From Termius or any shell already inside the VM, start or attach the work session.
export PATH="$HOME/.local/bin:$PATH"
tmux new -As work

# Inside that tmux session, clone or fetch an existing repo.
rucksack github doctor OWNER/EXISTING_REPO --execute
rucksack github repo clone OWNER/EXISTING_REPO --execute
cd ~/code/OWNER/EXISTING_REPO

# From the trusted Mac, clone or fetch an existing repo on the VM over SSH.
rucksack vm repo add OWNER/EXISTING_REPO --profile dev-vm --role developer --execute

# From the trusted Mac, create a repo with local human gh auth, then clone it in the VM.
gh repo create OWNER/NEW_REPO --private --template OWNER/blank-template
# Or use the local-only Provisioner App instead of human gh auth.
rucksack github provisioner repo create OWNER/NEW_REPO --visibility private --template OWNER/blank-template --execute
rucksack github repo clone OWNER/NEW_REPO --execute
cd ~/code/OWNER/NEW_REPO

# brokered fallback: local machine mints a short-lived installation token and streams it to the VM once.
rucksack vm repo add OWNER/REPO --profile dev-vm --role developer --backend brokered-app --execute

# compatibility only: uses whichever GitHub user is logged into gh on the VM.
rucksack vm repo add OWNER/REPO --profile dev-vm --role developer --backend gh --execute
```

Roles are `reader`, `developer`, and `maintainer`. The current executable slice
uses Rucksack-managed GitHub App selected-repo access by default. Install the
GitHub App config on the VM with `rucksack vm github install-app` so the VM can
mint revocable server-to-server tokens without personal `gh auth login`.
The VM Developer App does not request `administration: write`; create repos with
local human `gh` auth or a separate provisioner whose private key is not copied
to the VM.
After the permission split, rerun `rucksack github connect`, reinstall the
Developer App credentials with `rucksack vm github install-app`, and keep
`~/.config/rucksack/github-provisioner-app.pem` local-only.
`--backend brokered-app` streams a one-shot local token to the VM, and
`--backend gh` is compatibility only and inherits whichever GitHub user is
logged into `gh` on the VM. Revoke VM access by removing repositories from the
Rucksack GitHub App installation, uninstalling the App, rotating/deleting the
App key in GitHub, or deleting the VM PEM. Merge, auto-merge, CI/CD, and
protected-branch behavior require a separate policy layer.
