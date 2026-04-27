# Phase 3 — Store admin actions
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse


@admin.action(description='Clone store — set new name, target workspace & create in GeoServer')
def clone_store(modeladmin, request, queryset):
    """
    Redirect to the per-store clone form.
    Only works for a single store selection.
    """
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            'Select exactly one store to clone.',
            messages.ERROR,
        )
        return

    store = queryset.first()
    # Redirect to the existing clone form view wired at /<store_id>/clone/
    return HttpResponseRedirect(reverse('admin:store_clone', args=[store.pk]))
