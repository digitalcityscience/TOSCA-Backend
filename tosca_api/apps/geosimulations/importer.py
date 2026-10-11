"""Import a validated simulation package into TOSCA.

One service for every entry point: the ``import_simulation_package`` command,
the admin upload, and (Phase 4) the runner callback for runs users start.
Database rows are written in one transaction; files are uploaded to object
storage as they are produced and deleted again if the import fails, so a failed
import leaves nothing behind.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import math
import mimetypes
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.core.files import File
from django.core.files.storage import storages
from django.db import transaction

from tosca_api.apps.core.models import MediaAsset

from .models import (
    SimulationAsset,
    SimulationModel,
    SimulationOutput,
    SimulationRun,
    Status,
)
from .packages import PackageLimits, ValidatedPackage, open_package
from .styles import build_style
from .tiles import TileBuildError, build_pmtiles

logger = logging.getLogger(__name__)

PMTILES_MIME = "application/vnd.pmtiles"
OUTPUT_FIELDS = {"role", "kind", "title", "path"}


class PackageImportError(Exception):
    """The package is valid but cannot be imported (conflicts, tiling failures)."""


@dataclass(frozen=True)
class ImportResult:
    run: SimulationRun
    model_created: bool
    warnings: tuple[str, ...]


def import_package(
    source: str | Path,
    *,
    organization,
    user=None,
    publish: bool = False,
    replace: bool = False,
    limits: PackageLimits | None = None,
) -> ImportResult:
    """Validate ``source`` (directory or ``.zip``) and import it as a new run of ``organization``.

    Raises ``packages.PackageValidationError`` for invalid packages and
    ``PackageImportError`` when a valid package cannot be imported.
    """
    source = Path(source)
    with open_package(source, limits=limits) as package:
        importer = _PackageImporter(
            package, source, organization=organization, user=user, publish=publish
        )
        return importer.run(replace=replace)


class _PackageImporter:
    def __init__(self, package: ValidatedPackage, source: Path, *, organization, user, publish):
        self.package = package
        self.manifest = package.manifest
        self.source = source
        self.organization = organization
        self.user = user
        self.publish = publish
        self.alias = (
            MediaAsset.StorageAlias.PUBLIC if publish else MediaAsset.StorageAlias.DEFAULT
        )
        self.uploaded: list[tuple[str, str]] = []
        self.warnings: list[str] = [str(warning) for warning in package.warnings]

    def run(self, *, replace: bool) -> ImportResult:
        try:
            with transaction.atomic():
                model, model_created = self._model()
                self._clear_existing_run(model, replace=replace)
                run = self._create_run(model)
                run.source_package = self._archive(run)
                outputs = [
                    self._import_output(run, output, order)
                    for order, output in enumerate(self.manifest["outputs"])
                ]
                kpis = next(
                    (_kpis(output.data) for output in outputs if output.role == "kpis"), None
                )
                if kpis:
                    run.kpis = kpis
                if self.publish:
                    run.status = Status.PUBLISHED
                    if model.status != Status.PUBLISHED:
                        model.status = Status.PUBLISHED
                        model.save()
                run.save()
        except Exception:
            self._discard_uploads()
            raise
        return ImportResult(
            run=run,
            model_created=model_created,
            warnings=tuple(self.warnings),
        )

    # -- rows ---------------------------------------------------------------

    def _model(self) -> tuple[SimulationModel, bool]:
        info = self.manifest["model"]
        model = SimulationModel.objects.select_for_update().filter(key=info["key"]).first()
        if model is None:
            model = SimulationModel.objects.create(
                key=info["key"],
                title=info["title"],
                organization=self.organization,
                created_by=self.user,
            )
            return model, True
        if model.organization_id != self.organization.pk:
            raise PackageImportError(
                f"Simulation model {info['key']!r} belongs to another organization."
            )
        return model, False

    def _clear_existing_run(self, model: SimulationModel, *, replace: bool) -> None:
        existing = SimulationRun.objects.filter(model=model, key=self.manifest["run"]["key"]).first()
        if existing is None:
            return
        if not replace:
            raise PackageImportError(
                f"Run {existing.key!r} already exists for {model.key!r}; "
                "import with replace to overwrite it."
            )
        # Its stored files are removed after commit (signals.delete_stored_object),
        # so a failed re-import keeps the old run intact.
        existing.delete()

    def _create_run(self, model: SimulationModel) -> SimulationRun:
        info = self.manifest["run"]
        time = info["time"]
        return SimulationRun.objects.create(
            model=model,
            key=info["key"],
            title=info["title"],
            model_version=self.manifest["model"]["version"],
            parameters=info["parameters"],
            seed=info.get("seed"),
            time_start=datetime.fromisoformat(time["start"]),
            time_end=datetime.fromisoformat(time["end"]),
            timezone=time["timezone"],
            bounds=info.get("bounds"),
            manifest=self.manifest,
            execution_state=SimulationRun.ExecutionState.IMPORTED,
            created_by=self.user,
        )

    def _import_output(self, run: SimulationRun, output: dict, order: int) -> SimulationOutput:
        kind = output["kind"]
        metadata = {key: value for key, value in output.items() if key not in OUTPUT_FIELDS}
        fields = {"asset": None, "style": None, "legend": None, "data": None}
        if kind == SimulationOutput.Kind.VECTOR_LAYER:
            fields["asset"], tile_metadata = self._tile_layer(run, output)
            metadata.update(tile_metadata)
            styled = build_style(
                output, self.package.file(output["path"]), labels=self.manifest.get("labels")
            )
            fields["style"], fields["legend"] = styled.style, styled.legend
            self.warnings.extend(styled.warnings)
        elif kind == SimulationOutput.Kind.TABLE:
            fields["data"] = _read_table(self.package.file(output["path"]))
        else:
            fields["asset"] = self._store(run, self.package.file(output["path"]), output["path"])
        return SimulationOutput.objects.create(
            run=run,
            role=output["role"],
            kind=kind,
            title=output["title"],
            display_order=order,
            metadata=metadata,
            **fields,
        )

    # -- files --------------------------------------------------------------

    def _tile_layer(self, run: SimulationRun, output: dict) -> tuple[SimulationAsset, dict]:
        role = output["role"]
        with tempfile.TemporaryDirectory(prefix="geosim-tiles-") as staging:
            target = Path(staging) / f"{role}.pmtiles"
            try:
                tiles = build_pmtiles(
                    self.package.file(output["path"]), output["layer"], role, target
                )
            except TileBuildError as exc:
                raise PackageImportError(str(exc)) from exc
            asset = self._store(run, target, f"layers/{role}.pmtiles", mime=PMTILES_MIME)
        return asset, {
            "source_layer": role,
            "minzoom": tiles.minzoom,
            "maxzoom": tiles.maxzoom,
            "bounds": tiles.bounds,
            "features": tiles.features,
        }

    def _archive(self, run: SimulationRun) -> SimulationAsset:
        """Keep the package itself; a directory source is zipped first."""
        if self.source.is_file():
            return self._store(run, self.source, "package.zip", mime="application/zip")
        with tempfile.TemporaryDirectory(prefix="geosim-archive-") as staging:
            archive_path = Path(staging) / "package.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(self.package.root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(self.package.root).as_posix())
            return self._store(run, archive_path, "package.zip", mime="application/zip")

    def _store(self, run: SimulationRun, path: Path, name: str, *, mime: str | None = None) -> SimulationAsset:
        storage = storages[self.alias]
        key = f"geosimulations/{self.organization.slug}/{run.pk}/{name}"
        with path.open("rb") as handle:
            stored_name = storage.save(key, File(handle, name=Path(name).name))
        self.uploaded.append((self.alias, stored_name))
        return SimulationAsset.objects.create(
            run=run,
            storage_path=stored_name,
            original_name=Path(name).name,
            mime=mime or mimetypes.guess_type(name)[0] or "application/octet-stream",
            size=path.stat().st_size,
            sha256=_sha256(path),
            storage_alias=self.alias,
            owner_org=self.organization,
        )

    def _discard_uploads(self) -> None:
        for alias, name in self.uploaded:
            try:
                storages[alias].delete(name)
            except Exception:
                logger.exception("Could not remove %s after a failed simulation import", name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _typed(value: str):
    """Numbers become numbers; NaN/inf stay text because JSON (and jsonb) cannot hold them."""
    if value == "":
        return None
    for cast in (int, float):
        try:
            number = cast(value)
        except ValueError:
            continue
        return number if math.isfinite(number) else value
    return value


def _read_table(path: Path) -> dict:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        columns = next(reader)
        rows = [[_typed(cell) for cell in row] for row in reader]
    return {"columns": columns, "rows": rows}


def _kpis(table: dict | None) -> dict:
    """``metric,value[,unit]`` tables become ``{metric: value}``."""
    if not table or not {"metric", "value"} <= set(table["columns"]):
        return {}
    metric, value = table["columns"].index("metric"), table["columns"].index("value")
    return {row[metric]: row[value] for row in table["rows"] if row[metric] is not None}
