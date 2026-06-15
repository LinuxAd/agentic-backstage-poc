#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = ["mcp", "psycopg2-binary"]
# ///
"""second-brain MCP server: precise tools over the brain store + Backstage catalog.

Run: uv run brain/mcp_server/server.py   (stdio transport, for `claude mcp add`)
"""
import os

import psycopg2
from mcp.server.fastmcp import FastMCP

import catalog
import queries

DSN = os.environ.get("BRAIN_DSN", "postgresql://brain:brain@localhost:5432/brain")
BACKSTAGE_URL = os.environ.get("BACKSTAGE_URL", "http://localhost:3000")

mcp = FastMCP("second-brain")


def _conn():
    try:
        return psycopg2.connect(DSN)
    except Exception as e:
        raise RuntimeError(f"brain store unreachable at {DSN}: {e}")


def _run(fn, *args):
    conn = _conn()
    try:
        return fn(conn, *args)
    finally:
        conn.close()


@mcp.tool()
def list_services() -> list:
    """List platform services from the Backstage catalog (entity ref, owner, system)."""
    return catalog.list_services(BACKSTAGE_URL)


@mcp.tool()
def get_service_dossier(entity_ref: str) -> dict:
    """Assembled rollup for a service: open incidents, firing alerts, last deploy, quality trend, recent events."""
    return _run(queries.rebuild_dossier, entity_ref)


@mcp.tool()
def get_recent_incidents(entity_ref: str, days: int = 7) -> list:
    """Incidents for a service within the last N days (newest first)."""
    return _run(queries.recent_incidents, entity_ref, days)


@mcp.tool()
def get_active_alerts(entity_ref: str) -> list:
    """Currently firing alerts for a service."""
    return _run(queries.active_alerts, entity_ref)


@mcp.tool()
def get_quality_trend(entity_ref: str, weeks: int = 6) -> list:
    """Code-quality scan series for a service over the last N weeks (oldest first)."""
    return _run(queries.quality_trend, entity_ref, weeks)


@mcp.tool()
def get_recent_deploys(entity_ref: str, days: int = 7) -> list:
    """Deploys for a service within the last N days (newest first)."""
    return _run(queries.recent_deploys, entity_ref, days)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
