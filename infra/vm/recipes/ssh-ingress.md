# SSH Ingress Recipe

After Tailscale access is verified, tighten SSH ingress in both places:

1. Guest firewall: keep `ufw` enabled and allow SSH only where needed.
2. Cloud firewall/security list/NSG: restrict public TCP/22 to a break-glass
   source IP, or close it and use Tailscale SSH.
3. Keep a tested rollback path before removing public SSH.
4. Re-run `infra/vm/verify.sh` after every ingress change.

Do not run OCI, DNS, IAM, or firewall mutation commands from untrusted branch
code. Record the approved command and outcome in the trusted VM lane.
