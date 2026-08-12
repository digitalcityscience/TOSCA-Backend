"""
GeoServerSecuritySyncService (epic-11 ticket 08, canonical §5c).

Pushes a Workspace's owner-org + visibility onto GeoServer's Data Security
ACL as two workspace-wide rules (`<ws>.*.r`, `<ws>.*.w`). Django -> GeoServer,
one direction, synchronous on Workspace save. Never writes to Keycloak
(canonical §9 -- the KeycloakSyncService half of the old design is gone).

Error handling here is happy-path: failures are logged, not raised, so a
GeoServer outage never blocks a Workspace save. Marking the Workspace dirty
for retry is ticket 09's job, not this one.
"""
from __future__ import annotations

import logging

from .models import Workspace

logger = logging.getLogger(__name__)


class GeoServerSecuritySyncService:
    """Computes and pushes the §5c ACL rule pair for a single Workspace."""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def sync(self) -> bool:
        """Push both ACL rules for the workspace. Returns True iff both succeeded."""
        engine = self.workspace.geodata_engine
        if engine is None or not engine.is_active:
            return False

        try:
            client = engine.get_client()
        except Exception as e:
            logger.error(
                "GeoServer ACL sync: could not create client for workspace '%s': %s",
                self.workspace.name,
                e,
            )
            return False

        success = True
        for key, roles in self._rule_map().items():
            result = client.set_layer_rule(key, roles)
            if not result.success:
                success = False
                logger.error(
                    "GeoServer ACL sync failed for workspace '%s' rule '%s': %s",
                    self.workspace.name,
                    key,
                    result.error or result.message,
                )
        return success

    def _rule_map(self) -> dict[str, str]:
        """The §5c rule pair for this workspace's current organization + visibility."""
        org = self.workspace.organization
        ws_name = self.workspace.name
        is_public = self.workspace.visibility == Workspace.Visibility.PUBLIC
        return {
            f"{ws_name}.*.r": "*" if is_public else org.reader_role,
            f"{ws_name}.*.w": org.writer_role,
        }
