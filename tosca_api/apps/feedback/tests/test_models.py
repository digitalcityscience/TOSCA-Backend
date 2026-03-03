"""
Tests for GeoFeedback and FeedbackLayer models.

Covers:
- Model creation (valid cases)
- clean() validation (every edge case for rating_enabled / form_enabled / custom_form)
- Sanitization (XSS prevention)
- FeedbackLayer through model (ordering, uniqueness, auto-increment)
- Status and visibility choices
- FK / relationship behaviour
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from formbuilder.models import CustomForm
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.feedback.models import FeedbackLayer, GeoFeedback
from tosca_api.apps.layerrefs.models import LayerRef

User = get_user_model()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user():
    return User.objects.create_user(username="feedbackuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Feedback Campaign", created_by=user)


@pytest.fixture
def custom_form():
    return CustomForm.objects.create(
        name="Test Form",
        slug="test-form",
        status=CustomForm.FormStatus.PUBLISHED,
    )


@pytest.fixture
def draft_form():
    return CustomForm.objects.create(
        name="Draft Form",
        slug="draft-form",
        status=CustomForm.FormStatus.DRAFT,
    )


@pytest.fixture
def layer_ref():
    return LayerRef.objects.create(layer_name="workspace:feedback_layer")


@pytest.fixture
def feedback_rating_only(user, campaign):
    """A GeoFeedback with only rating enabled (simplest valid config)."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Rating Feedback",
        rating_enabled=True,
        form_enabled=False,
        created_by=user,
    )


@pytest.fixture
def feedback_form_only(user, campaign, custom_form):
    """A GeoFeedback with only form enabled."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Form Feedback",
        rating_enabled=False,
        form_enabled=True,
        custom_form=custom_form,
        created_by=user,
    )


@pytest.fixture
def feedback_both(user, campaign, custom_form):
    """A GeoFeedback with both rating and form enabled."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Full Feedback",
        rating_enabled=True,
        form_enabled=True,
        custom_form=custom_form,
        created_by=user,
    )


# =============================================================================
# Model Creation Tests
# =============================================================================


