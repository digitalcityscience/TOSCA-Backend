"""
Tests for scripts/check_no_env_files.py (issue 27: pre-commit hook blocking
real .env files from being committed).

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
    assert find_forbidden_env_files([".env.example"]) == []


def test_plain_env_file_is_forbidden():
    assert find_forbidden_env_files([".env"]) == [".env"]


def test_env_dev_is_forbidden():
    assert find_forbidden_env_files([".env.dev"]) == [".env.dev"]


def test_env_prod_and_env_test_are_forbidden():
    result = find_forbidden_env_files([".env.prod", ".env.test"])
    assert result == [".env.prod", ".env.test"]


def test_env_local_is_forbidden():
    assert find_forbidden_env_files([".env.local"]) == [".env.local"]


def test_nested_path_is_checked_by_basename():
    assert find_forbidden_env_files(["docker/example/.env.prod"]) == [
        "docker/example/.env.prod"
    ]


def test_unrelated_files_are_ignored():
    assert find_forbidden_env_files(["README.md", "pyproject.toml", "tosca_api/urls.py"]) == []


def test_mixed_staged_files_only_flags_the_env_ones():
    result = find_forbidden_env_files(["README.md", ".env.prod", "pyproject.toml", ".env.example"])
    assert result == [".env.prod"]


def test_main_returns_zero_when_nothing_forbidden():
    assert main([".env.example", "README.md"]) == 0


def test_main_returns_nonzero_when_forbidden_file_staged():
    assert main([".env.dev"]) == 1
