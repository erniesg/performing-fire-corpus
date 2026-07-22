# Tailscale Recipe

Use Tailscale to make SSH and deployment traffic private before tightening public
cloud ingress.

1. Install with `INSTALL_TAILSCALE=1 APPLY=1 infra/vm/bootstrap.sh`.
2. Authenticate interactively with `sudo tailscale up --ssh`, or use a
   secret-aware runner that does not print the auth key.
3. Prefer tagged devices and ACLs for deploy access.
4. Verify with `tailscale status` and `infra/vm/verify.sh`.

Do not store Tailscale auth keys in the repository, generated manifests, logs,
issues, pull requests, or chat.
