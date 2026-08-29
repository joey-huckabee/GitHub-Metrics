.DEFAULT_GOAL := help
POETRY ?= poetry
RUN := $(POETRY) run
PKG := github_metrics
TESTS := tests

.PHONY: help install format lint types test cov dead check clean hooks

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies
	$(POETRY) install --with dev

hooks: ## Install the pre-commit git hooks
	$(RUN) pre-commit install

format: ## Auto-format the codebase
	$(RUN) black $(PKG) $(TESTS)
	$(RUN) isort $(PKG) $(TESTS)
	$(RUN) ruff check --fix $(PKG) $(TESTS)

lint: ## Run the linters without modifying files
	$(RUN) black --check --diff $(PKG) $(TESTS)
	$(RUN) isort --check-only --diff $(PKG) $(TESTS)
	$(RUN) ruff check $(PKG) $(TESTS)
	$(RUN) pylint $(PKG) $(TESTS)

types: ## Run mypy
	$(RUN) mypy --config-file mypy.ini

test: ## Run the test suite
	$(RUN) pytest -m "not integration"

cov: test ## Alias for test (coverage is always on)

dead: ## Look for unused code
	$(RUN) vulture

check: lint types test dead ## Everything CI runs

clean: ## Remove build and cache artifacts
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
