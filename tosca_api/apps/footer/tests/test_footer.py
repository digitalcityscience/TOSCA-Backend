import json

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from tosca_api.apps.footer.forms import FooterDocumentForm
from tosca_api.apps.footer.models import (
    SOFT_LIMIT_DOCUMENTS,
    SOFT_LIMIT_LOGOS,
    Footer,
    FooterDocument,
    FooterLogo,
)
from tosca_api.apps.footer.services import footer_overflow_warnings, publish_footer


pytestmark = pytest.mark.django_db


def document_content(text: str) -> dict:
    return {"blocks": [{"type": "paragraph", "data": {"text": text}}]}


def add_logo(footer: Footer, *, order: int = 0):
    return FooterLogo.objects.create(
        footer=footer,
        image=f"footer/test/logo-{order}.png",
        alt_text=f"Logo {order}",
        display_order=order,
    )


def management_form(prefix: str, total: int, initial: int) -> dict:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def add_document(footer: Footer, slug: str, *, order: int = 0, text: str | None = None):
    return FooterDocument.objects.create(
        footer=footer,
        title=slug.title(),
        slug=slug,
        content=document_content(text or slug),
        display_order=order,
    )


def test_footer_document_editor_uses_footer_heading_profile():
    widget = FooterDocumentForm().fields["content"].widget

    assert widget.attrs["data-editorjs-profile"] == "footer"


def test_publish_replaces_the_previous_footer():
    first = Footer.objects.create(name="First")
    second = Footer.objects.create(name="Second")
    add_document(first, "privacy")
    add_document(second, "imprint")

    publish_footer(first.pk)
    publish_footer(second.pk)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_published is False
    assert second.is_published is True
    assert Footer.objects.filter(is_published=True).count() == 1


def test_published_snapshot_does_not_change_when_draft_is_edited():
    footer = Footer.objects.create(name="Global")
    document = add_document(footer, "privacy", text="Published text")
    publish_footer(footer.pk)

    document.content = document_content("Unpublished edit")
    document.save()

    footer.refresh_from_db()
    published_text = footer.published_snapshot["documents"][0]["content"]["blocks"][0]["data"][
        "text"
    ]
    assert published_text == "Published text"


def test_publish_accepts_many_logos_and_documents_in_display_order():
    footer = Footer.objects.create(name="Many items")
    slugs = [f"doc-{index}" for index in range(8)]
    for order, slug in reversed(list(enumerate(slugs))):
        add_document(footer, slug, order=order)
    for order in range(10):
        add_logo(footer, order=order)

    publish_footer(footer.pk)

    footer.refresh_from_db()
    snapshot = footer.published_snapshot
    assert [document["slug"] for document in snapshot["documents"]] == slugs
    assert [logo["display_order"] for logo in snapshot["logos"]] == list(range(10))


def test_overflow_warnings_only_above_soft_limits():
    footer = Footer.objects.create(name="Soft limits")
    for order in range(SOFT_LIMIT_LOGOS):
        add_logo(footer, order=order)
    for order in range(SOFT_LIMIT_DOCUMENTS):
        add_document(footer, f"doc-{order}", order=order)

    assert footer_overflow_warnings(footer) == []

    add_logo(footer, order=SOFT_LIMIT_LOGOS)
    add_document(footer, "one-more", order=SOFT_LIMIT_DOCUMENTS)

    warnings = footer_overflow_warnings(footer)
    assert len(warnings) == 2
    assert "logos" in warnings[0]
    assert "“More” menu" in warnings[1]


def test_publish_rejects_an_empty_active_document():
    footer = Footer.objects.create(name="Empty document")
    FooterDocument.objects.create(
        footer=footer,
        title="Privacy",
        slug="privacy",
        content={"blocks": []},
    )

    with pytest.raises(ValidationError, match="has no content"):
        publish_footer(footer.pk)


def test_public_footer_returns_null_when_nothing_is_published():
    response = APIClient().get(reverse("footer:published-footer"))

    assert response.status_code == 200
    assert response.json() == {"footer": None}
    assert response["Cache-Control"] == "no-cache"


