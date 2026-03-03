"""
Tests for FeedbackSubmission model.

Covers:
- Model creation (valid cases: with/without user, with/without rating, etc.)
- Rating validation (1-5 bounds, null allowed)
- form_data JSONB field (arbitrary dicts, nested objects, null)
- Geometry field (Point, LineString, Polygon, null)
- is_anonymized flag
- clean() validation (geometry rejected when allow_drawings=False)
- FK / relationship behaviour (cascade, SET_NULL)
- __str__ representation
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import GEOSGeometry, LineString, Point, Polygon
from django.core.exceptions import ValidationError

from formbuilder.models import CustomForm
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.feedback.models import FeedbackSubmission, GeoFeedback

User = get_user_model()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user():
    return User.objects.create_user(username="submitter", password="password")


@pytest.fixture
def other_user():
    return User.objects.create_user(username="other_user", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Submission Campaign", created_by=user)


@pytest.fixture
def custom_form():
    return CustomForm.objects.create(
        name="Submission Form",
        slug="submission-form",
        status=CustomForm.FormStatus.PUBLISHED,
    )


@pytest.fixture
def feedback_rating_only(user, campaign):
    """GeoFeedback with only rating enabled."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Rate Us",
        rating_enabled=True,
        form_enabled=False,
        allow_drawings=False,
        created_by=user,
    )


@pytest.fixture
def feedback_form_only(user, campaign, custom_form):
    """GeoFeedback with only form enabled."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Survey",
        rating_enabled=False,
        form_enabled=True,
        custom_form=custom_form,
        allow_drawings=False,
        created_by=user,
    )


@pytest.fixture
def feedback_with_drawings(user, campaign):
    """GeoFeedback with drawings enabled."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Map Feedback",
        rating_enabled=True,
        form_enabled=False,
        allow_drawings=True,
        created_by=user,
    )


