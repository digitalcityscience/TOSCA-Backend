"""
PostGIS metadata inspector.

Uses SQLAlchemy (postgresql+psycopg dialect) to connect to any PostGIS
database using the connection details stored on a Store object.

Rules:
- ALL queries use parameterized bindings — no f-strings into SQL.
- This module never touches the Django ORM; it receives plain Python values.
- Every public function returns a plain dict or list of dicts.
- On connection/query failure it raises PostGISInspectorError.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, SQLAlchemyError

logger = logging.getLogger(__name__)


class PostGISInspectorError(Exception):
    """Raised when a PostGIS metadata query fails."""


def _make_engine(host: str, port: int, database: str, username: str, password: str):
    """
    Build a SQLAlchemy engine using psycopg v3 driver.
    Connection is NOT pooled — engine is created per-request and disposed
    immediately after the query (use as context: engine.connect()).
    """
    url = URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def test_postgis_connection(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    schema: str,
) -> dict[str, Any]:
    """
    Validate that a PostGIS connection can be opened and that the schema exists.

    Returns diagnostic details on success and raises PostGISInspectorError on
    connection, authentication, database, or schema failures.
    """
    schema_sql = text(
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name = :schema
        """
    )
    try:
        engine = _make_engine(host, port, database, username, password)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()
            schema_name = conn.execute(schema_sql, {"schema": schema}).scalar_one_or_none()
        engine.dispose()
    except OperationalError as exc:
        logger.error("PostGIS connection test failed for %s@%s:%s/%s: %s", username, host, port, database, exc)
        raise PostGISInspectorError(
            f"Could not connect to PostGIS at {host}:{port}/{database}. "
            "Check host, port, database, username, and password."
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("PostGIS connection test query failed: %s", exc)
        raise PostGISInspectorError(f"PostGIS validation query failed: {exc}") from exc

    if schema_name is None:
        raise PostGISInspectorError(
            f"Schema '{schema}' does not exist in database '{database}'."
        )

    return {
        "host": host,
        "port": port,
        "database": database,
        "username": username,
        "schema": schema,
        "schema_exists": True,
    }


def get_geometry_tables(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    schema: str,
) -> list[dict]:
    """
    Return all tables with geometry columns in the given schema.

    Queries the PostGIS ``geometry_columns`` view using parameterized SQL.

    Returns a list of dicts:
        {
            "table_name": str,
            "geometry_column": str,
            "geometry_type": str,
            "srid": int,
        }

    Raises PostGISInspectorError on connection or query failure.
    """
    sql = text(
        """
        SELECT
            f_table_name      AS table_name,
            f_geometry_column AS geometry_column,
            type              AS geometry_type,
            srid
        FROM geometry_columns
        WHERE f_table_schema = :schema
        ORDER BY f_table_name
        """
    )
    try:
        engine = _make_engine(host, port, database, username, password)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"schema": schema}).fetchall()
        engine.dispose()
    except OperationalError as exc:
        logger.error("PostGIS connection failed for %s@%s:%s/%s: %s", username, host, port, database, exc)
        raise PostGISInspectorError(
            f"Could not connect to PostGIS at {host}:{port}/{database}: {exc}"
        ) from exc
    except SQLAlchemyError as exc:
        logger.error("PostGIS query failed: %s", exc)
        raise PostGISInspectorError(f"Metadata query failed: {exc}") from exc

    return [
        {
            "table_name": row.table_name,
            "geometry_column": row.geometry_column,
            "geometry_type": _normalize_geometry_type(row.geometry_type),
            "srid": row.srid or 4326,
        }
        for row in rows
    ]


def get_table_bbox(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
    schema: str,
    table: str,
    geometry_column: str,
) -> dict[str, Any] | None:
    """
    Return the bounding box of a PostGIS table via ST_Extent.

    Returns a dict:
        {"minx": float, "miny": float, "maxx": float, "maxy": float}
    or None if the table is empty or the extent is null.

    Raises PostGISInspectorError on failure.
    """
    # Build fully-qualified identifier safely — schema + table come from the
    # Django Store model (trusted internal data), but we validate they
    # contain only safe chars to be defensive.
    _assert_safe_identifier(schema, "schema")
    _assert_safe_identifier(table, "table")
    _assert_safe_identifier(geometry_column, "geometry_column")

    # We cannot use :param binding for identifiers (table/column names), but
    # the assertion above guards against injection.
    sql_str = f'SELECT ST_Extent("{geometry_column}") AS bbox FROM "{schema}"."{table}"'
    sql = text(sql_str)

    try:
        engine = _make_engine(host, port, database, username, password)
        with engine.connect() as conn:
            row = conn.execute(sql).fetchone()
        engine.dispose()
    except SQLAlchemyError as exc:
        logger.error("ST_Extent query failed for %s.%s: %s", schema, table, exc)
        raise PostGISInspectorError(f"Bounding box query failed: {exc}") from exc

    if row is None or row.bbox is None:
        return None

    # ST_Extent returns a string like 'BOX(minx miny,maxx maxy)'
    return _parse_box_string(str(row.bbox))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_safe_identifier(value: str, label: str) -> None:
    """
    Raises ValueError if the string contains characters that are not
    safe for use as a SQL identifier (letters, digits, underscore, hyphen).
    """
    import re
    if not re.match(r'^[A-Za-z0-9_\-]+$', value):
        raise ValueError(f"Unsafe SQL identifier for {label}: {value!r}")


def _normalize_geometry_type(raw: str) -> str:
    """
    Normalise geometry type strings from PostGIS geometry_columns to
    values accepted by the Layer model's GEOMETRY_TYPES choices.
    e.g. 'MULTIPOLYGON' → 'MultiPolygon'
    """
    mapping = {
        'POINT': 'Point',
        'LINESTRING': 'LineString',
        'POLYGON': 'Polygon',
        'MULTIPOINT': 'MultiPoint',
        'MULTILINESTRING': 'MultiLineString',
        'MULTIPOLYGON': 'MultiPolygon',
        'GEOMETRYCOLLECTION': 'GeometryCollection',
        'GEOMETRY': 'Polygon',  # fallback
    }
    return mapping.get(raw.upper(), 'Polygon')


def _parse_box_string(box: str) -> dict[str, float]:
    """
    Parse 'BOX(minx miny,maxx maxy)' returned by ST_Extent into a dict.
    """
    try:
        inner = box.replace('BOX(', '').replace(')', '')
        lo, hi = inner.split(',')
        minx, miny = lo.strip().split()
        maxx, maxy = hi.strip().split()
        return {
            'minx': float(minx),
            'miny': float(miny),
            'maxx': float(maxx),
            'maxy': float(maxy),
        }
    except Exception:
        return {}
