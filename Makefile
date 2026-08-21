# Thin wrappers so local development and CI run the same commands.
# `make check` is what CI runs; run it before pushing.

PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help install fmt lint types test test-ci test-thorough cov check clean \
        up down logs db-shell image image-test compose-config

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

test:  ## Fast suite (50 Hypothesis examples per property)
	$(BIN)/pytest

test-ci:  ## The suite as CI runs it (500 examples, derandomised)
	$(BIN)/pytest --hypothesis-profile=ci --cov=pymoney --cov-report=term-missing

test-thorough:  ## 5000 examples per property; for when you touch rounding
	$(BIN)/pytest --hypothesis-profile=thorough

cov:  ## Coverage with an HTML report
	$(BIN)/pytest --cov=pymoney --cov-report=html --cov-report=term-missing
	@echo "open htmlcov/index.html"

check: lint types test-ci  ## Everything CI checks

clean:  ## Remove caches and build output
	rm -rf build dist htmlcov .coverage coverage.xml junit.xml
	rm -rf .pytest_cache .ruff_cache .mypy_cache .hypothesis
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.egg-info' -type d -prune -exec rm -rf {} +

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
