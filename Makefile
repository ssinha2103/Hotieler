UV_IMAGE := ghcr.io/astral-sh/uv:0.8.22-python3.12-bookworm-slim
COMPOSE := docker compose
SERVICE := api

.PHONY: install lock build run stop seed test test-unit test-integration test-concurrency coverage lint typecheck smoke verify

install: build

lock:
	docker run --rm \
		--user "$$(id -u):$$(id -g)" \
		--env UV_CACHE_DIR=/tmp/uv-cache \
		--tmpfs /tmp:rw,mode=1777 \
		--volume "$(CURDIR):/workspace" \
		--workdir /workspace \
		$(UV_IMAGE) uv lock

build:
	$(COMPOSE) build $(SERVICE)

run:
	$(COMPOSE) up --build $(SERVICE)

stop:
	$(COMPOSE) down --remove-orphans

seed:
	./run.sh seed

test: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) pytest -q

test-unit: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) pytest -q tests/unit

test-integration: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) pytest -q tests/integration

test-concurrency: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) pytest -q tests/concurrency

coverage: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) pytest --cov=src/hotieler --cov-report=term-missing

lint: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) ruff format --check .
	$(COMPOSE) run --rm --no-deps $(SERVICE) ruff check .

typecheck: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) mypy src

smoke:
	bash -n scripts/docker-smoke.sh
	./scripts/docker-smoke.sh

verify: build
	$(COMPOSE) run --rm --no-deps $(SERVICE) sh -c \
		'ruff format --check . \
		&& ruff check . \
		&& mypy src \
		&& pytest -q --cov=src/hotieler --cov-report=term-missing \
		&& python -m compileall -q src'
