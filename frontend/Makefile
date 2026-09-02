# The web frontend: an npm workspace (packages/core, apps/web) plus the
# Playwright end-to-end suite, which starts the server from ../server's venv.

NPM ?= npm

.DEFAULT_GOAL := help
.PHONY: help install dev build test check e2e e2e-install image clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies
	$(NPM) ci

dev:  ## Run the web app with hot reload (proxies /api to :8000; run `make -C ../server serve` too)
	$(NPM) run dev

build:  ## Production build into apps/web/dist
	$(NPM) run build

test:  ## Unit and component tests
	$(NPM) test

check:  ## Everything CI checks: generated types current, typecheck, tests, build
	$(NPM) run check

e2e-install:  ## Download Playwright's Chromium (once; CI does this too)
	$(NPM) run e2e:install

e2e:  ## Browser walkthroughs against the real API and app (needs ../.venv with the server installed)
	$(NPM) run e2e

image:  ## Build the nginx image serving the built app
	docker build -t pymoney-frontend .

clean:  ## Remove build output and caches (keeps node_modules)
	rm -rf apps/web/dist coverage .vite e2e/test-results e2e/report
	find . -name '*.tsbuildinfo' -not -path '*/node_modules/*' -delete
