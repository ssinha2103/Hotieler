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


def test_compose_publishes_the_api_on_loopback_only() -> None:
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${HOTIELER_PORT:-8000}:8000"' in compose
