# VM Recipe

Reusable trusted VM setup for `performing-fire-corpus`.

This directory is for a trusted remote coding VM and for deployment targets that
need repeatable guest setup. The same VM can be used as the coding control plane
and as the app target, or it can deploy to same VM or another target. Treat the
combined mode as higher risk: branch code, deploy scripts, browser sessions, and
local cloud credentials share one host.

## Files

- `bootstrap.sh`: dry-run by default; installs baseline packages, Docker, nginx,
  firewall rules, a deploy user, optional Tailscale/Cloudflare tooling, and SSH
  hardening.
- `verify.sh`: checks expected ports, app health, service state, disk, memory,
  and Tailscale status without printing secret-bearing env values.
- `compose.yaml`: generic app container shape. Pin `APP_IMAGE` from a trusted
  deploy context or copy in a repo-specific override.
- `systemd/rucksack-app.service`: example unit that runs the compose app from
  `/opt/rucksack-app`.
- `nginx/app.conf` and `Caddyfile`: reverse-proxy examples for local app ports.
- `recipes/`: reusable runbooks for AI terminal setup, Tailscale, SSH ingress,
  and no-secret logging/files.

## Bootstrap

From a new machine, use `rucksack up` as the reusable three-step path: install
rucksack, approve provider/local/guest setup, then enter a tmux session. The
equivalent explicit form is `rucksack vm up`. Use `--provider gcp` for GCP VMs
or `--provider oci` for OCI VMs.

For first-time OCI users, Rucksack can guide and validate readiness but cannot
create the Oracle account or bypass capacity. Create/sign in to Oracle Cloud
Free Tier, complete Oracle's account checks, choose the home region carefully
because Always Free compute must be created there, configure OCI CLI with an API
signing key, then run `rucksack oci doctor --json`.

```bash
# First run: checks local prerequisites, discovers the VM, writes local
# profile/SSH config, bootstraps baseline guest tools, then opens tmux.
rucksack up --provider oci --approve-first-run
rucksack vm up --provider oci --execute --apply --bootstrap
rucksack vm up --provider gcp --approve-first-run

# Explicit equivalent for audited environments:
rucksack up --provider oci --setup-local --install-provider-cli --generate-ssh-key --create-if-missing --execute --apply --bootstrap

# Future runs: reuses the saved profile and opens tmux.
rucksack up --execute
rucksack vm up --execute

# If no OCI VM exists yet, Rucksack routes through a reviewed launch JSON
# instead of guessing compartment, subnet, image, or shape.
rucksack oci auth-start --open-browser
oci setup config
rucksack oci doctor --json
oci iam availability-domain list --compartment-id "$OCI_COMPARTMENT_ID"
oci network subnet list --compartment-id "$OCI_COMPARTMENT_ID" --all
oci compute image list --compartment-id "$OCI_COMPARTMENT_ID" --operating-system "Canonical Ubuntu" --shape VM.Standard.A1.Flex --sort-by TIMECREATED --sort-order DESC --all
rucksack oci plan-free-vm --shape a1 --compartment-id "$OCI_COMPARTMENT_ID" --availability-domain "$OCI_AD" --subnet-id "$OCI_SUBNET_ID" --image-id "$OCI_IMAGE_ID" --ssh-public-key ~/.ssh/id_ed25519.pub --display-name rucksack-coding --output ./oci-launch.json --apply
rucksack up --provider oci --approve-first-run --oci-launch-json ./oci-launch.json
# Lower-level capacity-only command:
rucksack oci capacity-hunt --launch-json ./oci-launch.json --execute

# Lower-level/fallback commands when you need to inspect or override discovery.
rucksack vm onboard --provider oci
rucksack vm onboard --provider oci --execute --apply
rucksack vm onboard --provider oci --oci-choice 1 --execute --apply
rucksack vm onboard --provider oci --oci-instance-id "$OCI_INSTANCE_ID" --execute --apply
rucksack vm onboard --provider oci --oci-compartment-id "$OCI_COMPARTMENT_ID" --oci-display-name "$VM_NAME" --execute --apply
rucksack vm onboard --provider oci --host "$VM_HOST" --remote-root ~/code
rucksack vm onboard --provider oci --host "$VM_HOST" --remote-root ~/code --apply
rucksack vm doctor --profile dev-vm --execute
rucksack vm bootstrap --profile dev-vm --source /path/to/rucksack --execute
rucksack vm start --profile dev-vm --execute
```

## AI Terminal Setup

Rucksack can automate VM package installation, Rucksack CLI install/check, AI
CLI installation, SSH profile setup, and tmux startup. It should not automate
subscription/OAuth approval or copy browser tokens into files. Complete those
login steps interactively on the trusted VM.

```bash
# Private phone-to-VM network, then Codex/Claude install/check plus tmux.
rucksack vm ai-terminal --profile dev-vm --source /path/to/rucksack --device-name iphone --public-key-file ~/Downloads/phone.pub --execute

# Equivalent lower-level commands.
rucksack vm bootstrap --profile dev-vm --source /path/to/rucksack --execute
rucksack vm network tailscale --profile dev-vm --execute
rucksack vm codex --profile dev-vm --install --execute --post-login-drain OWNER/REPO
rucksack vm claude --profile dev-vm --install --execute
```

Inside the VM tmux session:

```bash
codex login
codex login status
claude
```

Codex can help interpret local `rucksack oci doctor` and read-only OCI inventory
output, but it should not complete Oracle signup, handle MFA/payment steps, or
receive private keys, OCI config contents, or API signing key material.

