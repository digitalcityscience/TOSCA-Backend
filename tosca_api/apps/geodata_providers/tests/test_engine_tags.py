"""
Regression tests for issue 22: templatetags/__init__.py and engine_tags.py
contained byte-for-byte duplicated tag/filter definitions. __init__.py is
not discoverable via {% load %} (Django's tag-library autodiscovery skips
package __init__ modules), so it was dead code — emptied, keeping the
implementation in engine_tags.py.
"""
from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from tosca_api.apps.geodata_providers import templatetags
from tosca_api.apps.geodata_providers.models import GeodataEngine
from tosca_api.apps.geodata_providers.templatetags import engine_tags


class TemplateTagModuleDeduplicationTests(TestCase):
    def test_init_module_has_no_tag_library(self):
        self.assertFalse(hasattr(templatetags, 'register'))

    def test_engine_tags_module_still_has_the_tag_library(self):
        self.assertTrue(hasattr(engine_tags, 'register'))


class EngineTagsRenderingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='engine-tags-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Tag Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            is_active=True,
            is_default=True,
            created_by=self.user,
        )
        self.factory = RequestFactory()

    def test_load_engine_tags_and_render_selector(self):
        request = self.factory.get('/admin/')
        request.session = {}
        template = Template(
            "{% load engine_tags %}{% engine_selector %}"
        )
        rendered = template.render(Context({'request': request}))

        self.assertIn(self.engine.name, rendered)

    def test_engine_switch_url_appends_query_param(self):
        request = self.factory.get('/admin/geodata_providers/geodataengine/')
        template = Template(
            "{% load engine_tags %}{% engine_switch_url engine.id %}"
        )
        rendered = template.render(Context({'request': request, 'engine': self.engine}))

        self.assertEqual(rendered, f"/admin/geodata_providers/geodataengine/?switch_engine={self.engine.id}")

    def test_engine_status_icon_filter(self):
        template = Template("{% load engine_tags %}{{ engine|engine_status_icon }}")
        rendered = template.render(Context({'engine': self.engine}))

        self.assertEqual(rendered, "🎯")
