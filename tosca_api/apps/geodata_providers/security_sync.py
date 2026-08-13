"""
GeoServerSecuritySyncService (epic-11 ticket 08, canonical §5c).

Pushes a Workspace's owner-org + visibility onto GeoServer's Data Security
ACL as three workspace-wide rules (`<ws>.*.r`, `<ws>.*.w`, `<ws>.*.a`). Django
-> GeoServer, one direction, synchronous on Workspace save. Never writes to
Keycloak (canonical §9 -- the KeycloakSyncService half of the old design is gone).

Role model (2026-08-13, supersedes the GROUP_ADMIN/role-hierarchy detour):
GeoServer only needs **two** org roles, and workspace-admin comes from the ACL
`.a` rule -- *not* from a role parent/inheritance. Per workspace:

    <ws>.*.r = <writer>,<reader>   # read: writer + reader (writer also reads)
    <ws>.*.w = <writer>            # write: writer
    <ws>.*.a = <writer>            # workspace admin/config: writer

So ``ROLE_*_READER`` = read-only data consumer, and ``ROLE_*_WRITER`` =
**workspace editor/admin** (data write + GeoServer config for its own
workspace). No ``ROLE_*_ADMIN`` / ``GROUP_ADMIN`` role is needed on the GeoServer
side; Django ``ADMIN`` remains platform/org/user management only.

Ticket 09 revision (2026-08-12, product decision -- supersedes the
dirty/retry design canonical §10a originally sketched): a Workspace must
never exist in Django with an *attempted-and-failed* GeoServer ACL push, so
a push failure raises ``GeoServerACLSyncError`` instead of being logged and
swallowed. `Workspace.save()` wraps the write in `transaction.atomic()`
(see `models.py`), so the raise here rolls back the Workspace row too --
create fails outright, no `dirty` state, no retry command.

A missing/inactive `GeodataEngine` is *not* a push failure -- it's a
Workspace that has nothing to push to yet (e.g. metadata staged before an
engine is provisioned, or an engine intentionally deactivated). That stays
a silent no-op, same as ticket 08's original behavior.

The rules are pushed `.r` then `.w` then `.a` in `_rule_map()`: read is the
broader, "more encompassing" grant and GeoServer should see it applied first.
"""
from __future__ import annotations

import logging

from .models import Workspace

logger = logging.getLogger(__name__)


class GeoServerACLSyncError(Exception):
    """Raised when a Workspace's GeoServer ACL rules could not be pushed."""


class GeoServerSecuritySyncService:
    """Computes and pushes the §5c ACL rule pair for a single Workspace."""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def sync(self) -> None:
        """Push both ACL rules for the workspace.

        No-op if the workspace has no active `GeodataEngine` (nothing to push
        to yet). Otherwise raises ``GeoServerACLSyncError`` if the client
        can't be created or any rule push fails -- never returns a false-y
        "it's fine, logged" result for an actual failure.
        """
        engine = self.workspace.geodata_engine
        if engine is None or not engine.is_active:
            logger.info(
                "GeoServer ACL sync skipped for workspace '%s': no active GeodataEngine.",
                self.workspace.name,
            )
            return

        try:
            client = engine.get_client()
        except Exception as e:
            raise GeoServerACLSyncError(
                f"Could not create GeoServer client for workspace "
                f"'{self.workspace.name}': {e}"
            ) from e

        errors = []
        for key, roles in self._rule_map().items():
            result = client.set_layer_rule(key, roles)
            if not result.success:
                errors.append(f"{key}: {result.error or result.message}")

        if errors:
            message = (
                f"GeoServer ACL sync failed for workspace '{self.workspace.name}': "
                + "; ".join(errors)
            )
            logger.error(message)
            raise GeoServerACLSyncError(message)

    def _rule_map(self) -> dict[str, str]:
        """The §5c ACL rules for this workspace's current organization + visibility.

        Three rules (insertion order matters: `.r` broader, then `.w`, then `.a`):
        - `.r` read  -> `"*"` if public, else `writer,reader` (writer also reads)
        - `.w` write -> writer
        - `.a` admin -> writer (workspace admin/config; replaces any GROUP_ADMIN
          role -- see module docstring)
        """
        org = self.workspace.organization
        ws_name = self.workspace.name
        is_public = self.workspace.visibility == Workspace.Visibility.PUBLIC
        writer = org.writer_role
        read_roles = "*" if is_public else f"{writer},{org.reader_role}"
        return {
            f"{ws_name}.*.r": read_roles,
            f"{ws_name}.*.w": writer,
            f"{ws_name}.*.a": writer,
        }
