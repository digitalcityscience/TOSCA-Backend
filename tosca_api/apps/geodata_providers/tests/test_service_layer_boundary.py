"""
Architectural regression guard: geoserver.client / sync_service must only
be imported through engine_factory.EngineClientFactory. Every admin, admin
action/view, DRF view, and management command should go through the
factory so engine-type dispatch (GeoServer vs. the Martin placeholder)
stays centralized in one place.

This is a static/grep-style check rather than a runtime test, per the
issue's own suggested test approach — the thing being guarded against is
an import statement reappearing, not a behavior.
"""
import ast
from pathlib import Path

from django.test import SimpleTestCase

APP_ROOT = Path(__file__).resolve().parent.parent

# Files that ARE the service layer (or the factory that fronts it) — these
# are allowed to import geoserver.client / sync_service directly.
ALLOWED_FILES = {
    APP_ROOT / "engine_factory.py",
    APP_ROOT / "sync_service.py",
}

# Directories that are the sync layer itself, or test code (unit tests
# legitimately construct the class under test directly). Note services/
# is NOT excluded — services/commands/* should also go through the
# factory, and currently does.
ALLOWED_DIRS = {
    APP_ROOT / "sync",
    APP_ROOT / "tests",
    APP_ROOT / "geoserver",
}


def _iter_app_python_files():
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path in ALLOWED_FILES:
            continue
        if any(allowed_dir in path.parents for allowed_dir in ALLOWED_DIRS):
            continue
        yield path


def _forbidden_imports_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module.endswith("sync_service") or module.endswith("geoserver.client"):
            hits.append(f"line {node.lineno}: from {'.' * node.level}{module} import ...")
    return hits


class ServiceLayerBoundaryTests(SimpleTestCase):
    def test_no_view_or_admin_module_imports_sync_service_or_geoserver_client_directly(self):
        violations = {}
        for path in _iter_app_python_files():
            hits = _forbidden_imports_in(path)
            if hits:
                violations[str(path.relative_to(APP_ROOT))] = hits

        self.assertEqual(
            violations,
            {},
            "The following files import sync_service/geoserver.client directly "
            "instead of going through engine_factory.EngineClientFactory: "
            f"{violations}",
        )
