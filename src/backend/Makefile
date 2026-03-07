# Blips backend development tasks.
# Run `make help` to see all targets.

PYTHON     := venv/bin/python
PYTEST     := $(PYTHON) -m pytest
RUFF       := $(PYTHON) -m ruff
PYTHONPATH ?= .

.PHONY: help verify lint format fmt-check test unit contract qa-gates ci-parity migrate rollout-validate clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Full CI-equivalent check (run before opening a PR) ────────────────────────
verify: fmt-check lint qa-gates ## Run format-check + lint + QA gates (missing tests + coverage)

# ── Code quality ──────────────────────────────────────────────────────────────
lint: ## Check code with ruff (no fixes applied)
	$(RUFF) check app/ tests/

format: ## Auto-format code with ruff
	$(RUFF) format app/ tests/

fmt-check: ## Verify formatting (fails if any file would change)
	$(RUFF) format --check app/ tests/

# ── Tests ─────────────────────────────────────────────────────────────────────
test: ## Run all tests (unit + contract)
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit tests/contract -q

unit: ## Run unit tests only (fast)
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit -q

contract: ## Run contract/snapshot tests only
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/contract -q

qa-gates: ## Enforce missing-test detector + coverage threshold
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/qa_gates.py --min-coverage 50

ci-parity: ## Mirror GitHub backend CI checks locally (lint + unit + contract)
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/ci_parity.py

# ── Database ──────────────────────────────────────────────────────────────────
migrate: ## Apply pending Alembic migrations
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m alembic upgrade head

migrate-check: ## Show current Alembic revision
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m alembic current

# ── Rollout validation ───────────────────────────────────────────────────────
rollout-validate: ## Run controlled rollout health checks (requires API running)
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/post_launch_validation.py

# ── Housekeeping ──────────────────────────────────────────────────────────────
clean: ## Remove Python cache files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
