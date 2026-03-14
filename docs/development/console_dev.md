myproject/

├── geoconsole/                 # Yeni UI app — model yok
│   ├── __init__.py
│   ├── urls.py
│   ├── views.py             # List/Detail/Sync/Publish view'ları
│   ├── forms.py             # SearchForm, FilterForm (ModelForm değil)
│   ├── exceptions.py        # APIError, APITimeoutError
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   └── api_client.py    # GeoAPIClient — requests buradan çıkar
│   │
│   └── templates/
│       └── console/
│           ├── base.html
│           ├── engine_list.html
│           ├── workspace_list.html
│           ├── layer_list.html
│           ├── layer_detail.html
│           └── publish_confirm.html
│
├── myproject/
│   ├── settings.py
│   ├── urls.py              # include("console.urls") eklenecek
│   └── wsgi.py
│
└── manage.py

----
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

At the end it is going to be same as this html 
/Users/hsadmin/Desktop/coding/dcs-django-api/docs/development/geodata_console.html
Please follow the all desigin within this html. First jusf focus Engines and sync

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

🟢 PHASE 1 — ENGINES + SYNC

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
Please follow /Users/hsadmin/Desktop/coding/dcs-django-api/docs/development/UI-UX_Rules.md. First jusf focus Engines and sync

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

---
📋 Milestone Breakdown
🏗️ MILESTONE 1: DRF Foundation (30 mins)
Goal: Set up Django REST Framework infrastructure

Tasks:

Add djangorestframework to dependencies
Configure DRF settings in base.py
Create geodata_engine/api/ directory structure
Add API URL routing
Deliverables:

/api/ endpoint accessible
DRF browsable API working
🔧 MILESTONE 2: First API Endpoint (45 mins)
Goal: Create engines API with permissions

Tasks:

Create EnginesAPIViewSet in geodata_engine/api/views.py
Add permissions: IsAuthenticated + custom CanManageEngines
Implement: GET /api/engines/ and POST /api/engines/sync/
Create serializers for engine data
Deliverables:

GET /api/engines/ - list engines
POST /api/engines/{id}/sync/ - sync specific engine
Permission-protected endpoints
🔄 MILESTONE 3: Console API Integration (30 mins)
Goal: Update console to use API calls

Tasks:

Update console/views.py to use requests or Django test client
Replace direct model access with API calls
Add proper error handling for API failures
Update templates to handle API responses
Deliverables:

Console engines page uses API
Console sync buttons use API
Error messages from API displayed properly
🛡️ MILESTONE 4: Enhanced Security (45 mins)
Goal: Add audit logging and rate limiting

Tasks:

Create AuditLog model for tracking console operations
Add @ratelimit decorators to expensive operations
Create custom permission classes: ConsoleUserPermission
Add operation logging to all API endpoints
Deliverables:

All console operations logged
Rate limiting on sync operations (max 5/minute)
Role-based permissions (console_user vs admin)
🚀 MILESTONE 5: Additional Endpoints (60 mins)
Goal: Expand API for workspaces, stores, layers

Tasks:

Create WorkspacesAPIViewSet
Create StoresAPIViewSet
Create LayersAPIViewSet
Add bulk operations support
Create status/health endpoints
Deliverables:

Full CRUD API for all geodata entities
Bulk sync operations
/api/status/ health check endpoint
🎯 Implementation Priority
Week 1: Milestones 1-3 (Console working with API)
Week 2: Milestones 4-5 (Security + Full API)

🔧 Technical Decisions
DRF Configuration:

Use ViewSets for CRUD operations
Token authentication for internal calls
Custom permission classes
API versioning (/api/v1/)
Key Benefits After Completion:

✅ Clean separation: Console ↔ API ↔ Models
✅ Permission boundaries enforced
✅ All operations audited
✅ Rate limiting protection
✅ Future-ready for external API
Want me to start with Milestone 1: DRF Foundation setup?




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