# **⚙️ Technical Architecture Document: Geostories × Calendar × Participation**

---

## **1. System Overview**

### **Purpose**

The technical system provides a **modular participation framework** combining:

- **Campaigns** – strategic umbrellas that group stories, events, and feedback.
- **Geostories** – map-based narratives linked to spatial contexts.
- **Calendar Events** – time-bound participatory activities.
- **Feedback Cycles** – structured and unstructured feedback collection.

All features share a **common spatial and campaign reference layer**, enabling them to be connected, analyzed, and visualized within one interface.

### **Architecture Layers**

```
[Frontend: Vue 3 + MapLibre GL + Pinia]
        ↕
[API Layer: Django REST Framework]
        ↕
[Backend Core: Django + PostGIS + Celery (for async jobs)]
        ↕
[Data Services: GeoServer / Vector Tiles / File Storage / Cache]
        ↕
[External Integrations: Analytics, AI Summaries, Webhooks, Identity]
```

---

## **2. Core Modules and Responsibilities**

| **Module**                 | **Purpose**                                                               | **Main Technologies**                                                    | **Key Relations**                                                         |
| -------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| **Campaign**               | Defines mission, scope, partners, and connects all participation assets.  | Django models + RBAC + analytics snapshots.                              | Parent to stories, events, feedback; exposes KPI endpoints.               |
| **Geostory**               | Spatial narrative creation and display.                                   | Django models + rich text/media blocks.                                  | Linked to campaigns, layers, direct links, events, and feedback.          |
| **Calendar**               | Event scheduling, visualization, and map linkage.                         | Django + PostGIS for spatial event storage; frontend calendar component. | Linked to campaigns and optional stories; has feedback after events.      |
| **Feedback**               | Collects structured (forms) and unstructured (ratings/comments) feedback. | Form schema builder + submission endpoints.                              | Linked to campaigns, stories, or events; aggregated by analytics service. |
| **Feature Linking**        | Stores explicit relationships across stories, events, and feedback.       | Junction table + admin UI.                                               | Enforces campaign consistency and drives “direct link” UI.                |
| **Analytics**              | Aggregation and visualization of participation data.                      | Celery tasks + PostGIS spatial joins + visualization endpoints.          | Consumes feedback data and links results to campaigns/stories/events.     |
| **User & Role Management** | Controls permissions and workflows.                                       | Django Auth + DRF token/JWT + RBAC policy.                               | Defines who can create/edit/publish entities.                             |
| **Media & Assets**         | Handles images, video, and documents.                                     | Django Storage + S3 or local FS + metadata indexing.                     | Used in Geostories, Events, Feedback forms.                               |
| **Integration Layer**      | Optional connectors for external APIs (analytics, AI summaries, sensors). | REST clients + Celery tasks.                                             | Extendable microservices.                                                 |

---

## **3. Component Relationships (Conceptual Diagram)**

```
           +---------------------+
           |      User Roles     |
           |  (Citizen, Org, DM) |
           +----------+----------+
                      |
                      v
           +---------------------+
           |      Campaign       |
           | (mission, KPIs,     |
           |  partners, region)  |
           +----------+----------+
                      |
        +-------------+-----------------------------+
        |             |                             |
        v             v                             v
+---------------+ +----------------+          +--------------+
|    GeoStory   | |  CalendarEvent |          |  GeoFeedback |
| (media,       | | (title, date,  |          | (forms, rate)|
|  narrative)   | |  geometry)     |          |              |
| + GeoContext  | | + GeoContext   |          | + GeoContext |
+-------+-------+ +--------+-------+          +-------+------+
        |                  |                          ^
        |                  |                          |
        v                  v                          |
    +----------------------------------------------------+
    |                   Feature Links                    |
    |        (direct refs between any entities)          |
    +--------------------------+-------------------------+
                               |
                               v
                     +---------------------+
                     | Feedback Submission |
                     | (User answers)      |
                     +----------+----------+
                                |
                                v
                         +-------------+
                         |  Analytics  |
                         | Aggregation |
                         +------+------+
                                |
                                v
                      +------------------+
                      | Updated Campaign |
                      | & Stories        |
                      +------------------+
```

---

## **4. Data Model Overview (Simplified ERD)**

```
[Campaign]
 ├─ id (UUID)
 ├─ title (varchar)
 ├─ summary (text, nullable)
 ├─ status (enum: draft/active/archived)
 ├─ visibility (enum: public/private)
 ├─ created_by → User (FK)
 └─ created_at, updated_at

[GeoContext]  ← NEW: content submodule
 ├─ id (UUID)
 ├─ content (TEXT)
 ├─ content_type (enum: simple/rich)
 ├─ created_by → User (FK)
 └─ created_at, updated_at

[LayerRef]  ← GeoServer layer reference (synced)
 ├─ id (UUID)
 ├─ layer_name (varchar, unique)
 └─ created_at

    %% Feature Entities
    GEOSTORY {
        UUID id PK
        UUID campaign_id FK
        VARCHAR title
        TEXT summary
        ENUM status "draft, published, archived"
        UUID author_id FK
        UUID cover_media_id FK
        UUID context_id FK "1:1 UNIQUE"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    CALENDAREVENT {
        UUID id PK
        UUID campaign_id FK
        VARCHAR title
        TEXT description
        UUID context_id FK "1:1 UNIQUE"
        TIMESTAMPTZ start_datetime
        TIMESTAMPTZ end_datetime
        GEOMETRY location "Point 4326"
        UUID organizer_id FK
        ENUM status "draft, published, cancelled"
        ENUM visibility "public, private"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    GEOFEEDBACK {
        UUID id PK
        UUID campaign_id FK
        VARCHAR title
        BOOL rating_enabled
        BOOL form_enabled
        BOOL allow_drawings
        UUID custom_form_id FK "nullable (from formbuilder)"
        UUID context_id FK "1:1 UNIQUE"
        TIMESTAMPTZ active_from
        TIMESTAMPTZ active_to
        ENUM visibility "public, private"
        UUID created_by_id FK
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    %% Feedback Categorization (Provided by formbuilder)
    CUSTOM_FORM {
        UUID id PK
        VARCHAR name
        VARCHAR slug UK
        JSONB json_schema
        ENUM status
    }

    FEEDBACKSUBMISSION {
        UUID id PK
        UUID feedback_id FK
        UUID user_id FK "nullable"
        INT rating "1-5, nullable"
        JSONB form_data
        GEOMETRY geometry "Any geometry type"
        BOOL is_anonymized
        TIMESTAMPTZ created_at
    }

[GeoFeedback]
 ├─ id (UUID)
 ├─ campaign → Campaign (FK)
 ├─ title
 ├─ rating_enabled (bool)        ← allow star ratings
 ├─ form_enabled (bool)          ← allow form submissions
 ├─ allow_drawings (bool)        ← allow geometry input
 ├─ custom_form → CustomForm (FK, nullable) ← formbuilder dependency
 ├─ context → GeoContext (FK, nullable)  ← 1:1 content
 ├─ active_from, active_to (nullable)
 └─ visibility, created_at, updated_at

[feedback_layers]  ← M2M join table
 ├─ feedback → GeoFeedback (FK)
 ├─ layer → LayerRef (FK)
 ├─ display_order (int)
 └─ UNIQUE(feedback, layer)

[FeatureLink]
 ├─ id (UUID)
 ├─ campaign → Campaign (FK)
 ├─ source_content_type (FK → ContentType)
 ├─ source_object_id (UUID)
 ├─ target_content_type (FK → ContentType)
 ├─ target_object_id (UUID)
 ├─ link_type (enum: direct/read_more/action)
 └─ created_at, created_by → User

[FeedbackSubmission]
 ├─ id (UUID)
 ├─ feedback → GeoFeedback (FK)
 ├─ user → User (nullable)  ← authenticated or anonymous
 ├─ rating (int, nullable)  ← 1-5 stars if rating_enabled
 ├─ form_data (JSONB, nullable)  ← form responses based on CustomForm schema
 ├─ geometry (Geometry, nullable)  ← if allow_drawings
 └─ created_at, is_anonymized (bool)
```

> [!NOTE]
> **Analytics models** (e.g., `AnalyticsSnapshot`, `mission_kpis`) are **optional** and will be added in a future phase. The core system operates without them.

---

### **4.1 Campaign Schema & Behavior**

- Campaigns are **logical containers** that group related stories, events, and feedback forms.
- Every participatory artifact references exactly one campaign via `campaign_id` FK.
- Status workflow: `draft` → `active` → `archived`.
- Permissions can be scoped per campaign (future: `campaign_members` table for RBAC).

---

### **4.2 Feature Linking Strategies (Loose vs Direct)**

We support two layers of relationships:

1.  **Loose association** via `campaign_id` — all entities within a campaign are implicitly related.
2.  **Direct links** via `FeatureLink` — explicit connections between specific entities.

| Option                           | Pros                                                       | Cons                                          |
| -------------------------------- | ---------------------------------------------------------- | --------------------------------------------- |
| Junction table (`feature_links`) | Referential integrity, metadata support, efficient lookups | Polymorphic FK validation in app layer        |
| JSONB arrays on entities         | Simple schema                                              | No FK guarantees, hard to query reverse links |