@pytest.mark.django_db
class TestGeoFeedbackCreation:
    """Test valid GeoFeedback creation scenarios."""

    def test_create_with_rating_only(self, user, campaign):
        """Rating-only feedback (simplest valid config) should succeed."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Simple Rating",
            rating_enabled=True,
            form_enabled=False,
            created_by=user,
        )
        assert fb.id is not None
        assert fb.rating_enabled is True
        assert fb.form_enabled is False
        assert fb.custom_form is None
        assert fb.allow_drawings is False

    def test_create_with_form_only(self, user, campaign, custom_form):
        """Form-only feedback should succeed when custom_form is provided."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Form Only",
            rating_enabled=False,
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        assert fb.id is not None
        assert fb.rating_enabled is False
        assert fb.form_enabled is True
        assert fb.custom_form == custom_form

    def test_create_with_both_enabled(self, user, campaign, custom_form):
        """Both rating and form enabled should succeed."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Full Feedback",
            rating_enabled=True,
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        assert fb.rating_enabled is True
        assert fb.form_enabled is True
        assert fb.custom_form == custom_form

    def test_create_with_drawings_enabled(self, user, campaign):
        """Feedback with drawings enabled should succeed."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Map Drawing Feedback",
            rating_enabled=True,
            form_enabled=False,
            allow_drawings=True,
            created_by=user,
        )
        assert fb.allow_drawings is True

    def test_default_status_is_draft(self, user, campaign):
        """Default status should be DRAFT."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Status Test",
            created_by=user,
        )
        assert fb.status == GeoFeedback.Status.DRAFT

    def test_default_visibility_is_public(self, user, campaign):
        """Default visibility should be PUBLIC."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Visibility Test",
            created_by=user,
        )
        assert fb.visibility == GeoFeedback.Visibility.PUBLIC

    def test_str_returns_title(self, feedback_rating_only):
        """__str__ should return the title."""
        assert str(feedback_rating_only) == "Rating Feedback"

    def test_uuid_primary_key(self, feedback_rating_only):
        """ID should be a UUID."""
        import uuid

        assert isinstance(feedback_rating_only.id, uuid.UUID)

    def test_timestamps_populated(self, feedback_rating_only):
        """created_at and updated_at should be auto-populated."""
        assert feedback_rating_only.created_at is not None
        assert feedback_rating_only.updated_at is not None

    def test_create_with_description(self, user, campaign):
        """Feedback with description should succeed."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Described Feedback",
            description="This is a detailed description.",
            created_by=user,
        )
        assert fb.description == "This is a detailed description."

    def test_description_defaults_to_empty_string(self, user, campaign):
        """Description should default to empty string."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="No Description",
            created_by=user,
        )
        assert fb.description == ""

    def test_create_with_draft_custom_form(self, user, campaign, draft_form):
        """A draft custom form can still be linked (the form status is independent)."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Draft Form Feedback",
            rating_enabled=False,
            form_enabled=True,
            custom_form=draft_form,
            created_by=user,
        )
        assert fb.custom_form == draft_form


# =============================================================================
# Validation Tests - clean()
# =============================================================================


@pytest.mark.django_db
class TestGeoFeedbackValidation:
    """Test clean() validation edge cases thoroughly."""

    def test_rejects_both_disabled(self, user, campaign):
        """Must reject when BOTH rating_enabled and form_enabled are False."""
        with pytest.raises(ValidationError) as exc_info:
            GeoFeedback.objects.create(
                campaign=campaign,
                title="Invalid Feedback",
                rating_enabled=False,
                form_enabled=False,
                created_by=user,
            )
        errors = exc_info.value.message_dict
        assert "rating_enabled" in errors
        assert "form_enabled" in errors

    def test_rejects_form_enabled_without_custom_form(self, user, campaign):
        """Must reject when form_enabled=True but custom_form is None."""
        with pytest.raises(ValidationError) as exc_info:
            GeoFeedback.objects.create(
                campaign=campaign,
                title="Missing Form",
                rating_enabled=False,
                form_enabled=True,
                custom_form=None,
                created_by=user,
            )
        errors = exc_info.value.message_dict
        assert "custom_form" in errors

    def test_rejects_both_disabled_and_no_form(self, user, campaign):
        """Both disabled AND no form: should report both errors."""
        with pytest.raises(ValidationError) as exc_info:
            GeoFeedback.objects.create(
                campaign=campaign,
                title="Double Invalid",
                rating_enabled=False,
                form_enabled=False,
                custom_form=None,
                created_by=user,
            )
        errors = exc_info.value.message_dict
        # Should have the "at least one" error, but NOT the custom_form error
        # because form_enabled is False
        assert "rating_enabled" in errors
        assert "form_enabled" in errors

    def test_accepts_rating_only_without_form(self, user, campaign):
        """rating_enabled=True, form_enabled=False, no custom_form: valid."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Rating Only OK",
            rating_enabled=True,
            form_enabled=False,
            custom_form=None,
            created_by=user,
        )
        assert fb.id is not None

    def test_accepts_form_enabled_with_custom_form(self, user, campaign, custom_form):
        """form_enabled=True with custom_form set: valid."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Form Valid",
            rating_enabled=False,
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        assert fb.id is not None

    def test_accepts_both_enabled_with_custom_form(self, user, campaign, custom_form):
        """Both enabled with custom_form: valid."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Both Valid",
            rating_enabled=True,
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        assert fb.id is not None

    def test_custom_form_allowed_when_form_disabled(self, user, campaign, custom_form):
        """custom_form can be set even if form_enabled=False (not required but allowed)."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Extra Form OK",
            rating_enabled=True,
            form_enabled=False,
            custom_form=custom_form,
            created_by=user,
        )
        assert fb.custom_form == custom_form

    def test_allow_drawings_independent_of_rating_form(self, user, campaign):
        """allow_drawings does not affect the rating/form validation."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Drawings Only",
            rating_enabled=True,
            form_enabled=False,
            allow_drawings=True,
            created_by=user,
        )
        assert fb.allow_drawings is True

    def test_clean_called_on_save_via_full_clean(self, user, campaign):
        """Verify that clean() is called during save() (enforced by full_clean)."""
        fb = GeoFeedback(
            campaign=campaign,
            title="Test Clean",
            rating_enabled=False,
            form_enabled=False,
            created_by=user,
        )
        with pytest.raises(ValidationError):
            fb.save()

    def test_clean_directly_rejects_invalid_config(self, user, campaign):
        """Calling clean() directly should raise ValidationError."""
        fb = GeoFeedback(
            campaign=campaign,
            title="Direct Clean",
            rating_enabled=False,
            form_enabled=True,
            custom_form=None,
            created_by=user,
        )
        with pytest.raises(ValidationError) as exc_info:
            fb.clean()
        assert "custom_form" in exc_info.value.message_dict

    def test_form_enabled_true_with_form_then_disable_form(
        self, user, campaign, custom_form
    ):
        """Create valid feedback, then update to invalid state should raise."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Toggle Test",
            rating_enabled=True,
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        # Now disable rating and remove form -> both disabled
        fb.rating_enabled = False
        fb.form_enabled = False
        with pytest.raises(ValidationError):
            fb.save()

    def test_update_remove_custom_form_while_form_enabled(
        self, user, campaign, custom_form
    ):
        """Removing custom_form while form_enabled=True should fail."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Remove Form Test",
            rating_enabled=False,
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        fb.custom_form = None
        with pytest.raises(ValidationError) as exc_info:
            fb.save()
        assert "custom_form" in exc_info.value.message_dict


