# MCP server ("second-brain") — design

**Date:** 2026-06-15
**Status:** approved

## Goal

Expose the brain store to an AI agent as precise, boring, fast MCP tools. This is
the demo's tool layer: Claude Code calls these tools to assemble a grounded,
cited operational picture of a service keyed to its Backstage catalog entity ref.
*(Project spec §6.)*

The demo punchline runs entirely through this server: *"What's going on with
payment-gateway right now?"* → `get_service_dossier` + `get_active_alerts` →
grounded answer; *"could it relate to a recent change?"* → `get_recent_deploys`
surfaces the `v2.4.0` deploy ~30 min before the open incident.

## Scope

**In scope**
- `brain/mcp_server/{server.py, queries.py, catalog.py}` — the MCP server and its
  query/catalog modules.
- A `make mcp-register` target to register the server with Claude Code.
- A one-line Backstage app-config change (`charts/backstage/values.yaml`) so the
  catalog REST API is readable by `list_services`.

**Out of scope** (later / separate)
- `make demo` (port-forwards + printed URLs + connect command).
- The backup screen recording and rehearsal notes (`docs/architecture.md`).
- Any in-cluster deployment of the MCP server — it runs locally (spec §6).

## Decisions locked in (from brainstorming)

| Decision | Choice | Consequence |
|---|---|---|
| `list_services` source | Backstage REST API only | Purest catalog-as-spine; **hard dependency** on Backstage running |
| Dossier | Rich rollup, rebuilt on read, upserted to `dossiers` | One assembled read = the latency/token talking point (spec §4) |
| SDK / transport | `mcp` SDK (FastMCP), stdio | Standard; what `claude mcp add` consumes |
| Runtime | Local, `uv run` + PEP 723 inline deps | Consistent with the seeder; no system-Python pollution |
| Catalog HTTP client | stdlib `urllib` | One fewer dependency |
| Connection lifecycle | psycopg2 connection per tool call | Simple, no stale connections (cheap locally) |

## Architecture

A standalone Python MCP server, run on the host via `uv run`, speaking **stdio**
to Claude Code. It reads two backends:

- **Brain store** — psycopg2 to `BRAIN_DSN` (default
  `postgresql://brain:brain@localhost:5432/brain`). All entity-keyed reads use the
  composite indexes `idx_events_lookup` / `idx_events_kind`.
- **Backstage catalog** — HTTP `GET` to `BACKSTAGE_URL` (default
  `http://localhost:3000`) `/api/catalog/entities`.

```
Claude Code ──stdio──> server.py (FastMCP tools)
                          ├── queries.py ──psycopg2──> Postgres :5432
                          └── catalog.py ──urllib────> Backstage :3000
```

A connection is opened per tool call and closed after (cheap on localhost,
avoids stale-connection failures across a long-lived stdio session).

## Components

Three small files under `brain/mcp_server/`, each with one responsibility:

### `server.py` (entry script)
- PEP 723 header: `requires-python = ">=3.14"`, `dependencies = ["mcp", "psycopg2-binary"]`.
- Builds a `FastMCP("second-brain")` instance and defines the six tools as thin
  wrappers that open a connection and delegate to `queries.py` / `catalog.py`.
- `main()` runs the stdio server (`mcp.run()`).
- Run as `uv run server.py`; Python places the script's directory on `sys.path`,
  so `import queries` / `import catalog` resolve with no packaging.

### `queries.py`
Pure functions taking an open psycopg2 connection (independently testable):
- `normalize_ref(ref)` — bare `payment-gateway` → `component:default/payment-gateway`;
  leaves a full ref untouched.
- `recent_incidents(conn, ref, days)`, `active_alerts(conn, ref)`,
  `quality_trend(conn, ref, weeks)`, `recent_deploys(conn, ref, days)` — indexed
  `SELECT`s returning lists of dicts (newest first).
- `rebuild_dossier(conn, ref)` — assembles the rollup, `UPSERT`s into `dossiers`
  (`ON CONFLICT (entity_ref) DO UPDATE`), and returns it.

### `catalog.py`
- `list_services(base_url)` — `GET /api/catalog/entities?filter=kind=component`,
  maps each entity to `{entity_ref, name, owner, system}` where
  `entity_ref = "component:{namespace|default}/{name}"`. Raises a clear error if
  Backstage is unreachable or returns non-200.

