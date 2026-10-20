"""Portable simulation catalog seeds for installations without a runner.

A seed wraps the original, already validated run packages. Importing therefore
uses the same package pipeline as a runner upload and does not duplicate output
or storage logic here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import storage
from .importer import ImportResult, PackageImportError, import_package
from .models import SimulationModel, SimulationRun, Status


FORMAT = "tosca-simulation-seed"
FORMAT_VERSION = 1
MANIFEST_NAME = "seed.json"
COPY_BUFFER_SIZE = 1 << 20


class SeedBundleError(Exception):
    """The seed cannot be exported or imported safely."""


@dataclass(frozen=True)
class SeedImportResult:
    model: SimulationModel
    imported: tuple[SimulationRun, ...]
    skipped: tuple[SimulationRun, ...]
    omitted: tuple[dict, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_BUFFER_SIZE), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _model_data(model: SimulationModel) -> dict:
    return {
        "key": model.key,
        "title": model.title,
        "summary": model.summary,
        "description_content": model.description_content,
        "parameter_schema": model.parameter_schema,
        "default_parameters": model.default_parameters,
        # Kept as provenance only. The runner FK is deliberately never exported.
        "runner_version": model.runner_version,
    }


def _run_data(run: SimulationRun, package_name: str, checksum: str) -> dict:
    return {
        "key": run.key,
        "package": package_name,
        "sha256": checksum,
        "source_status": run.status,
        "title": run.title,
        "description_content": run.description_content,
        "visibility": run.visibility,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def export_seed(model: SimulationModel, destination: str | Path) -> tuple[Path, tuple[dict, ...]]:
    """Export ``model`` and every complete run with an archived source package."""
    destination = Path(destination)
    if destination.suffix.lower() != ".zip":
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / f"{model.key}-simulation-seed.zip"
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)

    runs = model.runs.select_related("source_package").order_by("created_at")
    omitted: list[dict] = []
    exported: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="geosim-seed-export-") as staging_name:
        staging = Path(staging_name)
        with zipfile.ZipFile(destination, "w") as bundle:
            for run in runs:
                if run.execution_state not in run.COMPLETE_STATES:
                    omitted.append(
                        {
                            "key": run.key,
                            "reason": f"execution state is {run.execution_state}",
                        }
                    )
                    continue
                if run.source_package_id is None:
                    raise SeedBundleError(
                        f"Complete run {run.key!r} has no archived source package; "
                        "it cannot be reproduced on another installation."
                    )
                member_name = f"runs/{run.key}.zip"
                local_package = staging / f"{run.pk}.zip"
                storage.download_to(
                    run.source_package.storage_alias,
                    run.source_package.storage_path,
                    local_package,
                )
                checksum = _sha256(local_package)
                if checksum != run.source_package.sha256:
                    raise SeedBundleError(
                        f"Checksum mismatch for the archived package of run {run.key!r}."
                    )
                # Nested ZIPs are already compressed; storing avoids costly and ineffective recompression.
                bundle.write(local_package, member_name, compress_type=zipfile.ZIP_STORED)
                exported.append(_run_data(run, member_name, checksum))

            manifest = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "model": _model_data(model),
                "runs": exported,
                "omitted_runs": omitted,
            }
            bundle.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return destination, tuple(omitted)


def _read_manifest(bundle: zipfile.ZipFile) -> dict:
    try:
        raw = bundle.read(MANIFEST_NAME)
    except KeyError as exc:
        raise SeedBundleError(f"Seed has no {MANIFEST_NAME}.") from exc
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedBundleError(f"Seed has an invalid {MANIFEST_NAME}: {exc}.") from exc
    if not isinstance(manifest, dict):
        raise SeedBundleError("Seed manifest must be a JSON object.")
    if manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
        raise SeedBundleError("Unsupported simulation seed format or version.")
    model = manifest.get("model")
    runs = manifest.get("runs")
    if not isinstance(model, dict) or not isinstance(model.get("key"), str):
        raise SeedBundleError("Seed manifest has no valid model key.")
    if not isinstance(runs, list):
        raise SeedBundleError("Seed manifest runs must be a list.")
    return manifest


def _upsert_model(info: dict, *, organization, user) -> SimulationModel:
    model = SimulationModel.objects.filter(key=info["key"]).first()
    if model is not None and model.organization_id != organization.pk:
        raise SeedBundleError(
            f"Simulation model {info['key']!r} belongs to another organization."
        )
    if model is None:
        model = SimulationModel(key=info["key"], organization=organization, created_by=user)
    model.title = info.get("title") or info["key"]
    model.summary = info.get("summary", "")
    model.description_content = info.get("description_content") or {"blocks": []}
    model.parameter_schema = info.get("parameter_schema")
    model.default_parameters = info.get("default_parameters") or {}
    model.runner = None
    model.runner_version = info.get("runner_version", "")
    model.status = Status.PUBLISHED
    model.save()
    return model


def _apply_run_metadata(run: SimulationRun, info: dict) -> None:
    run.title = info.get("title") or run.title
    run.description_content = info.get("description_content") or {"blocks": []}
    visibility = info.get("visibility")
    if visibility in SimulationRun.Visibility.values:
        run.visibility = visibility
    run.status = Status.PUBLISHED
    run.runner = None
    run.save()
    timestamps = {
        key: datetime.fromisoformat(value)
        for key, value in (
            ("created_at", info.get("created_at")),
            ("updated_at", info.get("updated_at")),
        )
        if value
    }
    if timestamps:
        SimulationRun.objects.filter(pk=run.pk).update(**timestamps)
        run.refresh_from_db(fields=list(timestamps))


def import_seed(
    source: str | Path,
    *,
    organization,
    user=None,
    replace: bool = False,
) -> SeedImportResult:
    """Import one portable seed; the model and every complete run become public."""
    source = Path(source)
    try:
        bundle = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SeedBundleError(f"Cannot open simulation seed {source}: {exc}.") from exc

    with bundle, tempfile.TemporaryDirectory(prefix="geosim-seed-import-") as staging_name:
        manifest = _read_manifest(bundle)
        model = _upsert_model(manifest["model"], organization=organization, user=user)
        staging = Path(staging_name)
        prepared: list[tuple[dict, Path]] = []
        seen_keys: set[str] = set()
        for number, info in enumerate(manifest["runs"]):
            if not isinstance(info, dict) or not isinstance(info.get("key"), str):
                raise SeedBundleError(f"Run entry {number} has no valid key.")
            if info["key"] in seen_keys:
                raise SeedBundleError(f"Duplicate run key {info['key']!r} in seed.")
            seen_keys.add(info["key"])
            member_name = info.get("package")
            if not isinstance(member_name, str) or member_name not in bundle.namelist():
                raise SeedBundleError(f"Run {info['key']!r} has no package in the seed.")
            local_package = staging / f"{number}.zip"
            with bundle.open(member_name) as package, local_package.open("wb") as target:
                shutil.copyfileobj(package, target, length=COPY_BUFFER_SIZE)
            if _sha256(local_package) != info.get("sha256"):
                raise SeedBundleError(f"Checksum mismatch for run {info['key']!r}.")
            prepared.append((info, local_package))

        imported: list[SimulationRun] = []
        skipped: list[SimulationRun] = []
        for info, package in prepared:
            try:
                result: ImportResult = import_package(
                    package,
                    organization=organization,
                    user=user,
                    publish=True,
                    replace=replace,
                    skip_existing=not replace,
                )
            except PackageImportError as exc:
                raise SeedBundleError(f"Run {info['key']!r} could not be imported: {exc}") from exc
            run = result.run
            if run is None or run.model_id != model.pk or run.key != info["key"]:
                raise SeedBundleError(f"Package for {info['key']!r} is not a run of model {model.key!r}.")
            if result.skipped and run.status != Status.PUBLISHED:
                raise SeedBundleError(
                    f"Existing run {run.key!r} is not published; re-run with --replace."
                )
            _apply_run_metadata(run, info)
            (skipped if result.skipped else imported).append(run)

    return SeedImportResult(
        model=model,
        imported=tuple(imported),
        skipped=tuple(skipped),
        omitted=tuple(manifest.get("omitted_runs") or ()),
    )
