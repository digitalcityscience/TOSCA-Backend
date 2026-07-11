"""
Typed result for GeoServer-first create/delete orchestration
(GeoServerClient._create_resource / _delete_resource and their callers:
create_workspace, delete_workspace, create_store, delete_store).

Supports dict-style read access (get/__getitem__/__contains__) so existing
callers built around free-form dicts keep working unchanged; new or updated
call sites should prefer the .success/.message/.error/.data attributes
directly instead.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_TOP_LEVEL_KEYS = ("success", "message", "error")


@dataclass
class OperationResult:
    success: bool
    message: str = ""
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default=None):
        if key in _TOP_LEVEL_KEYS:
            return getattr(self, key)
        return self.data.get(key, default)

    def __getitem__(self, key: str):
        if key in _TOP_LEVEL_KEYS:
            return getattr(self, key)
        return self.data[key]

    def __contains__(self, key: str) -> bool:
        return key in _TOP_LEVEL_KEYS or key in self.data
