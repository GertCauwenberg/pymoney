# PyMoney: three components, each with its own Makefile, deployable on its own.
#
#   server/     the Python package: core, stores, quotes, importers, HTTP API
#   desktop/    the PySide6 client (depends on server/)
#   frontend/   the React web app
#
# This file only delegates, so `make check` still checks everything and a new
# contributor still has one entry point. Component targets: `make -C server help`.

COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help install check test fmt i18n i18n-check clean clean-all \
        up down logs db-shell compose-config images \
        server desktop frontend mobile

help:  ## Show this help (component targets: make -C server help, etc.)
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Set up every component: one Python venv (.venv) for server + desktop, npm for frontend + mobile
	$(MAKE) -C server install
	$(MAKE) -C desktop install
	$(MAKE) -C frontend install
	$(MAKE) -C mobile install

check:  ## Everything CI checks, for all three components, plus the shared catalogue
	$(MAKE) -C server check
	$(MAKE) -C desktop check
	$(MAKE) -C frontend check
	$(MAKE) -C mobile mobile-check
	$(MAKE) i18n-check

test:  ## The fast Python suites (server, desktop) and the frontend tests
	$(MAKE) -C server test
	$(MAKE) -C desktop test
	$(MAKE) -C frontend test

fmt:  ## Format the Python components
	$(MAKE) -C server fmt
	$(MAKE) -C desktop fmt

i18n:  ## Copy the Dutch catalogue into the frontend and report untranslated strings (all components)
	.venv/bin/python scripts/i18n.py

i18n-check:  ## Fail if any string in any client lacks a Dutch translation (CI runs this)
	.venv/bin/python scripts/i18n.py --check

clean:  ## Remove caches and build output everywhere (keeps .venv, node_modules, your ledger)
	$(MAKE) -C server clean
	$(MAKE) -C desktop clean
	$(MAKE) -C frontend clean

clean-all: clean  ## Also remove .venv, node_modules and the local development ledger
	rm -rf .venv frontend/node_modules frontend/apps/web/node_modules frontend/packages/core/node_modules
	rm -f server/pymoney.sqlite server/pymoney.sqlite-wal server/pymoney.sqlite-shm

# --- the local stack --------------------------------------------------------

up:  ## Start PostgreSQL in the background
	$(COMPOSE) up -d db

down:  ## Stop everything (add ARGS=-v to delete the data volume)
	$(COMPOSE) down $(ARGS)

logs:  ## Follow the database log
	$(COMPOSE) logs -f db

db-shell:  ## psql into the development database
	$(COMPOSE) exec db psql --username "$${POSTGRES_USER:-pymoney}" \
		--dbname "$${POSTGRES_DB:-pymoney}"

compose-config:  ## Validate compose.yaml the way CI does
	$(COMPOSE) --env-file .env.example config --quiet && echo "compose.yaml ok"

images:  ## Build both deployable images: the server and the web frontend
	$(MAKE) -C server image
	$(MAKE) -C frontend image
