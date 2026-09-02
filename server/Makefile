# The server: core, stores, quotes, importers, HTTP API.
# `make check` is what CI runs for this component; run it before pushing.
#
# The virtualenv is shared with ../desktop by default (both are installed into
# it, editable), so one `make install` at the repository root sets up both.

PYTHON ?= python3
VENV   ?= ../.venv
BIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help install fmt lint types test test-ci test-all test-postgres test-thorough \
        cov check bench bench-scaling bench-import openapi serve migrate migration \
        image image-test seed-demo clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtualenv (if needed) and install in editable mode with dev extras
	test -x $(BIN)/python || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e '.[dev,local]'

fmt:  ## Format the code
	$(BIN)/ruff format src tests scripts benchmarks
	$(BIN)/ruff check --fix src tests scripts benchmarks

lint:  ## Lint without modifying anything (as CI does)
	$(BIN)/ruff check src tests scripts benchmarks
	$(BIN)/ruff format --check --diff src tests scripts benchmarks

types:  ## Type check
	$(BIN)/mypy

test:  ## Fast suite: memory + sqlite, 50 Hypothesis examples per property
	$(BIN)/pytest

test-ci:  ## The suite as CI runs it (500 examples, derandomised) with the coverage gate
	$(BIN)/pytest --hypothesis-profile=ci --stores=memory,sqlite,sqlite-file \
		-k "not postgres" --cov --cov-report=term-missing

test-all:  ## Every storage backend, including PostgreSQL
	$(BIN)/pytest --stores=memory,sqlite,sqlite-file,postgres --cov --cov-report=term-missing

test-postgres:  ## PostgreSQL only (uses pgserver if no URL is set)
	$(BIN)/pytest --stores=postgres
	$(BIN)/pytest tests/test_store.py -k postgres

test-thorough:  ## 5000 examples per property; for when you touch rounding
	$(BIN)/pytest --hypothesis-profile=thorough

cov:  ## Coverage with an HTML report
	$(BIN)/pytest --cov --cov-report=html --cov-report=term-missing
	@echo "open htmlcov/index.html"

check: lint types test-ci  ## Everything CI checks for the server (without PostgreSQL)

bench:  ## Storage benchmark
	$(BIN)/python benchmarks/bench_store.py --transactions 20000 --bulk

bench-scaling:  ## Does the ledger stay fast as it grows?
	$(BIN)/python benchmarks/bench_store.py --scaling 25000,50000,100000,200000

bench-import:  ## How long does a KMyMoney migration take?
	$(BIN)/python benchmarks/bench_kmymoney.py --transactions 50000

openapi:  ## Regenerate docs/openapi.json (the shipped contract; the frontend generates its types from it)
	$(BIN)/python scripts/openapi.py

serve:  ## Run the API locally (http://127.0.0.1:8000/docs)
	PYMONEY_DATABASE_URL=$${PYMONEY_DATABASE_URL:-sqlite+pysqlite:///pymoney.sqlite} \
	PYMONEY_CREATE_SCHEMA=1 PYMONEY_COOKIE_SECURE=0 \
	$(BIN)/uvicorn pymoney.api:app --reload --port 8000

migrate:  ## Apply migrations ($$PYMONEY_DATABASE_URL); the same command works inside the image
	$(BIN)/python -m pymoney.store.migrate

migration:  ## Autogenerate a migration: make migration M="what changed"
	$(BIN)/alembic revision --autogenerate -m "$(M)"

seed-demo:  ## Create a small fully-priced demo book through a running API (see scripts/seed_demo.py)
	$(BIN)/python scripts/seed_demo.py

image:  ## Build the runtime image
	docker build --target runtime -t pymoney:runtime .

image-test:  ## Build the test image and run the suite inside it
	docker build --target test -t pymoney:test .
	docker run --rm pymoney:test

clean:  ## Remove caches and build output (keeps the venv and your ledger)
	rm -rf build dist htmlcov .coverage .coverage.* coverage.xml junit.xml
	rm -rf .pytest_cache .ruff_cache .mypy_cache .hypothesis
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.egg-info' -type d -prune -exec rm -rf {} +
