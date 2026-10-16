SOURCE_FILES ?= $(shell git ls-files "**.py")
# Prefer the project's pinned ruff from the virtualenv over any system-wide one
RUFF ?= $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,ruff)

.PHONY: all
all:

.PHONY: only-test
only-test:
	python3 -m pytest

.PHONY: ruff
ruff:
	$(RUFF) check
	$(RUFF) format --check

.PHONY: tidy
tidy:
	$(RUFF) format
	$(RUFF) check --fix

.PHONY: typecheck
typecheck:
	PYRIGHT_PYTHON_FORCE_VERSION=latest pyright --skipunannotated --warnings

.PHONY: check-maintainability
check-maintainability:
	@echo "Checking maintainability (grade B or worse) …"
	@radon mi ${SOURCE_FILES} -n B | (! grep ".")

.PHONY: check-code-health
check-code-health:
	@echo "Checking code health…"
	@vulture ${SOURCE_FILES} --min-confidence 80

.PHONY: only-test-with-coverage
only-test-with-coverage:
	python3 -m pytest -v --cov --cov-report=xml --cov-report=term-missing

# aggregate targets

.PHONY: checkstyle
checkstyle: ruff typecheck check-maintainability check-code-health

.PHONY: test
test: only-test-with-coverage checkstyle

.PHONY: test-with-coverage
test-with-coverage: only-test-with-coverage checkstyle
