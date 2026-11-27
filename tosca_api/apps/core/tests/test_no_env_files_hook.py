"""
Tests for scripts/check_no_env_files.py (issue 27: pre-commit hook blocking
real .env files from being committed) and for the environment templates.

Loaded by file path rather than package import since scripts/ isn't a
Django app and has no __init__.py.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_no_env_files.py"

spec = importlib.util.spec_from_file_location("check_no_env_files", SCRIPT_PATH)
check_no_env_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_no_env_files)

find_forbidden_env_files = check_no_env_files.find_forbidden_env_files
main = check_no_env_files.main


def test_env_example_is_allowed():
    assert find_forbidden_env_files([".env.development.example", ".env.production.example"]) == []


def test_plain_env_file_is_forbidden():
    assert find_forbidden_env_files([".env"]) == [".env"]


def test_env_dev_is_forbidden():
    assert find_forbidden_env_files([".env.development"]) == [".env.development"]


def test_env_prod_and_env_test_are_forbidden():
    result = find_forbidden_env_files([".env.production", ".env.test"])
    assert result == [".env.production", ".env.test"]


def test_env_local_is_forbidden():
    assert find_forbidden_env_files([".env.local"]) == [".env.local"]


def test_nested_path_is_checked_by_basename():
    assert find_forbidden_env_files(["docker/example/.env.production"]) == [
        "docker/example/.env.production"
    ]


def test_unrelated_files_are_ignored():
    assert find_forbidden_env_files(["README.md", "pyproject.toml", "tosca_api/urls.py"]) == []


def test_mixed_staged_files_only_flags_the_env_ones():
    result = find_forbidden_env_files(
        ["README.md", ".env.production", "pyproject.toml", ".env.production.example"]
    )
    assert result == [".env.production"]


def test_main_returns_zero_when_nothing_forbidden():
    assert main([".env.development.example", "README.md"]) == 0


def test_main_returns_nonzero_when_forbidden_file_staged():
    assert main([".env.development"]) == 1


def test_old_names_are_forbidden():
    old = [".env.example", ".env.dev", ".env.prod"]
    assert find_forbidden_env_files(old) == old


def _variable_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text().splitlines():
        line = line.lstrip("# ").strip()
        name, sep, _ = line.partition("=")
        if sep and name.isupper() and name.replace("_", "").isalnum():
            names.append(name)
    return names


def test_both_templates_define_the_same_variables():
    development = _variable_names(REPO_ROOT / ".env.development.example")
    production = _variable_names(REPO_ROOT / ".env.production.example")
    assert development
    assert development == production