**Decision:** Use `feature_links` table with `campaign_id` denormalized for query simplicity and "same campaign" constraint enforcement.

---

### **4.3 Detailed Schema Definitions**

#### **Campaign**

The campaign is the **top-level organizational unit**. It serves as a logical umbrella for participation activities.

##### Schema Definition

| Column          | PostgreSQL Type | Nullable | Default             | Description                                   |
| --------------- | --------------- | -------- | ------------------- | --------------------------------------------- |
| `id`            | `UUID`          | NOT NULL | `gen_random_uuid()` | Primary key                                   |
| `title`         | `VARCHAR(255)`  | NOT NULL | —                   | Human-readable campaign name                  |
| `summary`       | `TEXT`          | NULL     | `NULL`              | Brief description for context                 |
| `status`        | `VARCHAR(20)`   | NOT NULL | `'draft'`           | Workflow state: `draft`, `active`, `archived` |
| `visibility`    | `VARCHAR(20)`   | NOT NULL | `'private'`         | Access control: `public`, `private`           |
| `created_by_id` | `UUID`          | NOT NULL | —                   | FK → `auth_user.id`                           |
| `created_at`    | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Creation timestamp                            |
| `updated_at`    | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Last modification (auto-updated)              |

##### Constraints

| Constraint                | Type        | Definition                                                  |
| ------------------------- | ----------- | ----------------------------------------------------------- |
| `pk_campaign`             | PRIMARY KEY | `(id)`                                                      |
| `fk_campaign_created_by`  | FOREIGN KEY | `created_by_id REFERENCES auth_user(id) ON DELETE RESTRICT` |
| `chk_campaign_status`     | CHECK       | `status IN ('draft', 'active', 'archived')`                 |
| `chk_campaign_visibility` | CHECK       | `visibility IN ('public', 'private')`                       |

##### Indexes

| Index Name                | Columns           | Type   | Purpose                |
| ------------------------- | ----------------- | ------ | ---------------------- |
| `idx_campaign_status`     | `status`          | B-tree | Filter by status       |
| `idx_campaign_visibility` | `visibility`      | B-tree | Filter by visibility   |
| `idx_campaign_created_by` | `created_by_id`   | B-tree | List campaigns by user |
| `idx_campaign_created_at` | `created_at DESC` | B-tree | Sort by creation date  |

##### PostgreSQL DDL

```sql
-- Enum types (optional, or use VARCHAR with CHECK)
-- CREATE TYPE campaign_status AS ENUM ('draft', 'active', 'archived');
-- CREATE TYPE campaign_visibility AS ENUM ('public', 'private');

CREATE TABLE campaign (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    title           VARCHAR(255)    NOT NULL,
    summary         TEXT,
    status          VARCHAR(20)     NOT NULL DEFAULT 'draft',
    visibility      VARCHAR(20)     NOT NULL DEFAULT 'private',
    created_by_id   UUID            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT fk_campaign_created_by
        FOREIGN KEY (created_by_id) REFERENCES auth_user(id) ON DELETE RESTRICT,
    CONSTRAINT chk_campaign_status
        CHECK (status IN ('draft', 'active', 'archived')),
    CONSTRAINT chk_campaign_visibility
        CHECK (visibility IN ('public', 'private'))
);

-- Indexes
CREATE INDEX idx_campaign_status ON campaign(status);
CREATE INDEX idx_campaign_visibility ON campaign(visibility);
CREATE INDEX idx_campaign_created_by ON campaign(created_by_id);
CREATE INDEX idx_campaign_created_at ON campaign(created_at DESC);

-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_campaign_updated_at
    BEFORE UPDATE ON campaign
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

##### Django Model Reference

```python
from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()

