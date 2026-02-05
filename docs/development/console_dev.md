tamam. bunu çok ciddiyetle ve titizlikle ele alıyorum.
Aşağıda sana VS Code Agent Mode (Claude) için adım adım, branch-bazlı, milestone’lı bir PROMPT SETİ veriyorum.

Bu setin amacı:
	•	Admin paneline dokunmadan
	•	Yeni bir console app kurmak
	•	Engine → Sync → Workspace → Store → Layer → Style → Jobs sırasıyla ilerlemek
	•	Her adımı ayrı branch, ayrı prompt, ayrı milestone olarak yapmak
	•	GeoServer ↔ Django tam birebir state garantisi vermek
	•	Sync konusunu merkez problem olarak ele almak

Aşağıdaki prompt’ları aynen Claude’a verebilirsin.

⸻

🧠 GENEL TALİMAT (HER BRANCH İÇİN GEÇERLİ)

Bu metni her prompt’un başına koy:

You are working in VS Code Agent Mode.

Rules:
- DO NOT touch existing Django admin panels.
- DO NOT refactor existing geodata_engine code unless explicitly instructed.
- ALL new development goes into a new Django app called `console`.
- Work incrementally and safely.
- Assume production-level correctness for sync logic.
- Prefer correctness over speed.

Before coding:
- Think
- Plan
- Then implement

After each milestone:
- Summarize what was done
- List any assumptions


⸻

🟢 PHASE 0 — BOOTSTRAP CONSOLE APP

🔀 Branch

feature/console-bootstrap

🎯 Goal

Create a minimal /console app without breaking anything.

📌 Prompt

## Task: Bootstrap Console App

Create a new Django app called `console`.

Requirements:
- Do NOT modify existing admin panels
- Do NOT change existing URLs
- Console must live under `/console/`
- Use Django templates (no JS frameworks)
- Minimal black & white layout
- No styling libraries (no Tailwind, no Bootstrap)

Deliverables:
- console/apps.py
- console/urls.py
- console/views.py
- console/templates/console/base.html
- console/templates/console/index.html

Base layout:
- Black header
- White background
- Simple vertical navigation

Navigation entries (disabled for now):
- Engines
- Workspaces
- Stores
- Layers
- Styles
- Sync
- Jobs

Milestone complete when:
- `/console/` loads successfully
- No existing admin functionality is affected


⸻

🟢 PHASE 1 — ENGINES + SYNC (BİRLİKTE, ÇOK KRİTİK)

⚠️ BU EN ÖNEMLİ PROMPT
Engine + Sync AYRILMAYACAK

🔀 Branch

feature/console-engines-sync

🎯 Goal
	•	Engine kavramını console’a entegre et
	•	GeoServer ↔ Django iki yönlü sync’i kusursuz kur

📌 Prompt

## Task: Console Engines + Bidirectional Sync (CRITICAL)

Scope:
- Implement engine management in `/console/engines`
- Implement bidirectional sync logic together with engines

Engine assumptions (POC):
- Only GeoServer engine is implemented
- GeoServer runs in docker-compose
- Credentials come from `.env.dev`
- On system startup:
  - One default engine exists
  - One default workspace exists: `vector`
  - One default PostGIS store exists, schema = value from ENV (e.g. `GIS_SCHEMA`)

### Engine Model Rules
- Django engine model MUST represent real engine state
- No fake or virtual engines allowed
- Engine has:
  - type
  - base_url
  - credentials
  - connection status

### Sync Philosophy (VERY IMPORTANT)

Rules:
1. GeoServer is authoritative for PUBLISHED state
2. Django is authoritative for METADATA & INTENT
3. Sync must always converge to a SINGLE identical state

Sync directions:
- Pull:
  - GeoServer → Django
- Push:
  - Django → GeoServer

### Sync Safety Rules
- NEVER delete Django objects first
- NEVER assume success
- ALWAYS verify engine state before and after actions

Patterns to enforce:
- Create:
  - Check if exists in engine
  - Create if missing
  - Verify
  - Then persist in Django
