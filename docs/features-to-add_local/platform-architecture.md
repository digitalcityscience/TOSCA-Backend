# TOSCA Platform Technical Architecture

This high-level schematic illustrates the structural relationships between the core entity, the three primary feature modules (`GeoStory`, `Calendar Event`, `GeoFeedback Map`), and the shared integration data models within the backend ecosystem.

```mermaid
flowchart TD
    %% Custom Styling
    classDef core fill:#29b6f6,stroke:#0288d1,stroke-width:3px,color:#fff,rx:15,ry:15
    classDef feature fill:#66bb6a,stroke:#388e3c,stroke-width:2px,color:#fff,rx:20,ry:20
    classDef data fill:#ab47bc,stroke:#7b1fa2,stroke-width:2px,color:#fff,rx:5,ry:5
    classDef poly fill:#ffb74d,stroke:#f57c00,stroke-width:2px,color:#000

    %% Core Parent Entity
    C{{"**Campaign**<br/><i>The Core Parent Container</i>"}}:::core

    %% Feature Domains
    subgraph Features ["Primary Feature Modules"]
        direction LR
        S(["**GeoStory**<br/><i>Narrative Focus</i>"]):::feature
        E(["**Calendar Event**<br/><i>Temporal/Location Focus</i>"]):::feature
        F(["**GeoFeedback Map**<br/><i>Citizen Engagement Focus</i>"]):::feature
    end

    %% Shared Integration Models
    subgraph DataIntegration ["Shared Data Layers & Media Context"]
        direction LR
        G["**GeoContext**<br/><i>1:1 Rich Text & Media Content</i>"]:::data
        L["**LayerRefs**<br/><i>M:N OGC Spatial Overlays</i>"]:::data
    end

    %% Polymorphic Networking
    P[["**FeatureLinks**<br/><i>Polymorphic Generic Relationships</i>"]]:::poly

    %% Relationships - Core to Features
    C -->|Scopes & Contains| S
    C -->|Scopes & Contains| E
    C -->|Scopes & Contains| F

    %% Relationships - Features to Data Integrations
    S -.->|Uses 1:1| G
    E -.->|Uses 1:1| G
    F -.->|Uses 1:1| G

    S -.->|Stacks M:N| L
    E -.->|Stacks M:N| L
    F -.->|Stacks M:N| L

    %% Relationships - Polymorphic Cross-linking
    S <==>|Links to/from via| P
    E <==>|Links to/from via| P
    F <==>|Links to/from via| P
```

### Architectural Breakdown

Based on the system's database schema:

1. **The Campaign Node**: All structural data must originate from a `Campaign`. It acts as the definitive scope boundary for stories, events, and feedback campaigns.
2. **The 3 Independent Modules**: `GeoStory`, `CalendarEvent`, and `GeoFeedback` exist as independent relational nodes directly underneath the central Campaign footprint.
3. **Shared Data Layers**: To ensure DRY (Don't Repeat Yourself) database architecture, all three core feature modules share identical structures for embedding rich HTML text (`GeoContext`) and spatial WMS mappings (`LayerRefs`).
4. **The FeatureLink Graph**: Because the modules are horizontally separated from each other inside the database tree, the system relies on the `FeatureLinks` model, acting as a polymorphic relational graph, to let any story point natively to any event or feedback map, and vice versa.
