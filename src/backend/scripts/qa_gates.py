#!/usr/bin/env python3
"""Coverage + missing-test quality gates for backend changes."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set


def _normalize(path: str) -> str:
    value = path.replace("\\", "/").strip()
    if value.startswith("src/backend/"):
        value = value[len("src/backend/") :]
    return value


def _run_git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def resolve_base_ref(cwd: Path, requested: Optional[str] = None) -> Optional[str]:
    """Resolve a sensible git base ref for diffing."""
    if requested:
        result = _run_git(["rev-parse", "--verify", requested], cwd)
        return requested if result.returncode == 0 else None

    for candidate in ("origin/main", "main", "master"):
        result = _run_git(["rev-parse", "--verify", candidate], cwd)
        if result.returncode == 0:
            return candidate
    return None


def changed_files(cwd: Path, base_ref: Optional[str]) -> List[str]:
    """Return changed files in current branch against base_ref."""
    files: Set[str] = set()

    if base_ref:
        args = ["diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...HEAD"]
    else:
        args = ["diff", "--name-only", "--diff-filter=ACMR", "HEAD~1", "HEAD"]

    result = _run_git(args, cwd)
    if result.returncode == 0:
        files.update(_normalize(line) for line in result.stdout.splitlines() if line.strip())

    # Include local staged + unstaged changes so local runs catch missing tests too.
    for local_args in (
        ["diff", "--name-only", "--diff-filter=ACMR"],
        ["diff", "--name-only", "--diff-filter=ACMR", "--cached"],
    ):
        local = _run_git(local_args, cwd)
        if local.returncode == 0:
            files.update(_normalize(line) for line in local.stdout.splitlines() if line.strip())

    untracked = _run_git(["ls-files", "--others", "--exclude-standard"], cwd)
    if untracked.returncode == 0:
        files.update(_normalize(line) for line in untracked.stdout.splitlines() if line.strip())

    return sorted(files)


def app_python_files(paths: Iterable[str]) -> List[str]:
    files = []
    for path in paths:
        normalized = _normalize(path)
        if not normalized.endswith(".py"):
            continue
        if not normalized.startswith("app/"):
            continue
        if normalized.endswith("__init__.py"):
            continue
        files.append(normalized)
    return sorted(set(files))


def changed_test_files(paths: Iterable[str]) -> List[str]:
    files = []
    for path in paths:
        normalized = _normalize(path)
        if normalized.startswith("tests/") and normalized.endswith(".py"):
            files.append(normalized)
    return sorted(set(files))


def existing_test_files(repo_root: Path) -> Set[str]:
    tests_root = repo_root / "tests"
    if not tests_root.exists():
        return set()
    return {
        _normalize(path.relative_to(repo_root).as_posix()) for path in tests_root.rglob("test_*.py")
    }


def detect_missing_tests(
    changed_app: Sequence[str],
    changed_tests: Sequence[str],
    existing_tests: Set[str],
) -> List[str]:
    """Detect changed app modules that have no matching test file."""
    changed_test_set = {_normalize(path) for path in changed_tests}
    missing: List[str] = []

    for app_file in changed_app:
        stem = Path(app_file).stem
        expected_name = f"test_{stem}.py"

        has_test = False
        for test_file in existing_tests:
            test_path = Path(test_file)
            if test_path.name == expected_name:
                has_test = True
                break

        if not has_test:
            for changed_test in changed_test_set:
                test_stem = Path(changed_test).stem
                if test_stem == f"test_{stem}" or stem in test_stem:
                    has_test = True
                    break

        if not has_test:
            missing.append(app_file)

    return sorted(missing)


def build_pytest_command(min_coverage: int, extra_args: Sequence[str]) -> List[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit",
        "tests/contract",
        "--cov=app",
        "--cov-report=term-missing",
        f"--cov-fail-under={min_coverage}",
        *extra_args,
    ]


def run_quality_gates(
    repo_root: Path,
    min_coverage: int,
    base_ref: Optional[str],
    extra_pytest_args: Sequence[str],
) -> int:
    changed = changed_files(repo_root, base_ref)
    changed_app = app_python_files(changed)
    changed_tests = changed_test_files(changed)
    missing = detect_missing_tests(changed_app, changed_tests, existing_test_files(repo_root))

    print("== QA Gate: Changed Files ==")
    print(f"Base ref: {base_ref or 'HEAD~1'}")
    print(f"Changed app files: {len(changed_app)}")
    print(f"Changed test files: {len(changed_tests)}")

    if missing:
        print("\nMissing tests for changed modules:")
        for path in missing:
            print(f"- {path}")
        return 2

    pytest_cmd = build_pytest_command(min_coverage=min_coverage, extra_args=extra_pytest_args)
    print("\n== QA Gate: Coverage ==")
    print(" ".join(pytest_cmd))
    result = subprocess.run(pytest_cmd, cwd=repo_root, check=False)
    return result.returncode


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run backend QA gates.")
    parser.add_argument("--min-coverage", type=int, default=50, help="Minimum % line coverage.")
    parser.add_argument(
        "--base-ref",
        default=None,
        help="Git ref used for changed-file detection (default: auto-resolve).",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra pytest args (prefix with --, e.g. -- -k editorial).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    base_ref = resolve_base_ref(repo_root, requested=args.base_ref)
    extra_args = list(args.pytest_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    return run_quality_gates(
        repo_root=repo_root,
        min_coverage=args.min_coverage,
        base_ref=base_ref,
        extra_pytest_args=extra_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