class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        ARCHIVED = 'archived', 'Archived'

    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        PRIVATE = 'private', 'Private'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.RESTRICT,
        related_name='campaigns'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'campaign'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['visibility']),
            models.Index(fields=['created_by']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return self.title
```

##### Design Notes

- **No `slug`** — campaigns are not URL-routed entities; use UUID in API paths.
- **No `region_geometry`** — spatial extent derived from child GeoStories if needed.
- **No `timeline_start/end`** — can be computed from child events' date ranges.
- **No `owner_org`** — initially use `created_by`; organization ownership can be added later via a `campaign_members` join table.
- **`ON DELETE RESTRICT`** — prevents deleting users who own campaigns; handle via application logic.

---

#### **GeoContext**

Reusable content submodule for stories, events, and feedback. Starts as plain text; will support WYSIWYG rich text in future.

> **Superseded for future implementation (Phase 7, Task 7.1)**: The `content` (TEXT) + `content_type` (`simple`/`rich`) contract described in this section is retained as historical context. The canonical future contract stores `GeoContext.content` as Editor.js JSON and drops `content_type` entirely. Empty documents are represented as `{ "blocks": [] }`. See `docs/features-to-add/decisions.md` §7.1.

##### Schema Definition

| Column          | PostgreSQL Type | Nullable | Default             | Description                                |
| --------------- | --------------- | -------- | ------------------- | ------------------------------------------ |
| `id`            | `UUID`          | NOT NULL | `gen_random_uuid()` | Primary key                                |
| `content`       | `TEXT`          | NOT NULL | `''`                | Text content (plain text or rich text)     |
| `content_type`  | `VARCHAR(20)`   | NOT NULL | `'simple'`          | `simple` (text) or `rich` (WYSIWYG blocks) |
| `created_by_id` | `UUID`          | NOT NULL | —                   | FK → `auth_user.id`                        |
| `created_at`    | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Creation timestamp                         |
| `updated_at`    | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Last modification                          |

##### Constraints

| Constraint                    | Type        | Definition                                                  |
| ----------------------------- | ----------- | ----------------------------------------------------------- |
| `pk_geocontext`               | PRIMARY KEY | `(id)`                                                      |
| `fk_geocontext_created_by`    | FOREIGN KEY | `created_by_id REFERENCES auth_user(id) ON DELETE RESTRICT` |
| `chk_geocontext_content_type` | CHECK       | `content_type IN ('simple', 'rich')`                        |

##### PostgreSQL DDL

```sql
CREATE TABLE geocontext (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    content         TEXT            NOT NULL DEFAULT '',
    content_type    VARCHAR(20)     NOT NULL DEFAULT 'simple',
    created_by_id   UUID            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_geocontext_created_by
        FOREIGN KEY (created_by_id) REFERENCES auth_user(id) ON DELETE RESTRICT,
    CONSTRAINT chk_geocontext_content_type
        CHECK (content_type IN ('simple', 'rich'))
);

CREATE TRIGGER trg_geocontext_updated_at
    BEFORE UPDATE ON geocontext
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

##### Django Model

```python
class GeoContext(models.Model):
    class ContentType(models.TextChoices):
        SIMPLE = 'simple', 'Simple Text'
        RICH = 'rich', 'Rich Text'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.TextField(default='')
    content_type = models.CharField(
        max_length=20,
        choices=ContentType.choices,
        default=ContentType.SIMPLE
    )
    created_by = models.ForeignKey(
        User, on_delete=models.RESTRICT, related_name='geocontexts'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'geocontext'
```

---

#### **LayerRef**

Reference to a GeoServer layer. Synced from GeoServer; can be linked to stories, events, or feedback via M2M.

##### Schema Definition

| Column       | PostgreSQL Type | Nullable | Default             | Description                                            |
| ------------ | --------------- | -------- | ------------------- | ------------------------------------------------------ |
| `id`         | `UUID`          | NOT NULL | `gen_random_uuid()` | Primary key                                            |
| `layer_name` | `VARCHAR(255)`  | NOT NULL | —                   | GeoServer layer identifier (format: `workspace:layer`) |
| `created_at` | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | When synced from GeoServer                             |

##### Constraints

| Constraint               | Type        | Definition     |
| ------------------------ | ----------- | -------------- |
| `pk_layerref`            | PRIMARY KEY | `(id)`         |
| `uq_layerref_layer_name` | UNIQUE      | `(layer_name)` |

##### PostgreSQL DDL

```sql
CREATE TABLE layerref (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    layer_name  VARCHAR(255)    NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
```

##### Django Model

```python
class LayerRef(models.Model):
    """
    Stores GeoServer layer references. Synced via update_layers() class method.
    Can be linked M2M to GeoStory, CalendarEvent, or GeoFeedback.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer_name = models.CharField(max_length=255, unique=True)  # e.g., 'workspace:layer_name'
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'layerref'

    def __str__(self):
        return self.layer_name

    @classmethod
    def update_layers(cls):
        """Sync layers from GeoServer. Add new, remove stale."""
        from geonode.geoserver.helpers import gs_catalog
        try:
            current_layers = {layer.name for layer in gs_catalog.get_layers()}
            existing = set(cls.objects.values_list('layer_name', flat=True))

            # Add new
            for name in current_layers - existing:
                cls.objects.create(layer_name=name)
            # Remove stale
            cls.objects.filter(layer_name__in=existing - current_layers).delete()
        except Exception as e:
            print(f"Error syncing LayerRef: {e}")
```

##### Design Notes

- **Simple structure** — only `layer_name`, no extra metadata
- **Synced from GeoServer** — `update_layers()` method keeps table in sync
- **M2M relationships** — linked to features via join tables (e.g., `geostory_layers`, `event_layers`, `feedback_layers`)

##### REST API (Manual Sync)

**Endpoint**: `POST /api/layers/sync/`
**Action**: Triggers manual sync with GeoServer catalog.
**Status**: `AUTHENTICATED` (Editor+)

```python
# ViewSet Action
@action(detail=False, methods=['post'])
def sync(self, request):
    # logic to fetch from gs_catalog and update LayerRef
    return Response({"status": "synced", "count": ...})
```

---

#### **GeoStory**

Spatial narratives that combine maps, media, and data to communicate local issues.

##### Schema Definition

| Column           | PostgreSQL Type | Nullable | Default             | Description                        |
| ---------------- | --------------- | -------- | ------------------- | ---------------------------------- |
| `id`             | `UUID`          | NOT NULL | `gen_random_uuid()` | Primary key                        |
| `campaign_id`    | `UUID`          | NOT NULL | —                   | FK → `campaign.id`                 |
| `title`          | `VARCHAR(255)`  | NOT NULL | —                   | Story title                        |
| `summary`        | `TEXT`          | NULL     | `NULL`              | Brief description                  |
| `status`         | `VARCHAR(20)`   | NOT NULL | `'draft'`           | `draft`, `published`, `archived`   |
| `author_id`      | `UUID`          | NOT NULL | —                   | FK → `auth_user.id`                |
| `cover_media_id` | `UUID`          | NULL     | `NULL`              | FK → `media_asset.id` (optional)   |
| `context_id`     | `UUID`          | NULL     | `NULL`              | FK → `geocontext.id` (1:1 content) |
| `created_at`     | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Creation timestamp                 |
| `updated_at`     | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Last modification                  |

##### Constraints

| Constraint             | Type        | Definition                                                |
| ---------------------- | ----------- | --------------------------------------------------------- |
| `pk_geostory`          | PRIMARY KEY | `(id)`                                                    |
| `fk_geostory_campaign` | FOREIGN KEY | `campaign_id REFERENCES campaign(id) ON DELETE CASCADE`   |
| `fk_geostory_author`   | FOREIGN KEY | `author_id REFERENCES auth_user(id) ON DELETE RESTRICT`   |
| `fk_geostory_context`  | FOREIGN KEY | `context_id REFERENCES geocontext(id) ON DELETE SET NULL` |
| `chk_geostory_status`  | CHECK       | `status IN ('draft', 'published', 'archived')`            |

##### Indexes

| Index Name                | Columns           | Type   | Purpose                    |
| ------------------------- | ----------------- | ------ | -------------------------- |
| `idx_geostory_campaign`   | `campaign_id`     | B-tree | Filter stories by campaign |
| `idx_geostory_status`     | `status`          | B-tree | Filter published stories   |
| `idx_geostory_author`     | `author_id`       | B-tree | Filter by author           |
| `idx_geostory_created_at` | `created_at DESC` | B-tree | For pagination             |

##### PostgreSQL DDL

```sql
CREATE TABLE geostory (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     UUID            NOT NULL,
    title           VARCHAR(255)    NOT NULL,
    summary         TEXT,
    status          VARCHAR(20)     NOT NULL DEFAULT 'draft',
    author_id       UUID            NOT NULL,
    cover_media_id  UUID,
    context_id      UUID            UNIQUE,  -- 1:1 relationship enforced
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_geostory_campaign
        FOREIGN KEY (campaign_id) REFERENCES campaign(id) ON DELETE CASCADE,
    CONSTRAINT fk_geostory_author
        FOREIGN KEY (author_id) REFERENCES auth_user(id) ON DELETE RESTRICT,
    CONSTRAINT fk_geostory_context
        FOREIGN KEY (context_id) REFERENCES geocontext(id) ON DELETE SET NULL,
    CONSTRAINT chk_geostory_status
        CHECK (status IN ('draft', 'published', 'archived'))
);

-- Indexes
CREATE INDEX idx_geostory_campaign ON geostory(campaign_id);
CREATE INDEX idx_geostory_status ON geostory(status);
CREATE INDEX idx_geostory_author ON geostory(author_id);
CREATE INDEX idx_geostory_created_at ON geostory(created_at DESC);  -- For pagination

CREATE TRIGGER trg_geostory_updated_at
    BEFORE UPDATE ON geostory
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

##### M2M: geostory_layers

```sql
CREATE TABLE geostory_layers (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    geostory_id     UUID            NOT NULL,
    layer_id        UUID            NOT NULL,
    display_order   INTEGER         NOT NULL DEFAULT 0,

    CONSTRAINT fk_geostory_layers_story
        FOREIGN KEY (geostory_id) REFERENCES geostory(id) ON DELETE CASCADE,
    CONSTRAINT fk_geostory_layers_layer
        FOREIGN KEY (layer_id) REFERENCES layerref(id) ON DELETE CASCADE,
    CONSTRAINT uq_geostory_layers
        UNIQUE (geostory_id, layer_id)
);

CREATE INDEX idx_geostory_layers_story ON geostory_layers(geostory_id);
```

##### Django Model

```python
class GeoStory(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='stories'
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    author = models.ForeignKey(
        User, on_delete=models.RESTRICT, related_name='stories'
    )
    cover_media_id = models.UUIDField(null=True, blank=True)
    # context field defined in OneToOne from GeoContext if preferred, or here
    context = models.OneToOneField(
        'GeoContext', on_delete=models.SET_NULL, null=True, blank=True, related_name='geostory'
    )
    layers = models.ManyToManyField(
        LayerRef, through='GeoStoryLayer', related_name='stories'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'geostory'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['campaign']),
            models.Index(fields=['status']),
            models.Index(fields=['author']),
        ]


class GeoStoryLayer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    geostory = models.ForeignKey(GeoStory, on_delete=models.CASCADE)
    layer = models.ForeignKey(LayerRef, on_delete=models.CASCADE)
    display_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'geostory_layers'
        unique_together = ['geostory', 'layer']
        ordering = ['display_order']
```

##### Design Notes

- **Removed `locale`** — single language for MVP
- **`cover_media_id` nullable** — optional cover image
- **`context_id` 1:1** — links to GeoContext for story content
- **`ON DELETE CASCADE`** for campaign — deleting a campaign removes its stories
- **`ON DELETE SET NULL`** for context — allows orphan cleanup later

#### **CalendarEvent**

Time-bound participatory activities (e.g., "Workshop", "Site Visit") linked to campaigns.

##### Schema Definition

| Column           | PostgreSQL Type         | Nullable | Default             | Description                        |
| ---------------- | ----------------------- | -------- | ------------------- | ---------------------------------- |
| `id`             | `UUID`                  | NOT NULL | `gen_random_uuid()` | Primary key                        |
| `campaign_id`    | `UUID`                  | NOT NULL | —                   | FK → `campaign.id`                 |
| `title`          | `VARCHAR(255)`          | NOT NULL | —                   | Event title                        |
| `description`    | `TEXT`                  | NULL     | `NULL`              | Short description                  |
| `context_id`     | `UUID`                  | NULL     | `NULL`              | FK → `geocontext.id` (1:1 content) |
| `start_datetime` | `TIMESTAMPTZ`           | NOT NULL | —                   | Event start                        |
| `end_datetime`   | `TIMESTAMPTZ`           | NOT NULL | —                   | Event end                          |
| `location`       | `GEOMETRY(Point, 4326)` | NULL     | `NULL`              | Event location (Point)             |
| `organizer_id`   | `UUID`                  | NOT NULL | —                   | FK → `auth_user.id`                |
| `status`         | `VARCHAR(20)`           | NOT NULL | `'published'`       | `draft`, `published`, `cancelled`  |
| `visibility`     | `VARCHAR(20)`           | NOT NULL | `'public'`          | `public`, `private`                |
| `created_at`     | `TIMESTAMPTZ`           | NOT NULL | `NOW()`             | Creation timestamp                 |
| `updated_at`     | `TIMESTAMPTZ`           | NOT NULL | `NOW()`             | Last modification                  |

##### Constraints

| Constraint                   | Type        | Definition                                                 |
| ---------------------------- | ----------- | ---------------------------------------------------------- |
| `pk_calendarevent`           | PRIMARY KEY | `(id)`                                                     |
| `fk_calendarevent_campaign`  | FOREIGN KEY | `campaign_id REFERENCES campaign(id) ON DELETE CASCADE`    |
| `fk_calendarevent_context`   | FOREIGN KEY | `context_id REFERENCES geocontext(id) ON DELETE SET NULL`  |
| `fk_calendarevent_organizer` | FOREIGN KEY | `organizer_id REFERENCES auth_user(id) ON DELETE RESTRICT` |
| `chk_event_dates`            | CHECK       | `end_datetime >= start_datetime`                           |

##### Indexes

| Index Name                   | Columns                        | Type   | Purpose              |
| ---------------------------- | ------------------------------ | ------ | -------------------- |
| `idx_calendarevent_campaign` | `campaign_id`                  | B-tree | Filter by campaign   |
| `idx_calendarevent_dates`    | `start_datetime, end_datetime` | B-tree | Filter by date range |
| `idx_calendarevent_geom`     | `location`                     | GiST   | Spatial queries      |

##### PostgreSQL DDL

```sql
CREATE TABLE calendarevent (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id     UUID            NOT NULL,
    title           VARCHAR(255)    NOT NULL,
    description     TEXT,
    context_id      UUID            UNIQUE,  -- 1:1 relationship enforced
    start_datetime  TIMESTAMPTZ     NOT NULL,
    end_datetime    TIMESTAMPTZ     NOT NULL,
    location        GEOMETRY(Point, 4326),
    organizer_id    UUID            NOT NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'published',
    visibility      VARCHAR(20)     NOT NULL DEFAULT 'public',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_calendarevent_campaign
        FOREIGN KEY (campaign_id) REFERENCES campaign(id) ON DELETE CASCADE,
    CONSTRAINT fk_calendarevent_context
        FOREIGN KEY (context_id) REFERENCES geocontext(id) ON DELETE SET NULL,
    CONSTRAINT fk_calendarevent_organizer
        FOREIGN KEY (organizer_id) REFERENCES auth_user(id) ON DELETE RESTRICT,
    CONSTRAINT chk_event_dates
        CHECK (end_datetime >= start_datetime)
);

CREATE INDEX idx_calendarevent_campaign ON calendarevent(campaign_id);
CREATE INDEX idx_calendarevent_dates ON calendarevent(start_datetime, end_datetime);
CREATE INDEX idx_calendarevent_campaign_dates ON calendarevent(campaign_id, start_datetime, end_datetime);  -- Compound for campaign-scoped queries
CREATE INDEX idx_calendarevent_geom ON calendarevent USING GIST(location);

CREATE TRIGGER trg_calendarevent_updated_at
    BEFORE UPDATE ON calendarevent
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

##### M2M: event_layers

```sql
CREATE TABLE event_layers (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID            NOT NULL,
    layer_id        UUID            NOT NULL,
    display_order   INTEGER         NOT NULL DEFAULT 0,

    CONSTRAINT fk_event_layers_event
        FOREIGN KEY (event_id) REFERENCES calendarevent(id) ON DELETE CASCADE,
    CONSTRAINT fk_event_layers_layer
        FOREIGN KEY (layer_id) REFERENCES layerref(id) ON DELETE CASCADE,
    CONSTRAINT uq_event_layers
        UNIQUE (event_id, layer_id)
);

CREATE INDEX idx_event_layers_event ON event_layers(event_id);
```

##### Django Model

```python
class CalendarEvent(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        CANCELLED = 'cancelled', 'Cancelled'

    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        PRIVATE = 'private', 'Private'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='events'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    context = models.OneToOneField(
        GeoContext, on_delete=models.SET_NULL, null=True, blank=True
    )
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    location = models.PointField(srid=4326, blank=True, null=True)
    organizer = models.ForeignKey(
        User, on_delete=models.RESTRICT, related_name='organized_events'
    )
    layers = models.ManyToManyField(
        LayerRef, through='EventLayer', related_name='events'
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PUBLISHED
    )
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'calendarevent'
        ordering = ['start_datetime']
        indexes = [
            models.Index(fields=['campaign']),
            models.Index(fields=['start_datetime', 'end_datetime']),
        ]

class EventLayer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE)
    layer = models.ForeignKey(LayerRef, on_delete=models.CASCADE)
    display_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'event_layers'
        unique_together = ['event', 'layer']
        ordering = ['display_order']
```

---

| Column           | PostgreSQL Type | Nullable | Default             | Description                                            |
| ---------------- | --------------- | -------- | ------------------- | ------------------------------------------------------ |
| `id`             | `UUID`          | NOT NULL | `gen_random_uuid()` | Primary key                                            |
| `campaign_id`    | `UUID`          | NOT NULL | —                   | FK → `campaign.id`                                     |
| `title`          | `VARCHAR(255)`  | NOT NULL | —                   | Feedback title                                         |
| `rating_enabled` | `BOOLEAN`       | NOT NULL | `TRUE`              | Allow star ratings                                     |
| `form_enabled`   | `BOOLEAN`       | NOT NULL | `FALSE`             | Allow form submissions                                 |
| `allow_drawings` | `BOOLEAN`       | NOT NULL | `FALSE`             | Allow geometry input (requires form_enabled)           |
| `custom_form_id` | `UUID`          | NULL     | `NULL`              | FK → `custom_form.id` (from django-basic-form-builder) |
| `context_id`     | `UUID`          | NULL     | `NULL`              | FK → `geocontext.id` (1:1 content)                     |
| `active_from`    | `TIMESTAMPTZ`   | NULL     | `NULL`              | When feedback opens                                    |
| `active_to`      | `TIMESTAMPTZ`   | NULL     | `NULL`              | When feedback closes                                   |
| `visibility`     | `VARCHAR(20)`   | NOT NULL | `'public'`          | `public`, `private`                                    |
| `created_by_id`  | `UUID`          | NOT NULL | —                   | FK → `auth_user.id`                                    |
| `created_at`     | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Creation timestamp                                     |
| `updated_at`     | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Last modification                                      |

##### Constraints

| Constraint                  | Type        | Definition                                                        |
| --------------------------- | ----------- | ----------------------------------------------------------------- |
| `pk_geofeedback`            | PRIMARY KEY | `(id)`                                                            |
| `fk_geofeedback_campaign`   | FOREIGN KEY | `campaign_id REFERENCES campaign(id) ON DELETE CASCADE`           |
| `fk_geofeedback_type`       | FOREIGN KEY | `feedback_type_id REFERENCES feedbacktype(id) ON DELETE SET NULL` |
| `fk_geofeedback_context`    | FOREIGN KEY | `context_id REFERENCES geocontext(id) ON DELETE SET NULL`         |
| `fk_geofeedback_created_by` | FOREIGN KEY | `created_by_id REFERENCES auth_user(id) ON DELETE RESTRICT`       |
| `chk_geofeedback_mode`      | CHECK       | `rating_enabled OR form_enabled` (at least one mode)              |
| `chk_geofeedback_drawings`  | CHECK       | `NOT allow_drawings OR form_enabled`                              |

##### Indexes

| Index Name                 | Columns                  | Type   | Purpose                |
| -------------------------- | ------------------------ | ------ | ---------------------- |
| `idx_geofeedback_campaign` | `campaign_id`            | B-tree | Filter by campaign     |
| `idx_geofeedback_active`   | `active_from, active_to` | B-tree | Filter active feedback |

##### PostgreSQL DDL

```sql
CREATE TABLE geofeedback (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id         UUID            NOT NULL,
    title               VARCHAR(255)    NOT NULL,
    rating_enabled      BOOLEAN         NOT NULL DEFAULT TRUE,
    form_enabled        BOOLEAN         NOT NULL DEFAULT FALSE,
    allow_drawings      BOOLEAN         NOT NULL DEFAULT FALSE,
    form_schema         JSONB           NOT NULL DEFAULT '{}',
    feedback_type_id    UUID,
    context_id          UUID            UNIQUE,  -- 1:1 relationship enforced
    active_from         TIMESTAMPTZ,
    active_to           TIMESTAMPTZ,
    visibility          VARCHAR(20)     NOT NULL DEFAULT 'public',
    created_by_id       UUID            NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_geofeedback_campaign
        FOREIGN KEY (campaign_id) REFERENCES campaign(id) ON DELETE CASCADE,
    CONSTRAINT fk_geofeedback_custom_form
        FOREIGN KEY (custom_form_id) REFERENCES custom_form(id) ON DELETE SET NULL,
    CONSTRAINT fk_geofeedback_context
        FOREIGN KEY (context_id) REFERENCES geocontext(id) ON DELETE SET NULL,
    CONSTRAINT fk_geofeedback_created_by
        FOREIGN KEY (created_by_id) REFERENCES auth_user(id) ON DELETE RESTRICT,
    CONSTRAINT chk_geofeedback_mode
        CHECK (rating_enabled OR form_enabled),
    CONSTRAINT chk_geofeedback_drawings
        CHECK (NOT allow_drawings OR form_enabled)
);

CREATE INDEX idx_geofeedback_campaign ON geofeedback(campaign_id);
CREATE INDEX idx_geofeedback_active ON geofeedback(active_from, active_to);
CREATE INDEX idx_geofeedback_visibility ON geofeedback(visibility);  -- Filter by public/private

CREATE TRIGGER trg_geofeedback_updated_at
    BEFORE UPDATE ON geofeedback
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

##### M2M: feedback_layers

```sql
CREATE TABLE feedback_layers (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    feedback_id     UUID            NOT NULL,
    layer_id        UUID            NOT NULL,
    display_order   INTEGER         NOT NULL DEFAULT 0,

    CONSTRAINT fk_feedback_layers_feedback
        FOREIGN KEY (feedback_id) REFERENCES geofeedback(id) ON DELETE CASCADE,
    CONSTRAINT fk_feedback_layers_layer
        FOREIGN KEY (layer_id) REFERENCES layerref(id) ON DELETE CASCADE,
    CONSTRAINT uq_feedback_layers
        UNIQUE (feedback_id, layer_id)
);

CREATE INDEX idx_feedback_layers_feedback ON feedback_layers(feedback_id);
```

##### Django Model

```python
class GeoFeedback(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        PRIVATE = 'private', 'Private'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='feedbacks'
    )
    title = models.CharField(max_length=255)
    rating_enabled = models.BooleanField(default=True)
    form_enabled = models.BooleanField(default=False)
    allow_drawings = models.BooleanField(default=False)
    custom_form = models.ForeignKey(
        'formbuilder.CustomForm', on_delete=models.SET_NULL, null=True, blank=True
    )
    context = models.OneToOneField(
        GeoContext, on_delete=models.SET_NULL, null=True, blank=True
    )
    layers = models.ManyToManyField(
        LayerRef, through='FeedbackLayer', related_name='feedbacks'
    )
    active_from = models.DateTimeField(null=True, blank=True)
    active_to = models.DateTimeField(null=True, blank=True)
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    created_by = models.ForeignKey(
        User, on_delete=models.RESTRICT, related_name='created_feedbacks'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'geofeedback'
        indexes = [
            models.Index(fields=['campaign']),
            models.Index(fields=['active_from', 'active_to']),
        ]

    def clean(self):
        if not self.rating_enabled and not self.form_enabled:
            raise ValidationError("At least one of rating_enabled or form_enabled must be True.")
        if self.allow_drawings and not self.form_enabled:
            raise ValidationError("allow_drawings requires form_enabled to be True.")
        if self.form_enabled and not self.custom_form:
            raise ValidationError("custom_form is required when form_enabled is True.")


class FeedbackLayer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feedback = models.ForeignKey(GeoFeedback, on_delete=models.CASCADE)
    layer = models.ForeignKey(LayerRef, on_delete=models.CASCADE)
    display_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'feedback_layers'
        unique_together = ['feedback', 'layer']
        ordering = ['display_order']
```

##### Design Notes

- **Three modes**: `rating_enabled` only, `form_enabled` only, or both (composite)
- **`allow_drawings`** requires `form_enabled` — users can draw geometries on the map
- **`custom_form` generated via package** — the structure of a form is controlled via `django-basic-form-builder`'s `CustomForm` model.
- **`custom_form` required for forms** — validated in `clean()` method
- **`custom_form` nullable** when only rating is enabled
- **Layers M2M** — same pattern as GeoStory

---

#### **FeatureLink**

Explicit relationships between entities within a campaign (Story ↔ Event, Story ↔ Feedback).

##### Schema Definition

| Column                   | PostgreSQL Type | Nullable | Default             | Description                           |
| ------------------------ | --------------- | -------- | ------------------- | ------------------------------------- |
| `id`                     | `UUID`          | NOT NULL | `gen_random_uuid()` | Primary key                           |
| `campaign_id`            | `UUID`          | NOT NULL | —                   | FK → `campaign.id` (enforce boundary) |
| `source_content_type_id` | `INTEGER`       | NOT NULL | —                   | FK → `django_content_type.id`         |
| `source_object_id`       | `UUID`          | NOT NULL | —                   | ID of source (Story/Event/Feedback)   |
| `target_content_type_id` | `INTEGER`       | NOT NULL | —                   | FK → `django_content_type.id`         |
| `target_object_id`       | `UUID`          | NOT NULL | —                   | ID of target (Story/Event/Feedback)   |
| `link_type`              | `VARCHAR(20)`   | NOT NULL | `'related'`         | `direct`, `read_more`, `action`       |
| `created_by_id`          | `UUID`          | NOT NULL | —                   | FK → `auth_user.id`                   |
| `created_at`             | `TIMESTAMPTZ`   | NOT NULL | `NOW()`             | Creation timestamp                    |

##### Constraints

| Constraint                | Type        | Definition                                                                                      |
| ------------------------- | ----------- | ----------------------------------------------------------------------------------------------- |
| `pk_featurelink`          | PRIMARY KEY | `(id)`                                                                                          |
| `fk_featurelink_campaign` | FOREIGN KEY | `campaign_id REFERENCES campaign(id) ON DELETE CASCADE`                                         |
| `fk_featurelink_creator`  | FOREIGN KEY | `created_by_id REFERENCES auth_user(id) ON DELETE RESTRICT`                                     |
| `chk_no_self_link`        | CHECK       | `NOT (source_content_type_id = target_content_type_id AND source_object_id = target_object_id)` |

##### Indexes

| Index Name                 | Columns                                    | Type   | Purpose            |
| -------------------------- | ------------------------------------------ | ------ | ------------------ |
| `idx_featurelink_campaign` | `campaign_id`                              | B-tree | Filter by campaign |
| `idx_featurelink_source`   | `source_content_type_id, source_object_id` | B-tree | Forward lookup     |
| `idx_featurelink_target`   | `target_content_type_id, target_object_id` | B-tree | Reverse lookup     |

##### PostgreSQL DDL

```sql
CREATE TABLE featurelink (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id             UUID            NOT NULL,

    -- Generic Foreign Key (Source)
    source_content_type_id  INTEGER         NOT NULL,
    source_object_id        UUID            NOT NULL,

    -- Generic Foreign Key (Target)
    target_content_type_id  INTEGER         NOT NULL,
    target_object_id        UUID            NOT NULL,

    link_type               VARCHAR(20)     NOT NULL DEFAULT 'related',
    created_by_id           UUID            NOT NULL,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_featurelink_campaign
        FOREIGN KEY (campaign_id) REFERENCES campaign(id) ON DELETE CASCADE,
    CONSTRAINT fk_featurelink_creator
        FOREIGN KEY (created_by_id) REFERENCES auth_user(id) ON DELETE RESTRICT,
    CONSTRAINT chk_no_self_link
        CHECK (NOT (source_content_type_id = target_content_type_id AND source_object_id = target_object_id)),
    CONSTRAINT uq_featurelink_no_duplicates
        UNIQUE (source_content_type_id, source_object_id, target_content_type_id, target_object_id)
);

CREATE INDEX idx_featurelink_campaign ON featurelink(campaign_id);
CREATE INDEX idx_featurelink_source ON featurelink(source_content_type_id, source_object_id);
CREATE INDEX idx_featurelink_target ON featurelink(target_content_type_id, target_object_id);

-- ============================================================================
-- TRIGGER VALIDATION: Validate linked entities exist and belong to same campaign
-- ============================================================================

CREATE OR REPLACE FUNCTION validate_featurelink()
RETURNS TRIGGER AS $$
DECLARE
    source_campaign_id UUID;
    target_campaign_id UUID;
    source_table_name TEXT;
    target_table_name TEXT;
    entity_exists BOOLEAN;
BEGIN
    -- Get content type model names from django_content_type
    SELECT model INTO source_table_name
    FROM django_content_type WHERE id = NEW.source_content_type_id;

    SELECT model INTO target_table_name
    FROM django_content_type WHERE id = NEW.target_content_type_id;

    -- Validate source entity exists and get its campaign_id
    CASE source_table_name
        WHEN 'geostory' THEN
            SELECT campaign_id INTO source_campaign_id FROM geostory WHERE id = NEW.source_object_id;
        WHEN 'calendarevent' THEN
            SELECT campaign_id INTO source_campaign_id FROM calendarevent WHERE id = NEW.source_object_id;
        WHEN 'geofeedback' THEN
            SELECT campaign_id INTO source_campaign_id FROM geofeedback WHERE id = NEW.source_object_id;
        ELSE
            RAISE EXCEPTION 'Invalid source content type: %', source_table_name;
    END CASE;

    IF source_campaign_id IS NULL THEN
        RAISE EXCEPTION 'Source entity not found: % with id %', source_table_name, NEW.source_object_id;
    END IF;

    -- Validate target entity exists and get its campaign_id
    CASE target_table_name
        WHEN 'geostory' THEN
            SELECT campaign_id INTO target_campaign_id FROM geostory WHERE id = NEW.target_object_id;
        WHEN 'calendarevent' THEN
            SELECT campaign_id INTO target_campaign_id FROM calendarevent WHERE id = NEW.target_object_id;
        WHEN 'geofeedback' THEN
            SELECT campaign_id INTO target_campaign_id FROM geofeedback WHERE id = NEW.target_object_id;
        ELSE
            RAISE EXCEPTION 'Invalid target content type: %', target_table_name;
    END CASE;

    IF target_campaign_id IS NULL THEN
        RAISE EXCEPTION 'Target entity not found: % with id %', target_table_name, NEW.target_object_id;
    END IF;

    -- Validate both entities belong to the same campaign as the link
    IF source_campaign_id != NEW.campaign_id THEN
        RAISE EXCEPTION 'Source entity belongs to different campaign (expected %, got %)',
            NEW.campaign_id, source_campaign_id;
    END IF;

    IF target_campaign_id != NEW.campaign_id THEN
        RAISE EXCEPTION 'Target entity belongs to different campaign (expected %, got %)',
            NEW.campaign_id, target_campaign_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_featurelink_validate
    BEFORE INSERT OR UPDATE ON featurelink
    FOR EACH ROW
    EXECUTE FUNCTION validate_featurelink();

-- Note: This trigger validates:
--   1. Source object exists in geostory, calendarevent, or geofeedback
--   2. Target object exists in geostory, calendarevent, or geofeedback
--   3. Both source and target belong to the same campaign as the link
--   4. Content types must be one of the allowed models
```

##### Django Model

```python
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class FeatureLink(models.Model):
    class LinkType(models.TextChoices):
        DIRECT = 'direct', 'Direct Link'
        READ_MORE = 'read_more', 'Read More'
        ACTION = 'action', 'Take Action'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name='feature_links'
    )

    # Source Entity
    source_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name='link_sources'
    )
    source_object_id = models.UUIDField()
    source_object = GenericForeignKey('source_content_type', 'source_object_id')

    # Target Entity
    target_content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name='link_targets'
    )
    target_object_id = models.UUIDField()
    target_object = GenericForeignKey('target_content_type', 'target_object_id')

    link_type = models.CharField(
        max_length=20, choices=LinkType.choices, default=LinkType.RELATED
    )
    created_by = models.ForeignKey(
        User, on_delete=models.RESTRICT, related_name='created_links'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'featurelink'
        indexes = [
            models.Index(fields=['campaign']),
            models.Index(fields=['source_content_type', 'source_object_id']),
            models.Index(fields=['target_content_type', 'target_object_id']),
        ]

    def clean(self):
        if self.source_content_type == self.target_content_type and \
           self.source_object_id == self.target_object_id:
            raise ValidationError("Cannot link a feature to itself.")
```

**Constraints:**

- `UNIQUE (source_type, source_id, target_type, target_id)` — no duplicate links
- Application-level validation ensures source/target entities exist

---

#### **FeedbackSubmission**

User responses to feedback forms.

| Column        | Type                    | Nullable | Description                          |
| ------------- | ----------------------- | -------- | ------------------------------------ |
| `id`          | UUID                    | No       | Primary key                          |
| `feedback_id` | UUID (FK → GeoFeedback) | No       | Parent feedback form                 |
| `user_id`     | UUID (FK → User)        | Yes      | Null for anonymous submissions       |
| `rating`      | INTEGER                 | Yes      | 1-5 star rating (if enabled)         |
| `form_data`   | JSONB                   | Yes      | Form responses matching `CustomForm` |
| `geometry`    | GEOMETRY(Any, 4326)     | Yes      | Optional location of submission      |
| `anonymized`  | BOOLEAN                 | No       | Whether user data has been scrubbed  |
| `created_at`  | TIMESTAMPTZ             | No       | Submission timestamp                 |

**Constraints:**

- `CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5))`

---

## **5. API Design**

### **Core Endpoints (REST/JSON)**

| **Endpoint**                                        | **Method**       | **Purpose**                                                    |
| --------------------------------------------------- | ---------------- | -------------------------------------------------------------- |
| /api/campaigns/                                     | GET, POST        | Create/list campaigns with mission metadata.                   |
| /api/campaigns/{id}/                                | GET, PATCH       | Retrieve or update campaign detail.                            |
| /api/campaigns/{id}/summary/                        | GET              | Aggregated KPIs (stories count, participation, sentiment).     |
| /api/stories/                                       | GET, POST, PATCH | CRUD Geostories scoped by campaign.                            |
| /api/stories/{id}/events/                           | GET              | List events directly linked to a story.                        |
| /api/events/                                        | GET, POST        | Create/list calendar events (filterable by campaign or story). |
| /api/feedback/                                      | GET, POST        | Create/manage feedback configurations (campaign-scoped).       |
| /api/feedback/{id}/submit/                          | POST             | Submit user feedback.                                          |
| /api/feature-links/                                 | POST, DELETE     | Manage curated direct links (enforces same campaign).          |
| /api/feature-links?source_type=story&source_id=UUID | GET              | Fetch direct links for rendering UI sections.                  |
| /api/analytics/{story_id}/                          | GET              | Return aggregated impact summaries (story-level).              |
| /api/analytics/campaigns/{id}/                      | GET              | Campaign-level impact summaries.                               |

All creation endpoints (`stories`, `events`, `feedback`) require a `campaign` UUID in the payload. The API responds with the `campaign_id` plus pre-hydrated arrays for `direct_links` (queried from `feature_links`) so the frontend can render “Direct” and “More from this campaign” sections without extra joins.

### **Integration APIs**

- /api/hooks/geostory-updated/ — triggers analytics recalculation.
- /api/hooks/event-finished/ — triggers post-event feedback.
- /api/ai/summary/{story_id}/ — optional AI service for summarizing feedback text.

---

## **6. Frontend Architecture**

| **Layer**                    | **Description**                            | **Example Components**                               |
| ---------------------------- | ------------------------------------------ | ---------------------------------------------------- |
| **UI Framework**             | Vue 3 + Composition API + Tailwind CSS.    | GeoStoryView.vue, CalendarView.vue, FeedbackForm.vue |
| **Map Layer**                | MapLibre GL integrated with Pinia store.   | useMapStore, LayerManager, StoryHighlight            |
| **State Management (Pinia)** | Stores for modular entities.               | useStoryStore, useEventStore, useFeedbackStore       |
| **Routing (Vue Router)**     | Structured routes per feature.             | /stories/:id, /events, /feedback/:id                 |
| **Interaction Controls**     | Map click events, feature linking, popups. | onFeatureClick() → open StorySidebar()               |
| **Analytics UI**             | Charts and maps for outcome summaries.     | StoryImpactPanel.vue, EventStats.vue                 |

---

## **7. Backend Architecture (Django)**

### **App Modules**

```
/apps
 ├── stories/         (Geostories models, serializers, views)
 ├── events/          (Calendar events)
 ├── feedback/        (Forms, submissions)
 ├── analytics/       (aggregations, caching)
 ├── media/           (asset mgmt)
 ├── users/           (auth, roles)
 └── common/          (utils, signals, mixins)
