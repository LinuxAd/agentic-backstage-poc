# MCP Server ("second-brain") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local MCP server that exposes the brain store to Claude Code as six precise tools, so the agent can assemble a grounded, cited operational picture of a service keyed to its Backstage catalog entity ref.

**Architecture:** A Python MCP server (`mcp` SDK / FastMCP, stdio) run on the host via `uv run` (PEP 723 inline deps). Three focused files under `brain/mcp_server/`: `queries.py` (SQL + dossier assembly over psycopg2 → `localhost:5432`), `catalog.py` (Backstage catalog REST via stdlib urllib → `localhost:3000`), and `server.py` (thin FastMCP tool wrappers + stdio entrypoint). A one-line Backstage app-config change permits the catalog read; `make mcp-register` wires it into Claude Code.

**Tech Stack:** Python 3.14 via `uv` (PEP 723), `mcp` (FastMCP), `psycopg2-binary`, stdlib `urllib`, Make.

**Design / contract:** `docs/superpowers/specs/2026-06-15-mcp-server-design.md`.

---

## Prerequisites (the running stack)

Tasks 1–2 need the seeded brain store: `make up && make db-up && make db-init && make seed` (Postgres on `localhost:5432`, 87 events). Task 3 additionally needs Backstage running with the auth tweak: `make backstage-up` (build + load + deploy — several minutes). Confirmed already in this environment: `uv`, Python 3.14.6, and that `mcp` + `psycopg2-binary` resolve on 3.14.

## File structure

- **Create** `brain/mcp_server/queries.py` — SQL query functions + `rebuild_dossier()`. Pure functions taking a psycopg2 connection. The data-access unit.
- **Create** `brain/mcp_server/catalog.py` — `list_services(base_url)` over the Backstage REST API. The catalog-access unit.
- **Create** `brain/mcp_server/server.py` — PEP 723 entry script: six FastMCP tools (thin wrappers) + stdio `main()`.
- **Create** `brain/mcp_server/smoke_test.py` — runnable integration assertions against the live seeded stack.
- **Modify** `charts/backstage/values.yaml` — add `backend.auth.dangerouslyDisableDefaultAuthPolicy: true` to `appConfig`.
- **Modify** `Makefile` — add `mcp-register` target.

`server.py` is run as `uv run brain/mcp_server/server.py`; Python puts the script's directory on `sys.path`, so `import queries` / `import catalog` resolve with no packaging. Only `server.py` and `smoke_test.py` carry PEP 723 metadata (they are entry points); `queries.py`/`catalog.py` are imported libraries.

---

### Task 1: `queries.py` — the four read functions

**Files:**
- Create: `brain/mcp_server/queries.py`

- [ ] **Step 1: Write `brain/mcp_server/queries.py`**

Create the file with exactly this content:

```python
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
```

- [ ] **Step 2: Verify against the live seeded DB**

Run:
```bash
uv run --with psycopg2-binary python -c "
import sys; sys.path.insert(0, 'brain/mcp_server')
import psycopg2, queries
c = psycopg2.connect('postgresql://brain:brain@localhost:5432/brain')
print('incidents7=', len(queries.recent_incidents(c, 'payment-gateway', 7)))
print('alerts=', len(queries.active_alerts(c, 'payment-gateway')))
print('trend=', [r['coverage'] for r in queries.quality_trend(c, 'payment-gateway', 6)])
print('deploys7=', [d['revision'] for d in queries.recent_deploys(c, 'payment-gateway', 7)])
"
```
Expected: `incidents7= 2`, `alerts= 2`, `trend= [78.0, 74.0, 71.0, 69.0, 66.0]`, `deploys7= ['v2.4.0', 'v2.3.1']`.

- [ ] **Step 3: Commit**

```bash
git add brain/mcp_server/queries.py
git commit -m "feat: mcp queries.py read functions"
```

---

### Task 2: `queries.py` — `rebuild_dossier` + helpers

**Files:**
- Modify: `brain/mcp_server/queries.py`

- [ ] **Step 1: Append the dossier functions**

Add these functions to the end of `brain/mcp_server/queries.py` (the `json`, `datetime`, `timezone` imports they use are already at the top from Task 1):

```python
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
```

