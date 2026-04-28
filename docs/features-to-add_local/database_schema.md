# GeoStory Event Feedback System - Database Schema

## Entity Relationship Diagram

```mermaid
erDiagram
    %% Core Entities
    CAMPAIGN {
        UUID id PK
        VARCHAR title
        TEXT summary
        ENUM status "draft, active, archived"
        ENUM visibility "public, private"
        UUID created_by_id FK
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    GEOCONTEXT {
        UUID id PK
        TEXT content
        ENUM content_type "simple, rich"
        UUID created_by_id FK
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    LAYERREF {
        UUID id PK
        VARCHAR layer_name UK "workspace:layer format"
        TIMESTAMPTZ created_at
    }

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

    %% Direct Linking (M:N via GenericForeignKey)
    FEATURELINK {
        UUID id PK
        UUID campaign_id FK
        INT source_content_type_id FK
        UUID source_object_id
        INT target_content_type_id FK
        UUID target_object_id
        ENUM link_type "direct, read_more, action"
        UUID created_by_id FK
        TIMESTAMPTZ created_at
    }

    %% Users (Django auth_user)
    USER {
        UUID id PK
        VARCHAR username
        VARCHAR email
    }

    %% =====================================================
    %% RELATIONSHIPS
    %% =====================================================

    %% Parent-Child (FK) Relationships
    CAMPAIGN ||--o{ GEOSTORY : "contains"
    CAMPAIGN ||--o{ CALENDAREVENT : "contains"
    CAMPAIGN ||--o{ GEOFEEDBACK : "contains"
    CAMPAIGN ||--o{ FEATURELINK : "scopes"

    %% 1:1 GeoContext (UNIQUE constraint enforced)
    GEOSTORY ||--o| GEOCONTEXT : "has content (1:1)"
    CALENDAREVENT ||--o| GEOCONTEXT : "has content (1:1)"
    GEOFEEDBACK ||--o| GEOCONTEXT : "has content (1:1)"

    %% Layer Associations (M:N via junction tables)
    GEOSTORY }o--o{ LAYERREF : "displays via geostory_layers"
    CALENDAREVENT }o--o{ LAYERREF : "displays via event_layers"
    GEOFEEDBACK }o--o{ LAYERREF : "displays via feedback_layers"

    %% Feedback Structure
    GEOFEEDBACK }o--o| CUSTOM_FORM : "uses schema from"
    GEOFEEDBACK ||--o{ FEEDBACKSUBMISSION : "receives"

    %% User Relations
    FEEDBACKSUBMISSION }o--o| USER : "submitted by"
    GEOSTORY }o--|| USER : "authored by"
    CALENDAREVENT }o--|| USER : "organized by"
    FEATURELINK }o--|| USER : "created by"
    GEOCONTEXT }o--|| USER : "created by"
    CAMPAIGN }o--|| USER : "created by"
    GEOFEEDBACK }o--|| USER : "created by"
    %% =====================================================
    %% STYLING
    %% =====================================================
    %% Define classes
    classDef core fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
    classDef feature fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    classDef feedback fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;
    classDef system fill:#f5f5f5,stroke:#616161,stroke-width:1px,stroke-dasharray: 5 5;

    %% Apply classes
    class CAMPAIGN,GEOCONTEXT,LAYERREF core;
    class GEOSTORY,CALENDAREVENT,GEOFEEDBACK feature;
    class FEEDBACKSUBMISSION feedback;
    class CUSTOM_FORM system;
    class USER,FEATURELINK system;
```

---

## FeatureLink: Direct Linking Between Features

FeatureLink enables **M:N direct linking** between any feature entities within the same campaign.

### Allowed Link Combinations

| Source        | Target        | Description                       |
| ------------- | ------------- | --------------------------------- |
| GeoStory      | GeoStory      | Story references another story    |
| GeoStory      | CalendarEvent | Story links to related event      |
| GeoStory      | GeoFeedback   | Story links to feedback form      |
| CalendarEvent | GeoStory      | Event references related story    |
| CalendarEvent | CalendarEvent | Event links to another event      |
| CalendarEvent | GeoFeedback   | Event links to feedback form      |
| GeoFeedback   | GeoStory      | Feedback references related story |
| GeoFeedback   | CalendarEvent | Feedback links to related event   |
| GeoFeedback   | GeoFeedback   | Feedback links to another form    |

### Constraints

- **Same campaign**: Source and target must belong to the same campaign
- **No self-links**: An entity cannot link to itself (CHECK constraint)
- **No duplicates**: Each source→target pair is unique (UNIQUE constraint)
- **Trigger validation**: `validate_featurelink()` ensures entities exist

---

## Junction Tables

### geostory_layers

| Column        | Type    | Description          |
| ------------- | ------- | -------------------- |
| id            | UUID PK | Primary key          |
| geostory_id   | UUID FK | References geostory  |
| layer_id      | UUID FK | References layerref  |
| display_order | INT     | Layer stacking order |

UNIQUE(geostory_id, layer_id)

### event_layers

| Column        | Type    | Description              |
| ------------- | ------- | ------------------------ |
| id            | UUID PK | Primary key              |
| event_id      | UUID FK | References calendarevent |
| layer_id      | UUID FK | References layerref      |
| display_order | INT     | Layer stacking order     |

UNIQUE(event_id, layer_id)

### feedback_layers

| Column        | Type    | Description            |
| ------------- | ------- | ---------------------- |
| id            | UUID PK | Primary key            |
| feedback_id   | UUID FK | References geofeedback |
| layer_id      | UUID FK | References layerref    |
| display_order | INT     | Layer stacking order   |

UNIQUE(feedback_id, layer_id)

---

## Key Design Decisions

### 1:1 GeoContext Relationship

Each feature entity (GeoStory, CalendarEvent, GeoFeedback) can have **one optional GeoContext** for rich content. This is enforced by:

- FK constraint on `context_id`
- **UNIQUE constraint** on `context_id` to prevent multiple entities sharing the same context

**Migration to N:N**: If needed later, drop the UNIQUE constraint and optionally create junction tables.

### FeatureLink vs Explicit Junction Tables

We use a **single polymorphic FeatureLink table** instead of explicit junction tables (e.g., `story_event_links`) because:

- Fewer tables to maintain
- Supports future entity types without schema changes
- Trigger validation ensures referential integrity
- Trade-off: No native FK enforcement on linked entities
