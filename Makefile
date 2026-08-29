# Thin wrappers so local development and CI run the same commands.
# `make check` is what CI runs; run it before pushing.

PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help install fmt lint types test test-ci test-all test-postgres \
        test-thorough cov check bench bench-scaling bench-import openapi \
        serve migrate \
        migration clean clean-all \
        up down logs db-shell image image-test compose-config \
        web-install web-dev web-build web-test web-check seed-demo e2e e2e-install \
        desktop desktop-build

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the virtualenv and install in editable mode with dev extras
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e '.[dev,local]'

fmt:  ## Format the code
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

lint:  ## Lint without modifying anything (as CI does)
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check --diff src tests

types:  ## Type check
	$(BIN)/mypy

test:  ## Fast suite: memory + sqlite, 50 Hypothesis examples per property
	$(BIN)/pytest

test-ci:  ## The suite as CI runs it (500 examples, derandomised)
	$(BIN)/pytest --hypothesis-profile=ci --stores=memory,sqlite,sqlite-file \
		-k "not postgres" --cov=src/pymoney --cov-report=term-missing

test-all:  ## Every storage backend, including PostgreSQL
	$(BIN)/pytest --stores=memory,sqlite,sqlite-file,postgres --cov=src/pymoney \
		--cov-report=term-missing

test-postgres:  ## PostgreSQL only (uses pgserver if no URL is set)
	$(BIN)/pytest --stores=postgres
	$(BIN)/pytest tests/test_store.py -k postgres

test-thorough:  ## 5000 examples per property; for when you touch rounding
	$(BIN)/pytest --hypothesis-profile=thorough

cov:  ## Coverage with an HTML report
	$(BIN)/pytest --cov=src/pymoney --cov-report=html --cov-report=term-missing
	@echo "open htmlcov/index.html"

check: lint types test-ci  ## Everything CI checks (without PostgreSQL)

bench:  ## Storage benchmark
	$(BIN)/python benchmarks/bench_store.py --transactions 20000 --bulk

bench-scaling:  ## Does the ledger stay fast as it grows?
	$(BIN)/python benchmarks/bench_store.py --scaling 25000,50000,100000,200000

bench-import:  ## How long does a KMyMoney migration take?
	$(BIN)/python benchmarks/bench_kmymoney.py --transactions 50000

openapi:  ## Regenerate docs/openapi.json (the shipped contract)
	$(BIN)/python scripts/openapi.py

serve:  ## Run the API locally (http://127.0.0.1:8000/docs)
	PYMONEY_DATABASE_URL=$${PYMONEY_DATABASE_URL:-sqlite+pysqlite:///pymoney.sqlite} \
	PYMONEY_CREATE_SCHEMA=1 PYMONEY_COOKIE_SECURE=0 \
	$(BIN)/uvicorn pymoney.api:app --reload --port 8000

migrate:  ## Apply migrations ($$PYMONEY_DATABASE_URL); the same command works inside the image
	$(BIN)/python -m pymoney.store.migrate

migration:  ## Autogenerate a migration: make migration M="what changed"
	$(BIN)/alembic revision --autogenerate -m "$(M)"

clean:  ## Remove caches and build output (keeps .venv, node_modules and your ledger)
	rm -rf build dist htmlcov .coverage .coverage.* coverage.xml junit.xml
	rm -rf .pytest_cache .ruff_cache .mypy_cache .hypothesis
	rm -rf frontend/apps/web/dist frontend/coverage frontend/.vite \
	       frontend/e2e/test-results frontend/e2e/report
	find . -name '*.tsbuildinfo' -not -path '*/node_modules/*' -delete
	find . \( -path ./.venv -o -path '*/node_modules' \) -prune -o \
	       -name __pycache__ -type d -prune -exec rm -rf {} +
	find . \( -path ./.venv -o -path '*/node_modules' \) -prune -o \
	       -name '*.egg-info' -type d -prune -exec rm -rf {} +
	@test ! -e pymoney.sqlite || echo "kept pymoney.sqlite (the 'make serve' ledger); 'make clean-all' removes it"

clean-all: clean  ## Also remove .venv, node_modules and the local development ledger
	rm -rf .venv frontend/node_modules frontend/apps/web/node_modules frontend/packages/core/node_modules
	rm -f pymoney.sqlite pymoney.sqlite-wal pymoney.sqlite-shm

# --- containers -----------------------------------------------------------

up:  ## Start PostgreSQL in the background
	$(COMPOSE) up -d db

down:  ## Stop everything (add ARGS=-v to delete the data volume)
	$(COMPOSE) down $(ARGS)

logs:  ## Follow the database log
	$(COMPOSE) logs -f db

db-shell:  ## psql into the development database
	$(COMPOSE) exec db psql --username "$${POSTGRES_USER:-pymoney}" \
		--dbname "$${POSTGRES_DB:-pymoney}"

image:  ## Build the runtime image
	docker build --target runtime -t pymoney:runtime .

image-test:  ## Build the test image and run the suite inside it
	docker build --target test -t pymoney:test .
	docker run --rm pymoney:test

compose-config:  ## Validate compose.yaml the way CI does
	$(COMPOSE) --env-file .env.example config --quiet && echo "compose.yaml ok"

# --- web frontend ---------------------------------------------------------
# Lives in frontend/ as an npm workspace: packages/core (platform-agnostic
# client, shared with future mobile/desktop apps) and apps/web (React + Vite).

NPM ?= npm --prefix frontend

web-install:  ## Install frontend dependencies
	$(NPM) ci

web-dev:  ## Run the web app with hot reload (proxies /api to :8000; run `make serve` too)
	$(NPM) run dev

web-build:  ## Production build into frontend/apps/web/dist
	$(NPM) run build

web-test:  ## Frontend unit and component tests
	$(NPM) test

web-check:  ## Everything frontend CI checks: generated types are current, typecheck, tests, build
	$(NPM) run check

seed-demo:  ## Create a small fully-priced demo book through a running API (see scripts/seed_demo.py)
	$(BIN)/python scripts/seed_demo.py

# --- end-to-end -------------------------------------------------------------
# A real browser against the real API and the real web app. Playwright starts
# both servers itself (fresh SQLite database, registration open); nothing else
# needs to be running. See frontend/README.md, "End-to-end tests".

e2e-install:  ## Download Playwright's Chromium (once; CI does this too)
	$(NPM) run e2e:install

e2e:  ## Browser walkthroughs: register, create a book, share, post, edit, delete
	$(NPM) run e2e

# --- desktop --------------------------------------------------------------------
# The PySide6 client (pip install -e '.[desktop]'). The same API, two ways: a
# thin online client or a local replica with an outbox. See README.md.

desktop:  ## Run the desktop app against $$PYMONEY_SERVER (default http://127.0.0.1:8000)
	$(BIN)/python -m pymoney.desktop

desktop-build:  ## Build a distributable folder with PyInstaller into dist/PyMoney
	$(BIN)/pip install -q pyinstaller
	cd packaging && ../$(BIN)/pyinstaller --noconfirm --distpath ../dist --workpath ../build/pyinstaller pymoney-desktop.spec
