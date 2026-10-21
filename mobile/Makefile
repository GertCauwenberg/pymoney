# PyMoney mobile (Expo). Component Makefile — root `make` only delegates.
#
# E0.1 targets. Device floor: iOS 16+ / Android 10+.

.DEFAULT_GOAL := help
.PHONY: help install mobile-dev mobile-ios mobile-android mobile-check mobile-e2e mobile-build clean \
        dev ios android check typecheck test e2e build

NPM ?= npm
NPX ?= npx

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## npm install for the mobile app
	$(NPM) install

mobile-dev:  ## Start the Expo dev server (metro)
	$(NPX) expo start

mobile-ios:  ## Run on an iOS simulator (needs macOS + Xcode)
	$(NPX) expo run:ios

mobile-android:  ## Run on an Android emulator/device
	$(NPX) expo run:android

mobile-check:  ## What CI runs for mobile (typecheck + tests)
	$(MAKE) typecheck
	$(MAKE) test

mobile-e2e:  ## Maestro e2e flows (E9.1)
	$(NPX) maestro test e2e

mobile-build:  ## Production export / EAS build entry point
	$(NPX) expo export --platform android --output-dir dist/android
	$(NPX) expo export --platform ios --output-dir dist/ios

typecheck:  ## TypeScript check
	$(NPM) run typecheck

test:  ## Unit / component tests (E0.5 harness)
	@if [ -d tests ] && [ -f vitest.config.ts -o -f jest.config.js ]; then \
		$(NPM) test; \
	else \
		echo "no test harness yet (E0.5); typecheck only"; \
		$(MAKE) typecheck; \
	fi

clean:  ## Remove Expo/Metro caches and native build output
	rm -rf .expo dist android/build android/app/build ios/build node_modules/.cache

# Short aliases
dev: mobile-dev
ios: mobile-ios
android: mobile-android
check: mobile-check
e2e: mobile-e2e
build: mobile-build
