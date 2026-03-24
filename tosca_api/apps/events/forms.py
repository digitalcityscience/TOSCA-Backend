from __future__ import annotations

import json
from zoneinfo import available_timezones

from django import forms
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.exceptions import ValidationError
from django.forms.models import ModelChoiceIteratorValue

from tosca_api.apps.geocontext.models import GeoContext

from .models import (
    CultureEventProfile,
    Event,
    EventSeries,
    EventType,
    PublicHealthEventProfile,
    SportsEventProfile,
    TaxonomyTerm,
    VALID_WEEKDAYS,
)
from .services import (
    EVENT_TEMPLATE_FIELDS,
    get_base_template_event,
    validate_event_template,
)

TIMEZONE_CHOICES = [(timezone_name, timezone_name) for timezone_name in sorted(available_timezones())]
WEEKDAY_CHOICES = [(weekday, weekday.title()) for weekday in sorted(VALID_WEEKDAYS)]


class EventTypeSelect(forms.Select):
    """Expose EventType profile metadata as option attributes for admin JS."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name,
            value,
            label,
            selected,
            index,
            subindex=subindex,
            attrs=attrs,
        )
        if isinstance(value, ModelChoiceIteratorValue) and value.instance is not None:
            option["attrs"]["data-profile-mode"] = value.instance.profile_mode or ""
            option["attrs"]["data-profile-key"] = value.instance.profile_key or ""
        return option


class EventSeriesAdminForm(forms.ModelForm):
    """Admin form with structured recurrence widgets and event-template fields."""

    # --- Recurrence widgets ---
    by_weekday = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, required=True)
    weekday_of_month = forms.ChoiceField(choices=[("", "---------"), *WEEKDAY_CHOICES], required=False)

    # --- Event template fields ---
    title = forms.CharField(max_length=255, required=False)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    location_mode = forms.ChoiceField(
        choices=[("", "---------")] + list(Event.LocationMode.choices),
        required=False,
    )
    location = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": '{"type": "Point", "coordinates": [lng, lat]}'}),
        required=False,
        help_text="GeoJSON Point, e.g. {\"type\": \"Point\", \"coordinates\": [10.0, 53.5]}",
    )
    online_url = forms.URLField(required=False)
    online_platform = forms.CharField(max_length=255, required=False)
    access_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    provider_name = forms.CharField(max_length=255, required=False)
    provider_url = forms.URLField(required=False)
    provider_contact = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    status = forms.ChoiceField(
        choices=Event.Status.choices,
        initial=Event.Status.DRAFT,
        required=False,
    )
    visibility = forms.ChoiceField(
        choices=Event.Visibility.choices,
        initial=Event.Visibility.PUBLIC,
        required=False,
    )
    context = forms.ModelChoiceField(
        queryset=GeoContext.objects.all(),
        required=False,
        label="Content override",
    )

    # --- Profile extension fields ---
    public_health_insurance_eligible = forms.BooleanField(required=False, label="Insurance eligible")
    public_health_referral_required = forms.BooleanField(required=False, label="Referral required")
    sports_sport_name = forms.CharField(required=False, max_length=255, label="Sport name")
    sports_skill_level = forms.CharField(required=False, max_length=100, label="Skill level")
    culture_format_label = forms.CharField(required=False, max_length=255, label="Format label")
    culture_age_rating = forms.CharField(required=False, max_length=50, label="Age rating")

    # --- Taxonomy ---
    taxonomy_term_ids = forms.ModelMultipleChoiceField(
        queryset=TaxonomyTerm.objects.filter(is_active=True).select_related("dimension"),
        required=False,
        label="Taxonomy terms",
        help_text="Select terms to apply to generated occurrences.",
    )

    class Meta:
        model = EventSeries
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        created_by_user = kwargs.pop("created_by_user", None)
        super().__init__(*args, **kwargs)
        self.fields["event_type"].widget = EventTypeSelect(choices=self.fields["event_type"].choices)
        if created_by_user is not None and self.instance.created_by_id is None:
            self.instance.created_by = created_by_user
        self.fields["timezone"].initial = self.instance.timezone or settings.TIME_ZONE
        self.fields["by_weekday"].initial = list(self.instance.by_weekday or [])
        self.fields["weekday_of_month"].initial = self.instance.weekday_of_month or ""
        self.fields["by_weekday"].help_text = "Choose one or more weekdays for weekly recurrence."
        self.fields["occurrence_count"].help_text = (
            "Optional. Stop after this many occurrences instead of using an end date."
        )
        self.fields["interval"].help_text = (
            "Repeat every N recurrence units. Example: weekly + 2 means every 2 weeks."
        )
        self.fields["weekday_of_month"].help_text = (
            "Choose the weekday used for nth-weekday monthly recurrence."
        )

        # Pre-populate template fields from the base occurrence on edit
        self._load_template_from_base_occurrence()

    def _load_template_from_base_occurrence(self) -> None:
        """Pre-fill event template fields from the first non-exception occurrence."""
        if not self.instance.pk:
            return

        base_event = get_base_template_event(self.instance)
        if base_event is None:
            return

        # Event template fields
        self.fields["title"].initial = base_event.title
        self.fields["description"].initial = base_event.description
        self.fields["location_mode"].initial = base_event.location_mode
        if base_event.location:
            self.fields["location"].initial = base_event.location.geojson
        self.fields["online_url"].initial = base_event.online_url
        self.fields["online_platform"].initial = base_event.online_platform
        self.fields["access_notes"].initial = base_event.access_notes
        self.fields["provider_name"].initial = base_event.provider_name
        self.fields["provider_url"].initial = base_event.provider_url
        self.fields["provider_contact"].initial = base_event.provider_contact
        self.fields["status"].initial = base_event.status
        self.fields["visibility"].initial = base_event.visibility
        self.fields["context"].initial = base_event.context_id

        # Profile extension fields
        self._load_profile_initials(base_event)

        # Taxonomy terms
        term_ids = list(
            base_event.event_terms.values_list("term_id", flat=True)
        )
        if term_ids:
            self.fields["taxonomy_term_ids"].initial = term_ids

    def _load_profile_initials(self, base_event: Event) -> None:
        """Pre-fill profile fields from the base occurrence's profile."""
        try:
            profile = base_event.public_health_profile
            self.fields["public_health_insurance_eligible"].initial = profile.insurance_eligible
            self.fields["public_health_referral_required"].initial = profile.referral_required
        except PublicHealthEventProfile.DoesNotExist:
            pass

        try:
            profile = base_event.sports_profile
            self.fields["sports_sport_name"].initial = profile.sport_name
            self.fields["sports_skill_level"].initial = profile.skill_level
        except SportsEventProfile.DoesNotExist:
            pass

        try:
            profile = base_event.culture_profile
            self.fields["culture_format_label"].initial = profile.format_label
            self.fields["culture_age_rating"].initial = profile.age_rating
        except CultureEventProfile.DoesNotExist:
            pass

    def clean(self):
        cleaned_data = super().clean()
        series_mode = cleaned_data.get("series_mode")
        recurrence_type = cleaned_data.get("recurrence_type")

        if series_mode == EventSeries.SeriesMode.MANUAL_BATCH:
            cleaned_data["recurrence_type"] = ""
            cleaned_data["end_date"] = None
            cleaned_data["occurrence_count"] = None
            cleaned_data["interval"] = 1
            cleaned_data["by_weekday"] = []
            cleaned_data["monthly_rule_type"] = ""
            cleaned_data["day_of_month"] = None
            cleaned_data["week_of_month"] = None
            cleaned_data["weekday_of_month"] = ""

        if recurrence_type != EventSeries.RecurrenceType.WEEKLY:
            cleaned_data["by_weekday"] = []

        if recurrence_type != EventSeries.RecurrenceType.MONTHLY:
            cleaned_data["monthly_rule_type"] = ""
            cleaned_data["day_of_month"] = None
            cleaned_data["week_of_month"] = None
            cleaned_data["weekday_of_month"] = ""

        # --- Validate template fields ---
        self._clean_event_template(cleaned_data)

        return cleaned_data

    def _clean_event_template(self, cleaned_data: dict) -> None:
        """Validate event-template fields and stash results for save_related."""
        title = cleaned_data.get("title", "")
        location_mode = cleaned_data.get("location_mode", "")

        # Template fields are required for generation
        if not title:
            self.add_error("title", "Title is required for event generation.")
            return
        if not location_mode:
            self.add_error("location_mode", "Location mode is required for event generation.")
            return

        # Parse and validate location GeoJSON
        location_raw = cleaned_data.get("location", "")
        location_geom = None
        if location_raw:
            try:
                location_geom = GEOSGeometry(location_raw)
            except Exception:
                self.add_error("location", "Invalid GeoJSON. Expected a valid GeoJSON Point.")
                return
            if location_geom.geom_type != "Point":
                self.add_error("location", "Location must be a GeoJSON Point.")
                return
            if location_geom.srid is None:
                location_geom.srid = 4326
            elif location_geom.srid != 4326:
                location_geom.transform(4326)

        # Build event template dict
        event_template = {
            "title": title,
            "description": cleaned_data.get("description", ""),
            "location_mode": location_mode,
            "location": location_geom,
            "online_url": cleaned_data.get("online_url", ""),
            "online_platform": cleaned_data.get("online_platform", ""),
            "access_notes": cleaned_data.get("access_notes", ""),
            "provider_name": cleaned_data.get("provider_name", ""),
            "provider_url": cleaned_data.get("provider_url", ""),
            "provider_contact": cleaned_data.get("provider_contact", ""),
            "status": cleaned_data.get("status", Event.Status.DRAFT),
            "visibility": cleaned_data.get("visibility", Event.Visibility.PUBLIC),
            "context": cleaned_data.get("context"),
        }

        # Enforce campaign/event_type immutability after occurrences exist
        if self.instance.pk and self.instance.events.exists():
            campaign = cleaned_data.get("campaign")
            if campaign and campaign != self.instance.campaign:
                self.add_error(
                    "campaign",
                    "Series campaign cannot be changed after occurrences exist.",
                )
            event_type = cleaned_data.get("event_type")
            if event_type and event_type != self.instance.event_type:
                self.add_error(
                    "event_type",
                    "Series event type cannot be changed after occurrences exist.",
                )

        # Build a temporary series candidate from cleaned form data for validation
        # (self.instance doesn't have form data applied during clean())
        series_candidate = EventSeries(
            pk=self.instance.pk if self.instance.pk else None,
            campaign=cleaned_data.get("campaign"),
            event_type=cleaned_data.get("event_type"),
            name=cleaned_data.get("name", ""),
            created_by=self.instance.created_by,
            default_context=cleaned_data.get("default_context"),
            series_mode=cleaned_data.get("series_mode", ""),
            recurrence_type=cleaned_data.get("recurrence_type", ""),
            start_date=cleaned_data.get("start_date"),
            end_date=cleaned_data.get("end_date"),
            occurrence_count=cleaned_data.get("occurrence_count"),
            interval=cleaned_data.get("interval", 1),
            start_time=cleaned_data.get("start_time"),
            end_time=cleaned_data.get("end_time"),
            timezone=cleaned_data.get("timezone", ""),
            monthly_rule_type=cleaned_data.get("monthly_rule_type", ""),
            day_of_month=cleaned_data.get("day_of_month"),
            week_of_month=cleaned_data.get("week_of_month"),
            weekday_of_month=cleaned_data.get("weekday_of_month", ""),
            by_weekday=cleaned_data.get("by_weekday", []),
            notes=cleaned_data.get("notes", ""),
        )

        # If there are already form errors, skip service-level validation
        if self.errors:
            self._template_data = event_template
            self._taxonomy_terms = list(cleaned_data.get("taxonomy_term_ids") or [])
            self._profile_data = self._build_profile_data(cleaned_data)
            return

        # Validate the template against the series using the shared service
        # For manual-batch, skip occurrence validation since inline dates
        # are not available yet (saved after form validation in save_related).
        if series_candidate.series_mode != EventSeries.SeriesMode.MANUAL_BATCH:
            try:
                validate_event_template(
                    series=series_candidate,
                    event_template=event_template,
                    organizer=self.instance.created_by,
                )
            except ValidationError as exc:
                error_dict = exc.message_dict if hasattr(exc, "message_dict") else {}
                for field, messages in error_dict.items():
                    if field in self.fields:
                        self.add_error(field, messages)
                    else:
                        self.add_error(None, messages)

        # Stash validated data for use in admin save_related
        self._template_data = event_template
        self._taxonomy_terms = list(cleaned_data.get("taxonomy_term_ids") or [])
        self._profile_data = self._build_profile_data(cleaned_data)

    @staticmethod
    def _build_profile_data(cleaned_data: dict) -> dict:
        """Extract profile extension data from cleaned form data."""
        return {
            "insurance_eligible": cleaned_data.get("public_health_insurance_eligible", False),
            "referral_required": cleaned_data.get("public_health_referral_required", False),
            "sport_name": cleaned_data.get("sports_sport_name", ""),
            "skill_level": cleaned_data.get("sports_skill_level", ""),
            "format_label": cleaned_data.get("culture_format_label", ""),
            "age_rating": cleaned_data.get("culture_age_rating", ""),
        }


