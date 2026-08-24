from __future__ import annotations

from uuid import UUID
from zoneinfo import available_timezones

from django import forms
from django.conf import settings
from django.contrib.gis import forms as gis_forms
from django.contrib.gis.geos import GEOSGeometry
from django.core.exceptions import ValidationError
from django.forms.models import ModelChoiceIteratorValue

from tosca_api.apps.core.editorjs import empty_document, validate_and_normalize
from tosca_api.apps.geocontext.widgets import EditorJsWidget

from .models import (
    CultureEventProfile,
    Event,
    EventTerm,
    EventSeries,
    EventType,
    LANGUAGE_CHOICES,
    PublicHealthEventProfile,
    SportsEventProfile,
    TaxonomyDimension,
    TaxonomyTerm,
    VALID_WEEKDAYS,
)
from .services import (
    get_base_template_event,
    resolve_taxonomy_assignments,
    validate_event_template,
    validate_publish_requirements,
)

TIMEZONE_CHOICES = [
    (timezone_name, timezone_name) for timezone_name in sorted(available_timezones())
]
WEEKDAY_ORDER = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
WEEKDAY_CHOICES = [
    (weekday, weekday.title()) for weekday in WEEKDAY_ORDER if weekday in VALID_WEEKDAYS
]


def validate_public_health_admin_amounts(form: forms.BaseForm, cleaned_data: dict) -> None:
    cost = cleaned_data.get("public_health_cost_amount_eur")
    reduced = cleaned_data.get("public_health_reduced_amount_eur")
    if cost is not None and reduced is not None and reduced > cost:
        form.add_error(
            "public_health_reduced_amount_eur",
            "Reduced amount cannot exceed cost amount.",
        )


def taxonomy_dimension_field_name(dimension: TaxonomyDimension) -> str:
    """Build the dynamic admin field name for a taxonomy dimension."""
    return f"taxonomy_dimension_{dimension.id.hex}"


def get_profile_key_choices() -> list[tuple[str, str]]:
    """Return profile-key choices backed by extension event types."""
    choices = [("", "Unscoped")]
    seen_profile_keys = {""}
    event_types = EventType.objects.filter(
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key__isnull=False,
    ).exclude(profile_key="")
    for event_type in event_types.order_by("label", "code"):
        if event_type.profile_key in seen_profile_keys:
            continue
        choices.append((event_type.profile_key, event_type.label))
        seen_profile_keys.add(event_type.profile_key)
    return choices


def get_taxonomy_dimensions_for_source(
    source_event: Event | None = None,
    *,
    include_all_profile_dimensions: bool = False,
) -> list[TaxonomyDimension]:
    """Return active dimensions plus any inactive dimensions already assigned.

    Dimensions with a non-empty ``profile_key`` are restricted to events whose
    ``event_type.profile_key`` matches. Unscoped dimensions are always offered.
    Already-assigned dimensions are kept even when they would otherwise be
    filtered out so admin can review/clear them.
    """
    event_profile_key = ""
    if source_event is not None and source_event.event_type_id:
        event_profile_key = source_event.event_type.profile_key or ""

    active = TaxonomyDimension.objects.filter(is_active=True).order_by("sort_order", "label")
    dimensions = [
        dimension
        for dimension in active
        if (
            include_all_profile_dimensions
            or not dimension.profile_key
            or dimension.profile_key == event_profile_key
        )
    ]
    seen_dimension_ids = {dimension.id for dimension in dimensions}

    if source_event is None:
        return dimensions

    assigned_dimensions = (
        TaxonomyDimension.objects.filter(terms__event_terms__event=source_event)
        .distinct()
        .order_by("sort_order", "label")
    )
    for dimension in assigned_dimensions:
        if dimension.id in seen_dimension_ids:
            continue
        dimensions.append(dimension)
        seen_dimension_ids.add(dimension.id)

    return dimensions