## Tool contracts

`severity` and `occurred_at` are columns; other fields read from `payload`.
Timestamps returned as ISO-8601 strings.

| Tool | Signature | Returns |
|---|---|---|
| `list_services` | `()` | `[{entity_ref, name, owner, system}]` (from Backstage) |
| `get_service_dossier` | `(entity_ref)` | `{entity_ref, generated_at, open_incidents:int, firing_alerts:int, last_deploy:{revision,image_tag,occurred_at}|null, quality_trend:{direction:"down"|"up"|"flat", first_coverage, last_coverage}|null, recent_events:[{kind,source,severity,occurred_at,summary}]}` |
| `get_recent_incidents` | `(entity_ref, days=7)` | `[{severity,status,title,started_at,resolved_at,occurred_at,url}]` |
| `get_active_alerts` | `(entity_ref)` | `[{alertname,severity,value,threshold,started_at}]` where `state='firing'` |
| `get_quality_trend` | `(entity_ref, weeks=4)` | `[{occurred_at,coverage,code_smells,quality_gate}]` (oldest→newest) |
| `get_recent_deploys` | `(entity_ref, days=7)` | `[{revision,image_tag,status,occurred_at}]` |

`quality_trend.direction` is computed by comparing first vs last coverage in the
window: `down` if last < first − 1.0, `up` if last > first + 1.0, else `flat`.

`recent_events[].summary` is a short human string per kind (incident/alert title,
deploy revision, scan coverage) so the dossier reads well without the agent
re-querying.

## Backstage auth (the "REST only" wrinkle)

The Backstage new backend returns 401 for unauthenticated requests by default, so
`list_services` would fail. This slice adds one line to the chart's `appConfig`
(`charts/backstage/values.yaml`):

```yaml
backend:
  auth:
    dangerouslyDisableDefaultAuthPolicy: true
```

This permits unauthenticated reads of the catalog API — acceptable for a local
demo (documented as demo-only). `make backstage-deploy` rolls the pod (the chart
already annotates the pod with a config hash). **Verification during
implementation:** if disabling the default policy is insufficient for the catalog
route, the fallback is acquiring a Backstage guest token in `catalog.py`; this
will be confirmed live before the slice is considered done.

## Error handling

- DB unreachable / query error → the tool raises with a concise message
  (`"brain store unreachable at <dsn>: <err>"`); FastMCP surfaces it to the agent
  as a tool error, never a silent empty result.
- Backstage unreachable / non-200 in `list_services` → clear error message naming
  the URL and status.
- Unknown/empty entity → tools return empty lists (a valid answer: "no events"),
  except `get_service_dossier`, which returns a zeroed rollup with
  `generated_at` set.

## Packaging & wiring

- Run: `uv run brain/mcp_server/server.py` (uv builds the env from the PEP 723
  metadata).
- Register: `make mcp-register` →
  `claude mcp add second-brain -- uv run --quiet $(CURDIR)/brain/mcp_server/server.py`
  (absolute path so Claude Code can launch it from any working directory).

## Testing

1. **Module integration (against live seeded DB + Backstage):** call the
   `queries.py` / `catalog.py` functions directly and assert known facts:
   - `rebuild_dossier(payment-gateway)` → `open_incidents=1`, `firing_alerts=2`,
     `quality_trend.direction="down"` (78→66), `last_deploy.revision="v2.4.0"`.
   - `active_alerts(payment-gateway)` → 2 rows; `recent_deploys` newest is `v2.4.0`.
   - `list_services()` → includes `component:default/payment-gateway` with a
     non-empty `owner` and `system`.
2. **End-to-end:** `make mcp-register`, then in Claude Code rehearse the four
   questions (spec §6) and confirm grounded, cited answers — including the agent
   discovering the deploy→incident correlation.

## Verification (done-when)

- `uv run brain/mcp_server/server.py` starts a stdio server without import/dep errors.
- The module integration assertions above all pass against the running stack
  (`make up && make db-up && make db-init && make seed && make backstage-up`).
- `make mcp-register` registers `second-brain`; Claude Code lists its six tools.
- Asking "what's going on with payment-gateway right now?" yields the open sev2 +
  two firing alerts; "could it relate to a recent change?" surfaces `v2.4.0`.