@pytest.fixture
def feedback_all_enabled(user, campaign, custom_form):
    """GeoFeedback with everything enabled."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Full Feedback",
        rating_enabled=True,
        form_enabled=True,
        custom_form=custom_form,
        allow_drawings=True,
        created_by=user,
    )


# =============================================================================
# Model Creation Tests
# =============================================================================


@pytest.mark.django_db
class TestFeedbackSubmissionCreation:
    """Test valid FeedbackSubmission creation scenarios."""

    def test_create_with_rating_only(self, feedback_rating_only, user):
        """Submission with just a rating."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        assert sub.id is not None
        assert sub.rating == 5
        assert sub.form_data is None
        assert sub.geometry is None
        assert sub.is_anonymized is False

    def test_create_with_form_data_only(self, feedback_form_only, user):
        """Submission with just form data."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data={"comment": "Great project!"},
        )
        assert sub.form_data == {"comment": "Great project!"}
        assert sub.rating is None

    def test_create_with_rating_and_form_data(self, feedback_all_enabled, user):
        """Submission with both rating and form data."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_all_enabled,
            submitted_by=user,
            rating=4,
            form_data={"comment": "Good", "satisfaction": "high"},
        )
        assert sub.rating == 4
        assert sub.form_data["comment"] == "Good"
        assert sub.form_data["satisfaction"] == "high"

    def test_create_anonymous_submission(self, feedback_rating_only):
        """Submission without a user (anonymous)."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=None,
            rating=3,
        )
        assert sub.submitted_by is None
        assert sub.rating == 3

    def test_create_with_geometry_point(self, feedback_with_drawings, user):
        """Submission with a Point geometry."""
        point = Point(9.993682, 53.551086, srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=4,
            geometry=point,
        )
        assert sub.geometry is not None
        assert sub.geometry.geom_type == "Point"
        assert sub.geometry.srid == 4326

    def test_create_with_geometry_linestring(self, feedback_with_drawings, user):
        """Submission with a LineString geometry."""
        line = LineString(
            (9.99, 53.55), (10.00, 53.56), (10.01, 53.57), srid=4326
        )
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=3,
            geometry=line,
        )
        assert sub.geometry.geom_type == "LineString"

    def test_create_with_geometry_polygon(self, feedback_with_drawings, user):
        """Submission with a Polygon geometry."""
        polygon = Polygon(
            ((9.99, 53.55), (10.01, 53.55), (10.01, 53.57), (9.99, 53.57), (9.99, 53.55)),
            srid=4326,
        )
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=2,
            geometry=polygon,
        )
        assert sub.geometry.geom_type == "Polygon"

    def test_create_with_is_anonymized(self, feedback_rating_only, user):
        """Submission with is_anonymized flag set."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
            is_anonymized=True,
        )
        assert sub.is_anonymized is True

    def test_uuid_primary_key(self, feedback_rating_only, user):
        """ID should be a UUID."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        assert isinstance(sub.id, uuid.UUID)

    def test_timestamps_populated(self, feedback_rating_only, user):
        """created_at and updated_at should be auto-populated."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        assert sub.created_at is not None
        assert sub.updated_at is not None

    def test_default_is_anonymized_false(self, feedback_rating_only, user):
        """is_anonymized should default to False."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        assert sub.is_anonymized is False


# =============================================================================
# Rating Validation Tests
# =============================================================================


@pytest.mark.django_db
class TestRatingValidation:
    """Test rating field validation (1-5 bounds)."""

    def test_rating_min_value_1(self, feedback_rating_only, user):
        """Rating of 1 is the minimum valid value."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=1,
        )
        assert sub.rating == 1

    def test_rating_max_value_5(self, feedback_rating_only, user):
        """Rating of 5 is the maximum valid value."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        assert sub.rating == 5

    def test_rating_mid_values(self, feedback_rating_only, user):
        """All intermediate ratings (2, 3, 4) should be valid."""
        for value in [2, 3, 4]:
            sub = FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=value,
            )
            assert sub.rating == value

    def test_rating_null_allowed(self, feedback_rating_only, user):
        """Null rating should be allowed (field is nullable)."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=None,
        )
        assert sub.rating is None

    def test_rating_zero_rejected(self, feedback_rating_only, user):
        """Rating of 0 should be rejected (MinValueValidator(1))."""
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=0,
            )
        assert "rating" in exc_info.value.message_dict

    def test_rating_six_rejected(self, feedback_rating_only, user):
        """Rating of 6 should be rejected (MaxValueValidator(5))."""
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=6,
            )
        assert "rating" in exc_info.value.message_dict

    def test_rating_negative_rejected(self, feedback_rating_only, user):
        """Negative ratings should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=-1,
            )
        assert "rating" in exc_info.value.message_dict

    def test_rating_very_large_rejected(self, feedback_rating_only, user):
        """Very large ratings should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=100,
            )
        assert "rating" in exc_info.value.message_dict


# =============================================================================
# Form Data (JSONB) Tests
# =============================================================================


