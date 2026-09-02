# The desktop client (PySide6). Depends on ../server, installed editable into the
# same virtualenv: `make install` here installs both.

PYTHON ?= python3
VENV   ?= ../.venv
BIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help install fmt lint types test test-ci cov check run build clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install the server (dependency) and the desktop, editable, with dev extras
	test -x $(BIN)/python || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e '../server[api]' -e '.[dev]'

fmt:  ## Format the code
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

lint:  ## Lint without modifying anything (as CI does)
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check --diff src tests

types:  ## Type check
	$(BIN)/mypy

test:  ## The suite, Qt offscreen, against the in-process API
	QT_QPA_PLATFORM=offscreen $(BIN)/pytest

test-ci:  ## The suite with the coverage gate, as CI runs it
	QT_QPA_PLATFORM=offscreen $(BIN)/pytest --cov --cov-report=term-missing

cov:  ## Coverage with an HTML report
	QT_QPA_PLATFORM=offscreen $(BIN)/pytest --cov --cov-report=html --cov-report=term-missing
	@echo "open htmlcov/index.html"

check: lint types test-ci  ## Everything CI checks for the desktop

run:  ## Run the desktop app against $$PYMONEY_SERVER (default http://127.0.0.1:8000)
	$(BIN)/python -m pymoney_desktop

build:  ## Build a distributable folder with PyInstaller into dist/PyMoney
	$(BIN)/pip install -q pyinstaller
	cd packaging && ../$(BIN)/pyinstaller --noconfirm --distpath ../dist --workpath ../build/pyinstaller pymoney-desktop.spec

clean:  ## Remove caches and build output
	rm -rf build dist htmlcov .coverage .coverage.* coverage.xml junit.xml
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.egg-info' -type d -prune -exec rm -rf {} +
