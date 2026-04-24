from django.test import SimpleTestCase

from tosca_api.apps.geodata_providers.services.commands.style_validation_service import (
    StyleValidationService,
)


class StyleValidationServiceTestCase(SimpleTestCase):
    def test_validate_mbstyle_accepts_minimal_style_without_sources(self):
        result = StyleValidationService.validate_mbstyle(
            content="""
            {
              "version": 8,
              "name": "point-circle-test",
              "layers": [
                {
                  "id": "point",
                  "type": "circle",
                  "paint": {
                    "circle-radius": 3,
                    "circle-color": "#FF0000",
                    "circle-pitch-scale": "map"
                  }
                }
              ]
            }
            """
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_validate_mbstyle_rejects_invalid_sources_type_when_provided(self):
        result = StyleValidationService.validate_mbstyle(
            content='{"version":8,"sources":[],"layers":[{"id":"point","type":"circle"}]}'
        )

        self.assertFalse(result["valid"])
        self.assertEqual(
            result["errors"],
            ["MBStyle sources must be an object when provided."],
        )
