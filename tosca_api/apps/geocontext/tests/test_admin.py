"""
Tests for GeoContext Django admin Editor.js authoring.
"""

import json
import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse
from django.test import override_settings
from PIL import Image

from tosca_api.apps.geocontext.admin import GeoContextAdmin, _extract_plain_text
from tosca_api.apps.geocontext.forms import GeoContextAdminForm
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geocontext.widgets import EditorJsWidget

User = get_user_model()


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="admin_geocontext", password="pw", email="a@a.test"
    )


@pytest.fixture
def admin_client(client, superuser):
    client.force_login(superuser)
    return client


def test_widget_media_includes_vendored_assets():
    media = EditorJsWidget().media
    js = list(media._js)
    assert "geocontext/editorjs/vendor/editorjs.umd.js" in js
    assert "geocontext/editorjs/vendor/header.umd.js" in js
    assert "geocontext/editorjs/vendor/list.umd.js" in js
    assert "geocontext/editorjs/vendor/quote.umd.js" in js
    assert "geocontext/editorjs/vendor/delimiter.umd.js" in js
    assert "geocontext/editorjs/vendor/code.umd.js" in js
    assert "geocontext/editorjs/vendor/image.umd.js" in js
    assert "geocontext/editorjs/init.js" in js
    assert "geocontext/editorjs/editor.css" in media._css["all"]


def test_widget_media_does_not_reference_any_cdn():
    media = EditorJsWidget().media
    rendered = str(media)
    for needle in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs."):
        assert needle not in rendered


def test_vendor_license_files_present():
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "static" / "geocontext" / "editorjs" / "vendor"
    for name in ("editorjs", "header", "list", "quote", "delimiter", "code", "image"):
        path = base / f"LICENSE.{name}"
        assert path.exists(), f"missing {path}"
        assert path.stat().st_size > 0


def test_editorjs_admin_assets_do_not_reference_cdn():
    from pathlib import Path

    static_root = Path(__file__).resolve().parents[1] / "static" / "geocontext" / "editorjs"
    for path in static_root.rglob("*"):
        if path.is_file():
            text = path.read_text(errors="ignore")
            for needle in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs."):
                assert needle not in text


@pytest.mark.django_db
def test_admin_change_form_renders_json_textarea(admin_client, superuser):
    ctx = GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "Hi"}}]},
        created_by=superuser,
    )
    url = reverse("admin:geocontext_geocontext_change", args=[ctx.id])
    response = admin_client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "data-editorjs-target" in html
    assert "geocontext/editorjs/vendor/editorjs.umd.js" in html
    assert "geocontext/editorjs/init.js" in html
    assert "paragraph" in html and "blocks" in html


@pytest.mark.django_db
def test_admin_add_form_renders_empty_canonical_json(admin_client):
    url = reverse("admin:geocontext_geocontext_add")
    response = admin_client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "data-editorjs-target" in html
    assert "blocks" in html


@pytest.mark.django_db
def test_admin_form_parses_json_and_persists(admin_client, superuser):
    url = reverse("admin:geocontext_geocontext_add")
    payload = {
        "content": json.dumps(
            {"blocks": [{"type": "paragraph", "data": {"text": "Saved"}}]}
        ),
        "created_by": str(superuser.id),
        "_save": "Save",
    }
    response = admin_client.post(url, payload, follow=True)
    assert response.status_code == 200
    ctx = GeoContext.objects.get(created_by=superuser)
    assert ctx.content == {
        "blocks": [{"type": "paragraph", "data": {"text": "Saved"}}]
    }


