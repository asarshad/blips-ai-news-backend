# Local-to-GitHub CI Parity

Use this checklist before opening a backend PR to match `.github/workflows/backend_pr.yml`.

## One-command parity run

From `src/backend/`:

```bash
python scripts/ci_parity.py
```

Equivalent Make target:

```bash
make ci-parity
```

## What it runs

1. `ruff format --check app/ tests/`
2. `ruff check app/ tests/`
3. `pytest tests/unit tests/contract`

## Optional integration parity

When Docker is available and you want parity with scheduled integration workflow:

```bash
python scripts/ci_parity.py --include-integration
```

This adds:

- `pytest tests/integration --force-enable-socket`
