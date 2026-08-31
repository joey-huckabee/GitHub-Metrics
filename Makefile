.DEFAULT_GOAL := help
POETRY ?= poetry
RUN := $(POETRY) run
PKG := github_metrics
TESTS := tests
SCRIPTS := scripts

.PHONY: help install format lint types test cov dead trace trace-check check clean hooks

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies
	$(POETRY) install --with dev

hooks: ## Install the pre-commit git hooks
	$(RUN) pre-commit install

format: ## Auto-format the codebase
	$(RUN) black .
	$(RUN) isort .
	$(RUN) ruff check --fix .

lint: ## Run the linters without modifying files
	$(RUN) black --check --diff .
	$(RUN) isort --check-only --diff .
	$(RUN) ruff check .
	$(RUN) pylint $(PKG) $(TESTS) $(SCRIPTS)

types: ## Run mypy
	$(RUN) mypy --config-file mypy.ini

test: ## Run the test suite
	$(RUN) pytest -m "not integration"

cov: test ## Alias for test (coverage is always on)

dead: ## Look for unused code
	$(RUN) vulture

trace: ## Regenerate docs/TRACE-MATRIX.md from requirements and test markers
	$(RUN) python scripts/build-trace-matrix.py

trace-check: ## Fail if the committed trace matrix is stale
	$(RUN) python scripts/build-trace-matrix.py --check

check: lint types test dead trace-check ## Everything CI runs

clean: ## Remove build and cache artifacts
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