@pytest.mark.django_db
class TestFormDataJSON:
    """Test the form_data JSONB field with various data shapes."""

    def test_simple_dict(self, feedback_form_only, user):
        """Simple key-value dict should be stored correctly."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data={"name": "Alice", "email": "alice@example.com"},
        )
        sub.refresh_from_db()
        assert sub.form_data["name"] == "Alice"
        assert sub.form_data["email"] == "alice@example.com"

    def test_nested_dict(self, feedback_form_only, user):
        """Nested dicts should be stored correctly."""
        data = {
            "address": {
                "street": "Main St 42",
                "city": "Hamburg",
                "zip": "20095",
            },
            "rating": 4,
        }
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data=data,
        )
        sub.refresh_from_db()
        assert sub.form_data["address"]["city"] == "Hamburg"

    def test_list_values(self, feedback_form_only, user):
        """List values in form_data should be stored correctly."""
        data = {"selected_options": ["option_a", "option_b", "option_c"]}
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data=data,
        )
        sub.refresh_from_db()
        assert len(sub.form_data["selected_options"]) == 3

    def test_mixed_types(self, feedback_form_only, user):
        """Mixed type values (str, int, bool, null, list) in JSON."""
        data = {
            "text_field": "hello",
            "number_field": 42,
            "bool_field": True,
            "null_field": None,
            "list_field": [1, 2, 3],
        }
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data=data,
        )
        sub.refresh_from_db()
        assert sub.form_data["text_field"] == "hello"
        assert sub.form_data["number_field"] == 42
        assert sub.form_data["bool_field"] is True
        assert sub.form_data["null_field"] is None
        assert sub.form_data["list_field"] == [1, 2, 3]

    def test_empty_dict(self, feedback_form_only, user):
        """Empty dict should be stored correctly."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data={},
        )
        sub.refresh_from_db()
        assert sub.form_data == {}

    def test_null_form_data(self, feedback_rating_only, user):
        """Null form_data should be allowed."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
            form_data=None,
        )
        assert sub.form_data is None

    def test_default_form_data_is_none(self, feedback_rating_only, user):
        """Default form_data should be None (not empty dict)."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        assert sub.form_data is None

    def test_unicode_in_form_data(self, feedback_form_only, user):
        """Unicode characters should be stored correctly in JSONB."""
        data = {"comment": "Ünîcödé tëst 🎉 日本語"}
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data=data,
        )
        sub.refresh_from_db()
        assert sub.form_data["comment"] == "Ünîcödé tëst 🎉 日本語"

    def test_large_form_data(self, feedback_form_only, user):
        """Reasonably large JSON should be accepted."""
        data = {f"field_{i}": f"value_{i}" for i in range(100)}
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data=data,
        )
        sub.refresh_from_db()
        assert len(sub.form_data) == 100


# =============================================================================
# Geometry Tests
# =============================================================================


