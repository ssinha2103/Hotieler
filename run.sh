#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SERVICE="api"
HOST_PORT="${HOTIELER_PORT:-8000}"
BASE_URL="${HOTIELER_BASE_URL:-}"
HEALTH_URL=""
SWAGGER_URL=""
OPENAPI_URL=""
START_TIMEOUT_SECONDS="${HOTIELER_START_TIMEOUT_SECONDS:-90}"

ACTION="start"
ACTION_WAS_SET=0
OPEN_SWAGGER=1

usage() {
  cat <<'EOF'
Usage: ./run.sh [action] [options]

Run the Hotel Booking Service entirely with Docker Compose. The default action is "start".

Actions:
  start       Build and start the API in the background, wait for health,
              and open Swagger when a supported desktop opener is available.
  stop        Stop the API and remove its Compose containers and network.
  restart     Stop, rebuild, and start the API again.
  status      Show Compose status and the current container health state.
  logs        Follow the last 200 API log lines. Press Ctrl-C to stop following.
  seed        Populate the running API through its public owner/property endpoints.
              The service must already be running; plain startup remains empty.
  test        Build the image and run the complete pytest suite in a container.
  help        Show this help message.

Options:
  --no-open   Do not open Swagger after start or restart.
  --open      Open Swagger after start or restart (the default).
  -h, --help  Show this help message.

Environment variables:
  HOTIELER_PORT                    Loopback host port (default: 8000)
  HOTIELER_BASE_URL                Browser URL override (default: derived from HOTIELER_PORT)
  HOTIELER_START_TIMEOUT_SECONDS   Health wait timeout in seconds (default: 90)

Examples:
  ./run.sh
  ./run.sh --no-open
  ./run.sh restart --no-open
  ./run.sh seed
  ./run.sh logs
  ./run.sh test
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

set_action() {
  if (( ACTION_WAS_SET )); then
    fail "only one action may be supplied"
  fi
  ACTION="$1"
  ACTION_WAS_SET=1
}

parse_arguments() {
  while (($#)); do
    case "$1" in
      start | stop | restart | status | logs | seed | test | help)
        set_action "$1"
        ;;
      --no-open)
        OPEN_SWAGGER=0
        ;;
      --open)
        OPEN_SWAGGER=1
        ;;
      -h | --help)
        set_action "help"
        ;;
      *)
        fail "unknown argument: $1 (run './run.sh help' for usage)"
        ;;
    esac
    shift
  done
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "Docker is required but was not found in PATH."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (the 'docker compose' command)."
  docker info >/dev/null 2>&1 || fail "Docker is installed, but its daemon is not available. Start Docker and retry."
}

validate_configuration() {
  case "$HOST_PORT" in
    '' | *[!0-9]*)
      fail "HOTIELER_PORT must be an integer between 1 and 65535."
      ;;
  esac
  HOST_PORT=$((10#${HOST_PORT}))
  if (( HOST_PORT < 1 || HOST_PORT > 65535 )); then
    fail "HOTIELER_PORT must be an integer between 1 and 65535."
  fi

  case "$START_TIMEOUT_SECONDS" in
    '' | *[!0-9]*)
      fail "HOTIELER_START_TIMEOUT_SECONDS must be a positive integer."
      ;;
  esac
  START_TIMEOUT_SECONDS=$((10#${START_TIMEOUT_SECONDS}))
  if (( START_TIMEOUT_SECONDS == 0 )); then
    fail "HOTIELER_START_TIMEOUT_SECONDS must be greater than zero."
  fi

  export HOTIELER_PORT="$HOST_PORT"
  if [[ -z "$BASE_URL" ]]; then
    BASE_URL="http://127.0.0.1:${HOST_PORT}"
  fi
  BASE_URL="${BASE_URL%/}"
  case "$BASE_URL" in
    http://* | https://*) ;;
    *) fail "HOTIELER_BASE_URL must start with http:// or https://." ;;
  esac
  HEALTH_URL="${BASE_URL}/health"
  SWAGGER_URL="${BASE_URL}/docs"
  OPENAPI_URL="${BASE_URL}/openapi.json"

  docker compose config --quiet
}

container_id() {
  docker compose ps --all --quiet "$SERVICE"
}

container_health() {
  local id="$1"
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$id" 2>/dev/null || true
}

show_failure_logs() {
  printf '\nRecent API logs:\n' >&2
  docker compose logs --tail=100 "$SERVICE" >&2 || true
}

wait_for_health() {
  local deadline=$((SECONDS + START_TIMEOUT_SECONDS))
  local id=""
  local health=""

  printf 'Waiting for the Hotel Booking Service to become healthy'
  while (( SECONDS < deadline )); do
    id="$(container_id)"
    if [[ -n "$id" ]]; then
      health="$(container_health "$id")"
      case "$health" in
        healthy)
          printf ' ready.\n'
          return 0
          ;;
        unhealthy | exited | dead)
          printf ' failed.\n' >&2
          show_failure_logs
          fail "API container entered state '$health'."
          ;;
      esac
    fi

    printf '.'
    sleep 1
  done

  printf ' timed out.\n' >&2
  show_failure_logs
  fail "API did not become healthy within ${START_TIMEOUT_SECONDS} seconds."
}