When `--source` points at a local `code/OWNER/REPO` checkout and no explicit
remote root is provided, Rucksack syncs it to `~/code/OWNER/REPO` on the VM.
Saved profile roots and explicit `--remote-root` values are respected as written.

Use `codex login --device-auth` if the VM cannot open a browser directly. For
Claude Max/Pro subscription login, run `claude` and follow the interactive
browser/device flow shown by Claude Code. If Codex says device code
authorization is disabled, enable it in ChatGPT Security Settings, then rerun
`codex login --device-auth`. API-key modes such as `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` are separate from subscription login and should stay in a
secret manager, not in repo files, generated manifests, logs, issues, or chat.

Codex app-server can wrap this later as the natural-language control plane, but
Rucksack keeps setup as explicit, testable CLI primitives. An AI assistant
should parse requests such as "authorize this Termius public key" into
`rucksack vm access`, `rucksack vm ai-terminal`, or `rucksack vm mobile` calls,
then stop at human login steps instead of copying tokens or browser state.

## Repository Access

Repository access uses GitHub App selected-repo access. Set up the GitHub App
from the trusted local machine because that step opens GitHub in your browser
and requires your user/org admin approval. Then install the App config and PEM
onto the VM so the VM can mint its own short-lived installation tokens for
HTTPS Git/API operations without `gh auth login` or a personal GitHub account.

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

Roles are `reader`, `developer`, and `maintainer`. `developer` means clone,
edit, commit, push branches, and create/update PRs. The VM Developer App does
not request `administration: write`; repository creation must use local human
`gh` auth on a trusted machine or a separate provisioner App whose private key is
not copied to the VM. Merge, auto-merge, CI/CD, and protected-branch behavior
require a separate policy layer. Do not treat personal `gh` auth as scoped
Rucksack access. To revoke the VM, remove a repo from the Rucksack GitHub App
installation, uninstall the App, rotate/delete the App private key in GitHub, or
delete `~/.config/rucksack/github-app.pem` on the VM. App installation tokens
are short-lived and repository/permission scoped, but the VM-held PEM can mint
new tokens until you revoke or rotate it.

After the permission split, reinstall refreshed daily Developer App credentials
onto existing VMs and keep the Provisioner App local-only:

```bash
rucksack github connect --owner OWNER --account-type org --execute
rucksack github repos add OWNER/REPO --execute
rucksack github doctor OWNER/REPO --execute
rucksack vm github install-app --profile dev-vm --execute
rucksack vm github doctor OWNER/REPO --profile dev-vm --execute
rucksack github provisioner connect --owner OWNER --account-type org --execute
rucksack github provisioner repo create OWNER/NEW_REPO --execute
```

Rotate by deleting or replacing the App private key in GitHub, deleting the old
local PEM, rerunning the matching `connect --execute`, and reinstalling only the
Developer App PEM on the VM. Never copy
`~/.config/rucksack/github-provisioner-app.pem` to a VM.

## Device SSH

Use `vm access` to authorize another laptop or desktop without copying private
keys between machines. Generate the SSH key on the new device, move only its
public key to a trusted machine that already reaches the VM, then run:

```bash
rucksack vm access --profile dev-vm
rucksack vm access --profile dev-vm --device-name macbook --public-key-file ~/Downloads/macbook.pub --execute
```

On the new device, create its own local Rucksack profile for the host printed by
`vm access`, then use `rucksack up --execute`:

```bash
rucksack vm onboard --provider oci --host <vm-host> --remote-root ~/code --apply
rucksack up --execute
```

Rucksack appends only that device public key to `~/.ssh/authorized_keys` on the
VM.

## Mobile SSH

Use phone-generated keys for mobile access. The phone keeps its private key;
Rucksack only receives the public key and appends it to the VM.

```bash
rucksack vm mobile --profile dev-vm
rucksack vm mobile --profile dev-vm --device-name iphone --public-key-file ~/Downloads/phone.pub --execute
```

Then connect from the mobile SSH app with the host/user printed by Rucksack and
run `tmux new -As work`. If the SSH app disconnects, reconnect to the VM and
run the same command to resume the existing shell.

Review the repo script before use. It prints the plan unless `APPLY=1` is set.

```bash
infra/vm/bootstrap.sh
sudo APPLY=1 SSH_AUTHORIZED_KEYS_FILE="$HOME/.ssh/authorized_keys" infra/vm/bootstrap.sh
sudo APPLY=1 INSTALL_CLOUDFLARE_TOOLS=1 SSH_AUTHORIZED_KEYS_FILE="$HOME/.ssh/authorized_keys" infra/vm/bootstrap.sh
```

Use `INSTALL_TAILSCALE=1` to install Tailscale and
`INSTALL_CLOUDFLARE_TOOLS=1` to install Cloudflare Wrangler. Authenticate
Tailscale from an interactive shell or a secret-aware runner; do not paste auth
keys, Cloudflare API tokens, or account credentials into chat, logs, commits,
issues, or pull requests.

## Verify

```bash
infra/vm/verify.sh
APP_SERVICE=rucksack-app.service EXPECTED_PORTS="22 80 443" HEALTHCHECK_URL=http://127.0.0.1:3000/health infra/vm/verify.sh
```

## Rollback

Keep deploys artifact-based. A safe rollback is usually one of:

- reset `APP_IMAGE` to the previous immutable image tag and restart the service;
- restore the previous `/opt/rucksack-app/compose.yaml`;
- disable the reverse proxy site and keep SSH/Tailscale access for repair.

Do not run infrastructure apply/destroy or production deploy commands from
untrusted branch code. Use `.agent/deploy.yaml` for human gates.
