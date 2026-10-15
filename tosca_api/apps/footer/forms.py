from django import forms
from django.forms.models import BaseInlineFormSet

from tosca_api.apps.core.editorjs import render_content_media_urls
from tosca_api.apps.footer.models import FooterDocument
from tosca_api.apps.geocontext.widgets import EditorJsWidget


class OrderedInlineFormSet(BaseInlineFormSet):
    """Normalizes display_order to a gapless 0..n sequence before validation."""

    def clean(self):
        if any(self.errors):
            return

        forms = [
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE", False)
        ]
        existing_forms = [form for form in forms if form.instance.pk]
        new_forms = [form for form in forms if not form.instance.pk]
        moved_forms = [form for form in existing_forms if "display_order" in form.changed_data]

        if len(moved_forms) == 1:
            moved_form = moved_forms[0]
            ordered_forms = sorted(
                (form for form in existing_forms if form is not moved_form),
                key=lambda form: form.initial.get("display_order", 0),
            )
            requested_order = moved_form.cleaned_data.get("display_order", 0)
            target_order = min(max(requested_order, 0), len(ordered_forms))
            ordered_forms.insert(target_order, moved_form)
        else:
            ordered_forms = sorted(
                existing_forms,
                key=lambda form: (
                    form.cleaned_data.get("display_order", 0),
                    form.initial.get("display_order", 0),
                ),
            )

        # New items always append, regardless of the cloned inline's initial value.
        ordered_forms.extend(new_forms)
        for display_order, form in enumerate(ordered_forms):
            form.cleaned_data["display_order"] = display_order
            form.instance.display_order = display_order
            # Rows shifted by another row's move must be saved even though the
            # editor did not touch them; the formset skips unchanged forms.
            if (
                form.instance.pk
                and form.initial.get("display_order") != display_order
                and "display_order" not in form.changed_data
            ):
                form.changed_data.append("display_order")

        # Run model/formset uniqueness validation after normalization.
        super().clean()


class FooterLogoInlineFormSet(OrderedInlineFormSet):
    pass


class FooterDocumentInlineFormSet(OrderedInlineFormSet):
    pass


class FooterDocumentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial["content"] = render_content_media_urls(self.instance.content)

    class Meta:
        model = FooterDocument
        fields = "__all__"
        widgets = {"content": EditorJsWidget(profile="footer")}
