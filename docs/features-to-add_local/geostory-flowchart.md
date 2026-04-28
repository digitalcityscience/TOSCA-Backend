# GeoStory Workflow Diagram

Based on the TOSCA-Backend data architecture, the following diagram illustrates the streamlined life-cycle of creating and publishing a GeoStory.

```mermaid
flowchart LR
    %% Define Styles
    classDef startend fill:#f9f9f9,stroke:#333,stroke-width:2px,rx:20,ry:20
    classDef process fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    classDef database fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,shape:cylinder
    classDef optional fill:#fff3e0,stroke:#f57c00,stroke-width:1px,stroke-dasharray: 5 5

    %% Nodes
    A([Start GeoStory Creation]):::startend
    B[Draft Core Narrative Title & Summary]:::process
    C[Attach Geo-referenced Map Layers]:::process
    D[Embed Rich Media into GeoContext]:::process
    E[Link to External Features & Events *Optional*]:::optional
    F[(Save Story to Database)]:::database
    G[Ensure Published Status for User Viewing]:::process
    H([End: GeoStory Live]):::startend

    %% Flow
    A --> B
    B --> C
    C --> D
    D --> E
    E --> F
    F --> G
    G --> H
```
