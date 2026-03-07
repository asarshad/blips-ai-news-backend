from scripts.qa_gates import (
    app_python_files,
    build_pytest_command,
    changed_test_files,
    detect_missing_tests,
)


def test_filters_changed_app_and_test_files():
    changed = [
        "app/services/playlist_service.py",
        "app/services/__init__.py",
        "tests/unit/test_playlist_service.py",
        "docs/TESTING.md",
    ]

    assert app_python_files(changed) == ["app/services/playlist_service.py"]
    assert changed_test_files(changed) == ["tests/unit/test_playlist_service.py"]


def test_detect_missing_tests_returns_uncovered_module():
    changed_app = ["app/services/topup_service.py"]
    changed_tests = []
    existing = {"tests/unit/test_playlist_service.py"}

    missing = detect_missing_tests(changed_app, changed_tests, existing)

    assert missing == ["app/services/topup_service.py"]


def test_detect_missing_tests_accepts_matching_existing_or_changed_tests():
    changed_app = ["app/services/editorial_repo.py", "app/api/admin/routes.py"]
    changed_tests = ["tests/unit/test_admin_routes.py"]
    existing = {"tests/unit/test_editorial_repo.py"}

    missing = detect_missing_tests(changed_app, changed_tests, existing)

    assert missing == []


def test_build_pytest_command_includes_coverage_gate():
    cmd = build_pytest_command(72, ["-k", "editorial"])
    assert "--cov=app" in cmd
    assert "--cov-fail-under=72" in cmd
    assert cmd[-2:] == ["-k", "editorial"]