- [ ] **Step 2: Verify dossier assembly + cache upsert**

Run:
```bash
uv run --with psycopg2-binary python -c "
import sys, json; sys.path.insert(0, 'brain/mcp_server')
import psycopg2, queries
c = psycopg2.connect('postgresql://brain:brain@localhost:5432/brain')
d = queries.rebuild_dossier(c, 'payment-gateway')
print(json.dumps({k: d[k] for k in ['open_incidents','firing_alerts','last_deploy','quality_trend']}, indent=2))
print('recent_events=', len(d['recent_events']))
"
```
Expected: `open_incidents` 1, `firing_alerts` 2, `last_deploy.revision` `v2.4.0`, `quality_trend` `{"direction":"down","first_coverage":78.0,"last_coverage":66.0}`, `recent_events= 5`.

Then confirm the cache row was written:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc \
  "select entity_ref, summary->>'open_incidents' from dossiers;"
```
Expected: `component:default/payment-gateway|1`.

- [ ] **Step 3: Commit**

```bash
git add brain/mcp_server/queries.py
git commit -m "feat: mcp dossier assembly (rebuild_dossier)"
```

---

### Task 3: `catalog.py` + Backstage auth config

**Files:**
- Create: `brain/mcp_server/catalog.py`
- Modify: `charts/backstage/values.yaml`

- [ ] **Step 1: Write `brain/mcp_server/catalog.py`**

Create the file with exactly this content:

```python
"""Backstage catalog client for the MCP server (stdlib only)."""
import json
import urllib.error
import urllib.request


