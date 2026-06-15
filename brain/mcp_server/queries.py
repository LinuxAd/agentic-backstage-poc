"""Brain-store queries and dossier assembly for the MCP server.

Pure functions: each takes an open psycopg2 connection. Returned rows are plain
dicts with ISO-8601 timestamps so they serialize straight to MCP tool output.
"""
import json
from datetime import datetime, timezone

from psycopg2.extras import RealDictCursor


def normalize_ref(ref):
    """A bare name ('payment-gateway') becomes a full component ref."""
    return ref if ":" in ref else f"component:default/{ref}"


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def recent_incidents(conn, ref, days=7):
    ref = normalize_ref(ref)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT severity,
                   payload->>'status'      AS status,
                   payload->>'title'       AS title,
                   payload->>'started_at'  AS started_at,
                   payload->>'resolved_at' AS resolved_at,
                   payload->>'url'         AS url,
                   occurred_at
            FROM events
            WHERE entity_ref = %s AND kind = 'incident'
              AND occurred_at >= now() - make_interval(days => %s)
            ORDER BY occurred_at DESC
            """,
            (ref, days),
        )
        rows = cur.fetchall()
    for r in rows:
        r["occurred_at"] = _iso(r["occurred_at"])
    return [dict(r) for r in rows]


def active_alerts(conn, ref):
    ref = normalize_ref(ref)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT payload->>'alertname'         AS alertname,
                   severity,
                   (payload->>'value')::float     AS value,
                   (payload->>'threshold')::float AS threshold,
                   payload->>'started_at'         AS started_at,
                   occurred_at
            FROM events
            WHERE entity_ref = %s AND kind = 'alert'
              AND payload->>'state' = 'firing'
            ORDER BY occurred_at DESC
            """,
            (ref,),
        )
        rows = cur.fetchall()
    for r in rows:
        r["occurred_at"] = _iso(r["occurred_at"])
    return [dict(r) for r in rows]


def quality_trend(conn, ref, weeks=6):
    ref = normalize_ref(ref)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT occurred_at,
                   (payload->>'coverage')::float  AS coverage,
                   (payload->>'code_smells')::int AS code_smells,
                   payload->>'quality_gate'       AS quality_gate
            FROM events
            WHERE entity_ref = %s AND kind = 'scan'
              AND occurred_at >= now() - make_interval(weeks => %s)
            ORDER BY occurred_at ASC
            """,
            (ref, weeks),
        )
        rows = cur.fetchall()
    for r in rows:
        r["occurred_at"] = _iso(r["occurred_at"])
    return [dict(r) for r in rows]


def recent_deploys(conn, ref, days=7):
    ref = normalize_ref(ref)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT payload->>'revision'  AS revision,
                   payload->>'image_tag' AS image_tag,
                   payload->>'status'    AS status,
                   occurred_at
            FROM events
            WHERE entity_ref = %s AND kind = 'deploy'
              AND occurred_at >= now() - make_interval(days => %s)
            ORDER BY occurred_at DESC
            """,
            (ref, days),
        )
        rows = cur.fetchall()
    for r in rows:
        r["occurred_at"] = _iso(r["occurred_at"])
    return [dict(r) for r in rows]
