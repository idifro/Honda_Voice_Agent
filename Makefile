# Common tasks (CLAUDE.md "Commands"). `make test` is the fast check: no database, no network, no LLM.
# Python always runs through uv; the frontend through the pinned pnpm (corepack).

UV   := uv run
PNPM := corepack pnpm@9.15.9
FE   := cd frontend &&

.DEFAULT_GOAL := help
.PHONY: help install lint format test test-python test-frontend test-integration evals signals check check-quick

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-17s %s\n", $$1, $$2}'

install: ## install Python and frontend dependencies
	uv sync
	$(FE) $(PNPM) install

lint: ## ruff (lint + format check) and the frontend's eslint
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(FE) $(PNPM) exec eslint app components lib

format: ## auto-format Python and the frontend
	$(UV) ruff check --fix .
	$(UV) ruff format .
	$(FE) $(PNPM) exec eslint --fix app components lib

test: lint test-python test-frontend ## fast checks: lint, Python unit tests, frontend tests (no DB, no network)

test-python: ## Python unit tests
	$(UV) pytest -q

test-frontend: ## frontend tests (Vitest: sign-in, token route, rate limits, leads, CSV)
	$(FE) $(PNPM) test

test-integration: ## database tests (needs DATABASE_URL in .env; frontend reader tests need frontend/.env.local)
	$(UV) pytest -q -m integration

evals: ## scripted conversations with the real LLM and database (~15 min; must pass >= 95 %)
	$(UV) python -m evals.run_text_evals --min-pass 0.95

signals: ## caller-signal accuracy on the 150 labelled turns (nano; >= 90 % per language)
	$(UV) python -m evals.signals_labelled

check: ## pre-demo gate: everything above (scripts/check_all.sh)
	scripts/check_all.sh

check-quick: ## the pre-demo gate without the scripted conversations (~2 min)
	SKIP_EVALS=1 scripts/check_all.sh