# =============================================================================
# Status & Visibility Choices Tests
# =============================================================================


@pytest.mark.django_db
class TestGeoFeedbackStatusChoices:
    """Test all status and visibility choices."""

    def test_all_status_choices_valid(self, user, campaign):
        """All defined status choices should be saveable."""
        for status in GeoFeedback.Status:
            fb = GeoFeedback.objects.create(
                campaign=campaign,
                title=f"Status {status}",
                status=status,
                created_by=user,
            )
            assert fb.status == status

    def test_all_visibility_choices_valid(self, user, campaign):
        """All defined visibility choices should be saveable."""
        for vis in GeoFeedback.Visibility:
            fb = GeoFeedback.objects.create(
                campaign=campaign,
                title=f"Visibility {vis}",
                visibility=vis,
                created_by=user,
            )
            assert fb.visibility == vis


# =============================================================================
# Sanitization Tests
# =============================================================================


@pytest.mark.django_db
class TestGeoFeedbackSanitization:
    """Test that XSS and HTML injection are stripped on save."""

    def test_sanitizes_title_script_tag(self, user, campaign):
        """Script tags in title must be stripped."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="<script>alert('xss')</script>Feedback",
            created_by=user,
        )
        assert "<script>" not in fb.title
        assert "Feedback" in fb.title

    def test_sanitizes_title_onerror(self, user, campaign):
        """Event handler attributes in title must be stripped."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title='<img onerror="alert(1)">Title',
            created_by=user,
        )
        assert "onerror" not in fb.title
        assert "Title" in fb.title

    def test_sanitizes_description_script_tag(self, user, campaign):
        """Script tags in description must be stripped."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Safe Title",
            description="<script>alert('xss')</script>Safe description",
            created_by=user,
        )
        assert "<script>" not in fb.description
        assert "Safe description" in fb.description

    def test_sanitizes_description_html_tags(self, user, campaign):
        """HTML tags in description must be stripped (simple sanitization)."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Title",
            description="<b>Bold</b> and <i>italic</i>",
            created_by=user,
        )
        assert "<b>" not in fb.description
        assert "<i>" not in fb.description
        assert "Bold" in fb.description
        assert "italic" in fb.description

    def test_sanitizes_javascript_url_in_title(self, user, campaign):
        """JavaScript URLs in title must be stripped."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title='<a href="javascript:alert(1)">Click</a>',
            created_by=user,
        )
        assert "javascript:" not in fb.title

    def test_preserves_plain_text(self, user, campaign):
        """Plain text content should pass through unchanged."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Normal title with no HTML",
            description="Normal description with special chars: <> & 'quotes'",
            created_by=user,
        )
        assert fb.title == "Normal title with no HTML"

    def test_sanitizes_empty_title_ok(self, user, campaign):
        """Empty string sanitization should not error."""
        # Title is CharField so Django will enforce max_length but not blankness at DB
        # However, the model allows blank=False by default which means validation
        # will catch it. Let's test sanitization doesn't break on minimal input.
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="x",
            description="",
            created_by=user,
        )
        assert fb.description == ""


