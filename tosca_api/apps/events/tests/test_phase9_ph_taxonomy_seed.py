import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import (
    Event,
    EventTerm,
    EventType,
    TaxonomyDimension,
    TaxonomyTerm,
)

User = get_user_model()

PH_DIMENSION_CODES = {
    "field_of_action",
    "format_ph",
    "organization_type",
    "age_group",
    "audience_spec",
    "cost_category",
    "accessibility",
    "further_info",
}

EXPECTED_TERM_COUNTS = {
    "field_of_action": 14,
    "format_ph": 15,
    "organization_type": 47,
    "age_group": 7,
    "audience_spec": 23,
    "cost_category": 9,
    "accessibility": 13,
    "further_info": 9,
}


@pytest.fixture
def user():
    return User.objects.create_user(username="phase9", password="pw")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Phase 9", created_by=user)


def _make_event(user, campaign, event_type):
    return Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Phase 9 event",
        summary="Phase 9 summary",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )


@pytest.mark.django_db
def test_ph_taxonomy_dimensions_are_seeded_with_english_labels():
    dimensions = {
        dimension.code: dimension
        for dimension in TaxonomyDimension.objects.filter(code__in=PH_DIMENSION_CODES)
    }

    assert set(dimensions) == PH_DIMENSION_CODES
    assert dimensions["field_of_action"].label == "Field of Action"
    assert dimensions["format_ph"].label == "Offer Type"
    assert dimensions["organization_type"].label == "Organization Type"
    assert dimensions["cost_category"].label == "Cost and Funding"

    for dimension in dimensions.values():
        assert dimension.profile_key == "public_health"
        assert dimension.is_active is True

    assert dimensions["format_ph"].selection_mode == TaxonomyDimension.SelectionMode.SINGLE
    assert dimensions["organization_type"].selection_mode == TaxonomyDimension.SelectionMode.SINGLE
    assert dimensions["field_of_action"].selection_mode == TaxonomyDimension.SelectionMode.MULTIPLE


@pytest.mark.django_db
def test_ph_taxonomy_terms_are_seeded_with_expected_counts_and_hierarchy():
    for code, expected_count in EXPECTED_TERM_COUNTS.items():
        assert TaxonomyTerm.objects.filter(dimension__code=code).count() == expected_count

    organization_type = TaxonomyDimension.objects.get(code="organization_type")
    school = TaxonomyTerm.objects.get(dimension=organization_type, code="grundschule")
    health_center = TaxonomyTerm.objects.get(dimension=organization_type, code="gesundheitszentrum")
    welfare = TaxonomyTerm.objects.get(dimension=organization_type, code="wohlfahrtsverband")

    assert school.label == "Primary School"
    assert school.parent.code == "bildungseinrichtung"
    assert health_center.label == "Health Center"
    assert health_center.parent.code == "gesundheitsdienstleister"
    assert welfare.parent is None


@pytest.mark.django_db
def test_ph_taxonomy_seed_can_be_re_run_without_duplicates():
    before_dimensions = TaxonomyDimension.objects.filter(code__in=PH_DIMENSION_CODES).count()
    before_terms = TaxonomyTerm.objects.filter(dimension__code__in=PH_DIMENSION_CODES).count()

    migration = importlib.import_module("tosca_api.apps.events.migrations.0018_seed_ph_taxonomy")
    migration.seed_ph_taxonomy(apps, None)
    migration.seed_ph_taxonomy(apps, None)

    assert TaxonomyDimension.objects.filter(code__in=PH_DIMENSION_CODES).count() == before_dimensions
    assert TaxonomyTerm.objects.filter(dimension__code__in=PH_DIMENSION_CODES).count() == before_terms
    assert TaxonomyTerm.objects.filter(
        dimension__code="organization_type",
        code="grundschule",
    ).count() == 1


@pytest.mark.django_db
def test_seeded_ph_dimension_rejects_assignment_to_general_event(user, campaign):
    general_type = EventType.objects.get(code="general")
    event = _make_event(user, campaign, general_type)
    term = TaxonomyTerm.objects.get(dimension__code="field_of_action", code="sport_bewegung")

    candidate = EventTerm(event=event, term=term)
    with pytest.raises(ValidationError) as exc:
        candidate.clean()

    assert "term" in exc.value.message_dict