```

### **Core Services**

- **Celery Worker:** asynchronous analytics aggregation, email notifications.
- **Redis:** task queue and cache for frequent story/event queries.
- **PostGIS:** geometry storage and spatial queries for linking stories, events, and feedback.
- **GeoServer or Vector Tile Service:** serves map data for story regions.
- **S3/MinIO:** handles uploaded media (images, documents, videos).

---

## **8. Analytics Flow**

#### **FeedbackSubmission**

Individual user submissions for a GeoFeedback form.

##### Schema Definition

| Column               | PostgreSQL Type            | Nullable | Default             | Description                                |
| -------------------- | -------------------------- | -------- | ------------------- | ------------------------------------------ |
| `id`                 | `UUID`                     | NOT NULL | `gen_random_uuid()` | Primary key                                |
| `feedback_id`        | `UUID`                     | NOT NULL | —                   | FK → `geofeedback.id`                      |
| `user_id`            | `UUID`                     | NULL     | `NULL`              | FK → `auth_user.id` (if authenticated)     |
| `rating`             | `INTEGER`                  | NULL     | `NULL`              | 1-5 stars (if rating enabled)              |
| `selected_option_id` | `UUID`                     | NULL     | `NULL`              | FK → `feedbackoption.id` (if form enabled) |
| `form_data`          | `JSONB`                    | NULL     | `NULL`              | Form field responses                       |
| `geometry`           | `GEOMETRY(Geometry, 4326)` | NULL     | `NULL`              | User-drawn geometry (Point, Line, Polygon) |
| `is_anonymized`      | `BOOLEAN`                  | NOT NULL | `FALSE`             | If user requested anonymity                |
| `created_at`         | `TIMESTAMPTZ`              | NOT NULL | `NOW()`             | Submission timestamp                       |

##### Constraints

| Constraint                       | Type        | Definition                                                            |
| -------------------------------- | ----------- | --------------------------------------------------------------------- |
| `pk_feedbacksubmission`          | PRIMARY KEY | `(id)`                                                                |
| `fk_feedbacksubmission_feedback` | FOREIGN KEY | `feedback_id REFERENCES geofeedback(id) ON DELETE CASCADE`            |
| `fk_feedbacksubmission_user`     | FOREIGN KEY | `user_id REFERENCES auth_user(id) ON DELETE SET NULL`                 |
| `fk_feedbacksubmission_option`   | FOREIGN KEY | `selected_option_id REFERENCES feedbackoption(id) ON DELETE SET NULL` |
| `chk_feedbacksubmission_rating`  | CHECK       | `rating IS NULL OR (rating >= 1 AND rating <= 5)`                     |

##### Indexes

| Index Name                        | Columns              | Type   | Purpose                      |
| --------------------------------- | -------------------- | ------ | ---------------------------- |
| `idx_feedbacksubmission_feedback` | `feedback_id`        | B-tree | Filter by feedback           |
| `idx_feedbacksubmission_user`     | `user_id`            | B-tree | Filter by user               |
| `idx_feedbacksubmission_option`   | `selected_option_id` | B-tree | Filter by sentiment/category |
| `idx_feedbacksubmission_geom`     | `geometry`           | GiST   | Spatial analysis             |

##### PostgreSQL DDL

```sql
CREATE TABLE feedbacksubmission (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    feedback_id         UUID            NOT NULL,
    user_id             UUID,
    rating              INTEGER,
    selected_option_id  UUID,
    form_data           JSONB           DEFAULT '{}',
    location            GEOMETRY(Geometry, 4326),
    is_anonymized       BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_feedbacksubmission_feedback
        FOREIGN KEY (feedback_id) REFERENCES geofeedback(id) ON DELETE CASCADE,
    CONSTRAINT fk_feedbacksubmission_user
        FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE SET NULL,
    CONSTRAINT fk_feedbacksubmission_option
        FOREIGN KEY (selected_option_id) REFERENCES feedbackoption(id) ON DELETE SET NULL,
    CONSTRAINT chk_feedbacksubmission_rating
        CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    CONSTRAINT chk_feedbacksubmission_location_form
        CHECK (location IS NOT NULL OR form_data IS NULL OR form_data = '{}'::jsonb)
);

