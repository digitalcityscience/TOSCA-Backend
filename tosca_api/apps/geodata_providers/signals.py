"""
Workspace `post_save` -> GeoServer ACL sync (epic-11 ticket 08, canonical §5c/§11).

Fires synchronously on create, and on update only when a field the ACL rules
actually depend on (`organization`, `visibility`) changed -- an unrelated
Workspace edit (e.g. `description`) must not trigger a GeoServer round-trip.
Wired up in `GeodataProvidersConfig.ready()`.
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Workspace
from .security_sync import GeoServerSecuritySyncService

_PRIOR_STATE_ATTR = "_security_sync_prior_state"


@receiver(pre_save, sender=Workspace)
def _capture_workspace_prior_state(sender, instance, **kwargs):
    try:
        prior = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        setattr(instance, _PRIOR_STATE_ATTR, None)
    else:
        setattr(instance, _PRIOR_STATE_ATTR, (prior.organization_id, prior.visibility))


@receiver(post_save, sender=Workspace)
def _sync_workspace_acl(sender, instance, created, **kwargs):
    prior_state = getattr(instance, _PRIOR_STATE_ATTR, None)
    current_state = (instance.organization_id, instance.visibility)
    if not created and prior_state == current_state:
        return
    GeoServerSecuritySyncService(instance).sync()
