SOURCE_FILES ?= $(shell git ls-files "**.py")
PYTHON_RUN ?=

.PHONY: help
help: ## Display this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

.PHONY: all
all: help

.PHONY: only-test
only-test: ## Run unit tests without coverage or style checks
	$(PYTHON_RUN) python3 -m pytest

.PHONY: ruff
ruff: ## Run ruff linting and formatting checks
	$(PYTHON_RUN) ruff check
	$(PYTHON_RUN) ruff format --check

.PHONY: tidy
tidy: ## Format code and fix linting issues
	$(PYTHON_RUN) ruff format
	$(PYTHON_RUN) ruff check --fix

.PHONY: typecheck
typecheck: ## Run the ty type checker
	$(PYTHON_RUN) ty check

.PHONY: check-lock
check-lock: ## Verify uv.lock is in sync with pyproject.toml
	uv lock --check

.PHONY: check-maintainability
check-maintainability: ## Enforce Radon maintainability grade
	@echo "Checking maintainability (grade B or worse) …"
	@radon mi ${SOURCE_FILES} -n B | (! grep ".")

.PHONY: check-code-health
check-code-health: ## Find dead code with vulture
	@echo "Checking code health…"
	@vulture ${SOURCE_FILES} --min-confidence 80

.PHONY: only-test-with-coverage
only-test-with-coverage: ## Run unit tests with coverage report
	$(PYTHON_RUN) python3 -m pytest -v --cov --cov-report=term-missing

# aggregate targets

.PHONY: checkstyle
checkstyle: ruff typecheck check-maintainability check-code-health check-lock ## Run style and static analysis checks

.PHONY: test
test: only-test-with-coverage checkstyle ## Run all tests with coverage and style checks

.PHONY: test-with-coverage
test-with-coverage: only-test-with-coverage checkstyle ## Alias for test
