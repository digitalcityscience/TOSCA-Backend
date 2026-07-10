"""
GeodataEngine.save() atomically unsets any other default engine before
saving itself as the new default. This covers the regression this fix
targets: a failure between the unset and the save must not leave zero
default engines.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import models as django_models
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine


def _make_engine(name, user, is_default=False):
    return GeodataEngine.objects.create(
        name=name,
        description="test",
        engine_type="geoserver",
        base_url="http://example.com/geoserver",
        public_url="http://example.com/geoserver",
        admin_username="admin",
        admin_password="secret",
        is_default=is_default,
        created_by=user,
    )


class DefaultEngineAtomicityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="atomic-user", password="testpass123")

    def test_setting_a_new_default_unsets_the_old_one(self):
        engine_a = _make_engine("Engine A", self.user, is_default=True)
        engine_b = _make_engine("Engine B", self.user, is_default=False)

        engine_b.is_default = True
        engine_b.save(update_fields=["is_default"])

        engine_a.refresh_from_db()
        engine_b.refresh_from_db()
        assert engine_a.is_default is False
        assert engine_b.is_default is True

    def test_failure_saving_new_default_leaves_old_default_untouched(self):
        """Regression test: a failure between the unset-others update and
        the final save() must roll back the unset too, not just skip the
        final save. Before this fix, those two writes were not in the same
        transaction, so a failure here could leave zero default engines.
        """
        engine_a = _make_engine("Engine A", self.user, is_default=True)
        engine_b = _make_engine("Engine B", self.user, is_default=False)

        real_save = django_models.Model.save

        def failing_save(self, *args, **kwargs):
            if isinstance(self, GeodataEngine) and self.pk == engine_b.pk:
                raise RuntimeError("simulated failure mid-save")
            return real_save(self, *args, **kwargs)

        engine_b.is_default = True
        with patch.object(django_models.Model, "save", failing_save):
            with self.assertRaises(RuntimeError):
                engine_b.save(update_fields=["is_default"])

        engine_a.refresh_from_db()
        engine_b.refresh_from_db()
        # The unset-others update must have rolled back along with the
        # failed save — engine_a is still the (only) default.
        assert engine_a.is_default is True
        assert engine_b.is_default is False