# =============================================================================
# FeedbackLayer Tests
# =============================================================================


@pytest.mark.django_db
class TestFeedbackLayer:
    """Test the FeedbackLayer through model."""

    def test_create_feedback_layer(self, feedback_rating_only, layer_ref):
        """Create a feedback layer link."""
        fl = FeedbackLayer.objects.create(
            feedback=feedback_rating_only,
            layer=layer_ref,
            display_order=1,
        )
        assert fl.feedback == feedback_rating_only
        assert fl.layer == layer_ref
        assert fl.display_order == 1
        assert feedback_rating_only.layers.count() == 1

    def test_feedback_layer_str(self, feedback_rating_only, layer_ref):
        """__str__ should show feedback, layer, and order."""
        fl = FeedbackLayer.objects.create(
            feedback=feedback_rating_only,
            layer=layer_ref,
            display_order=1,
        )
        result = str(fl)
        assert "Rating Feedback" in result
        assert "workspace:feedback_layer" in result
        assert "1" in result

    def test_feedback_layer_auto_increment_order(self, feedback_rating_only):
        """Display_order should auto-increment when left at default 0."""
        layer1 = LayerRef.objects.create(layer_name="workspace:layer_a")
        layer2 = LayerRef.objects.create(layer_name="workspace:layer_b")

        fl1 = FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer1
        )
        assert fl1.display_order == 0  # First layer keeps 0

        fl2 = FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer2
        )
        assert fl2.display_order == 1  # Second auto-increments to 1

    def test_feedback_layer_auto_increment_third(self, feedback_rating_only):
        """Third layer should auto-increment to 2."""
        layer1 = LayerRef.objects.create(layer_name="workspace:l1")
        layer2 = LayerRef.objects.create(layer_name="workspace:l2")
        layer3 = LayerRef.objects.create(layer_name="workspace:l3")

        FeedbackLayer.objects.create(feedback=feedback_rating_only, layer=layer1)
        FeedbackLayer.objects.create(feedback=feedback_rating_only, layer=layer2)
        fl3 = FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer3
        )
        assert fl3.display_order == 2

    def test_feedback_layer_explicit_order_preserved(self, feedback_rating_only, layer_ref):
        """Explicit display_order should be preserved (not overwritten)."""
        fl = FeedbackLayer.objects.create(
            feedback=feedback_rating_only,
            layer=layer_ref,
            display_order=42,
        )
        assert fl.display_order == 42

    def test_feedback_layer_unique_together(self, feedback_rating_only, layer_ref):
        """Duplicate feedback-layer pairs must be rejected."""
        FeedbackLayer.objects.create(
            feedback=feedback_rating_only,
            layer=layer_ref,
        )
        with pytest.raises(IntegrityError):
            FeedbackLayer.objects.create(
                feedback=feedback_rating_only,
                layer=layer_ref,
            )

    def test_multiple_feedbacks_same_layer(self, user, campaign, layer_ref):
        """Same layer can be linked to different feedbacks."""
        fb1 = GeoFeedback.objects.create(
            campaign=campaign,
            title="Feedback 1",
            created_by=user,
        )
        fb2 = GeoFeedback.objects.create(
            campaign=campaign,
            title="Feedback 2",
            created_by=user,
        )
        FeedbackLayer.objects.create(feedback=fb1, layer=layer_ref)
        FeedbackLayer.objects.create(feedback=fb2, layer=layer_ref)

        assert fb1.layers.count() == 1
        assert fb2.layers.count() == 1

    def test_feedback_layer_ordering(self, feedback_rating_only):
        """Layers should be ordered by display_order, then created_at."""
        layer_a = LayerRef.objects.create(layer_name="workspace:ordered_a")
        layer_b = LayerRef.objects.create(layer_name="workspace:ordered_b")
        layer_c = LayerRef.objects.create(layer_name="workspace:ordered_c")

        FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer_c, display_order=3
        )
        FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer_a, display_order=1
        )
        FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer_b, display_order=2
        )

        ordered = list(
            FeedbackLayer.objects.filter(
                feedback=feedback_rating_only
            ).values_list("layer__layer_name", flat=True)
        )
        assert ordered == [
            "workspace:ordered_a",
            "workspace:ordered_b",
            "workspace:ordered_c",
        ]

    def test_feedback_layer_cascade_delete(self, feedback_rating_only, layer_ref):
        """Deleting feedback should cascade-delete its layers."""
        FeedbackLayer.objects.create(
            feedback=feedback_rating_only, layer=layer_ref
        )
        feedback_id = feedback_rating_only.id
        feedback_rating_only.delete()

        assert FeedbackLayer.objects.filter(feedback_id=feedback_id).count() == 0


