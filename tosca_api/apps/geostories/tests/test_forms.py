"""
Tests for GeoStory forms and admin validation.
"""

from django.test import TestCase

from tosca_api.apps.campaigns.models import Campaign
from django.forms.models import inlineformset_factory
from tosca_api.apps.geostories.admin import GeoStoryLayerFormSet
from tosca_api.apps.geostories.models import GeoStory, GeoStoryLayer
from tosca_api.apps.layerrefs.models import LayerRef
from django.contrib.auth import get_user_model

User = get_user_model()


class GeoStoryFormTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="validuser", password="password")
        self.campaign = Campaign.objects.create(title="Test", created_by=self.user)
        self.story = GeoStory.objects.create(
            title="Story", campaign=self.campaign, author=self.user
        )
        self.layer1 = LayerRef.objects.create(layer_name="layer1")
        self.layer2 = LayerRef.objects.create(layer_name="layer2")

    def test_duplicate_order_validation(self):
        """Test that formset rejects duplicate display_order."""
        FormSetClass = inlineformset_factory(
            GeoStory,
            GeoStoryLayer,
            formset=GeoStoryLayerFormSet,
            fields=("layer", "display_order"),
            extra=0,
        )
        
        data = {
            "geostorylayer_set-TOTAL_FORMS": "2",
            "geostorylayer_set-INITIAL_FORMS": "0",
            "geostorylayer_set-0-layer": self.layer1.id,
            "geostorylayer_set-0-display_order": "0",
            "geostorylayer_set-1-layer": self.layer2.id,
            "geostorylayer_set-1-display_order": "0",  # Duplicate!
        }
    
        formset = FormSetClass(
            data=data, instance=self.story
        )
        
        self.assertFalse(formset.is_valid())
        self.assertIn("Duplicate display order", str(formset.non_form_errors()))

    def test_unique_order_is_valid(self):
        """Test that formset accepts unique display_orders."""
        FormSetClass = inlineformset_factory(
            GeoStory,
            GeoStoryLayer,
            formset=GeoStoryLayerFormSet,
            fields=("layer", "display_order"),
            extra=0,
        )

        data = {
            "geostorylayer_set-TOTAL_FORMS": "2",
            "geostorylayer_set-INITIAL_FORMS": "0",
            "geostorylayer_set-0-layer": self.layer1.id,
            "geostorylayer_set-0-display_order": "0",
            "geostorylayer_set-1-layer": self.layer2.id,
            "geostorylayer_set-1-display_order": "1",  # Unique
        }
        
        formset = FormSetClass(
            data=data, instance=self.story
        )
        
        self.assertTrue(formset.is_valid())
