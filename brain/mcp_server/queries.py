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


def _scalar(conn, sql, params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def _recent_events(conn, ref, limit=5):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT kind, source, severity, payload, occurred_at
            FROM events
            WHERE entity_ref = %s
            ORDER BY occurred_at DESC
            LIMIT %s
            """,
            (ref, limit),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        p = r["payload"]  # psycopg2 returns jsonb as a dict
        if r["kind"] == "incident":
            summary = p.get("title")
        elif r["kind"] == "alert":
            summary = p.get("alertname")
        elif r["kind"] == "deploy":
            summary = p.get("revision")
        elif r["kind"] == "scan":
            summary = f"coverage {p.get('coverage')}%"
        else:
            summary = None
        out.append({
            "kind": r["kind"],
            "source": r["source"],
            "severity": r["severity"],
            "occurred_at": _iso(r["occurred_at"]),
            "summary": summary,
        })
    return out


def rebuild_dossier(conn, ref):
    ref = normalize_ref(ref)

    open_incidents = _scalar(
        conn,
        "SELECT count(*) FROM events WHERE entity_ref=%s AND kind='incident' "
        "AND payload->>'status'='open'",
        (ref,),
    ) or 0
    firing_alerts = _scalar(
        conn,
        "SELECT count(*) FROM events WHERE entity_ref=%s AND kind='alert' "
        "AND payload->>'state'='firing'",
        (ref,),
    ) or 0

    deploys = recent_deploys(conn, ref, days=3650)  # effectively unbounded
    last_deploy = None
    if deploys:
        d = deploys[0]
        last_deploy = {
            "revision": d["revision"],
            "image_tag": d["image_tag"],
            "occurred_at": d["occurred_at"],
        }

    scans = quality_trend(conn, ref, weeks=520)  # effectively all scans
    trend = None
    if len(scans) >= 2:
        first = scans[0]["coverage"]
        last = scans[-1]["coverage"]
        if last < first - 1.0:
            direction = "down"
        elif last > first + 1.0:
            direction = "up"
        else:
            direction = "flat"
        trend = {
            "direction": direction,
            "first_coverage": first,
            "last_coverage": last,
        }

    generated_at = datetime.now(timezone.utc)
    summary = {
        "entity_ref": ref,
        "generated_at": generated_at.isoformat(),
        "open_incidents": int(open_incidents),
        "firing_alerts": int(firing_alerts),
        "last_deploy": last_deploy,
        "quality_trend": trend,
        "recent_events": _recent_events(conn, ref, limit=5),
    }

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dossiers (entity_ref, generated_at, summary)
            VALUES (%s, %s, %s)
            ON CONFLICT (entity_ref) DO UPDATE
              SET generated_at = EXCLUDED.generated_at,
                  summary      = EXCLUDED.summary
            """,
            (ref, generated_at, json.dumps(summary)),
        )
    return summary