def list_services(base_url):
    """Return [{entity_ref, name, owner, system}] for catalog Components."""
    url = base_url.rstrip("/") + "/api/catalog/entities?filter=kind=component"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            entities = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Backstage catalog returned {e.code} at {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Backstage unreachable at {url}: {e.reason}") from e

    services = []
    for ent in entities:
        md = ent.get("metadata", {})
        spec = ent.get("spec", {})
        ns = md.get("namespace", "default")
        name = md.get("name")
        services.append({
            "entity_ref": f"component:{ns}/{name}",
            "name": name,
            "owner": spec.get("owner"),
            "system": spec.get("system"),
        })
    return services
```

- [ ] **Step 2: Permit the catalog read in Backstage app-config**

In `charts/backstage/values.yaml`, inside the `appConfig: |` block, under `backend:` (sibling of `baseUrl`/`listen`/`database`), add an `auth` section. After the `database:` block's `connection: ':memory:'` line, add (matching the 4-space indentation of `database:`):

```yaml
    auth:
      # Demo-only: the new Backstage backend 401s unauthenticated requests by
      # default; this lets the local MCP server read the catalog API.
      dangerouslyDisableDefaultAuthPolicy: true
```

- [ ] **Step 3: Roll out Backstage with the new config**

Run: `make backstage-up`
Expected: ends with `✓ backstage deployed → http://localhost:3000` (the config-hash annotation rolls the pod). This builds + loads + deploys; it takes several minutes.

- [ ] **Step 4: Verify the catalog read works unauthenticated**

Run:
```bash
curl -sS "http://localhost:3000/api/catalog/entities?filter=kind=component" | head -c 200; echo
uv run python -c "
import sys; sys.path.insert(0, 'brain/mcp_server')
import catalog
s = catalog.list_services('http://localhost:3000')
print('count=', len(s))
pg = [x for x in s if x['entity_ref'] == 'component:default/payment-gateway']
print('payment-gateway=', pg)
"
```
Expected: the `curl` returns a JSON array (not a 401 error body); `count=` is > 0; `payment-gateway=` shows one entry with non-null `owner` (`group:default/guests`) and `system` (`system:default/examples` or `examples`).

If the `curl` still returns 401, the default-auth-policy toggle was insufficient — implement the documented fallback in `catalog.py`: obtain a Backstage guest token (`POST /api/auth/guest/refresh`) and send it as `Authorization: Bearer <token>`. Re-run this step until the catalog read succeeds.

- [ ] **Step 5: Commit**

```bash
git add brain/mcp_server/catalog.py charts/backstage/values.yaml
git commit -m "feat: mcp catalog client + allow unauthenticated catalog reads"
```

---

### Task 4: `server.py` — FastMCP tools + stdio entrypoint

**Files:**
- Create: `brain/mcp_server/server.py`

- [ ] **Step 1: Write `brain/mcp_server/server.py`**

Create the file with exactly this content:

```python
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
```

- [ ] **Step 2: Verify the server module imports and registers all six tools**

Run:
```bash
uv run --with mcp --with psycopg2-binary python -c "
import sys; sys.path.insert(0, 'brain/mcp_server')
import asyncio, server
names = sorted(t.name for t in asyncio.run(server.mcp.list_tools()))
print(names)
assert names == ['get_active_alerts','get_quality_trend','get_recent_deploys','get_recent_incidents','get_service_dossier','list_services'], names
print('OK: 6 tools registered on', server.mcp.name)
"
```
Expected: the sorted list of all six tool names, then `OK: 6 tools registered on second-brain`.

- [ ] **Step 3: Commit**

```bash
git add brain/mcp_server/server.py
git commit -m "feat: mcp server.py FastMCP tools + stdio entrypoint"
```

---

### Task 5: Smoke test, registration, end-to-end

**Files:**
- Create: `brain/mcp_server/smoke_test.py`
- Modify: `Makefile`

- [ ] **Step 1: Write `brain/mcp_server/smoke_test.py`**

Create the file with exactly this content:

```python
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
```

- [ ] **Step 2: Run the smoke test**

Run: `uv run brain/mcp_server/smoke_test.py`
Expected: `✓ smoke test passed: <N> services; payment-gateway dossier OK` and exit code 0.

- [ ] **Step 3: Add the `mcp-register` Makefile target**

Append `mcp-register` to the `.PHONY:` line. Insert this target immediately before the `help:` target (recipe lines use real TABs):

```makefile
mcp-register: ## Register the second-brain MCP server with Claude Code
	@claude mcp add second-brain -- uv run --quiet "$(CURDIR)/brain/mcp_server/server.py"
	@echo "✓ registered 'second-brain' — restart the Claude Code session to load its tools"
```

- [ ] **Step 4: Register the server**

Run: `make mcp-register`
Expected: ends with `✓ registered 'second-brain' …`. Confirm with:
```bash
claude mcp list
```
Expected: `second-brain` appears in the list.

- [ ] **Step 5: Commit**

```bash
git add brain/mcp_server/smoke_test.py Makefile
git commit -m "feat: mcp smoke test + make mcp-register"
```

- [ ] **Step 6: End-to-end rehearsal (manual)**

In a **new** Claude Code session (so the MCP server is loaded), ask the four demo questions and confirm grounded answers:
1. "What services exist on this platform and who owns them?" → `list_services` returns the catalog components with owners/system.
2. "What's going on with payment-gateway right now?" → `get_service_dossier` / `get_active_alerts` report the open sev2 "p99 latency breach on /charge" and the two firing alerts.
3. "Could the open incident be related to a recent change?" → `get_recent_deploys` surfaces the `v2.4.0` deploy ~30 min before the incident.
4. "Which service's code quality is trending the wrong way?" → `get_quality_trend` shows `payment-gateway` falling 78→66 while others are flat.

This step is verification only — no commit.

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** six tools (Tasks 1,2,4), Backstage-REST `list_services` + auth tweak (Task 3), rich dossier rebuilt-on-read + upsert (Task 2), stdio/uv/PEP 723 packaging (Task 4), `make mcp-register` (Task 5), error handling (`_conn` message, `catalog` RuntimeErrors), testing (Task 5 smoke + manual rehearsal). ✓
- **Placeholders:** none — full code in every create/modify step; the guest-token fallback in Task 3 Step 4 is a concrete contingency with the exact endpoint, gated on an observed 401. ✓
- **Consistency:** function names/signatures match across files — `queries.{recent_incidents,active_alerts,quality_trend,recent_deploys,rebuild_dossier,normalize_ref}` and `catalog.list_services(base_url)` are defined in Tasks 1–3 and called identically in `server.py` (Task 4) and `smoke_test.py` (Task 5); `_run(fn, *args)` passes the connection as the first arg, matching every query function's `(conn, ...)` signature; entity ref `component:default/payment-gateway` and DSN/URL defaults are identical everywhere. ✓
```