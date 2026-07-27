.PHONY: help install lock sync fmt lint typecheck test test-unit test-integration run-api run-cli docker-build docker-up migrate init-db doctor

PYTHON ?= python
PKG := court_monitor

help:
	@echo "court-monitor make targets:"
	@echo "  make install       - uv sync (dev deps)"
	@echo "  make lock          - refresh uv.lock"
	@echo "  make fmt           - ruff format (write)"
	@echo "  make lint          - ruff check"
	@echo "  make typecheck     - mypy"
	@echo "  make test          - pytest (all)"
	@echo "  make test-unit     - pytest unit only"
	@echo "  make test-integration - pytest integration only"
	@echo "  make init-db       - create schema (alembic upgrade head)"
	@echo "  make migrate       - run migrations"
	@echo "  make doctor        - environment sanity check"
	@echo "  make run-api       - uvicorn (FastAPI)"
	@echo "  make run-cli       - Typer CLI REPL"
	@echo "  make docker-build  - build image"
	@echo "  make docker-up     - compose up (postgres + app)"

install:
	uv sync

lock:
	uv lock

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint:
	uv run ruff check src tests

typecheck:
	uv run mypy src

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v

init-db:
	uv run court-monitor init-db

migrate:
	uv run court-monitor migrate

doctor:
	uv run court-monitor doctor

run-api:
	uv run uvicorn court_monitor.api.app:app --reload --host 0.0.0.0 --port 8000

run-cli:
	uv run court-monitor --help

docker-build:
	docker build -t court-monitor:dev .

docker-up:
	docker compose up --build
