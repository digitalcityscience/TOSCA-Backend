# Catalog API Summary

## Purpose

`catalog_api` is the public read/query surface for frontend consumers.
It is not a CRUD interface and it should not contain provider mutation logic.

Its role is to expose a clean, stable catalog view of provider data for the
Web GIS frontend.

## Main Goal

The first implementation goal was pragmatic:

- keep the frontend working with minimal changes
- replace direct GeoServer REST metadata reads with Django endpoints
- preserve the response shapes the frontend already expects where useful

At the same time, the longer-term goal is to move toward a cleaner,
provider-aware catalog contract.

## What Was Implemented

### 1. Separate catalog app

`catalog_api` was created as a distinct app instead of mixing read concerns into
`geodata_providers`.

This establishes a clear split:

- `geodata_providers` = internal provider management
- `catalog_api` = public catalog read surface

### 2. Query-service-based design

`catalog_api` is designed to depend on read/query services from
`geodata_providers/services/queries/` instead of:

- calling provider admin logic
- using mutation services
- relying on raw GeoServer REST responses

This keeps the public API independent from internal CRUD workflows.

### 3. Catalog v1 direction

The v1 catalog work focused on a compatibility layer for the existing frontend.

Main ideas:

- frontend metadata calls should go to Django
- tile/WMS/WMTS access may still point to GeoServer directly
- visible catalog data should be filtered at Django level
- only active providers and visible/published layers should appear

### 4. Initial endpoint set

The catalog planning converged on a small read-oriented surface such as:

- workspace list
- provider list
- global layer list
- workspace layer list
- layer info/detail
- style detail

The implemented URL structure is versioned and read-oriented under
`/api/v1/catalog/`.

The provider bootstrap endpoint is available at `/api/v1/catalog/providers`.
It returns active providers only, with a minimal public shape containing
`name` and `base_url`.

## Contract Direction

The catalog contract should:

- return frontend-usable metadata
- avoid raw GeoServer-specific noise where possible
- expose style information from Django’s style domain
- remain provider-aware without leaking internal provider orchestration details

In practice, that means:

- keep payloads stable
- normalize provider/workspace/layer/style metadata
- avoid using provider CRUD endpoints as frontend dependencies

## Key Decisions

- `catalog_api` is the only external/public gateway for provider-derived read
  data.
- It must stay read-only.
- It should reuse query services instead of duplicating query logic.
- It should be versioned from the beginning.
- It should evolve from v1 compatibility toward a cleaner, generalized v2
  contract.

## Relationship To Frontend

The catalog planning was driven by the current frontend expectations described
in:

- `geoserver.ts`
- `map.ts`
- `catatlog_api.md`

The important principle is:

- frontend-facing metadata comes from Django
- map tile delivery can still come from provider map services

## Current Architectural Position

At this stage, `catalog_api` should be treated as:

- the public read API
- the stable boundary for frontend catalog consumption
- the layer that translates internal provider state into consumer-facing
  responses

## Remaining Direction

Expected future work:

- continue tightening the v1 response contract
- expand style-aware layer detail responses
- generalize provider-aware catalog behavior for future v2 work
- keep docs and frontend expectations aligned

## Source Notes

This summary compresses the decisions from the recent catalog planning notes,
especially:

- `2026-04-21_catalog_api_plan.md`
- `2026-04-22_catalog_api_v1_and_generalization_plan.md`
- `2026-04-22_catalog_query_services_plan.md`
- `2026-04-23_style_domain_provider_and_catalog_plan.md`
- `catatlog_api.md`
