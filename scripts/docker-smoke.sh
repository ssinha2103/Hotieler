#!/usr/bin/env bash

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly SERVICE="api"
readonly CONTAINER_PORT="8000"
START_TIMEOUT_SECONDS="${HOTIELER_SMOKE_TIMEOUT_SECONDS:-120}"
NORMALIZED_INTEGER=""
readonly PROJECT_NAME="${HOTIELER_SMOKE_PROJECT_NAME:-hotieler-smoke-${PPID}-$$}"
readonly TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hotieler-smoke.XXXXXX")"
readonly LOG_FILE="${HOTIELER_SMOKE_LOG_FILE:-${TEMP_DIR}/compose.log}"

CONTAINER_ID=""
HOST_PORT=""

compose() {
  HOTIELER_PORT=0 \
    docker compose --project-directory "${PROJECT_DIR}" --project-name "${PROJECT_NAME}" "$@"
}

fail() {
  printf 'Smoke check failed: %s\n' "$*" >&2
  return 1
}

normalize_bounded_integer() {
  local raw_value="$1"
  local minimum="$2"
  local maximum="$3"
  local error_message="$4"
  local leading_zeroes=""
  local normalized=""

  case "$raw_value" in
    '' | *[!0-9]*)
      fail "$error_message"
      return 1
      ;;
  esac

  leading_zeroes="${raw_value%%[!0]*}"
  normalized="${raw_value#"$leading_zeroes"}"
  if [[ -z "$normalized" ]]; then
    normalized="0"
  fi

  if (( ${#normalized} > ${#maximum} )) \
    || { (( ${#normalized} == ${#maximum} )) && [[ "$normalized" > "$maximum" ]]; }; then
    fail "$error_message"
    return 1
  fi

  NORMALIZED_INTEGER=$((10#${normalized}))
  if (( NORMALIZED_INTEGER < minimum )); then
    fail "$error_message"
    return 1
  fi
}

validate_configuration() {
  normalize_bounded_integer \
    "${START_TIMEOUT_SECONDS}" 1 86400 \
    "HOTIELER_SMOKE_TIMEOUT_SECONDS must be an integer between 1 and 86400."
  START_TIMEOUT_SECONDS="${NORMALIZED_INTEGER}"

  if [[ ! "${PROJECT_NAME}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    fail "HOTIELER_SMOKE_PROJECT_NAME must contain only lowercase letters, digits, hyphens, or underscores."
  fi

  command -v docker >/dev/null 2>&1 || fail "Docker is required."
  command -v curl >/dev/null 2>&1 || fail "curl is required."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
  docker info >/dev/null 2>&1 || fail "The Docker daemon is unavailable."
  compose config --quiet
}

capture_logs() {
  compose logs --no-color "${SERVICE}" >"${LOG_FILE}" 2>&1 || true
}

cleanup() {
  local exit_code="$?"
  trap - EXIT INT TERM

  capture_logs
  compose down --volumes --remove-orphans --rmi local >/dev/null 2>&1 || true

  if (( exit_code != 0 )); then
    printf '\nDocker runtime logs:\n' >&2
    cat "${LOG_FILE}" >&2 || true
  fi

  rm -rf -- "${TEMP_DIR}"
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

container_state() {
  docker inspect --format '{{.State.Status}}' "${CONTAINER_ID}" 2>/dev/null || true
}

container_health() {
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "${CONTAINER_ID}" 2>/dev/null || true
}

resolve_container() {
  local deadline=$((SECONDS + START_TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    CONTAINER_ID="$(compose ps --all --quiet "${SERVICE}")"
    if [[ -n "${CONTAINER_ID}" ]]; then
      HOST_PORT="$(
        docker inspect \
          --format '{{(index (index .NetworkSettings.Ports "8000/tcp") 0).HostPort}}' \
          "${CONTAINER_ID}" 2>/dev/null || true
      )"
      if [[ -n "${HOST_PORT}" ]]; then
        return 0
      fi
    fi
    sleep 1
  done

  fail "the API container or its isolated host port was not created in time."
}

wait_for_api() {
  local deadline=$((SECONDS + START_TIMEOUT_SECONDS))
  local health=""
  local state=""
  local base_url="http://127.0.0.1:${HOST_PORT}"

  printf 'Waiting for isolated API at %s' "${base_url}"
  while (( SECONDS < deadline )); do
    health="$(container_health)"
    state="$(container_state)"
    case "${state}:${health}" in
      running:healthy)
        if curl --fail --silent --show-error --max-time 5 \
          "${base_url}/health" >/dev/null; then
          printf ' ready.\n'
          return 0
        fi
        ;;
      exited:* | dead:* | *:unhealthy)
        printf ' failed.\n' >&2
        fail "container entered state ${state}:${health}."
        ;;
    esac
    printf '.'
    sleep 1
  done

  printf ' timed out.\n' >&2
  fail "the API did not become healthy within ${START_TIMEOUT_SECONDS} seconds."
}

fetch() {
  local path="$1"
  curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${HOST_PORT}${path}"
}

assert_contains() {
  local body="$1"
  local expected="$2"
  local description="$3"

  if [[ "${body}" != *"${expected}"* ]]; then
    fail "${description} did not contain ${expected}."
  fi
}

assert_equals() {
  local actual="$1"
  local expected="$2"
  local description="$3"

  if [[ "${actual}" != "${expected}" ]]; then
    fail "${description} was ${actual}; expected ${expected}."
  fi
}

catalogue_search() {
  fetch "/api/v1/properties/search?city=Bengaluru&check_in=2099-01-01&check_out=2099-01-02&guest_count=1"
}

check_endpoints() {
  local health_body=""
  local openapi_body=""

  health_body="$(fetch /health)"
  assert_contains "${health_body}" '"status":"healthy"' "/health response"

  openapi_body="$(fetch /openapi.json)"
  assert_contains "${openapi_body}" '"openapi":' "/openapi.json response"
  assert_contains "${openapi_body}" '"/api/v1/bookings"' "/openapi.json response"
  assert_equals "$(catalogue_search)" '[]' "fresh-start catalogue search"
}

seed_through_public_api() {
  compose exec -T "${SERVICE}" \
    python scripts/seed_local_data.py --base-url http://127.0.0.1:8000 >/dev/null
  assert_contains "$(catalogue_search)" '"property_name":"Northstar Bengaluru"' \
    "catalogue search after public-API seeding"
}

check_runtime_identity() {
  local runtime_uid=""

  runtime_uid="$(compose exec -T "${SERVICE}" id -u)"
  if [[ ! "${runtime_uid}" =~ ^[0-9]+$ ]]; then
    fail "container UID is not numeric: ${runtime_uid}."
  fi
  if [[ "${runtime_uid}" == "0" ]]; then
    fail "API container is running as root."
  fi
}

check_uvicorn_process_count() {
  local process_list=""
  local uvicorn_count="0"

  # Docker Desktop does not expose the same `ps` flags on every host, so use
  # Docker's portable default table. The init wrapper also mentions "uvicorn";
  # only the executable path identifies the actual server process.
  process_list="$(docker top "${CONTAINER_ID}")"
  while IFS= read -r process; do
    if [[ "${process}" == *'/opt/venv/bin/uvicorn'* ]]; then
      uvicorn_count=$((uvicorn_count + 1))
    fi
  done <<<"${process_list}"

  if (( uvicorn_count != 1 )); then
    fail "expected exactly one Uvicorn process, found ${uvicorn_count}."
  fi
}

main() {
  validate_configuration
  compose up --build --detach --remove-orphans "${SERVICE}"
  resolve_container
  wait_for_api
  check_endpoints
  seed_through_public_api
  check_runtime_identity
  check_uvicorn_process_count

  printf 'Docker smoke gate passed: health, OpenAPI, public-API seeding, non-root UID, and one Uvicorn process.\n'
}

main "$@"