def test_public_footer_returns_only_the_published_snapshot():
    footer = Footer.objects.create(name="Global")
    add_document(footer, "privacy", text="Public")
    publish_footer(footer.pk)

    response = APIClient().get(reverse("footer:published-footer"))

    assert response.status_code == 200
    document = response.json()["documents"][0]
    assert document["slug"] == "privacy"
    assert document["path"] == "/privacy"
    assert "content" not in document
    assert response["Cache-Control"] == "no-cache"
    assert response["ETag"].startswith('"footer-')

    revalidated = APIClient().get(
        reverse("footer:published-footer"),
        HTTP_IF_NONE_MATCH=response["ETag"],
    )
    assert revalidated.status_code == 304


def test_document_endpoint_serves_document_content():
    footer = Footer.objects.create(name="Global")
    add_document(footer, "privacy", text="Public text")
    publish_footer(footer.pk)

    response = APIClient().get(
        reverse("footer:published-footer-document", kwargs={"slug": "privacy"})
    )

    assert response.status_code == 200
    assert response.json()["content"]["blocks"][0]["data"]["text"] == "Public text"
    assert response["ETag"].startswith('"footer-')


def test_document_endpoint_only_serves_currently_published_document():
    first = Footer.objects.create(name="First")
    second = Footer.objects.create(name="Second")
    add_document(first, "privacy")
    add_document(second, "imprint")
    publish_footer(first.pk)

    client = APIClient()
    assert (
        client.get(
            reverse("footer:published-footer-document", kwargs={"slug": "privacy"})
        ).status_code
        == 200
    )

    publish_footer(second.pk)

    assert (
        client.get(
            reverse("footer:published-footer-document", kwargs={"slug": "privacy"})
        ).status_code
        == 404
    )
    assert (
        client.get(
            reverse("footer:published-footer-document", kwargs={"slug": "imprint"})
        ).status_code
        == 200
    )


def test_admin_publish_control_publishes_from_the_list_workflow(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="footer-admin",
        email="footer@example.com",
        password="test-password",
    )
    footer = Footer.objects.create(name="Admin footer")
    add_document(footer, "privacy")
    client.force_login(user)

    publish_url = reverse("admin:footer_footer_publish", args=[footer.pk])
    confirmation = client.get(publish_url)
    response = client.post(publish_url)

    footer.refresh_from_db()
    assert confirmation.status_code == 200
    assert b"/privacy" in confirmation.content
    assert response.status_code == 302
    assert footer.is_published is True


