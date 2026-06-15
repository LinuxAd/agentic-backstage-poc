#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = ["psycopg2-binary"]
# ///
"""Integration smoke test for the MCP server's data + catalog layers.

Requires the running stack: seeded brain store on localhost:5432 and Backstage on
localhost:3000. Run: uv run brain/mcp_server/smoke_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2  # noqa: E402

import catalog  # noqa: E402
import queries  # noqa: E402

DSN = os.environ.get("BRAIN_DSN", "postgresql://brain:brain@localhost:5432/brain")
BACKSTAGE_URL = os.environ.get("BACKSTAGE_URL", "http://localhost:3000")
REF = "payment-gateway"

conn = psycopg2.connect(DSN)

dossier = queries.rebuild_dossier(conn, REF)
assert dossier["open_incidents"] == 1, dossier
assert dossier["firing_alerts"] == 2, dossier
assert dossier["last_deploy"]["revision"] == "v2.4.0", dossier
assert dossier["quality_trend"]["direction"] == "down", dossier
assert dossier["quality_trend"]["first_coverage"] == 78.0, dossier
assert dossier["quality_trend"]["last_coverage"] == 66.0, dossier

assert len(queries.active_alerts(conn, REF)) == 2
assert queries.recent_deploys(conn, REF, 7)[0]["revision"] == "v2.4.0"

services = catalog.list_services(BACKSTAGE_URL)
refs = [s["entity_ref"] for s in services]
assert "component:default/payment-gateway" in refs, refs
pg = next(s for s in services if s["entity_ref"] == "component:default/payment-gateway")
assert pg["owner"] and pg["system"], pg

conn.close()
print(f"✓ smoke test passed: {len(services)} services; payment-gateway dossier OK")
