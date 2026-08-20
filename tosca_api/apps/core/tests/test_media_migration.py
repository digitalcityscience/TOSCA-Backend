from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.storage import FileSystemStorage, storages
from django.core.management import call_command
from django.core.management.base import CommandError

from tosca_api.apps.core import media_migration as mm
from tosca_api.apps.core.media_migration import MediaMigrator
from tosca_api.apps.core.models import MediaAsset


def _write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make(tmp_path: Path):
    source = tmp_path / "source"
    default_root = tmp_path / "default"
    public_root = tmp_path / "public"
    for directory in (source, default_root, public_root):
        directory.mkdir()
    backends = {
        "default": FileSystemStorage(location=str(default_root)),
        "media_public": FileSystemStorage(location=str(public_root)),
    }
    migrator = MediaMigrator(
        source_root=source, storage_for_alias=lambda alias: backends[alias]
    )
    return migrator, source, backends, default_root, public_root


def test_routes_by_prefix_and_copies_bytes(tmp_path):
    migrator, source, backends, _, _ = _make(tmp_path)
    _write(source, "geocontext/editorjs/x/a.png", b"public-bytes")
    _write(source, "other/b.txt", b"private-bytes")

    entries = migrator.migrate()

    by_path = {e.path: e for e in entries}
    assert by_path["geocontext/editorjs/x/a.png"].alias == "media_public"
    assert by_path["other/b.txt"].alias == "default"
    assert all(e.action == mm.ACTION_MIGRATED and e.status == mm.STATUS_OK for e in entries)
    with backends["media_public"].open("geocontext/editorjs/x/a.png") as fh:
        assert fh.read() == b"public-bytes"
    with backends["default"].open("other/b.txt") as fh:
        assert fh.read() == b"private-bytes"


def test_dry_run_writes_nothing(tmp_path):
    migrator, source, backends, _, _ = _make(tmp_path)
    _write(source, "geocontext/editorjs/x/a.png", b"public-bytes")

    entries = migrator.migrate(dry_run=True)

    assert entries[0].action == mm.ACTION_WOULD_MIGRATE
    assert not backends["media_public"].exists("geocontext/editorjs/x/a.png")


def test_idempotent_rerun_skips_present_objects(tmp_path):
    migrator, source, _, _, _ = _make(tmp_path)
    _write(source, "other/b.txt", b"private-bytes")

    migrator.migrate()
    second = migrator.migrate()

    assert second[0].action == mm.ACTION_SKIPPED
    assert second[0].status == mm.STATUS_OK


def test_size_mismatch_is_flagged_and_only_overwritten_on_request(tmp_path):
    migrator, source, backends, _, _ = _make(tmp_path)
    from django.core.files.base import ContentFile

    backends["default"].save("other/b.txt", ContentFile(b"old"))
    _write(source, "other/b.txt", b"brand-new-longer-bytes")

    flagged = migrator.migrate()[0]
    assert flagged.action == mm.ACTION_SKIPPED
    assert flagged.status == mm.STATUS_MISMATCH
    with backends["default"].open("other/b.txt") as fh:
        assert fh.read() == b"old"  # left untouched

    replaced = migrator.migrate(overwrite=True)[0]
    assert replaced.action == mm.ACTION_OVERWRITTEN
    assert replaced.status == mm.STATUS_OK
    with backends["default"].open("other/b.txt") as fh:
        assert fh.read() == b"brand-new-longer-bytes"


class _FailingStorage:
    def exists(self, name):
        return False

    def size(self, name):  # pragma: no cover - not reached in this test
        return 0

    def save(self, name, content):
        raise RuntimeError("boom")

    def delete(self, name):  # pragma: no cover - not reached in this test
        pass


def test_partial_failure_is_isolated(tmp_path):
    _, source, backends, _, _ = _make(tmp_path)
    _write(source, "geocontext/editorjs/x/a.png", b"public-bytes")
    _write(source, "other/b.txt", b"private-bytes")
    routes = {"default": backends["default"], "media_public": _FailingStorage()}
    migrator = MediaMigrator(source_root=source, storage_for_alias=lambda alias: routes[alias])

    entries = {e.path: e for e in migrator.migrate()}

    assert entries["geocontext/editorjs/x/a.png"].status == mm.STATUS_FAILED
    assert entries["other/b.txt"].status == mm.STATUS_OK
    assert backends["default"].exists("other/b.txt")