- Delete:
  - Delete in engine FIRST
  - Verify deletion
  - Then delete Django object
- Update:
  - Compare
  - Apply minimal change
  - Verify

### Console UI
- `/console/engines/`
  - Engine list
  - Connection status
  - Buttons:
    - Test connection
    - Sync from engine
    - Sync to engine

Each button:
- Executes sync logic
- Shows success / error clearly

Milestone complete when:
- Engine list is visible in console
- Sync buttons work
- Engine ↔ Django state never diverges
- Errors are explicit and logged


⸻

🟢 PHASE 2 — WORKSPACES

🔀 Branch

feature/console-workspaces

🎯 Goal

Workspace CRUD + sync-safe behavior

📌 Prompt

## Task: Console Workspaces

Implement `/console/workspaces`.

Rules:
- Workspace is a logical domain
- Workspace must exist BOTH in Django and engine
- Workspace CRUD must use sync-safe patterns from engine phase

UI:
- List workspaces
- Create workspace
- Delete workspace (engine first!)
- Sync buttons per workspace

Default:
- Workspace `vector` MUST always exist
- Cannot be deleted

Milestone complete when:
- Workspace CRUD works
- Sync never leaves ghost workspaces


⸻

🟢 PHASE 3 — STORES

🔀 Branch

feature/console-stores

🎯 Goal

Store management bound to PostGIS schemas

📌 Prompt

## Task: Console Stores

Implement `/console/stores`.

Rules:
- Store = PostGIS connection + schema
- Schema comes from ENV (e.g. `GIS_SCHEMA`)
- One store can be reused across workspaces

UI:
- List stores
- Create store
- Test connection
- Assign store to workspace

Critical:
- Store MUST reflect real DB state
- Schema existence must be validated

Milestone complete when:
- Store connects
- Schema validated
- Workspace-store relationship stable


⸻

🟢 PHASE 4 — LAYERS

🔀 Branch

feature/console-layers

🎯 Goal

Layer import + publish

📌 Prompt

## Task: Console Layers

Implement `/console/layers`.

Features:
- Upload GeoJSON / GeoPackage
- GDAL inspect (geometry, CRS)
- Import into PostGIS
- Register layer in Django
- Publish to GeoServer via engine plugin

Rules:
- Data ALWAYS ends in PostGIS
- Layer creation is EXPLICIT
- Publishing is a separate step

Milestone complete when:
- Layer imported correctly
- Layer published
- Sync state is consistent


⸻

🟢 PHASE 5 — STYLES

🔀 Branch

feature/console-styles

🎯 Goal

Engine-based style management

📌 Prompt

## Task: Console Styles

Implement `/console/styles`.

Features:
- Upload SLD or Mapbox Style JSON
- Validate before publish:
  - SLD: XML + XSD validation
  - MBStyle: JSON schema validation
- Publish style to GeoServer

Optional:
- External validation using geostyler.org logic (best-effort)

Rules:
- Style is a separate domain object
- Style != Layer
- Engine consumes styles

Milestone complete when:
- Styles validate
- Styles publish
- Errors are explicit


⸻

🟢 PHASE 6 — JOBS (Django-Q)

🔀 Branch

feature/console-jobs

🎯 Goal

Async job tracking

📌 Prompt

## Task: Console Jobs (Django-Q)

Use Django-Q for background tasks.

Rules:
- No Celery for now
- Job states:
  - pending
  - running
  - failed
  - completed

UI:
- `/console/jobs`
- Show job list
- Show error messages

Milestone complete when:
- Long operations are async
- Job status visible


⸻

🧠 FINAL NOT

Bu prompt seti:
	•	seni GeoServer’a kilitlemez
	•	seni admin panel hack’inden kurtarır
	•	seni gerçek bir geo data platformuna götürür

Bu noktada yaptığın şey
“bir Django admin UI” değil
bir geospatial control plane.

Eğer istersen bir sonraki adımda:
	•	branch → merge stratejisi
	•	rollback planı
	•	test checklist’i

de çıkarırım.