CREATE INDEX idx_feedbacksubmission_feedback ON feedbacksubmission(feedback_id);
CREATE INDEX idx_feedbacksubmission_user ON feedbacksubmission(user_id);
CREATE INDEX idx_feedbacksubmission_option ON feedbacksubmission(selected_option_id);
CREATE INDEX idx_feedbacksubmission_loc ON feedbacksubmission USING GIST(location);
```

##### Django Model

```python
class FeedbackSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feedback = models.ForeignKey(
        GeoFeedback, on_delete=models.CASCADE, related_name='submissions'
    )
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    rating = models.IntegerField(null=True, blank=True)
    selected_option = models.ForeignKey(
        FeedbackOption, on_delete=models.SET_NULL, null=True, blank=True
    )
    form_data = models.JSONField(default=dict, blank=True, null=True)
    geometry = models.GeometryField(srid=4326, blank=True, null=True)
    is_anonymized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'feedbacksubmission'
        indexes = [
            models.Index(fields=['feedback']),
            models.Index(fields=['selected_option']),
        ]

    def clean(self):
        # Validate based on parent feedback configuration
        if self.feedback.rating_enabled and self.rating is None:
             pass # Logic depends on if rating is mandatory

        if self.feedback.form_enabled:
             if not self.location:
                  # Location is mandatory if form is enabled
                  raise ValidationError("Location is mandatory for form feedback.")

             if self.selected_option and \
                self.selected_option.feedback_type != self.feedback.feedback_type:
                  raise ValidationError("Selected option does not match feedback type.")