@pytest.mark.django_db
def test_admin_form_persists_image_block_with_caption_alt_fallback(
    admin_client, superuser, tmp_path
):
    image_buf = io.BytesIO()
    Image.new("RGB", (240, 240), color=(20, 80, 140)).save(image_buf, format="PNG")

    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        storage_path = default_storage.save(
            "geocontext/editorjs/context-id/admin.png",
            ContentFile(image_buf.getvalue()),
        )
        url = reverse("admin:geocontext_geocontext_add")
        payload = {
            "content": json.dumps(
                {
                    "blocks": [
                        {
                            "type": "image",
                            "data": {
                                "file": {
                                    "url": f"/media/{storage_path}",
                                    "mime": "image/jpeg",
                                    "width": 1,
                                    "height": 1,
                                },
                                "caption": "<strong>Admin caption</strong>",
                                "withBorder": True,
                                "withBackground": False,
                                "stretched": True,
                            },
                        }
                    ]
                }
            ),
            "created_by": str(superuser.id),
            "_save": "Save",
        }
        response = admin_client.post(url, payload, follow=True)

    assert response.status_code == 200
    ctx = GeoContext.objects.get(created_by=superuser)
    block = ctx.content["blocks"][0]
    assert block["type"] == "image"
    assert block["data"]["alt"] == "Admin caption"
    assert block["data"]["caption"] == "<strong>Admin caption</strong>"
    assert block["data"]["file"]["mime"] == "image/png"
    assert block["data"]["file"]["width"] == 240
    assert block["data"]["file"]["height"] == 240


@pytest.mark.django_db
def test_admin_form_shows_image_block_validation_errors(
    admin_client, superuser, tmp_path
):
    image_buf = io.BytesIO()
    Image.new("RGB", (240, 240), color=(20, 80, 140)).save(image_buf, format="PNG")

    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        storage_path = default_storage.save(
            "geocontext/editorjs/context-id/missing-alt.png",
            ContentFile(image_buf.getvalue()),
        )
        url = reverse("admin:geocontext_geocontext_add")
        payload = {
            "content": json.dumps(
                {
                    "blocks": [
                        {
                            "type": "image",
                            "data": {"file": {"url": f"/media/{storage_path}"}},
                        }
                    ]
                }
            ),
            "created_by": str(superuser.id),
            "_save": "Save",
        }
        response = admin_client.post(url, payload)

    assert response.status_code == 200
    assert b"requires non-empty" in response.content
    assert not GeoContext.objects.filter(created_by=superuser).exists()


@pytest.mark.django_db
def test_admin_form_rejects_malformed_json(admin_client, superuser):
    url = reverse("admin:geocontext_geocontext_add")
    payload = {
        "content": "{not json",
        "created_by": str(superuser.id),
        "_save": "Save",
    }
    response = admin_client.post(url, payload)
    assert response.status_code == 200
    assert b"valid JSON" in response.content
    assert not GeoContext.objects.filter(created_by=superuser).exists()


def test_form_clean_handles_empty_and_dict_inputs():
    form = GeoContextAdminForm()
    assert form.clean_content.__self__ is form  # sanity
    for empty in ("", None, {}):
        form.cleaned_data = {"content": empty}
        assert form.clean_content() == {"blocks": []}
    form.cleaned_data = {"content": {"blocks": [{"type": "paragraph", "data": {"text": "x"}}]}}
    assert form.clean_content() == {
        "blocks": [{"type": "paragraph", "data": {"text": "x"}}]
    }


def test_extract_plain_text_handles_block_variants():
    doc = {
        "blocks": [
            {"type": "header", "data": {"text": "Title", "level": 2}},
            {"type": "paragraph", "data": {"text": "Hello world"}},
            {
                "type": "list",
                "data": {
                    "style": "unordered",
                    "items": [
                        {"content": "one", "items": []},
                        {"content": "two", "items": []},
                    ],
                    "meta": {},
                },
            },
            {"type": "quote", "data": {"text": "wisdom", "caption": "src"}},
            {"type": "code", "data": {"code": "x=1"}},
            {"type": "delimiter", "data": {}},
        ]
    }
    text = _extract_plain_text(doc)
    for needle in ("Title", "Hello world", "one", "two", "wisdom", "x=1"):
        assert needle in text


def test_extract_plain_text_empty_document():
    assert _extract_plain_text({"blocks": []}) == ""
    assert _extract_plain_text(None) == ""


@pytest.mark.django_db
def test_changelist_preview_empty_and_truncated(superuser):
    empty = GeoContext.objects.create(content={"blocks": []}, created_by=superuser)
    long_text = "alpha bravo " * 20
    populated = GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": long_text}}]},
        created_by=superuser,
    )
    admin = GeoContextAdmin(GeoContext, None)
    assert admin.content_preview(empty) == "(empty)"
    preview = admin.content_preview(populated)
    assert preview.endswith("...")
    assert len(preview) <= 78
