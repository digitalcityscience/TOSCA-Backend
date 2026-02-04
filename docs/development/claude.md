# Claude Agent Instructions – Geo Data Engine POC

# Django Admin + PostGIS + GeoServer (Engine-Agnostic Design)

---

## 🎯 Mission

Implement a **Django-admin–driven geospatial data management backend**.

This system:

- Uses **GeoServer as the publishing engine for the POC**
- Manages **workspaces, stores, and layers in Django**
- Treats GeoServer as a **replaceable backend engine**
- Uses **PostGIS as the single source of truth for data**

> Core idea: **Django manages data and intent, engines only publish.**

---

## 🧠 NON-NEGOTIABLE PRINCIPLES

### 1. Django is the Control Plane

Django owns:

- Workspaces
- Data stores (PostGIS connections + schema)
- Layers (logical datasets)
- Publishing state

GeoServer:

- Never owns data
- Never decides structure
- Only publishes what Django tells it to publish

---

### 2. Engine-Agnostic ≠ Engine-Abstracted Everywhere

Important clarification:

- **Domain model MUST be engine-agnostic**
- **Publishing implementation IS engine-specific**
- For the POC:
  - GeoServer is the **only implemented engine**
  - Others (pygeoapi, Martin, pg_tileserv) are future targets

DO NOT:

- Over-engineer adapters
- Create generic publishing frameworks
- Guess future APIs

DO:

- Keep models clean
- Keep publishing logic isolated
- Make future replacement _possible_, not implemented

---

## 🧱 Core Domain Concepts (MANDATORY)

These concepts MUST exist as Django models.

### Workspace

- Logical grouping of data (e.g. `mobility`, `environment`)
- Maps to:
  - GeoServer workspace (today)
  - PostGIS schema / logical group (future engines)

### Store

- Represents a **PostGIS connection + schema**
- Contains:
  - host
  - port
  - database
  - user
  - password
  - schema
- One store can serve multiple workspaces

### Layer

- Logical dataset
- Backed by:
  - a PostGIS table or view
- Created via:
  - file upload (GeoJSON, GeoPackage)
  - database introspection
- Publishing is **explicit**, never implicit

---

## 👥 Users & Scope (POC)

For this POC:

- Only **superadmin** flows are implemented
- Ignore fine-grained RBAC for now
- Assume trusted internal usage

Later roles (admin/editor) are **out of scope**

---

## 🔄 CORE WORKFLOWS (POC)

### 1️⃣ Upload & Import Layer

User action:

- In Django admin → “Add Layer”
- Uploads:
  - GeoJSON
  - GeoPackage

System behavior:

1. Detect CRS, geometry, fields
2. Import into **default PostGIS instance**
3. Store data in a selected schema:
   - `public`
   - `gis`
   - `mobility`
   - (predefined list only)
4. Create a physical PostGIS table
5. Register Layer metadata in Django

⚠️ Rule:

> Data always lives in PostGIS — never “inside GeoServer”.

---

### 2️⃣ Database Introspection (“Get layers from DB”)

User action:

- Select Workspace
- Workspace has a linked PostGIS Store
- Click “Get layers from DB”

System behavior:

1. Connect to PostGIS
2. Inspect selected schema
3. List tables/views with geometry columns
4. Allow user to select which ones become Layers
5. Register selected layers in Django (no publishing yet)

---

### 3️⃣ Publish Layer (GeoServer – POC)

User action:

- Click “Publish” on a Layer

System behavior:

1. Use **geoserver-rest Python library**
2. Create workspace if missing
3. Create PostGIS datastore if missing
4. Publish FeatureType from existing PostGIS table
5. Store published state + proxy URL in Django

---

## 🧩 TECHNICAL CONSTRAINTS (CRITICAL – DO NOT VIOLATE)

### 1. GeoServer Communication

- MUST use **geoserver-rest** Python library
- Import style:
  ```python
  from geo.Geoserver import Geoserver
  ```
