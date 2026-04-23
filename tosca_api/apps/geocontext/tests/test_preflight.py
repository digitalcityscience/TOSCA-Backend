"""
Tests for the ``geocontext_preflight`` management command.

Covers Task 7.4 acceptance criteria:

- output is sorted and byte-stable across repeated runs
- the command does not mutate database rows
- rows whose content fails canonical validation are reported by ID
- optional ``--legacy-input-json`` dry-runs the HTML converter without
  touching the DB and lists media-bearing entries by their input ID
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from tosca_api.apps.geocontext.models import GeoContext

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="preflight_user", password="pw", email="p@p.test"
    )


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("geocontext_preflight", stdout=out, **kwargs)
    return out.getvalue()


# ----------------------------------------------------------------------
# Existing rows
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_preflight_clean_dataset_reports_no_failures(user):
    GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "ok"}}]},
        created_by=user,
    )
    GeoContext.objects.create(content={"blocks": []}, created_by=user)

    output = _run()
    assert "2 existing GeoContext row(s)" in output
    assert "0 fail canonical validation" in output
    assert "No failures detected" in output


@pytest.mark.django_db
def test_preflight_does_not_mutate_rows(user):
    ctx = GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "keep"}}]},
        created_by=user,
    )
    before_updated = ctx.updated_at
    before_content = ctx.content

    _run()
    _run()  # run twice to confirm idempotency

    ctx.refresh_from_db()
    assert ctx.content == before_content
    assert ctx.updated_at == before_updated


@pytest.mark.django_db
def test_preflight_output_is_stable_across_runs(user):
    for _ in range(3):
        GeoContext.objects.create(
            content={"blocks": [{"type": "paragraph", "data": {"text": "x"}}]},
            created_by=user,
        )
    first = _run()
    second = _run()
    assert first == second


@pytest.mark.django_db
def test_preflight_reports_invalid_rows_by_id(user):
    good = GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "ok"}}]},
        created_by=user,
    )
    # Insert an invalid row by bypassing save() validation.
    bad = GeoContext.objects.create(
        content={"blocks": []}, created_by=user
    )
    # Force invalid content post-save using update() so the model save()
    # normalization is skipped.
    GeoContext.objects.filter(pk=bad.pk).update(
        content={"blocks": [{"type": "paragraph"}]}  # missing data
    )

    output = _run()
    assert str(bad.id) in output
    assert "INVALID" in output
    assert "1 fail canonical validation" in output
    # Good row should not be listed as invalid.
    invalid_section = [
        line for line in output.splitlines() if line.startswith("  INVALID")
    ]
    assert len(invalid_section) == 1
    assert str(good.id) not in invalid_section[0]


# ----------------------------------------------------------------------
# Legacy input dry-run
# ----------------------------------------------------------------------


@pytest.mark.django_db
def test_preflight_dry_run_reports_media_blocked_ids(tmp_path, user):
    legacy = {
        "row-c": "<p>fine</p>",
        "row-a": "<p>hi</p><img src='x'>",
        "row-b": "<figure><figcaption>c</figcaption></figure>",
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    output = _run(legacy_input_json=str(path))

    # Media rows reported with sorted IDs.
    lines = [line.strip() for line in output.splitlines() if "row-" in line]
    media_lines = [line for line in lines if line.startswith("MEDIA")]
    assert len(media_lines) == 2
    ids_in_order = [line.split()[1].rstrip(":") for line in media_lines]
    assert ids_in_order == sorted(ids_in_order)
    assert "row-a" in ids_in_order
    assert "row-b" in ids_in_order
    # The clean row is not listed as a failure.
    assert not any(line.startswith("MEDIA row-c") for line in media_lines)


@pytest.mark.django_db
def test_preflight_dry_run_does_not_create_rows(tmp_path, user):
    legacy = {"row-1": "<p>hello</p>"}
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    before = GeoContext.objects.count()
    _run(legacy_input_json=str(path))
    assert GeoContext.objects.count() == before


@pytest.mark.django_db
def test_preflight_rejects_missing_legacy_file(tmp_path):
    missing = tmp_path / "nope.json"
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        _run(legacy_input_json=str(missing))


@pytest.mark.django_db
def test_preflight_rejects_malformed_legacy_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        _run(legacy_input_json=str(path))