def build_taxonomy_dimension_form_fields(
    source_event: Event | None = None,
    *,
    include_all_profile_dimensions: bool = False,
) -> tuple[
    list[TaxonomyDimension],
    dict[str, TaxonomyDimension],
    dict[str, forms.Field],
    set[UUID],
    set[UUID],
]:
    """Build reusable dynamic admin fields for taxonomy dimensions."""
    taxonomy_dimensions = get_taxonomy_dimensions_for_source(
        source_event,
        include_all_profile_dimensions=include_all_profile_dimensions,
    )
    taxonomy_dimension_fields: dict[str, TaxonomyDimension] = {}
    form_fields: dict[str, forms.Field] = {}

    assigned_terms = (
        list(source_event.event_terms.select_related("term__dimension").all())
        if source_event is not None
        else []
    )
    assigned_term_ids = {event_term.term_id for event_term in assigned_terms}
    allowed_inactive_term_ids = {
        event_term.term_id for event_term in assigned_terms if not event_term.term.is_active
    }
    allowed_inactive_dimension_ids = {
        event_term.term.dimension_id
        for event_term in assigned_terms
        if not event_term.term.dimension.is_active
    }
    assigned_term_ids_by_dimension: dict[UUID, list[str]] = {}
    for event_term in assigned_terms:
        assigned_term_ids_by_dimension.setdefault(event_term.term.dimension_id, []).append(
            str(event_term.term_id)
        )

    for dimension in taxonomy_dimensions:
        field_name = taxonomy_dimension_field_name(dimension)
        terms = list(
            TaxonomyTerm.objects.filter(dimension=dimension).order_by(
                "parent_id", "sort_order", "label"
            )
        )
        choices = [
            (
                str(term.id),
                f"{term.label}{' (inactive)' if not term.is_active else ''}",
            )
            for term in terms
            if term.is_active or term.id in assigned_term_ids
        ]
        help_bits = [dimension.description] if dimension.description else []
        if dimension.selection_mode == TaxonomyDimension.SelectionMode.SINGLE:
            field = forms.ChoiceField(
                required=False,
                choices=[("", "---------"), *choices],
                label=dimension.label,
                help_text=" ".join(help_bits).strip(),
            )
            field.initial = assigned_term_ids_by_dimension.get(dimension.id, [""])[0]
        else:
            field = forms.MultipleChoiceField(
                required=False,
                choices=choices,
                label=dimension.label,
                help_text=" ".join(help_bits).strip(),
                widget=forms.CheckboxSelectMultiple,
            )
            field.initial = assigned_term_ids_by_dimension.get(dimension.id, [])

        field.widget.attrs["data-taxonomy-profile-key"] = dimension.profile_key or ""
        form_fields[field_name] = field
        taxonomy_dimension_fields[field_name] = dimension

    return (
        taxonomy_dimensions,
        taxonomy_dimension_fields,
        form_fields,
        allowed_inactive_dimension_ids,
        allowed_inactive_term_ids,
    )


class TaxonomyAssignmentAdminMixin:
    """Shared dynamic taxonomy-dimension fields for admin authoring forms."""

    _taxonomy_dimensions: list[TaxonomyDimension]
    _taxonomy_dimension_fields: dict[str, TaxonomyDimension]
    _taxonomy_allowed_inactive_dimension_ids: set
    _taxonomy_allowed_inactive_term_ids: set

    def _initialize_taxonomy_dimension_fields(
        self,
        *,
        source_event: Event | None = None,
        include_all_profile_dimensions: bool = False,
    ) -> None:
        (
            self._taxonomy_dimensions,
            self._taxonomy_dimension_fields,
            taxonomy_form_fields,
            self._taxonomy_allowed_inactive_dimension_ids,
            self._taxonomy_allowed_inactive_term_ids,
        ) = build_taxonomy_dimension_form_fields(
            source_event,
            include_all_profile_dimensions=include_all_profile_dimensions,
        )
        self.fields.update(taxonomy_form_fields)

    def clean_taxonomy_assignments(self, cleaned_data: dict) -> list[TaxonomyTerm]:
        """Resolve grouped taxonomy assignments from dynamic admin fields."""
        taxonomy_assignments = []
        for field_name, dimension in self._taxonomy_dimension_fields.items():
            raw_value = cleaned_data.get(field_name)
            if raw_value in (None, "", []):
                continue

            term_ids = raw_value if isinstance(raw_value, list) else [raw_value]
            taxonomy_assignments.append(
                {
                    "dimension_id": dimension.id,
                    "term_ids": [UUID(str(term_id)) for term_id in term_ids],
                }
            )

        try:
            return resolve_taxonomy_assignments(
                taxonomy_assignments,
                event_profile_key=self._event_profile_key_for_cleaned_data(cleaned_data),
                allow_inactive_dimension_ids=self._taxonomy_allowed_inactive_dimension_ids,
                allow_inactive_term_ids=self._taxonomy_allowed_inactive_term_ids,
            )
        except ValidationError as exc:
            error_messages = exc.message_dict.get("taxonomy_assignments", exc.messages)
            self.add_error(None, error_messages)
            return []

    @staticmethod
    def _event_profile_key_for_cleaned_data(cleaned_data: dict) -> str:
        event_type = cleaned_data.get("event_type")
        if event_type is not None and event_type.profile_mode == EventType.ProfileMode.EXTENSION:
            return event_type.profile_key or ""
        return ""


