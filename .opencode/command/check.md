---
description: Run the repo's full check suite (typecheck, tests, build, i18n) and report failures.
agent: build
---

Run the project's canonical verification and report the result.

1. Run `make check` from the repository root.
2. If it fails, name the failing step and the first actionable error. Do not fix anything unless asked.
3. If it passes, report the summary line (tests passed, build ok).

Extra instructions: $ARGUMENTS
