from __future__ import annotations

from zoneinfo import available_timezones

from django import forms
from django.conf import settings
from django.forms.models import ModelChoiceIteratorValue

from .models import (
    CultureEventProfile,
    Event,
    EventSeries,
    EventType,
    PublicHealthEventProfile,
    SportsEventProfile,
    VALID_WEEKDAYS,
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
    """Admin form that replaces raw JSON entry with structured recurrence widgets."""

    by_weekday = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, required=True)
    weekday_of_month = forms.ChoiceField(choices=[("", "---------"), *WEEKDAY_CHOICES], required=False)

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
            return cleaned_data

        if recurrence_type != EventSeries.RecurrenceType.WEEKLY:
            cleaned_data["by_weekday"] = []

        if recurrence_type != EventSeries.RecurrenceType.MONTHLY:
            cleaned_data["monthly_rule_type"] = ""
            cleaned_data["day_of_month"] = None
            cleaned_data["week_of_month"] = None
            cleaned_data["weekday_of_month"] = ""

        return cleaned_data


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
