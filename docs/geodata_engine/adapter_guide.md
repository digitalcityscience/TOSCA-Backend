# Adapter Guide

## Goal
Add a new engine without changing API contracts.

## Steps
1. Implement a new adapter class under `tosca_api/apps/geodata_engine/adapters/`.
2. Inherit `EngineAdapter` and implement required methods:
   - `validate`
   - `sync`
   - `create_workspace`
   - `delete_workspace`
   - `create_store`
   - `publish_layer`
   - `unpublish_layer`
   - `preview_layer`
3. Register adapter in `adapters/registry.py` with `engine_type` key.
4. Ensure `GeodataEngine.engine_type` has the matching value.
5. Add tests for idempotent behavior and permission-safe usage.

## Rule
- Services must not import engine clients directly.
- Services only talk to adapters from registry.
