# Target-o-meter developer targets.
#
# Backend runs under `uv`; frontend (Phase 2+) under `src/frontend/`. The
# frontend toolchain does not exist yet in S-01 Phase 1, so every frontend
# target no-ops with a note when `src/frontend/package.json` is absent — the
# same targets light up automatically once Phase 2 lands.
#
# Commands are written to be copy-pasteable; `.PHONY` keeps them from being
# shadowed by same-named files.

.PHONY: help run migrate check be-test fe-test system-test acceptance-test

# Frontend presence guard. Exported so sub-makes / shell snippets can read it.
FE_DIR := src/frontend
FE_PKG := $(FE_DIR)/package.json

# --- Backend ---------------------------------------------------------------

run: migrate  ## Start Django dev server (:8000) in DEBUG mode (migrate first)
	uv run python src/manage.py runserver

migrate:  ## Apply Django migrations
	uv run python src/manage.py migrate

# --- Checks (backend + frontend) -------------------------------------------
#
# `check` runs every linter / type-checker / import-verification the project
# has, and fixes formatting where the tool supports it (ruff --fix, tsc via
# the fe build). It is the prerequisite for `be-test` and `fe-test` so a
# lint regression can never pass the gate silently.

check:  ## Run backend + frontend linters, fix formatting where possible
	# Backend: ruff check with --fix (safe autofixes) then a gating re-check,
	# then import-linter (architecture contracts from AGENTS.md §6). We do NOT
	# run `ruff format .` repo-wide: the existing tree passes `ruff check` but
	# predates the formatter, so a wholesale format would produce a huge
	# unrelated diff. `ruff check --fix` handles the safe autofixes; deeper
	# formatting is left to a dedicated cleanup if desired.
	uv run ruff check --fix .
	uv run ruff check .
	uv run lint-imports
	@if [ -f "$(FE_PKG)" ]; then \
		echo "→ frontend toolchain present: running npm checks"; \
		( cd $(FE_DIR) && npm run lint --if-present ); \
		( cd $(FE_DIR) && npx tsc --noEmit ); \
	else \
		echo "→ frontend toolchain absent (no $(FE_PKG)); skipping frontend checks"; \
	fi

# --- Tests -----------------------------------------------------------------

be-test: check  ## Run backend tests (default suite, excludes uat)
	uv run pytest

fe-test: check  ## Run frontend tests (no-op until Phase 2)
	@if [ -f "$(FE_PKG)" ]; then \
		( cd $(FE_DIR) && npm run test ); \
	else \
		echo "fe-test: no frontend toolchain yet (no $(FE_PKG)); skipping."; \
	fi

system-test:  ## Run system tests (tests/system/, the dev marker)
	uv run pytest tests/system

# Acceptance tests hit REAL Auth0 and are gated behind RUN_UAT=1 + the `uat`
# marker (conftest.py skips every uat test unless RUN_UAT is set). We pass both
# so the target actually runs them instead of skipping, and scope the run to
# tests/acceptance/ so unrelated suites are not collected. pytest exits 5 when
# no tests are collected (the current state — only conftest fixtures exist);
# that's "nothing to run", not a failure, so we tolerate it.
acceptance-test:  ## Run acceptance tests (real Auth0; set RUN_UAT=1 + creds)
	@uv run pytest tests/acceptance -m uat; \
	status=$$?; \
	if [ $$status -eq 5 ]; then \
		echo "acceptance-test: no acceptance tests collected yet (only conftest fixtures present); nothing to run."; \
	elif [ $$status -ne 0 ]; then \
		exit $$status; \
	fi

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