@pytest.mark.django_db
class TestGeometryField:
    """Test the GeometryField with various geometry types."""

    def test_point_geometry(self, feedback_with_drawings, user):
        """Point geometry should be stored and retrievable."""
        point = Point(9.993682, 53.551086, srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=4,
            geometry=point,
        )
        sub.refresh_from_db()
        assert sub.geometry.geom_type == "Point"
        assert sub.geometry.x == pytest.approx(9.993682, rel=1e-5)
        assert sub.geometry.y == pytest.approx(53.551086, rel=1e-5)

    def test_linestring_geometry(self, feedback_with_drawings, user):
        """LineString geometry for path drawings."""
        line = LineString(
            (9.99, 53.55), (10.00, 53.56), (10.01, 53.57), srid=4326
        )
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=3,
            geometry=line,
        )
        sub.refresh_from_db()
        assert sub.geometry.geom_type == "LineString"
        assert sub.geometry.num_points == 3

    def test_polygon_geometry(self, feedback_with_drawings, user):
        """Polygon geometry for area drawings."""
        polygon = Polygon(
            ((9.99, 53.55), (10.01, 53.55), (10.01, 53.57), (9.99, 53.57), (9.99, 53.55)),
            srid=4326,
        )
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=2,
            geometry=polygon,
        )
        sub.refresh_from_db()
        assert sub.geometry.geom_type == "Polygon"

    def test_null_geometry_allowed(self, feedback_rating_only, user):
        """Null geometry should be allowed."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
            geometry=None,
        )
        assert sub.geometry is None

    def test_geometry_srid_4326(self, feedback_with_drawings, user):
        """Geometry SRID should be 4326 (WGS84)."""
        point = Point(10.0, 53.5, srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=4,
            geometry=point,
        )
        sub.refresh_from_db()
        assert sub.geometry.srid == 4326

    def test_geometry_from_wkt(self, feedback_with_drawings, user):
        """Geometry created from WKT string should work."""
        geom = GEOSGeometry("POINT(9.993682 53.551086)", srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=4,
            geometry=geom,
        )
        sub.refresh_from_db()
        assert sub.geometry.geom_type == "Point"

    def test_geometry_from_geojson(self, feedback_with_drawings, user):
        """Geometry created from GeoJSON string should work."""
        geojson = '{"type": "Point", "coordinates": [9.993682, 53.551086]}'
        geom = GEOSGeometry(geojson, srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=4,
            geometry=geom,
        )
        sub.refresh_from_db()
        assert sub.geometry.geom_type == "Point"


# =============================================================================
# Validation Tests - clean()
# =============================================================================


@pytest.mark.django_db
class TestFeedbackSubmissionValidation:
    """Test clean() validation for submission constraints."""

    def test_geometry_rejected_when_drawings_disabled(
        self, feedback_rating_only, user
    ):
        """Geometry should be rejected when feedback.allow_drawings is False."""
        point = Point(10.0, 53.5, srid=4326)
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=5,
                geometry=point,
            )
        assert "geometry" in exc_info.value.message_dict

    def test_geometry_accepted_when_drawings_enabled(
        self, feedback_with_drawings, user
    ):
        """Geometry should be accepted when feedback.allow_drawings is True."""
        point = Point(10.0, 53.5, srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=5,
            geometry=point,
        )
        assert sub.geometry is not None

    def test_null_geometry_ok_when_drawings_disabled(
        self, feedback_rating_only, user
    ):
        """Null geometry should be fine even when drawings are disabled."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
            geometry=None,
        )
        assert sub.geometry is None

    def test_null_geometry_ok_when_drawings_enabled(
        self, feedback_with_drawings, user
    ):
        """Null geometry should be fine when drawings are enabled (optional)."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_with_drawings,
            submitted_by=user,
            rating=5,
            geometry=None,
        )
        assert sub.geometry is None

    def test_clean_called_on_save(self, feedback_rating_only, user):
        """Verify clean() is invoked via save()."""
        point = Point(10.0, 53.5, srid=4326)
        sub = FeedbackSubmission(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
            geometry=point,
        )
        with pytest.raises(ValidationError):
            sub.save()

    def test_clean_directly(self, feedback_rating_only, user):
        """Calling clean() directly should validate."""
        point = Point(10.0, 53.5, srid=4326)
        sub = FeedbackSubmission(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
            geometry=point,
        )
        with pytest.raises(ValidationError) as exc_info:
            sub.clean()
        assert "geometry" in exc_info.value.message_dict

    def test_rating_out_of_range_in_clean(self, feedback_rating_only, user):
        """Clean should catch out-of-range ratings."""
        sub = FeedbackSubmission(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=10,
        )
        with pytest.raises(ValidationError) as exc_info:
            sub.clean()
        assert "rating" in exc_info.value.message_dict

    def test_polygon_geometry_rejected_when_drawings_disabled(
        self, feedback_rating_only, user
    ):
        """Polygon geometry rejected when allow_drawings=False."""
        polygon = Polygon(
            ((9.99, 53.55), (10.01, 53.55), (10.01, 53.57), (9.99, 53.57), (9.99, 53.55)),
            srid=4326,
        )
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=5,
                geometry=polygon,
            )
        assert "geometry" in exc_info.value.message_dict

    def test_linestring_geometry_rejected_when_drawings_disabled(
        self, feedback_rating_only, user
    ):
        """LineString geometry rejected when allow_drawings=False."""
        line = LineString(
            (9.99, 53.55), (10.00, 53.56), srid=4326
        )
        with pytest.raises(ValidationError) as exc_info:
            FeedbackSubmission.objects.create(
                feedback=feedback_rating_only,
                submitted_by=user,
                rating=5,
                geometry=line,
            )
        assert "geometry" in exc_info.value.message_dict


# =============================================================================
# __str__ Tests
# =============================================================================


@pytest.mark.django_db
class TestFeedbackSubmissionStr:
    """Test __str__ representation."""

    def test_str_with_user_and_rating(self, feedback_rating_only, user):
        """String should show user and rating."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=5,
        )
        result = str(sub)
        assert "submitter" in result
        assert "★5" in result

    def test_str_anonymous(self, feedback_rating_only):
        """String should show Anonymous for null user."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=None,
            rating=3,
        )
        result = str(sub)
        assert "Anonymous" in result

    def test_str_no_rating(self, feedback_form_only, user):
        """String should show 'no rating' when rating is null."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_form_only,
            submitted_by=user,
            form_data={"key": "val"},
        )
        result = str(sub)
        assert "no rating" in result

    def test_str_contains_feedback_title(self, feedback_rating_only, user):
        """String should include parent feedback title."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=user,
            rating=4,
        )
        result = str(sub)
        assert "Rate Us" in result


# =============================================================================
# Relationship Tests
# =============================================================================


@pytest.mark.django_db
class TestFeedbackSubmissionRelationships:
    """Test FK and relationship behavior."""

    def test_feedback_fk_cascade(self, feedback_rating_only, user):
        """Deleting GeoFeedback should cascade-delete submissions."""
        FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=user, rating=5
        )
        feedback_id = feedback_rating_only.id
        feedback_rating_only.delete()
        assert FeedbackSubmission.objects.filter(feedback_id=feedback_id).count() == 0

    def test_submitted_by_set_null_on_delete(self, feedback_rating_only, other_user):
        """Deleting user should SET_NULL on submitted_by."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=other_user, rating=4
        )
        other_user.delete()
        sub.refresh_from_db()
        assert sub.submitted_by is None

    def test_reverse_relation_submissions(self, feedback_rating_only, user):
        """GeoFeedback.submissions reverse relation should work."""
        FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=user, rating=5
        )
        FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=user, rating=3
        )
        assert feedback_rating_only.submissions.count() == 2

    def test_user_feedback_submissions_relation(self, feedback_rating_only, user):
        """User.feedback_submissions reverse relation should work."""
        FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=user, rating=5
        )
        assert user.feedback_submissions.count() == 1

    def test_multiple_submissions_same_feedback(self, feedback_rating_only, user):
        """Multiple submissions to the same feedback should be allowed."""
        sub1 = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=user, rating=5
        )
        sub2 = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only, submitted_by=user, rating=3
        )
        assert sub1.id != sub2.id
        assert feedback_rating_only.submissions.count() == 2

    def test_campaign_cascade_deletes_submissions(self, user, campaign):
        """Deleting campaign should cascade through feedback to submissions."""
        fb = GeoFeedback.objects.create(
            campaign=campaign,
            title="Cascade chain",
            created_by=user,
        )
        FeedbackSubmission.objects.create(
            feedback=fb, submitted_by=user, rating=5
        )
        campaign.delete()
        assert FeedbackSubmission.objects.count() == 0

    def test_anonymous_submission_without_user(self, feedback_rating_only):
        """Anonymous submission (no user) should persist correctly."""
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_rating_only,
            submitted_by=None,
            rating=4,
        )
        sub.refresh_from_db()
        assert sub.submitted_by is None

    def test_submission_with_all_fields(self, feedback_all_enabled, user):
        """Submission with all fields populated should work."""
        point = Point(10.0, 53.5, srid=4326)
        sub = FeedbackSubmission.objects.create(
            feedback=feedback_all_enabled,
            submitted_by=user,
            rating=5,
            form_data={"q1": "answer1", "q2": 42},
            geometry=point,
            is_anonymized=False,
        )
        sub.refresh_from_db()
        assert sub.rating == 5
        assert sub.form_data["q1"] == "answer1"
        assert sub.geometry.geom_type == "Point"
        assert sub.is_anonymized is False