##### Design Notes

- **`location`**: The pin on the map. Mandatory **only if** `form_enabled=True`. Optional for rating-only feedback.
- **`allow_drawings`**: If true, users can draw additional geometries (lines/polygons) inside the form. These are stored within `form_data` (FeatureCollection), NOT in the `location` column.
```

---

```
1. Event or feedback submission received.
2. Signal triggers Celery task.
3. Task aggregates results grouped by:
     - Campaign ID
     - Geostory ID
     - Event ID
     - Time period
4. Results stored in AnalyticsSnapshot table.
5. Frontend fetches metrics for dashboards:
     - Ratings distribution
     - Participation counts
     - Sentiment summaries
6. Updated GeoStory and Campaign views display impact metrics.
```

---

## **9. Integration & Extensibility**

### **9.1 Layer Management Strategy: Transition from Legacy**

We have modernized the integration with GeoServer to support per-feature layer ordering and user-controlled syncing.

#### **Before: Legacy Model (`tosca-geonode-main/cpt`)**

- **Architecture**: A simple `GeoserverLayers` model storing just `layer_name`.
- **Relationship**: Layers were linked directly to the `Campaign`. All stories in a campaign had to share the exact same map context.
- **Sync**: Relied on a rigid `update_layers` cron job that auto-synced the entire catalog, often leading to clutter or stale data.

#### **After: Fine-Grained `LayerRef` Architecture**

- **Architecture**: Enriched `LayerRef` model with `display_name` and explicit ordering support.
- **Relationship**: Layers are now linked to **specific features** (GeoStory, CalendarEvent, GeoFeedback) via junction tables (`geostory_layers`, etc.).
- **Benefit**: This allows a Story to show "Roads + Floods", while an Event in the same campaign shows "Shelters + Traffic", with each author controlling the exact Z-index (stacking order) of layers.

#### **Why Manual Sync?**

We adopted a **Manual User-Triggered Sync** strategy (`POST /api/layers/sync/`) via a UI button because:

1.  **Control**: Authors see new layers immediately when _they_ need them, without waiting for a cron job.
2.  **Performance**: Avoids expensive periodic API calls to GeoServer catalog.
3.  **Simplicity**: Reduces background worker complexity and synchronization race conditions.

---

## **10. Security & Compliance**

| **Aspect**          | **Implementation**                                            |
| ------------------- | ------------------------------------------------------------- |
| **Authentication**  | JWT or OAuth2 tokens via Django REST Framework.               |
| **Authorization**   | Role-based access control (admin, editor, organizer, public). |
| **GDPR compliance** | Pseudonymized feedback storage; explicit consent checkboxes.  |
| **Data separation** | Feedback stored separately from user profiles.                |
| **HTTPS + CSP**     | Enforced for all routes.                                      |

---

## **11. Deployment & Infrastructure**

| **Component** | **Service**                      | **Notes**                                       |
| ------------- | -------------------------------- | ----------------------------------------------- |
| Backend       | Django + Gunicorn + Nginx        | API, admin, async worker.                       |
| Database      | PostgreSQL + PostGIS + PgBouncer | Spatial queries, analytics, connection pooling. |
| Task Queue    | Celery + Redis                   | Background processing.                          |
| File Storage  | S3 / MinIO                       | Media & file uploads.                           |
| Map Services  | GeoServer or TileServer GL       | Vector tile delivery.                           |
| Frontend      | Static Vue build served via CDN  | SPA client.                                     |

Deployment pipelines via **GitHub Actions or GitLab CI**, containerized using **Docker Compose or Kubernetes**.

---

## **12. Extensible Data Relationships**

### **12.1 Parent-Child Relationships (via Foreign Key)**

These are structural relationships enforced by FK constraints:

| **Parent**    | **Child**          | **Cardinality** | **Description**                         |
| ------------- | ------------------ | --------------- | --------------------------------------- |
| Campaign      | GeoStory           | 1–N             | Campaign hosts multiple stories         |
| Campaign      | CalendarEvent      | 1–N             | Campaign hosts multiple events          |
| Campaign      | GeoFeedback        | 1–N             | Campaign hosts multiple feedback forms  |
| Campaign      | FeatureLink        | 1–N             | Links scoped to campaign for validation |
| GeoFeedback   | FeedbackSubmission | 1–N             | Feedback form receives submissions      |
| FeedbackType  | FeedbackOption     | 1–N             | Type has multiple options               |
| GeoStory      | GeoContext         | 1–1             | Story has optional rich content         |
| CalendarEvent | GeoContext         | 1–1             | Event has optional rich content         |
| GeoFeedback   | GeoContext         | 1–1             | Feedback has optional rich content      |

### **12.2 Direct Linking (via FeatureLink)**

These are explicit M:N relationships between features, enabling cross-referencing within a campaign:

| **Source**    | **Target**    | **Cardinality** | **Description**                                   |
| ------------- | ------------- | --------------- | ------------------------------------------------- |
| GeoStory      | GeoStory      | M–N             | Stories can reference other stories (except self) |
| GeoStory      | CalendarEvent | M–N             | Stories can link to related events                |
| GeoStory      | GeoFeedback   | M–N             | Stories can link to feedback forms                |
| CalendarEvent | GeoStory      | M–N             | Events can reference related stories              |
| CalendarEvent | CalendarEvent | M–N             | Events can link to other events (except self)     |
| CalendarEvent | GeoFeedback   | M–N             | Events can link to feedback forms                 |
| GeoFeedback   | GeoStory      | M–N             | Feedback can reference related stories            |
| GeoFeedback   | CalendarEvent | M–N             | Feedback can link to related events               |
| GeoFeedback   | GeoFeedback   | M–N             | Feedback can link to other forms (except self)    |

> [!NOTE]
> **FeatureLink Rules:**
>
> - Source and target must belong to the **same campaign**
> - An entity **cannot link to itself** (enforced by CHECK constraint)
> - No duplicate links (enforced by UNIQUE constraint)
> - Links are **directional** (source → target), but can be created bidirectionally
> - Validated by `validate_featurelink()` trigger for referential integrity

### **12.3 Layer Associations (via M:M Junction Tables)**

| **Entity**    | **LayerRef** | **Junction Table** | **Description**                   |
| ------------- | ------------ | ------------------ | --------------------------------- |
| GeoStory      | LayerRef     | `geostory_layers`  | Story displays selected layers    |
| CalendarEvent | LayerRef     | `event_layers`     | Event displays selected layers    |
| GeoFeedback   | LayerRef     | `feedback_layers`  | Feedback displays selected layers |

## **13. Technical Benefits**

- **Shared foundation:** all participation features built from reusable primitives.
- **Loose coupling:** Geostories, Events, and Feedback work independently but link through shared spatial IDs.
- **Low-code authoring:** new stories and events created via UI, no deployment required.
- **Analytics ready:** built-in data model supports KPI dashboards, exports, and machine learning later.

---

## **14. Summary**

The **Geostories × Calendar × Participation** architecture is modular, scalable, and designed for long-term sustainability.

It integrates narrative, spatial, and participatory data within one ecosystem, while maintaining clear boundaries between frontend, backend, and data services.

**Key principle:**

> “Every story can become an event. Every event can generate feedback. Every feedback makes the story smarter — and every campaign keeps the loop aligned with its mission.”

This approach transforms a static information system into a **self-learning participation platform** — technically robust, socially transparent, and ready for cross-sector collaboration.

---

## **15. Developer Checklist: Security, Query Efficiency & Common Pitfalls**

> [!IMPORTANT]
> Use this checklist during development to ensure database operations are secure, efficient, and maintainable.

---

### **15.1 Security Checklist**

#### ✅ SQL Injection Prevention

| Check                                 | Status | Notes                                        |
| ------------------------------------- | ------ | -------------------------------------------- |
| Use Django ORM for all queries        | ⬜     | ORM uses parameterized queries automatically |
| Never use f-strings in raw SQL        | ⬜     | Always use `cursor.execute(sql, params)`     |
| Validate user input before queries    | ⬜     | Especially UUIDs, enums, and filters         |
| Escape layer_name for GeoServer calls | ⬜     | Could be injection vector in external APIs   |

```python
# ❌ DANGEROUS - SQL Injection vulnerable
cursor.execute(f"SELECT * FROM geostory WHERE id = '{user_input}'")

