#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = ["psycopg2-binary"]
# ///
"""Synthetic event seeder for the SRE Second Brain brain store.

Writes ~30 days of believable operational events keyed to existing Backstage
catalog entity refs. Deterministic (fixed RNG); timestamps anchor to now() so the
protagonist always has an open incident "right now". Idempotent: truncates and
regenerates on every run.

Run: make seed   (or: uv run brain/seeder/seed.py)
"""
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values, Json

DSN = os.environ.get("BRAIN_DSN", "postgresql://brain:brain@localhost:5432/brain")
RNG = random.Random(42)
NOW = datetime.now(timezone.utc)

PROTAGONIST = "component:default/payment-gateway"
BACKGROUND = [
    "component:default/notification-dispatcher",
    "component:default/inventory-tracker",
    "component:default/auth-service",
    "component:default/fraud-detection-service",
]


def hours(n):
    return NOW - timedelta(hours=n)


def days(n):
    return NOW - timedelta(days=n)


def build_events():
    """Return the full list of event tuples. Filled in by later tasks."""
    return []


def main():
    events = build_events()
    conn = psycopg2.connect(DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE events RESTART IDENTITY;")
            cur.execute("DELETE FROM dossiers;")
            if events:
                execute_values(
                    cur,
                    "INSERT INTO events "
                    "(entity_ref, source, kind, severity, payload, occurred_at) "
                    "VALUES %s",
                    [
                        (ref, src, kind, sev, Json(payload), ts)
                        for (ref, src, kind, sev, payload, ts) in events
                    ],
                )
        services = {e[0] for e in events}
        print(f"✓ seeded {len(events)} events across {len(services)} services")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