def test_rollback_deletes_created_objects(tmp_path):
    migrator, source, backends, _, _ = _make(tmp_path)
    _write(source, "geocontext/editorjs/x/a.png", b"public-bytes")

    migrated = migrator.migrate()
    assert backends["media_public"].exists("geocontext/editorjs/x/a.png")

    undone = migrator.rollback(migrated)

    assert undone[0].action == mm.ACTION_DELETED
    assert not backends["media_public"].exists("geocontext/editorjs/x/a.png")


def test_rollback_leaves_overwritten_objects_in_place(tmp_path):
    migrator, source, backends, _, _ = _make(tmp_path)
    from django.core.files.base import ContentFile

    backends["default"].save("other/b.txt", ContentFile(b"old"))
    _write(source, "other/b.txt", b"replacement")

    overwritten = migrator.migrate(overwrite=True)
    assert overwritten[0].action == mm.ACTION_OVERWRITTEN

    undone = migrator.rollback(overwritten)

    assert undone == []  # overwritten objects are not rollback candidates
    assert backends["default"].exists("other/b.txt")


def test_rollback_dry_run_reports_without_deleting(tmp_path):
    migrator, source, backends, _, _ = _make(tmp_path)
    _write(source, "geocontext/editorjs/x/a.png", b"public-bytes")
    migrated = migrator.migrate()

    planned = migrator.rollback(migrated, dry_run=True)

    assert planned[0].action == mm.ACTION_WOULD_DELETE
    assert backends["media_public"].exists("geocontext/editorjs/x/a.png")


def test_report_json_roundtrips_and_csv_has_header(tmp_path):
    migrator, source, _, _, _ = _make(tmp_path)
    _write(source, "other/b.txt", b"private-bytes")
    entries = migrator.migrate()

    restored = mm.load_report(mm.report_to_json(entries))
    assert restored == entries

    csv_text = mm.report_to_csv(entries)
    assert csv_text.splitlines()[0] == "path,alias,source_size,action,status,detail"


@pytest.mark.django_db
def test_verify_tracked_assets_reports_only_missing(tmp_path):
    migrator, _, backends, _, _ = _make(tmp_path)
    from django.core.files.base import ContentFile

    backends["media_public"].save(
        "geocontext/editorjs/z/present.png", ContentFile(b"here")
    )
    for name in ("present.png", "missing.png"):
        MediaAsset.objects.create(
            storage_path=f"geocontext/editorjs/z/{name}",
            original_name=name,
            mime="image/png",
            width=1,
            height=1,
            size=4,
        )

    missing = migrator.verify_tracked_assets()

    assert [e.path for e in missing] == ["geocontext/editorjs/z/missing.png"]
    assert missing[0].action == mm.ACTION_MISSING


def test_command_dry_run_writes_report(tmp_path):
    source = tmp_path / "src"
    (source / "geocontext/editorjs/c").mkdir(parents=True)
    (source / "geocontext/editorjs/c/cmd.png").write_bytes(b"cmd-bytes")
    report = tmp_path / "report.json"

    call_command(
        "migrate_media_to_storage",
        "--source",
        str(source),
        "--dry-run",
        "--report",
        str(report),
    )

    assert report.is_file()
    loaded = mm.load_report(report.read_text(encoding="utf-8"))
    assert loaded[0].action == mm.ACTION_WOULD_MIGRATE
    # Nothing was written to the real destination.
    assert not storages["media_public"].exists("geocontext/editorjs/c/cmd.png")


def test_command_rollback_missing_report_raises(tmp_path):
    with pytest.raises(CommandError, match="Rollback report not found"):
        call_command(
            "migrate_media_to_storage", "--rollback", str(tmp_path / "nope.json")
        )
