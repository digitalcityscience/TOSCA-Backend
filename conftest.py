"""Test-only defaults.

Epic-11 ticket 02 made ``Workspace.organization`` / ``Campaign.organization``
required FKs with no model-level default (deliberate -- org must be explicit
in production). Pre-existing tests across the suite create these models
without an organization, so ``save()`` is wrapped here to fill one in with a
seeded default org. This is test infrastructure only; it never runs outside
pytest.

Both models call ``self.full_clean()`` at the *top* of their own ``save()``
override, before ``super().save()`` (and therefore before Django's
``pre_save`` signal) ever runs -- so a `pre_save` receiver would validate
against a still-empty ``organization`` and fail. Wrapping ``save()`` itself
runs before that validation, and covers both ``Model.objects.create(...)``
and ``Model(...).save()`` call styles uniformly.

The default org id is re-resolved on every save rather than cached: each
Django ``TestCase`` wraps its body in its own rolled-back transaction, so an
id cached from an earlier test would dangle. The lookup itself only happens
inside `save()`, so `SimpleTestCase`-based tests (DB access forbidden) never
trigger it.
"""


def _default_organization_id():
    from tosca_api.apps.organizations.models import Organization

    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org.id


def _with_default_organization(model):
    original_save = model.save

    def save(self, *args, **kwargs):
        if not self.organization_id:
            self.organization_id = _default_organization_id()
        return original_save(self, *args, **kwargs)

    model.save = save


def _default_acl_sync_client(self):
    """Test-only default for ``GeodataEngine.get_client()``.

    Epic-11 ticket 09 made ``Workspace.save()`` hard-fail (raises and rolls
    back) when its GeoServer ACL push fails -- deliberate, so a Workspace can
    never exist in Django without a matching enforced ACL. That save happens
    via the `post_save` signal in ``geodata_providers/signals.py``, which
    calls ``GeoServerSecuritySyncService.sync()`` -> ``engine.get_client()``.

    Pre-existing unit tests across the suite create ``Workspace`` fixtures
    with no real GeoServer reachable and no interest in ACL behavior, so this
    patches ``get_client`` to return a mock whose ``set_layer_rule`` always
    succeeds. Tests that care about ACL push behavior (e.g.
    `test_security_sync_service.py`) override this per-test with their own
    ``patch.object(GeodataEngine, 'get_client', ...)``, which wins for the
    duration of that `with` block.

    Note this only affects the signal's ``engine.get_client()`` call --
    ``WorkspaceService``/``StoreService``/etc. call
    ``EngineClientFactory.create_client(engine)`` directly for their own
    catalog-sync client, a separate call site this patch does not touch.
    """
    from unittest.mock import MagicMock

    from tosca_api.apps.geodata_providers.results import OperationResult

    client = MagicMock()
    client.set_layer_rule.return_value = OperationResult(success=True, message="ok (test default)")
    return client


def pytest_configure(config):
    from tosca_api.apps.campaigns.models import Campaign
    from tosca_api.apps.geodata_providers.models import Workspace

    _with_default_organization(Workspace)
    _with_default_organization(Campaign)


def pytest_runtest_setup(item):
    """Swap in the ``get_client`` test default for non-integration tests only.

    `-m integration` tests (epic-11 ticket 10) hit a real GeoServer and need
    ``GeodataEngine.get_client()`` untouched; everything else gets the
    always-succeeds mock from ``_default_acl_sync_client`` for the duration
    of that one test (restored in ``pytest_runtest_teardown`` below).
    """
    from tosca_api.apps.geodata_providers.models import GeodataEngine

    if "integration" in item.keywords:
        return
    item._acl_sync_client_original = GeodataEngine.get_client
    GeodataEngine.get_client = _default_acl_sync_client


def pytest_runtest_teardown(item, nextitem):
    from tosca_api.apps.geodata_providers.models import GeodataEngine

    original = getattr(item, "_acl_sync_client_original", None)
    if original is not None:
        GeodataEngine.get_client = original