print_urls() {
  printf '\nHotel Booking Service is ready:\n'
  printf '  Health:  %s\n' "$HEALTH_URL"
  printf '  Swagger: %s\n' "$SWAGGER_URL"
  printf '  OpenAPI: %s\n\n' "$OPENAPI_URL"
}

open_swagger() {
  if [[ -n "${CI:-}" ]]; then
    printf 'Swagger was not opened because this is a CI environment.\n'
    return 0
  fi

  if [[ "$(uname -s)" == "Darwin" ]] && command -v open >/dev/null 2>&1; then
    if open "$SWAGGER_URL" >/dev/null 2>&1; then
      printf 'Opened Swagger in the default browser.\n'
      return 0
    fi
  elif command -v wslview >/dev/null 2>&1; then
    if wslview "$SWAGGER_URL" >/dev/null 2>&1; then
      printf 'Opened Swagger in the default browser.\n'
      return 0
    fi
  elif [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
    if xdg-open "$SWAGGER_URL" >/dev/null 2>&1; then
      printf 'Opened Swagger in the default browser.\n'
      return 0
    fi
  elif [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v gio >/dev/null 2>&1; then
    if gio open "$SWAGGER_URL" >/dev/null 2>&1; then
      printf 'Opened Swagger in the default browser.\n'
      return 0
    fi
  fi

  printf 'Could not open a desktop browser automatically. Open %s manually.\n' "$SWAGGER_URL"
}

start_service() {
  docker compose up --build --detach --remove-orphans "$SERVICE"
  wait_for_health
  print_urls

  if (( OPEN_SWAGGER )); then
    open_swagger
  else
    printf 'Automatic Swagger opening was disabled.\n'
  fi
}

stop_service() {
  docker compose down --remove-orphans
  printf 'Hotel Booking Service stopped.\n'
}

show_status() {
  local id=""
  local health="not running"

  docker compose ps "$SERVICE"
  id="$(container_id)"
  if [[ -n "$id" ]]; then
    health="$(container_health "$id")"
  fi

  printf '\nAPI state: %s\n' "$health"
  if [[ "$health" == "healthy" ]]; then
    print_urls
  fi
}

run_tests() {
  docker compose build "$SERVICE"
  docker compose run --rm --no-deps "$SERVICE" pytest -q
}

seed_local_catalog() {
  local id=""
  local health=""

  id="$(container_id)"
  if [[ -z "$id" ]]; then
    fail "the API is not running; run './run.sh start --no-open' first"
  fi

  health="$(container_health "$id")"
  if [[ "$health" != "healthy" ]]; then
    fail "the API must be healthy before seeding (current state: '$health')"
  fi

  docker compose exec -T "$SERVICE" \
    python scripts/seed_local_data.py --base-url http://127.0.0.1:8000
}

main() {
  parse_arguments "$@"

  if [[ "$ACTION" == "help" ]]; then
    usage
    return 0
  fi

  cd "$SCRIPT_DIR"
  require_docker
  validate_configuration

  case "$ACTION" in
    start)
      start_service
      ;;
    stop)
      stop_service
      ;;
    restart)
      stop_service
      start_service
      ;;
    status)
      show_status
      ;;
    logs)
      docker compose logs --follow --tail=200 "$SERVICE"
      ;;
    seed)
      seed_local_catalog
      ;;
    test)
      run_tests
      ;;
  esac
}

main "$@"
