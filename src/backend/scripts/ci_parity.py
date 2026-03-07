#!/usr/bin/env python3
"""Local-to-GitHub CI parity runner for backend checks."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def build_command_plan(
    include_integration: bool = False, python_executable: Optional[str] = None
) -> List[List[str]]:
    """Build command sequence that mirrors GitHub backend workflows."""
    python = python_executable or sys.executable
    commands = [
        [python, "-m", "ruff", "format", "--check", "app/", "tests/"],
        [python, "-m", "ruff", "check", "app/", "tests/"],
        [python, "-m", "pytest", "tests/unit", "tests/contract"],
    ]
    if include_integration:
        commands.append(
            [python, "-m", "pytest", "tests/integration", "--force-enable-socket"]
        )
    return commands


def run_plan(
    commands: Sequence[Sequence[str]],
    *,
    cwd: Path,
    env_overrides: Optional[Dict[str, str]] = None,
) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONWARNINGS", "ignore:urllib3 v2 only supports OpenSSL.*")
    env.setdefault("PYTHONPATH", str(cwd))
    if env_overrides:
        env.update(env_overrides)

    for command in commands:
        printable = " ".join(command)
        print(f"$ {printable}")
        result = subprocess.run(command, cwd=cwd, env=env, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run local backend CI parity checks.")
    parser.add_argument(
        "--include-integration",
        action="store_true",
        help="Run integration tests (requires Docker).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands without executing.",
    )
    args = parser.parse_args(argv)

    cwd = Path(__file__).resolve().parents[1]
    plan = build_command_plan(include_integration=args.include_integration)

    if args.dry_run:
        for command in plan:
            print(" ".join(command))
        return 0

    return run_plan(plan, cwd=cwd)


if __name__ == "__main__":
    raise SystemExit(main())
