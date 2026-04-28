# Geodata Providers Summary

## Purpose

`geodata_providers` is the internal provider-management domain of the platform.
It is not the public read API. Its main responsibility is to manage provider
instances and keep Django state aligned with remote provider state.

Core resources:

- `GeodataEngine`
- `Workspace`
- `Store`
- `Layer`
- `Style`

## What Was Implemented

### 1. Provider domain model

The provider tree was established around:

- `GeodataEngine` as the top-level provider instance
- `Workspace` as the logical namespace/container
- `Store` as the data connection
- `Layer` as the published dataset
- `Style` as a first-class provider domain object

This gives the project a stable internal model independent from direct
GeoServer REST payloads.

### 2. Command/orchestration services

CRUD and sync orchestration was moved out of admin and API handlers into
service classes under:

- `services/commands/geodata_engine_service.py`
- `services/commands/workspace_service.py`
- `services/commands/store_service.py`
- `services/commands/layer_service.py`
- `services/commands/style_validation_service.py`

The main execution pattern is:

`pre-check -> remote mutate -> verify -> local persist/delete`

This makes admin flows and any future internal APIs reuse the same business
rules.

### 3. Remote-first consistency

The provider domain was hardened around a remote-first rule:

- Django should not treat a resource as created, updated, or deleted unless the
  remote provider operation was verified.
- Idempotent remote cases such as "already exists" or "already deleted" are
  treated explicitly.
- Sync remains the recovery mechanism when remote and local state drift.

### 4. Query service layer

Read/query logic was separated from command logic under:

- `services/queries/provider_query_service.py`
- `services/queries/workspace_query_service.py`
- `services/queries/layer_query_service.py`
- `services/queries/style_query_service.py`

This layer exists so consumer-facing APIs can read normalized provider data
without depending on admin logic or mutation services.

### 5. Style domain direction

The style domain was clarified with these decisions:

- `Style` is a top-level provider domain model.
- Style content is owned by Django.
- Provider sync is optional and capability-dependent.
- Style assignment to layers is a link/assignment concern, not a separate main
  domain.
- Cross-provider style assignment must be possible.

This prepares the system for Martin-like providers and future provider-aware
catalog responses.

## Current Architectural Position

At the moment, `geodata_providers` should be treated as:

- internal provider-management domain
- admin-facing operational surface
- shared backend service layer for future read APIs

It should **not** be treated as the public consumer-facing API surface.

## Key Decisions

- Provider CRUD belongs to admin/internal workflows, not public API exposure.
- Business logic must stay in services, not in admin classes or views.
- Query services are the boundary that public APIs should depend on.
- Catalog/API payloads should be derived from Django domain data, not raw
  GeoServer response shapes.

## Remaining Direction

Expected future work:

- keep admin workflows stable and provider-safe
- use query services as the base for catalog responses
- continue reducing duplicated orchestration logic
- keep public exposure limited to catalog-facing read APIs

## Source Notes

This summary compresses the decisions from the recent provider planning notes,
especially:

- `2026-04-20_geodata_providers_admin_crud_plan.md`
- `2026-04-21_geodata_providers_service_refactor_plan.md`
- `2026-04-22_catalog_query_services_plan.md`
- `2026-04-23_style_domain_provider_and_catalog_plan.md`
