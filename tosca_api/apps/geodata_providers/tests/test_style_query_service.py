from django.test import SimpleTestCase

from tosca_api.apps.geodata_providers.services.queries import StyleQueryService


class StyleQueryServiceTestCase(SimpleTestCase):
    def test_list_styles_returns_empty_placeholder_result(self):
        self.assertEqual(StyleQueryService.list_styles(), [])

    def test_get_style_detail_raises_clear_placeholder_error(self):
        with self.assertRaisesMessage(NotImplementedError, "style domain is not defined"):
            StyleQueryService.get_style_detail(style_id="placeholder-style")
