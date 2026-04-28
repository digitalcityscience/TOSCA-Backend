# PostGIS Performance Checklist

## Production Readiness & Incident Review

### 1. Data Model & Schema

Primary Keys & IDs
• Tables use BIGINT / BIGSERIAL as internal primary keys
• UUIDs (if any) are external identifiers, not PKs
• No composite PKs involving geometry columns

Geometry Columns
• Geometry column has a fixed SRID
• Geometry type is explicit (POLYGON, MULTIPOLYGON, etc.)
• No mixed geometry types in the same column
• No runtime ST_Transform required for common queries

JSONB & Attributes
• JSONB used only for optional metadata
• All frequently filtered attributes are real columns
• No EAV tables for core spatial entities

⸻

### 2. Spatial Indexing

GiST / SP-GiST Indexes
• Every geometry column has a GiST or SP-GiST index
• Index created after bulk loads, not before
• Index size reviewed (pg_relation_size)

Bounding Box Usage
• Queries use bounding box pre-filtering (&&)
• No spatial predicate without index support

Correct pattern:

geom && envelope
AND ST_Intersects(geom, envelope)

Composite Indexes
• Attribute + geometry composite indexes exist where needed
• Index order matches query filter order
• No unused indexes on spatial tables

⸻

### 3. Query Design

Spatial Predicates
• ST_Intersects used only after bounding box filtering
• ST_DWithin preferred for proximity queries
• No ST_Buffer or ST_Union in WHERE clauses

Reprojection
• No ST_Transform in WHERE or JOIN
• Data stored in query SRID, not display SRID
• Reprojection happens at ingest or output stage

SELECT Shape
• No SELECT \* in hot paths
• Geometry only selected when needed
• MVT queries return minimal attributes

⸻

### 4. Pagination & APIs

Pagination Strategy
• No OFFSET pagination on spatial endpoints
• Keyset / cursor pagination implemented
• Stable ordering guaranteed

Tile Pipelines
• Tile envelope filtering happens before geometry ops
• MVT queries avoid unnecessary joins
• Geometry simplified per zoom level

⸻

### 5. Transactions & Concurrency

Transaction Scope
• No long-running transactions involving spatial reads
• Map queries are read-only and auto-commit
• Bulk writes are batched

Writes & Updates
• Geometry updates are rare and isolated
• No mass geometry updates in OLTP tables
• HOT updates not assumed for geometry columns

⸻

### 6. Triggers & Derived Data

Trigger Usage
• No business logic implemented via triggers
• Triggers only used for:
• Auditing
• Cached derived values (area, centroid)
• Lightweight validation

Derived Geometry
• Derived geometry columns are precomputed
• No cascading geometry recomputation

⸻

### 7. Partitioning & Retention

Partitioning Strategy
• Time-series spatial data is partitioned
• Partition pruning verified with EXPLAIN
• Partitions dropped, not deleted

Retention
• TTL or partition-drop policy exists
• No “we’ll clean it later” data paths

⸻

### 8. Scaling & Infrastructure

Connection Management
• PgBouncer in front of Postgres
• Pool size bounded and tested
• No unbounded connection growth from map clients

Read Scaling
• Read replicas used only for read-safe queries
• Replica lag acceptable for map use cases
• No writes routed to replicas

⸻

### 9. EXPLAIN & Observability

Query Analysis
• Slow spatial queries captured
• EXPLAIN (ANALYZE, BUFFERS) reviewed
• Row estimate accuracy checked
• Index usage confirmed

Monitoring
• Slow query logging enabled
• Index bloat monitored
• Vacuum activity observed
• Replication lag tracked

⸻

### 10. Migrations & Schema Changes

Spatial Migrations
• Geometry changes follow expand / backfill / contract
• Dual geometry columns used when needed
• Index rebuild cost accounted for

Deployment Safety
• Migrations tested on production-sized data
• Rollback plan exists
• No blocking DDL in peak hours

⸻

### 11. Backup & Recovery

Backups
• PITR enabled
• WAL archive tested
• Restore tested on spatial integrity

Validation
• Geometry validity checks after restore
• SRIDs verified
• Indexes rebuilt if needed

⸻

### 12. Search & Caching

Text Search
• No LIKE '%foo%' on large tables
• Trigram or full-text indexes used
• Search combined safely with spatial filters

Caching
• Cache is derived state
• Cache invalidation strategy exists
• Tiles cached, not raw spatial queries

⸻

🚨 High-Risk Red Flags (Immediate Action)
• ❌ ST_Transform in WHERE
• ❌ Spatial query without GiST index
• ❌ OFFSET pagination on feature APIs
• ❌ JSONB filtering in hot paths
• ❌ Long transactions holding spatial locks
• ❌ Geometry updates inside OLTP traffic
• ❌ No tested restore process

⸻

Recommended Usage
• ✅ Run before production deploys
• ✅ Use during incident response
• ✅ Use for code reviews
• ✅ Attach to architecture reviews