class EventAdminForm(forms.ModelForm):
    """Admin form that keeps extension profile fields on the event page."""

    public_health_insurance_eligible = forms.BooleanField(required=False)
    public_health_referral_required = forms.BooleanField(required=False)
    sports_sport_name = forms.CharField(required=False, max_length=255)
    sports_skill_level = forms.CharField(required=False, max_length=100)
    culture_format_label = forms.CharField(required=False, max_length=255)
    culture_age_rating = forms.CharField(required=False, max_length=50)

    class Meta:
        model = Event
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["event_type"].widget = EventTypeSelect(choices=self.fields["event_type"].choices)
        self.fields["public_health_insurance_eligible"].label = "Insurance eligible"
        self.fields["public_health_referral_required"].label = "Referral required"
        self.fields["sports_sport_name"].label = "Sport name"
        self.fields["sports_skill_level"].label = "Skill level"
        self.fields["culture_format_label"].label = "Format label"
        self.fields["culture_age_rating"].label = "Age rating"
        self._load_existing_profile_initials()

    def _load_existing_profile_initials(self) -> None:
        if not self.instance.pk:
            return

        try:
            profile = self.instance.public_health_profile
            self.fields["public_health_insurance_eligible"].initial = (
                profile.insurance_eligible
            )
            self.fields["public_health_referral_required"].initial = (
                profile.referral_required
            )
        except PublicHealthEventProfile.DoesNotExist:
            pass

        try:
            profile = self.instance.sports_profile
            self.fields["sports_sport_name"].initial = profile.sport_name
            self.fields["sports_skill_level"].initial = profile.skill_level
        except SportsEventProfile.DoesNotExist:
            pass

        try:
            profile = self.instance.culture_profile
            self.fields["culture_format_label"].initial = profile.format_label
            self.fields["culture_age_rating"].initial = profile.age_rating
        except CultureEventProfile.DoesNotExist:
            pass

    def save(self, commit=True):
        event = super().save(commit=commit)
        if commit:
            self.save_profile(event)
        return event

    def save_profile(self, event: Event) -> None:
        event_type: EventType | None = self.cleaned_data.get("event_type") or event.event_type
        profile_mode = getattr(event_type, "profile_mode", None)
        profile_key = (
            event_type.profile_key
            if event_type and profile_mode == EventType.ProfileMode.EXTENSION
            else None
        )

        self._delete_non_matching_profiles(event, keep_profile_key=profile_key)

        if profile_key == PublicHealthEventProfile.expected_profile_key:
            profile, _ = PublicHealthEventProfile.objects.get_or_create(event=event)
            profile.insurance_eligible = self.cleaned_data["public_health_insurance_eligible"]
            profile.referral_required = self.cleaned_data["public_health_referral_required"]
            profile.save()
        elif profile_key == SportsEventProfile.expected_profile_key:
            profile, _ = SportsEventProfile.objects.get_or_create(event=event)
            profile.sport_name = self.cleaned_data["sports_sport_name"]
            profile.skill_level = self.cleaned_data["sports_skill_level"]
            profile.save()
        elif profile_key == CultureEventProfile.expected_profile_key:
            profile, _ = CultureEventProfile.objects.get_or_create(event=event)
            profile.format_label = self.cleaned_data["culture_format_label"]
            profile.age_rating = self.cleaned_data["culture_age_rating"]
            profile.save()

    def _delete_non_matching_profiles(self, event: Event, *, keep_profile_key: str | None) -> None:
        profile_relations = {
            PublicHealthEventProfile.expected_profile_key: "public_health_profile",
            SportsEventProfile.expected_profile_key: "sports_profile",
            CultureEventProfile.expected_profile_key: "culture_profile",
        }

        for profile_key, relation in profile_relations.items():
            if profile_key == keep_profile_key:
                continue
            try:
                getattr(event, relation).delete()
            except (
                PublicHealthEventProfile.DoesNotExist,
                SportsEventProfile.DoesNotExist,
                CultureEventProfile.DoesNotExist,
            ):
                continue
