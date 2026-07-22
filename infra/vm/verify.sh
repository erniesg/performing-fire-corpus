#!/usr/bin/env sh
set -eu

APP_SERVICE="${APP_SERVICE:-rucksack-app.service}"
EXPECTED_PORTS="${EXPECTED_PORTS:-22 80 443}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"
MIN_FREE_DISK_MB="${MIN_FREE_DISK_MB:-512}"
MIN_AVAILABLE_MEM_MB="${MIN_AVAILABLE_MEM_MB:-128}"
failures=0

pass() {
  printf 'pass: %s
' "$*"
}

warn() {
  printf 'warn: %s
' "$*"
}

fail() {
  printf 'fail: %s
' "$*"
  failures=$((failures + 1))
}

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet "$APP_SERVICE"; then
    pass "service active: $APP_SERVICE"
  else
    fail "service not active: $APP_SERVICE"
  fi
else
  warn "systemctl unavailable; skipping service check"
fi

if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -ltn 2>/dev/null || true)"
  for port in $EXPECTED_PORTS; do
    if printf '%s
' "$listeners" | awk '{print $4}' | grep -Eq "[:.]$port$"; then
      pass "port listening: $port"
    else
      fail "port not listening: $port"
    fi
  done
else
  warn "ss unavailable; skipping port checks"
fi

if [ -n "$HEALTHCHECK_URL" ]; then
  safe_url="${HEALTHCHECK_URL%%\?*}"
  if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 5 "$HEALTHCHECK_URL" >/dev/null; then
    pass "healthcheck ok: $safe_url"
  else
    fail "healthcheck failed: $safe_url"
  fi
else
  warn "HEALTHCHECK_URL unset; skipping app health check"
fi

free_disk_mb="$(df -Pk / | awk 'NR==2 {print int($4 / 1024)}')"
if [ "$free_disk_mb" -ge "$MIN_FREE_DISK_MB" ]; then
  pass "free disk MB: $free_disk_mb"
else
  fail "free disk below ${MIN_FREE_DISK_MB}MB: $free_disk_mb"
fi

if command -v free >/dev/null 2>&1; then
  available_mem_mb="$(free -m | awk '/Mem:/ {print $7}')"
  if [ "${available_mem_mb:-0}" -ge "$MIN_AVAILABLE_MEM_MB" ]; then
    pass "available memory MB: $available_mem_mb"
  else
    fail "available memory below ${MIN_AVAILABLE_MEM_MB}MB: ${available_mem_mb:-0}"
  fi
else
  warn "free unavailable; skipping memory check"
fi

if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    pass "tailscale status ok"
  else
    warn "tailscale installed but not connected"
  fi
else
  warn "tailscale not installed"
fi

exit "$failures"
