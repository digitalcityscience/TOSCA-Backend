# TOSCA — Rename Geodata Engine to Geodata Providers

**Target app:** Rename `geodata_engine` to `geodata_providers`
**Last updated:** 18 March 2026

---

## Legend
- ✅ Done
- 🔄 In Progress
- ⬜ Not Started

---

## GOAL & SCOPE

Rename the `geodata_engine` app to `geodata_providers` throughout the entire codebase, including:

- App folder name
- All imports and references in code
- Model app_labels and related_names
- URL patterns and namespaces
- Admin registrations
- Templates and static files
- Tests and configurations
- Documentation

### What this is NOT
- This does not change the functionality or models
- This does not touch existing migrations (only create new ones if needed)
- This does not affect geo_console or other apps

### Rules
- Follow sync philosophy for any engine operations
- Update tasks.md status after each task
- Test thoroughly after changes

---

## TASKS

### Phase 1: Preparation
- ✅ Create this tasks.md file
- ✅ Find all references to `geodata_engine` in codebase

### Phase 2: Code Changes
- ✅ Rename app folder: `tosca_api/apps/geodata_engine/` → `tosca_api/apps/geodata_providers/`
- ✅ Update all import statements
- ✅ Update model app_labels
- ✅ Update URL configurations
- ✅ Update admin.py registrations
- ✅ Update settings and INSTALLED_APPS
- ✅ Update templates and static files
- ✅ Update admin templates and URLs
- ⬜ Update tests

### Phase 3: Database and Migrations
- ✅ Create new migrations if needed (without touching existing ones)
- ✅ Update any hardcoded app references in migrations

### Phase 4: Documentation and Cleanup
- ⬜ Update README and docs
- ⬜ Update any hardcoded references in scripts
- ⬜ Test the application
- ⬜ Update this tasks.md to ✅

---

## NOTES
- Use grep to find all references
- Be careful with model relationships and foreign keys
- Test admin panel, API, and console after changes