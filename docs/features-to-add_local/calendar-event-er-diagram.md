# Calendar Event ER Diagram

The following Entity-Relationship diagram outlines the technical database schema for `CalendarEvent` within the TOSCA-Backend. It illustrates how an event integrates with its parent campaign, its author, the core narrative context, map layers, and polymorphic feature links.

```mermaid
erDiagram
    %% Core Entity
    CALENDAR-EVENT {
        UUID id PK
        CharField title
        TextField description
        DateTimeField start_datetime
        DateTimeField end_datetime
        PointField location "Optional (SRID 4326)"
        CharField status "draft | published | cancelled"
        CharField visibility "public | private"
        UUID campaign_id FK
        UUID organizer_id FK
        UUID context_id FK "1:1 relation"
    }

    %% Related Entities
    CAMPAIGN {
        UUID id PK
        CharField name
    }

    USER {
        int id PK
        CharField username
    }

    GEO-CONTEXT {
        UUID id PK
        TextField content
        CharField content_type
    }

    LAYER-REF {
        UUID id PK
        CharField title
    }

    %% Through Model
    EVENT-LAYER {
        UUID id PK
        UUID event_id FK
        UUID layer_id FK
        int display_order
    }

    %% Generic Relations
    FEATURE-LINK {
        UUID id PK
        CharField source_content_type
        UUID source_object_id
        CharField target_content_type
        UUID target_object_id
    }

    %% Relationships
    CAMPAIGN ||--o{ CALENDAR-EVENT : "contains"
    USER ||--o{ CALENDAR-EVENT : "organizes (created_by)"
    GEO-CONTEXT ||--o| CALENDAR-EVENT : "provides rich narrative for"

    CALENDAR-EVENT ||--o{ EVENT-LAYER : "orders layer via"
    LAYER-REF ||--o{ EVENT-LAYER : "is referenced by"

    CALENDAR-EVENT ||--o{ FEATURE-LINK : "polymorphically links to (as source or target)"
```
