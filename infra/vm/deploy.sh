#!/usr/bin/env sh
set -eu

say() {
  printf '%s\n' "$*"
}

quote_for_remote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"
}

github_actions_deploy() {
  DEPLOY_ENV="${DEPLOY_ENV:?DEPLOY_ENV is required}"
  APP_ENV="$DEPLOY_ENV"
  APP_ENV="${APP_ENV:?APP_ENV is required}"
  RUCKSACK_VM_HOST="${RUCKSACK_VM_HOST:?RUCKSACK_VM_HOST is required}"
  RUCKSACK_VM_USER="${RUCKSACK_VM_USER:?RUCKSACK_VM_USER is required}"
  RUCKSACK_VM_SSH_KEY="${RUCKSACK_VM_SSH_KEY:?RUCKSACK_VM_SSH_KEY is required}"
  REMOTE_ROOT="${REMOTE_ROOT:?REMOTE_ROOT is required}"
  ENV_FILE="${ENV_FILE:?ENV_FILE is required}"
  COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME is required}"
  HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"
  RUCKSACK_APP_ENV="${RUCKSACK_APP_ENV:-}"

  key="$RUNNER_TEMP/rucksack_vm_key"
  install -m 700 -d "$RUNNER_TEMP" "$HOME/.ssh"
  printf '%s\n' "$RUCKSACK_VM_SSH_KEY" > "$key"
  chmod 600 "$key"
  ssh-keyscan -H "$RUCKSACK_VM_HOST" >> "$HOME/.ssh/known_hosts"

  remote_env_dir="$(dirname "$ENV_FILE")"
  remote_release="$REMOTE_ROOT/releases/${GITHUB_SHA:-manual}"
  remote_current="$REMOTE_ROOT/current"
  ssh_base="ssh -i $key -o IdentitiesOnly=yes $RUCKSACK_VM_USER@$RUCKSACK_VM_HOST"

  say "preparing remote release for $DEPLOY_ENV"
  $ssh_base "mkdir -p $(quote_for_remote "$remote_release") $(quote_for_remote "$remote_env_dir")"
  if [ -n "$RUCKSACK_APP_ENV" ]; then
    printf '%s\n' "$RUCKSACK_APP_ENV" | $ssh_base "umask 077 && cat > $(quote_for_remote "$ENV_FILE")"
  fi
  if command -v rsync >/dev/null 2>&1; then
    rsync -az --delete       -e "ssh -i $key -o IdentitiesOnly=yes"       --exclude='.git'       --exclude='.env'       --exclude='.env.*'       ./ "$RUCKSACK_VM_USER@$RUCKSACK_VM_HOST:$remote_release/"
  else
    archive="$RUNNER_TEMP/rucksack-release.tgz"
    tar --exclude='.git' --exclude='.env' --exclude='.env.*' -czf "$archive" .
    scp -i "$key" -o IdentitiesOnly=yes "$archive" "$RUCKSACK_VM_USER@$RUCKSACK_VM_HOST:$remote_release/source.tgz"
    $ssh_base "cd $(quote_for_remote "$remote_release") && tar -xzf source.tgz && rm -f source.tgz"
  fi
  if $ssh_base "[ -L $(quote_for_remote "$remote_current") ]"; then
    $ssh_base "readlink $(quote_for_remote "$remote_current") > $(quote_for_remote "$REMOTE_ROOT/previous-release")"
  fi
  remote_app_env="$(quote_for_remote "$APP_ENV")"
  remote_app_dir="$(quote_for_remote "$remote_release")"
  remote_env_file="$(quote_for_remote "$ENV_FILE")"
  remote_project="$(quote_for_remote "$COMPOSE_PROJECT_NAME")"
  remote_health="$(quote_for_remote "$HEALTHCHECK_URL")"
  $ssh_base "cd $(quote_for_remote "$remote_release") && APP_ENV=$remote_app_env APP_DIR=$remote_app_dir APP_ENV_FILE=$remote_env_file COMPOSE_PROJECT_NAME=$remote_project HEALTHCHECK_URL=$remote_health APPLY=1 sh infra/vm/deploy.sh"
  $ssh_base "ln -sfn $(quote_for_remote "$remote_release") $(quote_for_remote "$remote_current")"
}

local_deploy() {
  APP_ENV="${APP_ENV:?APP_ENV is required}"
  APP_DIR="${APP_DIR:-$PWD}"
  APP_ENV_FILE="${APP_ENV_FILE:-}"
  COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-rucksack-$APP_ENV}"
  HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"
  APPLY="${APPLY:-0}"

  say "Rucksack VM deploy"
  say "environment: $APP_ENV"
  say "app dir: $APP_DIR"
  say "compose project: $COMPOSE_PROJECT_NAME"
  if [ "$APPLY" != "1" ]; then
    say "dry-run only; set APPLY=1 to restart services"
    exit 0
  fi

  cd "$APP_DIR"
  compose_file=""
  if [ -f compose.yaml ]; then
    compose_file="compose.yaml"
  elif [ -f docker-compose.yml ]; then
    compose_file="docker-compose.yml"
  elif [ -f infra/vm/compose.yaml ] && grep -q "rucksack-ci-compose" infra/vm/compose.yaml; then
    compose_file="infra/vm/compose.yaml"
  fi

  if [ -n "$compose_file" ]; then
    env_args=""
    if [ -n "$APP_ENV_FILE" ] && [ -f "$APP_ENV_FILE" ]; then
      env_args="--env-file $APP_ENV_FILE"
    fi
    # shellcheck disable=SC2086
    docker compose --project-name "$COMPOSE_PROJECT_NAME" --file "$compose_file" $env_args up -d --build
  elif [ -f package.json ]; then
    npm ci
    npm run build --if-present
    say "no compose file detected; build completed but service restart must be wired by the repo"
  else
    say "no supported runtime marker detected; add compose.yaml or repo-specific deploy steps"
  fi

  if [ -n "$HEALTHCHECK_URL" ]; then
    curl --fail --silent --show-error --retry 10 --retry-delay 3 "$HEALTHCHECK_URL" >/dev/null
    say "healthcheck passed"
  else
    say "HEALTHCHECK_URL not set; skipped healthcheck"
  fi
}

case "${1:-}" in
  --github-actions)
    github_actions_deploy
    ;;
  *)
    local_deploy
    ;;
esac