def test_admin_inline_persists_editorjs_document_content(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="footer-content-admin",
        email="footer-content@example.com",
        password="test-password",
    )
    footer = Footer.objects.create(name="Editable footer")
    content = {
        "blocks": [
            {"type": "header", "data": {"text": "Page title", "level": 1}},
            {"type": "header", "data": {"text": "Section", "level": 2}},
            {"type": "header", "data": {"text": "Subsection", "level": 3}},
        ]
    }
    client.force_login(user)

    response = client.post(
        reverse("admin:footer_footer_change", args=[footer.pk]),
        {
            "name": footer.name,
            "logos-TOTAL_FORMS": "0",
            "logos-INITIAL_FORMS": "0",
            "logos-MIN_NUM_FORMS": "0",
            "logos-MAX_NUM_FORMS": "1000",
            "documents-TOTAL_FORMS": "1",
            "documents-INITIAL_FORMS": "0",
            "documents-MIN_NUM_FORMS": "0",
            "documents-MAX_NUM_FORMS": "1000",
            "documents-0-title": "Privacy",
            "documents-0-slug": "privacy",
            "documents-0-content": json.dumps(content),
            "documents-0-display_order": "0",
            "documents-0-is_active": "on",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    assert footer.documents.get(slug="privacy").content == content


def test_admin_inline_moves_existing_document_and_appends_new_document(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="footer-order-admin",
        email="footer-order@example.com",
        password="test-password",
    )
    footer = Footer.objects.create(name="Ordered footer")
    first = add_document(footer, "privacy", order=0)
    second = add_document(footer, "imprint", order=1)
    third = add_document(footer, "terms", order=2)
    client.force_login(user)

    response = client.post(
        reverse("admin:footer_footer_change", args=[footer.pk]),
        {
            "name": footer.name,
            "logos-TOTAL_FORMS": "0",
            "logos-INITIAL_FORMS": "0",
            "logos-MIN_NUM_FORMS": "0",
            "logos-MAX_NUM_FORMS": "1000",
            "documents-TOTAL_FORMS": "4",
            "documents-INITIAL_FORMS": "3",
            "documents-MIN_NUM_FORMS": "0",
            "documents-MAX_NUM_FORMS": "1000",
            "documents-0-id": str(first.pk),
            "documents-0-title": first.title,
            "documents-0-slug": first.slug,
            "documents-0-content": json.dumps(first.content),
            "documents-0-display_order": "0",
            "documents-0-is_active": "on",
            "documents-1-id": str(second.pk),
            "documents-1-title": second.title,
            "documents-1-slug": second.slug,
            "documents-1-content": json.dumps(second.content),
            "documents-1-display_order": "1",
            "documents-1-is_active": "on",
            "documents-2-id": str(third.pk),
            "documents-2-title": third.title,
            "documents-2-slug": third.slug,
            "documents-2-content": json.dumps(third.content),
            "documents-2-display_order": "0",
            "documents-2-is_active": "on",
            "documents-3-title": "Accessibility",
            "documents-3-slug": "accessibility",
            "documents-3-content": json.dumps(document_content("Accessibility")),
            # A cloned inline can submit a stale value; new rows must still append.
            "documents-3-display_order": "0",
            "documents-3-is_active": "",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    assert list(
        footer.documents.order_by("display_order").values_list("slug", "display_order")
    ) == [
        ("terms", 0),
        ("privacy", 1),
        ("imprint", 2),
        ("accessibility", 3),
    ]


def test_admin_inline_moves_existing_logo(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="footer-logo-order-admin",
        email="footer-logo-order@example.com",
        password="test-password",
    )
    footer = Footer.objects.create(name="Logo order footer")
    logos = [add_logo(footer, order=order) for order in range(3)]
    client.force_login(user)

    data = {
        "name": footer.name,
        **management_form("logos", 3, 3),
        **management_form("documents", 0, 0),
        "_save": "Save",
    }
    for index, logo in enumerate(logos):
        data |= {
            f"logos-{index}-id": str(logo.pk),
            f"logos-{index}-alt_text": logo.alt_text,
            f"logos-{index}-destination_url": "",
            f"logos-{index}-display_order": str(index),
            f"logos-{index}-is_active": "on",
        }
    # Move the last logo to the front: a direct swap would collide without the
    # deferred constraint and formset normalization.
    data["logos-2-display_order"] = "0"

    response = client.post(reverse("admin:footer_footer_change", args=[footer.pk]), data)

    assert response.status_code == 302
    assert list(footer.logos.order_by("display_order").values_list("pk", flat=True)) == [
        logos[2].pk,
        logos[0].pk,
        logos[1].pk,
    ]


def test_admin_warns_without_blocking_when_footer_may_overflow(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="footer-overflow-admin",
        email="footer-overflow@example.com",
        password="test-password",
    )
    footer = Footer.objects.create(name="Overflowing footer")
    for order in range(SOFT_LIMIT_DOCUMENTS + 1):
        add_document(footer, f"doc-{order}", order=order)
    client.force_login(user)

    publish_url = reverse("admin:footer_footer_publish", args=[footer.pk])
    confirmation = client.get(publish_url)
    response = client.post(publish_url)

    footer.refresh_from_db()
    assert "“More” menu".encode() in confirmation.content
    assert response.status_code == 302
    assert footer.is_published is True
