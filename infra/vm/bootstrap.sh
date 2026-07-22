#!/usr/bin/env sh
set -eu

APPLY="${APPLY:-0}"
APP_USER="${APP_USER:-deploy}"
APP_NAME="${APP_NAME:-rucksack-app}"
APP_DIR="${APP_DIR:-/opt/$APP_NAME}"
SSH_PORT="${SSH_PORT:-22}"
OPEN_HTTP="${OPEN_HTTP:-1}"
OPEN_HTTPS="${OPEN_HTTPS:-1}"
INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-0}"
INSTALL_CLOUDFLARE_TOOLS="${INSTALL_CLOUDFLARE_TOOLS:-0}"
TAILSCALE_AUTHKEY_FILE="${TAILSCALE_AUTHKEY_FILE:-}"
SSH_AUTHORIZED_KEYS_FILE="${SSH_AUTHORIZED_KEYS_FILE:-}"

say() {
  printf '%s
' "$*"
}

run() {
  if [ "$APPLY" = "1" ]; then
    say "+ $*"
    sh -c "$*"
  else
    say "dry-run: $*"
  fi
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    say "run as root, usually with sudo"
    exit 2
  fi
}

reject() {
  say "$*"
  exit 2
}

validate_inputs() {
  case "$APP_USER" in
    ""|*[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-]*) reject "unsafe APP_USER" ;;
  esac
  case "$APP_NAME" in
    ""|*[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-]*) reject "unsafe APP_NAME" ;;
  esac
  case "$SSH_PORT" in
    ""|*[!0123456789]*) reject "unsafe SSH_PORT" ;;
  esac
  case "$APP_DIR" in
    /*) ;;
    *) reject "APP_DIR must be an absolute path" ;;
  esac
  case "$APP_DIR$SSH_AUTHORIZED_KEYS_FILE$TAILSCALE_AUTHKEY_FILE" in
    *"'"*) reject "paths must not contain single quotes" ;;
  esac
}

write_ssh_hardening() {
  if [ -z "$SSH_AUTHORIZED_KEYS_FILE" ] || [ ! -r "$SSH_AUTHORIZED_KEYS_FILE" ]; then
    say "skipping restrictive SSH hardening until SSH_AUTHORIZED_KEYS_FILE is readable"
    return
  fi
  if [ "$APPLY" = "1" ]; then
    mkdir -p /etc/ssh/sshd_config.d
    cat > /etc/ssh/sshd_config.d/90-rucksack-hardening.conf <<'SSHCONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
X11Forwarding no
AllowTcpForwarding yes
SSHCONF
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
  else
    say "dry-run: write /etc/ssh/sshd_config.d/90-rucksack-hardening.conf"
    say "dry-run: PasswordAuthentication no"
  fi
}

install_authorized_keys() {
  if [ -z "$SSH_AUTHORIZED_KEYS_FILE" ] || [ ! -r "$SSH_AUTHORIZED_KEYS_FILE" ]; then
    say "no SSH_AUTHORIZED_KEYS_FILE provided; not copying keys for $APP_USER"
    return
  fi
  run "install -d -m 700 -o '$APP_USER' -g '$APP_USER' '/home/$APP_USER/.ssh'"
  run "install -m 600 -o '$APP_USER' -g '$APP_USER' '$SSH_AUTHORIZED_KEYS_FILE' '/home/$APP_USER/.ssh/authorized_keys'"
}

install_cloudflare_tools() {
  if [ "$INSTALL_CLOUDFLARE_TOOLS" != "1" ]; then
    say "set INSTALL_CLOUDFLARE_TOOLS=1 to install Cloudflare Wrangler"
    return
  fi
  if ! command -v npm >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1 || [ "$APPLY" != "1" ]; then
      run "DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs npm"
    else
      say "npm required for Wrangler; install Node.js/npm before enabling Cloudflare tooling"
      return
    fi
  fi
  run "if ! command -v wrangler >/dev/null 2>&1; then npm install -g wrangler@latest; fi"
  run "wrangler --version"
}

validate_inputs

if [ "$APPLY" != "1" ]; then
  say "dry-run only; rerun with APPLY=1 after review"
else
  require_root
fi

if command -v apt-get >/dev/null 2>&1; then
  run "apt-get update"
  run "DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg git ufw fail2ban unattended-upgrades nginx docker.io docker-compose-plugin"
else
  say "unsupported package manager: add distro-specific package install commands here"
fi

install_cloudflare_tools

run "id '$APP_USER' >/dev/null 2>&1 || useradd --create-home --shell /bin/bash '$APP_USER'"
run "usermod -aG docker '$APP_USER' || true"
run "install -d -m 755 -o '$APP_USER' -g '$APP_USER' '$APP_DIR'"
install_authorized_keys
write_ssh_hardening

run "systemctl enable --now docker 2>/dev/null || true"
run "systemctl enable --now nginx 2>/dev/null || true"
run "systemctl enable --now fail2ban 2>/dev/null || true"
run "systemctl enable --now unattended-upgrades 2>/dev/null || true"

run "ufw default deny incoming"
run "ufw default allow outgoing"
run "ufw allow '$SSH_PORT/tcp'"
if [ "$OPEN_HTTP" = "1" ]; then
  run "ufw allow 80/tcp"
fi
if [ "$OPEN_HTTPS" = "1" ]; then
  run "ufw allow 443/tcp"
fi
run "ufw --force enable"

if [ "$INSTALL_TAILSCALE" = "1" ]; then
  run "curl -fsSL https://tailscale.com/install.sh | sh"
  say "authenticate Tailscale with: tailscale up --ssh"
  if [ -n "$TAILSCALE_AUTHKEY_FILE" ]; then
    run "test -r '$TAILSCALE_AUTHKEY_FILE'"
    say "TAILSCALE_AUTHKEY_FILE is readable; use a secret-aware runner for non-interactive tailscale up"
  fi
else
  say "set INSTALL_TAILSCALE=1 to install Tailscale"
fi

say "next: copy compose.yaml into $APP_DIR, install the systemd unit, then run infra/vm/verify.sh"
