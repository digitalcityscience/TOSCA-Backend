"""Import a validated simulation package into TOSCA.

One service for every entry point: the ``import_simulation_package`` command,
the admin upload, and (Phase 4) the runner callback for runs users start.
Database rows are written in one transaction; files are uploaded to object
storage as they are produced and deleted again if the import fails, so a failed
import leaves nothing behind. A package holds a run, or a comparison of two runs
of the same model that were imported before.
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

from django.core.files.storage import storages
from django.db import transaction
from django.db.models import ProtectedError

from tosca_api.apps.core.models import MediaAsset

from .models import (
    SimulationAsset,
    SimulationComparison,
    SimulationModel,
    SimulationOutput,
    SimulationRun,
    Status,
)
from . import storage
from .packages import PackageLimits, ValidatedPackage, open_package
from .styles import build_style
from .tiles import TileBuildError, build_pmtiles

logger = logging.getLogger(__name__)

PMTILES_MIME = "application/vnd.pmtiles"
OUTPUT_FIELDS = {"role", "kind", "title", "path"}


# What a protected run is used by, in words for the import error.
_PROTECTING_USES = {
    "geostories.GeoStorySceneSimulation": "GeoStory scenes",
    "geosimulations.SimulationComparison": "comparisons",
}


class PackageImportError(Exception):
    """The package is valid but cannot be imported (conflicts, tiling failures)."""


@dataclass(frozen=True)
class ImportResult:
    run: SimulationRun | None
    model_created: bool
    warnings: tuple[str, ...]
    comparison: SimulationComparison | None = None
    skipped: bool = False

    @property
    def item(self) -> SimulationRun | SimulationComparison:
        """The imported run or comparison."""
        return self.comparison or self.run


def import_package(
    source: str | Path,
    *,
    organization,
    user=None,
    publish: bool = False,
    replace: bool = False,
    skip_existing: bool = False,
    into_run: SimulationRun | None = None,
    limits: PackageLimits | None = None,
) -> ImportResult:
    """Validate ``source`` (directory or ``.zip``) and import it as a run or comparison of ``organization``.

    ``skip_existing`` leaves a run or comparison with the same key untouched and
    returns it with ``skipped=True`` (idempotent seeding). ``into_run`` fills a
    run that was started in TOSCA and is waiting for its package (runner jobs);
    the package must be a run of that run's model with that run's key. Raises
    ``packages.PackageValidationError`` for invalid packages and
    ``PackageImportError`` when a valid package cannot be imported.
    """
    source = Path(source)
    with open_package(source, limits=limits) as package:
        if skip_existing and not replace:
            existing = _existing_item(package, organization)
            if existing is not None:
                return ImportResult(
                    run=existing if isinstance(existing, SimulationRun) else None,
                    comparison=existing if isinstance(existing, SimulationComparison) else None,
                    model_created=False,
                    warnings=(),
                    skipped=True,
                )
        importer = _PackageImporter(
            package, source, organization=organization, user=user, publish=publish
        )
        if into_run is not None:
            return importer.fill(into_run)
        return importer.run(replace=replace)


def _existing_item(package: ValidatedPackage, organization) -> SimulationRun | SimulationComparison | None:
    """The organization's run or comparison with this package's key, if any."""
    model = SimulationModel.objects.filter(
        key=package.manifest["model"]["key"], organization=organization
    ).first()
    if model is None:
        return None
    if package.kind == "comparison":
        return model.comparisons.filter(key=package.manifest["comparison"]["key"]).first()
    return model.runs.filter(key=package.manifest["run"]["key"]).first()


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
        if self.package.kind == "comparison":
            return self._run_comparison(replace=replace)
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

    def fill(self, run: SimulationRun) -> ImportResult:
        """Import the package into ``run``, a run started in TOSCA (no new run row)."""
        if self.package.kind != "run":
            raise PackageImportError("A started run needs a run package, not a comparison package.")
        if self.manifest["model"]["key"] != run.model.key:
            raise PackageImportError(
                f"The package is for model {self.manifest['model']['key']!r}, the run for {run.model.key!r}."
            )
        if self.manifest["run"]["key"] != run.key:
            raise PackageImportError(
                f"The package is for run {self.manifest['run']['key']!r}, expected {run.key!r}."
            )
        try:
            with transaction.atomic():
                run = SimulationRun.objects.select_for_update().get(pk=run.pk)
                if run.outputs.exists():
                    raise PackageImportError(f"Run {run.key!r} already has outputs.")
                info = self.manifest["run"]
                time = info["time"]
                run.model_version = self.manifest["model"]["version"]
                run.seed = run.seed if run.seed is not None else info.get("seed")
                run.time_start = datetime.fromisoformat(time["start"])
                run.time_end = datetime.fromisoformat(time["end"])
                run.timezone = time["timezone"]
                run.bounds = info.get("bounds")
                run.manifest = self.manifest
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
                run.execution_state = SimulationRun.ExecutionState.SUCCEEDED
                run.execution_error = ""
                run.save()
        except Exception:
            self._discard_uploads()
            raise
        return ImportResult(run=run, model_created=False, warnings=tuple(self.warnings))

    def _run_comparison(self, *, replace: bool) -> ImportResult:
        try:
            with transaction.atomic():
                model = self._existing_model()
                baseline, candidate = self._compared_runs(model)
                self._clear_existing_comparison(model, replace=replace)
                info = self.manifest["comparison"]
                comparison = SimulationComparison.objects.create(
                    model=model,
                    key=info["key"],
                    title=info["title"],
                    baseline_run=baseline,
                    candidate_run=candidate,
                    bounds=info.get("bounds"),
                    manifest=self.manifest,
                    created_by=self.user,
                )
                comparison.source_package = self._archive(comparison)
                for order, output in enumerate(self.manifest["outputs"]):
                    self._import_output(comparison, output, order)
                if self.publish:
                    comparison.status = Status.PUBLISHED
                comparison.save()
        except Exception:
            self._discard_uploads()
            raise
        return ImportResult(
            run=None, comparison=comparison, model_created=False, warnings=tuple(self.warnings)
        )

    # -- rows ---------------------------------------------------------------

    def _existing_model(self) -> SimulationModel:
        key = self.manifest["model"]["key"]
        model = SimulationModel.objects.filter(key=key).first()
        if model is None:
            raise PackageImportError(
                f"Simulation model {key!r} does not exist; import its runs before comparing them."
            )
        if model.organization_id != self.organization.pk:
            raise PackageImportError(f"Simulation model {key!r} belongs to another organization.")
        return model

    def _compared_runs(self, model: SimulationModel) -> tuple[SimulationRun, SimulationRun]:
        info = self.manifest["comparison"]
        runs = []
        for side in ("baseline", "candidate"):
            run_key = info[side]["run_key"]
            run = SimulationRun.objects.filter(model=model, key=run_key).first()
            if run is None:
                raise PackageImportError(
                    f"The {side} run {run_key!r} of {model.key!r} does not exist; import it first."
                )
            if self.publish and run.status != Status.PUBLISHED:
                raise PackageImportError(
                    f"The {side} run {run_key!r} is not published; publish both runs "
                    "before publishing their comparison."
                )
            runs.append(run)
        return runs[0], runs[1]

    def _clear_existing_comparison(self, model: SimulationModel, *, replace: bool) -> None:
        key = self.manifest["comparison"]["key"]
        existing = SimulationComparison.objects.filter(model=model, key=key).first()
        if existing is None:
            return
        if not replace:
            raise PackageImportError(
                f"Comparison {key!r} already exists for {model.key!r}; "
                "import with replace to overwrite it."
            )
        existing.delete()

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
        try:
            existing.delete()
        except ProtectedError as exc:
            users = sorted(
                {
                    _PROTECTING_USES.get(obj._meta.label, str(obj._meta.verbose_name_plural))
                    for obj in exc.protected_objects
                }
            )
            raise PackageImportError(
                f"Run {existing.key!r} is used by {' and '.join(users)}; remove it there "
                "before replacing it."
            ) from exc

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

    def _import_output(
        self, owner: SimulationRun | SimulationComparison, output: dict, order: int
    ) -> SimulationOutput:
        kind = output["kind"]
        metadata = {key: value for key, value in output.items() if key not in OUTPUT_FIELDS}
        fields = {"asset": None, "style": None, "legend": None, "data": None}
        if kind == SimulationOutput.Kind.VECTOR_LAYER:
            fields["asset"], tile_metadata = self._tile_layer(owner, output)
            metadata.update(tile_metadata)
            styled = build_style(
                output, self.package.file(output["path"]), labels=self.manifest.get("labels")
            )
            fields["style"], fields["legend"] = styled.style, styled.legend
            self.warnings.extend(styled.warnings)
        elif kind == SimulationOutput.Kind.TABLE:
            fields["data"] = _read_table(self.package.file(output["path"]))
        else:
            fields["asset"] = self._store(owner, self.package.file(output["path"]), output["path"])
        return SimulationOutput.objects.create(
            **_owner_fields(owner),
            role=output["role"],
            kind=kind,
            title=output["title"],
            display_order=order,
            metadata=metadata,
            **fields,
        )

    # -- files --------------------------------------------------------------

    def _tile_layer(
        self, owner: SimulationRun | SimulationComparison, output: dict
    ) -> tuple[SimulationAsset, dict]:
        role = output["role"]
        with tempfile.TemporaryDirectory(prefix="geosim-tiles-") as staging:
            target = Path(staging) / f"{role}.pmtiles"
            try:
                tiles = build_pmtiles(
                    self.package.file(output["path"]), output["layer"], role, target
                )
            except TileBuildError as exc:
                raise PackageImportError(str(exc)) from exc
            asset = self._store(owner, target, f"layers/{role}.pmtiles", mime=PMTILES_MIME)
        return asset, {
            "source_layer": role,
            "minzoom": tiles.minzoom,
            "maxzoom": tiles.maxzoom,
            "bounds": tiles.bounds,
            "features": tiles.features,
        }

    def _archive(self, owner: SimulationRun | SimulationComparison) -> SimulationAsset:
        """Keep the package itself; a directory source is zipped first."""
        if self.source.is_file():
            return self._store(owner, self.source, "package.zip", mime="application/zip")
        with tempfile.TemporaryDirectory(prefix="geosim-archive-") as staging:
            archive_path = Path(staging) / "package.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(self.package.root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(self.package.root).as_posix())
            return self._store(owner, archive_path, "package.zip", mime="application/zip")

    def _store(
        self,
        owner: SimulationRun | SimulationComparison,
        path: Path,
        name: str,
        *,
        mime: str | None = None,
    ) -> SimulationAsset:
        guessed_type, encoding = mimetypes.guess_type(name)
        content_type = mime or guessed_type or "application/octet-stream"
        folder = f"comparisons/{owner.pk}" if isinstance(owner, SimulationComparison) else str(owner.pk)
        key = f"geosimulations/{self.organization.slug}/{folder}/{name}"
        # A gzip replay is stored with Content-Encoding, so browsers hand over JSON.
        stored_name = storage.upload_file(
            self.alias, key, path, content_type=content_type, content_encoding=encoding
        )
        self.uploaded.append((self.alias, stored_name))
        return SimulationAsset.objects.create(
            **_owner_fields(owner),
            storage_path=stored_name,
            original_name=Path(name).name,
            mime=content_type,
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


def _owner_fields(owner: SimulationRun | SimulationComparison) -> dict:
    if isinstance(owner, SimulationComparison):
        return {"comparison": owner}
    return {"run": owner}


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
