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


def protagonist_events():
    ref = PROTAGONIST
    ev = []

    # Contrast #1: a resolved incident with NO deploy in the preceding hours.
    started = hours(30)
    ev.append((ref, "pagerduty", "incident", "sev3", {
        "title": "intermittent timeout spike on /charge",
        "status": "resolved",
        "started_at": started.isoformat(),
        "resolved_at": hours(29).isoformat(),
        "service": "payment-gateway",
        "url": "https://pd.example/incidents/PD-1980",
    }, started))

    # Contrast #2: a healthy deploy that caused no incident.
    ev.append((ref, "argocd", "deploy", None, {
        "revision": "v2.3.1",
        "image_tag": "1.8.4->1.9.0",
        "status": "Succeeded",
        "sync_status": "Synced",
        "author": "platform-bot",
        "url": "https://argo.example/applications/payment-gateway",
    }, hours(26)))

    # THE change: deploy ~2h ago.
    ev.append((ref, "argocd", "deploy", None, {
        "revision": "v2.4.0",
        "image_tag": "1.9.0->1.10.0",
        "status": "Succeeded",
        "sync_status": "Synced",
        "author": "platform-bot",
        "url": "https://argo.example/applications/payment-gateway",
    }, hours(2)))

    # The open sev2 incident, ~30 min after v2.4.0 (the planted correlation).
    inc_start = hours(1.5)
    ev.append((ref, "pagerduty", "incident", "sev2", {
        "title": "p99 latency breach on /charge",
        "status": "open",
        "started_at": inc_start.isoformat(),
        "resolved_at": None,
        "service": "payment-gateway",
        "url": "https://pd.example/incidents/PD-2041",
    }, inc_start))

    # Two firing alerts, started with the incident.
    ev.append((ref, "prometheus", "alert", "critical", {
        "alertname": "HighErrorRate",
        "expr": "rate(http_requests_total{code=~\"5..\"}[5m]) > 0.05",
        "value": 0.087,
        "threshold": 0.05,
        "state": "firing",
        "started_at": inc_start.isoformat(),
        "resolved_at": None,
    }, inc_start))
    ev.append((ref, "prometheus", "alert", "warning", {
        "alertname": "HighLatencyP99",
        "expr": "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) > 0.8",
        "value": 1.24,
        "threshold": 0.8,
        "state": "firing",
        "started_at": inc_start.isoformat(),
        "resolved_at": None,
    }, inc_start))

    # Weekly SonarQube scans: coverage trending DOWN, last gate ERROR.
    coverages = [78.0, 74.0, 71.0, 69.0, 66.0]
    smells = [120, 150, 175, 198, 214]
    for i, (cov, sm) in enumerate(zip(coverages, smells)):
        week_ago = 28 - i * 7  # 28, 21, 14, 7, 0 days ago
        gate = "ERROR" if i == len(coverages) - 1 else "OK"
        ev.append((ref, "sonarqube", "scan", None, {
            "coverage": cov,
            "code_smells": sm,
            "bugs": 5 + i,
            "vulnerabilities": 1 + (i // 2),
            "quality_gate": gate,
            "project_key": "payment-gateway",
        }, days(week_ago)))

    return ev


def build_events():
    """Return the full list of event tuples."""
    events = []
    events += protagonist_events()
    return events


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