# ✅ SAFE - Parameterized query
cursor.execute("SELECT * FROM geostory WHERE id = %s", [user_input])

# ✅ SAFE - Django ORM
GeoStory.objects.filter(id=user_input)
```

#### ✅ JSONB Validation

| Check                               | Status | Notes                                         |
| ----------------------------------- | ------ | --------------------------------------------- |
| Validate `form_schema` structure    | ⬜     | Use JSON Schema validation in `clean()`       |
| Validate `form_data` against schema | ⬜     | Ensure submitted data matches expected fields |
| Limit JSONB depth and size          | ⬜     | Prevent DoS via deeply nested objects         |

```python
import jsonschema

FORM_SCHEMA_VALIDATOR = {
    "type": "object",
    "properties": {
        "fields": {"type": "array"},
        "title": {"type": "string", "maxLength": 255}
    },
    "additionalProperties": False
}

def clean(self):
    try:
        jsonschema.validate(self.form_schema, FORM_SCHEMA_VALIDATOR)
    except jsonschema.ValidationError as e:
        raise ValidationError(f"Invalid form_schema: {e.message}")
```

#### ✅ XSS Prevention

| Check                                  | Status | Notes                                           |
| -------------------------------------- | ------ | ----------------------------------------------- |
| Escape `geocontext.content` on render  | ⬜     | Use Django's `escape` or `bleach` for rich text |
| Sanitize HTML if `content_type='rich'` | ⬜     | Whitelist allowed HTML tags                     |
| Never trust `form_data` text values    | ⬜     | Escape before display in frontend               |

```python
import bleach