class TaxonomyDimensionAdminForm(forms.ModelForm):
    """Admin form that restricts profile_key to known event-type profiles."""

    profile_key = forms.ChoiceField(required=False)

    class Meta:
        model = TaxonomyDimension
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = get_profile_key_choices()
        current_profile_key = self.instance.profile_key if self.instance.pk else ""
        if current_profile_key and current_profile_key not in {value for value, _ in choices}:
            choices.append((current_profile_key, current_profile_key))
        self.fields["profile_key"].choices = choices


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


class EventSeriesAdminForm(TaxonomyAssignmentAdminMixin, forms.ModelForm):
    """Admin form with structured recurrence widgets and event-template fields."""

    # --- Recurrence widgets ---
    by_weekday = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, required=True)
    weekday_of_month = forms.ChoiceField(
        choices=[("", "---------"), *WEEKDAY_CHOICES], required=False
    )

    # --- Event template fields ---
    title = forms.CharField(max_length=255, required=False)
    summary = forms.CharField(max_length=100, required=False)
    location_mode = forms.ChoiceField(
        choices=[("", "---------")] + list(Event.LocationMode.choices),
        required=False,
    )
    location = gis_forms.PointField(
        required=False,
        help_text="Pick a point on the map for physical or hybrid events.",
    )
    venue_address = forms.CharField(max_length=512, required=False)
    district = forms.CharField(max_length=120, required=False)
    online_url = forms.URLField(required=False)
    online_platform = forms.CharField(max_length=255, required=False)
    access_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    provider_name = forms.CharField(max_length=255, required=False)
    provider_address = forms.CharField(max_length=512, required=False)
    provider_phone = forms.CharField(max_length=50, required=False)
    provider_email = forms.EmailField(required=False)
    provider_social = forms.CharField(max_length=512, required=False)
    provider_url = forms.URLField(required=False)
    language = forms.MultipleChoiceField(
        choices=LANGUAGE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    language_note = forms.CharField(max_length=255, required=False)
    lead_name = forms.CharField(max_length=120, required=False)
    external_url = forms.URLField(required=False)
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
    content_override = forms.JSONField(
        required=False,
        label="Occurrence content override",
        widget=EditorJsWidget(),
    )
    inherit_series_content = forms.BooleanField(
        required=False,
        initial=True,
        label="Use the series default content",
    )

    # --- Profile extension fields ---
    public_health_insurance_eligible = forms.BooleanField(
        required=False, label="Insurance eligible"
    )
    public_health_referral_required = forms.BooleanField(required=False, label="Referral required")
    public_health_target_age_note = forms.CharField(
        required=False,
        max_length=120,
        label="Target age note",
    )
    public_health_registration = forms.ChoiceField(
        choices=[("", "---------"), *PublicHealthEventProfile.Registration.choices],
        required=False,
        label="Registration",
    )
    public_health_short_notice_possible = forms.BooleanField(
        required=False,
        label="Short-notice participation possible",
    )
    public_health_cost_amount_eur = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=0,
        label="Cost amount (EUR)",
    )
    public_health_reduced_amount_eur = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=0,
        label="Reduced amount (EUR)",
    )
    public_health_subsidy_program = forms.CharField(
        required=False,
        max_length=255,
        label="Subsidy program",
    )
    public_health_transit_note = forms.CharField(
        required=False,
        max_length=255,
        label="Transit note",
    )
    sports_sport_name = forms.CharField(required=False, max_length=255, label="Sport name")
    sports_skill_level = forms.CharField(required=False, max_length=100, label="Skill level")
    culture_format_label = forms.CharField(required=False, max_length=255, label="Format label")
    culture_age_rating = forms.CharField(required=False, max_length=50, label="Age rating")

    class Meta:
        model = EventSeries
        fields = "__all__"
        widgets = {"default_content": EditorJsWidget()}

    def __init__(self, *args, **kwargs):
        created_by_user = kwargs.pop("created_by_user", None)
        super().__init__(*args, **kwargs)
        self.fields["event_type"].widget = EventTypeSelect(
            choices=self.fields["event_type"].choices
        )
        if created_by_user is not None and self.instance.created_by_id is None:
            self.instance.created_by = created_by_user
        self.fields["timezone"].initial = self.instance.timezone or settings.TIME_ZONE
        self.fields["by_weekday"].initial = list(self.instance.by_weekday or [])
        self.fields["weekday_of_month"].initial = self.instance.weekday_of_month or ""
        self.fields["by_weekday"].help_text = "Choose one or more weekdays for weekly recurrence."
        self.fields[
            "occurrence_count"
        ].help_text = "Optional. Stop after this many occurrences instead of using an end date."
        self.fields[
            "interval"
        ].help_text = "Repeat every N recurrence units. Example: weekly + 2 means every 2 weeks."
        self.fields[
            "weekday_of_month"
        ].help_text = "Choose the weekday used for nth-weekday monthly recurrence."

        # Pre-populate template fields from the base occurrence on edit
        base_event = self._load_template_from_base_occurrence()
        self._initialize_taxonomy_dimension_fields(
            source_event=base_event,
            include_all_profile_dimensions=base_event is None,
        )

    def _load_template_from_base_occurrence(self) -> Event | None:
        """Pre-fill event template fields from the first non-exception occurrence."""
        if not self.instance.pk:
            return None

        base_event = get_base_template_event(self.instance)
        if base_event is None:
            return None

        # Event template fields
        self.fields["title"].initial = base_event.title
        self.fields["summary"].initial = base_event.summary
        self.fields["location_mode"].initial = base_event.location_mode
        if base_event.location:
            self.fields["location"].initial = base_event.location
        self.fields["venue_address"].initial = base_event.venue_address
        self.fields["district"].initial = base_event.district
        self.fields["online_url"].initial = base_event.online_url
        self.fields["online_platform"].initial = base_event.online_platform
        self.fields["access_notes"].initial = base_event.access_notes
        self.fields["provider_name"].initial = base_event.provider_name
        self.fields["provider_address"].initial = base_event.provider_address
        self.fields["provider_phone"].initial = base_event.provider_phone
        self.fields["provider_email"].initial = base_event.provider_email
        self.fields["provider_social"].initial = base_event.provider_social
        self.fields["provider_url"].initial = base_event.provider_url
        self.fields["language"].initial = list(base_event.language or [])
        self.fields["language_note"].initial = base_event.language_note
        self.fields["lead_name"].initial = base_event.lead_name
        self.fields["external_url"].initial = base_event.external_url
        self.fields["status"].initial = base_event.status
        self.fields["visibility"].initial = base_event.visibility
        self.fields["content_override"].initial = base_event.content_override or empty_document()
        self.fields["inherit_series_content"].initial = base_event.content_override is None

        # Profile extension fields
        self._load_profile_initials(base_event)

        return base_event

    def _load_profile_initials(self, base_event: Event) -> None:
        """Pre-fill profile fields from the base occurrence's profile."""
        try:
            profile = base_event.public_health_profile
            self.fields["public_health_insurance_eligible"].initial = profile.insurance_eligible
            self.fields["public_health_referral_required"].initial = profile.referral_required
            self.fields["public_health_target_age_note"].initial = profile.target_age_note
            self.fields["public_health_registration"].initial = profile.registration
            self.fields[
                "public_health_short_notice_possible"
            ].initial = profile.short_notice_possible
            self.fields["public_health_cost_amount_eur"].initial = profile.cost_amount_eur
            self.fields["public_health_reduced_amount_eur"].initial = profile.reduced_amount_eur
            self.fields["public_health_subsidy_program"].initial = profile.subsidy_program
            self.fields["public_health_transit_note"].initial = profile.transit_note
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
        if cleaned_data.get("inherit_series_content"):
            cleaned_data["content_override"] = None
        else:
            cleaned_data["content_override"] = validate_and_normalize(
                cleaned_data.get("content_override")
            )
        validate_public_health_admin_amounts(self, cleaned_data)
        self._clean_event_template(cleaned_data)
        self._taxonomy_terms = self.clean_taxonomy_assignments(cleaned_data)

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
        location_value = cleaned_data.get("location")
        location_geom = None
        if location_value:
            if isinstance(location_value, GEOSGeometry):
                location_geom = location_value
            else:
                try:
                    location_geom = GEOSGeometry(location_value)
                except Exception:
                    self.add_error("location", "Invalid location geometry.")
                    return
            if location_geom.geom_type != "Point":
                self.add_error("location", "Location must be a point.")
                return
            if location_geom.srid is None:
                location_geom.srid = 4326
            elif location_geom.srid != 4326:
                location_geom.transform(4326)

        # Build event template dict
        event_template = {
            "title": title,
            "summary": cleaned_data.get("summary", ""),
            "location_mode": location_mode,
            "location": location_geom,
            "venue_address": cleaned_data.get("venue_address", ""),
            "district": cleaned_data.get("district", ""),
            "online_url": cleaned_data.get("online_url", ""),
            "online_platform": cleaned_data.get("online_platform", ""),
            "access_notes": cleaned_data.get("access_notes", ""),
            "provider_name": cleaned_data.get("provider_name", ""),
            "provider_address": cleaned_data.get("provider_address", ""),
            "provider_phone": cleaned_data.get("provider_phone", ""),
            "provider_email": cleaned_data.get("provider_email", ""),
            "provider_social": cleaned_data.get("provider_social", ""),
            "provider_url": cleaned_data.get("provider_url", ""),
            "language": cleaned_data.get("language", []) or [],
            "language_note": cleaned_data.get("language_note", ""),
            "lead_name": cleaned_data.get("lead_name", ""),
            "external_url": cleaned_data.get("external_url", ""),
            "status": cleaned_data.get("status", Event.Status.DRAFT),
            "visibility": cleaned_data.get("visibility", Event.Visibility.PUBLIC),
            "content_override": cleaned_data.get("content_override"),
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
            default_content=cleaned_data.get("default_content") or empty_document(),
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

        publish_errors = validate_publish_requirements(event_template)
        for field, message in publish_errors.items():
            self.add_error(field if field in self.fields else None, message)

        # Stash validated data for use in admin save_related
        self._template_data = event_template
        self._profile_data = self._build_profile_data(cleaned_data)

    @staticmethod
    def _build_profile_data(cleaned_data: dict) -> dict:
        """Extract profile extension data from cleaned form data."""
        return {
            "insurance_eligible": cleaned_data.get("public_health_insurance_eligible", False),
            "referral_required": cleaned_data.get("public_health_referral_required", False),
            "target_age_note": cleaned_data.get("public_health_target_age_note", ""),
            "registration": cleaned_data.get("public_health_registration", ""),
            "short_notice_possible": cleaned_data.get(
                "public_health_short_notice_possible",
                False,
            ),
            "cost_amount_eur": cleaned_data.get("public_health_cost_amount_eur"),
            "reduced_amount_eur": cleaned_data.get("public_health_reduced_amount_eur"),
            "subsidy_program": cleaned_data.get("public_health_subsidy_program", ""),
            "transit_note": cleaned_data.get("public_health_transit_note", ""),
            "sport_name": cleaned_data.get("sports_sport_name", ""),
            "skill_level": cleaned_data.get("sports_skill_level", ""),
            "format_label": cleaned_data.get("culture_format_label", ""),
            "age_rating": cleaned_data.get("culture_age_rating", ""),
        }


class EventAdminForm(TaxonomyAssignmentAdminMixin, forms.ModelForm):
    """Admin form that keeps extension profile fields on the event page."""

    public_health_insurance_eligible = forms.BooleanField(required=False)
    public_health_referral_required = forms.BooleanField(required=False)
    public_health_target_age_note = forms.CharField(required=False, max_length=120)
    public_health_registration = forms.ChoiceField(
        choices=[("", "---------"), *PublicHealthEventProfile.Registration.choices],
        required=False,
    )
    public_health_short_notice_possible = forms.BooleanField(required=False)
    public_health_cost_amount_eur = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=0,
    )
    public_health_reduced_amount_eur = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=0,
    )
    public_health_subsidy_program = forms.CharField(required=False, max_length=255)
    public_health_transit_note = forms.CharField(required=False, max_length=255)
    sports_sport_name = forms.CharField(required=False, max_length=255)
    sports_skill_level = forms.CharField(required=False, max_length=100)
    culture_format_label = forms.CharField(required=False, max_length=255)
    culture_age_rating = forms.CharField(required=False, max_length=50)
    inherit_series_content = forms.BooleanField(
        required=False,
        label="Use the series default content",
    )

    class Meta:
        model = Event
        fields = "__all__"
        widgets = {"content_override": EditorJsWidget()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["event_type"].widget = EventTypeSelect(
            choices=self.fields["event_type"].choices
        )
        self.fields["public_health_insurance_eligible"].label = "Insurance eligible"
        self.fields["public_health_referral_required"].label = "Referral required"
        self.fields["public_health_target_age_note"].label = "Target age note"
        self.fields["public_health_registration"].label = "Registration"
        self.fields[
            "public_health_short_notice_possible"
        ].label = "Short-notice participation possible"
        self.fields["public_health_cost_amount_eur"].label = "Cost amount (EUR)"
        self.fields["public_health_reduced_amount_eur"].label = "Reduced amount (EUR)"
        self.fields["public_health_subsidy_program"].label = "Subsidy program"
        self.fields["public_health_transit_note"].label = "Transit note"
        self.fields["sports_sport_name"].label = "Sport name"
        self.fields["sports_skill_level"].label = "Skill level"
        self.fields["culture_format_label"].label = "Format label"
        self.fields["culture_age_rating"].label = "Age rating"
        if self.instance._state.adding:
            # A brand-new event's id is already populated (UUID pk default),
            # so pk is not None here -- _state.adding is the reliable signal
            # that this instance has no persisted series/content_override yet
            # to inspect. Default to the inheriting stance the model itself
            # treats as the default (a null content_override inherits the
            # series). Staff who don't want inheritance must uncheck it.
            self.fields["inherit_series_content"].initial = True
        else:
            self.fields["inherit_series_content"].initial = bool(
                self.instance.series_id and self.instance.content_override is None
            )
        source_event = self.instance if self.instance.pk else None
        self._initialize_taxonomy_dimension_fields(
            source_event=source_event,
            include_all_profile_dimensions=source_event is None,
        )
        self._load_existing_profile_initials()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("inherit_series_content"):
            if cleaned_data.get("series") is None:
                self.add_error(
                    "inherit_series_content",
                    "Only events in a series can inherit series content.",
                )
            cleaned_data["content_override"] = None
        else:
            cleaned_data["content_override"] = validate_and_normalize(
                cleaned_data.get("content_override")
            )
        validate_public_health_admin_amounts(self, cleaned_data)
        self._taxonomy_terms = self.clean_taxonomy_assignments(cleaned_data)
        publish_errors = validate_publish_requirements(cleaned_data)
        for field, message in publish_errors.items():
            self.add_error(field if field in self.fields else None, message)
        return cleaned_data

    def _load_existing_profile_initials(self) -> None:
        if not self.instance.pk:
            return

        try:
            profile = self.instance.public_health_profile
            self.fields["public_health_insurance_eligible"].initial = profile.insurance_eligible
            self.fields["public_health_referral_required"].initial = profile.referral_required
            self.fields["public_health_target_age_note"].initial = profile.target_age_note
            self.fields["public_health_registration"].initial = profile.registration
            self.fields[
                "public_health_short_notice_possible"
            ].initial = profile.short_notice_possible
            self.fields["public_health_cost_amount_eur"].initial = profile.cost_amount_eur
            self.fields["public_health_reduced_amount_eur"].initial = profile.reduced_amount_eur
            self.fields["public_health_subsidy_program"].initial = profile.subsidy_program
            self.fields["public_health_transit_note"].initial = profile.transit_note
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
            self.save_taxonomy(event)
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
            profile.target_age_note = self.cleaned_data["public_health_target_age_note"]
            profile.registration = self.cleaned_data["public_health_registration"]
            profile.short_notice_possible = self.cleaned_data["public_health_short_notice_possible"]
            profile.cost_amount_eur = self.cleaned_data["public_health_cost_amount_eur"]
            profile.reduced_amount_eur = self.cleaned_data["public_health_reduced_amount_eur"]
            profile.subsidy_program = self.cleaned_data["public_health_subsidy_program"]
            profile.transit_note = self.cleaned_data["public_health_transit_note"]
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

    def save_taxonomy(self, event: Event) -> None:
        EventTerm.objects.filter(event=event).delete()
        for term in getattr(self, "_taxonomy_terms", []):
            EventTerm.objects.create(event=event, term=term)
