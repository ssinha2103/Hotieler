from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = PROJECT_ROOT / "run.sh"
SMOKE_SCRIPT = PROJECT_ROOT / "scripts" / "docker-smoke.sh"


def _fake_docker(tmp_path: Path) -> Path:
    binary_directory = tmp_path / "bin"
    binary_directory.mkdir()
    docker = binary_directory / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

case "$*" in
  "compose version" | "info" | "compose config --quiet" | "compose ps api")
    exit 0
    ;;
  "compose ps --all --quiet api")
    printf 'test-container-id\\n'
    ;;
  inspect*)
    printf 'healthy\\n'
    ;;
  *)
    printf 'unexpected docker invocation: %s\\n' "$*" >&2
    exit 97
    ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return binary_directory


def _fake_smoke_commands(tmp_path: Path) -> tuple[Path, Path]:
    binary_directory = tmp_path / "smoke-bin"
    binary_directory.mkdir()
    seeded_state = tmp_path / "seeded"
    docker = binary_directory / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

case "$*" in
  "compose version" | "info" | compose*" config --quiet" | compose*" up --build --detach --remove-orphans api")
    exit 0
    ;;
  compose*" ps --all --quiet api")
    printf 'test-container-id\\n'
    ;;
  inspect*"NetworkSettings.Ports"*)
    printf '49152\\n'
    ;;
  inspect*"State.Health"*)
    printf 'healthy\\n'
    ;;
  inspect*"State.Status"*)
    printf 'running\\n'
    ;;
  compose*" exec -T api python scripts/seed_local_data.py"*)
    : >"${FAKE_SMOKE_STATE}"
    ;;
  compose*" exec -T api id -u")
    printf '10001\\n'
    ;;
  "top test-container-id")
    printf 'UID PID CMD\\n10001 42 /opt/venv/bin/uvicorn hotieler.main:app\\n'
    ;;
  compose*" logs --no-color api" | compose*" down --volumes --remove-orphans --rmi local")
    exit 0
    ;;
  *)
    printf 'unexpected docker invocation: %s\\n' "$*" >&2
    exit 97
    ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    curl = binary_directory / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

url="${!#}"
case "$url" in
  */health)
    printf '{"status":"healthy"}'
    ;;
  */openapi.json)
    printf '{"openapi":"3.1.0","paths":{"/api/v1/bookings":{}}}'
    ;;
  */api/v1/properties/search*)
    if [[ -f "${FAKE_SMOKE_STATE}" ]]; then
      printf '[{"property_name":"Northstar Bengaluru"}]'
    else
      printf '[]'
    fi
    ;;
  *)
    printf 'unexpected curl URL: %s\\n' "$url" >&2
    exit 98
    ;;
esac
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    return binary_directory, seeded_state


def _run_status(tmp_path: Path, **overrides: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{_fake_docker(tmp_path)}:{environment['PATH']}"
    environment.pop("HOTIELER_BASE_URL", None)
    environment.update(overrides)
    return subprocess.run(
        [str(RUN_SCRIPT), "status"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_launcher_derives_urls_from_alternate_port_and_accepts_leading_zero_timeout(
    tmp_path: Path,
) -> None:
    result = _run_status(
        tmp_path,
        HOTIELER_PORT="9000",
        HOTIELER_START_TIMEOUT_SECONDS="08",
    )

    assert result.returncode == 0, result.stderr
    assert "http://127.0.0.1:9000/health" in result.stdout
    assert "http://127.0.0.1:9000/docs" in result.stdout
    assert "http://127.0.0.1:9000/openapi.json" in result.stdout


def test_launcher_explicit_base_url_overrides_derived_url(tmp_path: Path) -> None:
    result = _run_status(
        tmp_path,
        HOTIELER_PORT="9000",
        HOTIELER_BASE_URL="https://hotel.test/local//",
    )

    assert result.returncode == 0, result.stderr
    assert "https://hotel.test/local/docs" in result.stdout
    assert "http://127.0.0.1:9000" not in result.stdout


def test_launcher_rejects_out_of_range_port(tmp_path: Path) -> None:
    result = _run_status(tmp_path, HOTIELER_PORT="65536")

    assert result.returncode == 1
    assert "HOTIELER_PORT must be an integer between 1 and 65535" in result.stderr


@pytest.mark.parametrize(
    ("variable", "message"),
    [
        ("HOTIELER_PORT", "HOTIELER_PORT must be an integer between 1 and 65535"),
        (
            "HOTIELER_START_TIMEOUT_SECONDS",
            "HOTIELER_START_TIMEOUT_SECONDS must be an integer between 1 and 86400",
        ),
    ],
)
def test_launcher_rejects_oversized_integers_without_bash_overflow(
    tmp_path: Path,
    variable: str,
    message: str,
) -> None:
    result = _run_status(tmp_path, **{variable: "9" * 200})

    assert result.returncode == 1
    assert message in result.stderr


def test_smoke_launcher_rejects_oversized_timeout_without_bash_overflow(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment["PATH"] = f"{_fake_docker(tmp_path)}:{environment['PATH']}"
    environment["HOTIELER_SMOKE_TIMEOUT_SECONDS"] = "9" * 200

    result = subprocess.run(
        [str(SMOKE_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "HOTIELER_SMOKE_TIMEOUT_SECONDS must be an integer between 1 and 86400" in result.stderr


def test_smoke_launcher_accepts_leading_zero_timeout_through_deadline_arithmetic(
    tmp_path: Path,
) -> None:
    binary_directory, seeded_state = _fake_smoke_commands(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = f"{binary_directory}:{environment['PATH']}"
    environment["FAKE_SMOKE_STATE"] = str(seeded_state)
    environment["HOTIELER_SMOKE_TIMEOUT_SECONDS"] = "08"
    environment["HOTIELER_SMOKE_PROJECT_NAME"] = "hotieler-smoke-unit"
    environment["HOTIELER_SMOKE_LOG_FILE"] = str(tmp_path / "smoke.log")

    result = subprocess.run(
        [str(SMOKE_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Docker smoke gate passed" in result.stdout
    assert seeded_state.exists()


def test_compose_publishes_the_api_on_loopback_only() -> None:
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${HOTIELER_PORT:-8000}:8000"' in compose
