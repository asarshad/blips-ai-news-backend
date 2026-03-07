from scripts.ci_parity import build_command_plan


def test_build_command_plan_default_matches_pr_ci():
    commands = build_command_plan(include_integration=False, python_executable="python")
    assert commands == [
        ["python", "-m", "ruff", "format", "--check", "app/", "tests/"],
        ["python", "-m", "ruff", "check", "app/", "tests/"],
        ["python", "-m", "pytest", "tests/unit", "tests/contract"],
    ]


def test_build_command_plan_with_integration_adds_integration_step():
    commands = build_command_plan(include_integration=True, python_executable="python")
    assert commands[-1] == [
        "python",
        "-m",
        "pytest",
        "tests/integration",
        "--force-enable-socket",
    ]
