"""Builders for GeoSimulations test fixtures."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationModel,
    SimulationRun,
    Status,
)
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

HAMBURG = ZoneInfo("Europe/Berlin")
START = datetime(2026, 9, 8, 11, tzinfo=HAMBURG)
SHA = "sha256:" + "a" * 64


def org(slug: str) -> Organization:
    organization, _ = Organization.objects.get_or_create(slug=slug, defaults={"name": slug.upper()})
    OrganizationAppEntitlement.objects.get_or_create(
        organization=organization, app_label="geosimulations"
    )
    return organization


def simulation_model(organization, key="hamburg-bike-abm", **fields) -> SimulationModel:
    values = {"organization": organization, "key": key, "title": "Hamburg bike simulation"}
    values.update(fields)
    return SimulationModel.objects.create(**values)


def simulation_run(model, key="turn-queues-n1000-v2", **fields) -> SimulationRun:
    values = {
        "model": model,
        "key": key,
        "title": "1,000 cyclists to Hbf",
        "model_version": "0.2.0",
        "parameters": {"agents": 1000, "seed": 42},
        "seed": 42,
        "time_start": START,
        "time_end": START + timedelta(hours=3, minutes=14, seconds=29),
        "timezone": "Europe/Berlin",
        "bounds": [9.73, 53.395, 10.325, 53.739],
    }
    values.update(fields)
    return SimulationRun.objects.create(**values)


def published_run(organization, model_key="hamburg-bike-abm", run_key="published-run"):
    model = SimulationModel.objects.filter(key=model_key).first() or simulation_model(
        organization, key=model_key, status=Status.PUBLISHED
    )
    return simulation_run(model, key=run_key, status=Status.PUBLISHED)


def asset(organization, path="geosimulations/run/replay/trips.json.gz", **fields) -> SimulationAsset:
    values = {
        "storage_path": path,
        "original_name": "trips.json.gz",
        "mime": "application/json",
        "size": 3_043_001,
        "sha256": SHA,
        "owner_org": organization,
    }
    values.update(fields)
    return SimulationAsset.objects.create(**values)