# =============================================================================
# Relationship Tests
# =============================================================================


@pytest.mark.django_db
class TestGeoFeedbackRelationships:
    """Test FK and relationship behavior."""

    def test_campaign_fk_cascade(self, user, campaign):
        """Deleting campaign should cascade-delete its feedbacks."""
        GeoFeedback.objects.create(
            campaign=campaign, title="Cascade test", created_by=user
        )
        campaign_id = campaign.id
        campaign.delete()
        assert GeoFeedback.objects.filter(campaign_id=campaign_id).count() == 0

    def test_created_by_protect(self, user, campaign):
        """Deleting a user who created feedback should be PROTECTED."""
        from django.db.models import ProtectedError

        GeoFeedback.objects.create(
            campaign=campaign, title="Protect test", created_by=user
        )
        with pytest.raises(ProtectedError):
            user.delete()

    def test_custom_form_set_null_on_delete(self, user, campaign, custom_form):
        """Deleting a CustomForm should set FK to null (not cascade)."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Form Delete Test",
            rating_enabled=True,
            form_enabled=False,
            custom_form=custom_form,
            created_by=user,
        )
        custom_form.delete()
        fb.refresh_from_db()
        assert fb.custom_form is None

    def test_context_set_null_on_delete(self, user, campaign):
        """Deleting a GeoContext should set FK to null (not cascade)."""
        from tosca_api.apps.geocontext.models import GeoContext

        ctx = GeoContext.objects.create(content="Test content", created_by=user)
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Context Delete Test",
            context=ctx,
            created_by=user,
        )
        ctx.delete()
        fb.refresh_from_db()
        assert fb.context is None

    def test_reverse_relation_campaign_feedbacks(self, user, campaign):
        """Campaign.feedbacks reverse relation should work."""
        GeoFeedback.objects.create(
            campaign=campaign, title="FB 1", created_by=user
        )
        GeoFeedback.objects.create(
            campaign=campaign, title="FB 2", created_by=user
        )
        assert campaign.feedbacks.count() == 2

    def test_reverse_relation_custom_form_feedbacks(self, user, campaign, custom_form):
        """CustomForm.feedbacks reverse relation should work."""
        GeoFeedback.objects.create(
            campaign=campaign,
            title="FB with form 1",
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        GeoFeedback.objects.create(
            campaign=campaign,
            title="FB with form 2",
            form_enabled=True,
            custom_form=custom_form,
            created_by=user,
        )
        assert custom_form.feedbacks.count() == 2