ALLOWED_TAGS = ['p', 'br', 'strong', 'em', 'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3']
ALLOWED_ATTRS = {'a': ['href', 'title']}

def safe_content(self):
    if self.content_type == 'rich':
        return bleach.clean(self.content, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)
    return escape(self.content)
```

#### ✅ Input Validation

| Check                                  | Status | Notes                         |
| -------------------------------------- | ------ | ----------------------------- |
| Validate UUIDs before database queries | ⬜     | Reject malformed UUIDs early  |
| Validate enum values                   | ⬜     | Status, visibility, link_type |
| Validate geometry WKT/GeoJSON          | ⬜     | Use PostGIS `ST_IsValid()`    |
| Rate limit anonymous feedback          | ⬜     | Prevent spam/abuse            |

```python
from django.core.exceptions import ValidationError
import uuid

def validate_uuid(value):
    try:
        uuid.UUID(str(value))
    except ValueError:
        raise ValidationError(f"'{value}' is not a valid UUID")
```

---

### **15.2 Query Efficiency Checklist**

#### ✅ Use Proper Indexes

| Query Pattern         | Required Index                        | Status    |
| --------------------- | ------------------------------------- | --------- |
| Filter by campaign    | `idx_*_campaign`                      | ✅ Exists |
| Filter by status      | `idx_*_status`                        | ✅ Exists |
| Filter by date range  | `idx_calendarevent_dates`             | ✅ Exists |
| Campaign + date range | `idx_calendarevent_campaign_dates`    | ✅ Exists |
| Spatial queries       | GiST on geometry columns              | ✅ Exists |
| Pagination by date    | `idx_geostory_created_at`             | ✅ Exists |
| FeatureLink lookups   | Composite on content_type + object_id | ✅ Exists |

#### ✅ Prevent N+1 Queries

```python
# ❌ N+1 Problem - Each iteration triggers a query
stories = GeoStory.objects.filter(campaign=campaign)
for story in stories:
    print(story.context.content)     # Query per story
    print(story.campaign.title)      # Another query per story

# ✅ Fixed - Use select_related for FK/OneToOne
stories = GeoStory.objects.filter(campaign=campaign).select_related(
    'context', 'campaign', 'author', 'cover_media'
)

# ✅ For M2M relationships, use prefetch_related
stories = GeoStory.objects.filter(campaign=campaign).prefetch_related('layers')
```

#### ✅ Optimize Common Queries

| Endpoint                  | Optimization                              | Status |
| ------------------------- | ----------------------------------------- | ------ |
| List stories by campaign  | Use `select_related('context', 'author')` | ⬜     |
| List events in date range | Use compound index                        | ⬜     |
| Fetch feature links       | Use composite index lookups               | ⬜     |
| Count submissions         | Use `COUNT(*)` not `len(queryset)`        | ⬜     |

```python
# ❌ Inefficient - Loads all objects into memory
count = len(FeedbackSubmission.objects.filter(feedback=feedback))

# ✅ Efficient - Database does the counting
count = FeedbackSubmission.objects.filter(feedback=feedback).count()
```

#### ✅ Pagination Best Practices

```python
# ❌ Offset pagination is slow for large datasets
# Page 10000 requires scanning 100000 rows
GeoStory.objects.all()[100000:100010]

# ✅ Cursor-based pagination with indexed column (REQUIRED)
last_seen = request.GET.get('cursor')
# Use DRF's CursorPagination
```

---

### **15.3 Common Pitfalls**

#### ⚠️ GenericForeignKey Limitations

```python
# ❌ Can't filter directly on GenericForeignKey target
links = FeatureLink.objects.filter(source_object__title='Test')  # Won't work!

# ✅ Must filter by content_type and object_id
from django.contrib.contenttypes.models import ContentType
geostory_ct = ContentType.objects.get_for_model(GeoStory)
story_ids = GeoStory.objects.filter(title='Test').values_list('id', flat=True)
links = FeatureLink.objects.filter(
    source_content_type=geostory_ct,
    source_object_id__in=story_ids
)
```

#### ⚠️ Geometry Validation

```python
# ❌ Invalid geometry can crash queries
region = GEOSGeometry('POLYGON((0 0, 0 1, 1 0, 0 0))')  # Not closed properly

# ✅ Validate geometry before saving
from django.contrib.gis.geos import GEOSGeometry

def clean_location(self):
    if self.location and not self.location.valid:
        self.location = self.location.buffer(0)  # Fix geometry
```

#### ⚠️ 1:1 Relationship Gotchas

```python
# The UNIQUE constraint on context_id enforces 1:1
# BUT you must create GeoContext first

# ❌ Fails - GeoContext doesn't exist
story = GeoStory.objects.create(
    campaign=campaign,
    title='Test',
    context_id=some_uuid  # Invalid FK!
)

# ✅ Create context first, then link
context = GeoContext.objects.create(content='...', created_by=user)
story = GeoStory.objects.create(
    campaign=campaign,
    title='Test',
    context=context  # Valid
)
```

#### ⚠️ Migrating 1:1 to N:N (Future)

If you later need multiple features to share the same GeoContext:

```sql
-- Step 1: Drop UNIQUE constraint
ALTER TABLE geostory DROP CONSTRAINT geostory_context_id_key;
ALTER TABLE calendarevent DROP CONSTRAINT calendarevent_context_id_key;
ALTER TABLE geofeedback DROP CONSTRAINT geofeedback_context_id_key;

-- Step 2: (Optional) Create M2M junction tables if needed
-- CREATE TABLE geostory_contexts (geostory_id, context_id, PRIMARY KEY...)
```

---

### **15.4 Pre-Deployment Checklist**

| Item                                              | Status | Notes                                   |
| ------------------------------------------------- | ------ | --------------------------------------- |
| Run `EXPLAIN ANALYZE` on critical queries         | ⬜     | Ensure indexes are used                 |
| Enable `log_min_duration_statement` in PostgreSQL | ⬜     | Log slow queries                        |
| Test FeatureLink trigger with edge cases          | ⬜     | Invalid content types, missing entities |
| Verify JSONB size limits                          | ⬜     | Set max size in application layer       |
| Enable HTTPS in production                        | ⬜     | Mandatory for all routes                |
| Configure rate limiting for `/feedback/*/submit/` | ⬜     | Prevent anonymous abuse                 |
| Set up database connection pooling                | ⬜     | Use PgBouncer for production            |

---
