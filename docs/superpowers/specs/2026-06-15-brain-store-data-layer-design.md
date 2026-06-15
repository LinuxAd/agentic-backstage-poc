# Brain-store data layer + schema — design

**Date:** 2026-06-15
**Status:** approved

## Goal

Stand up the "brain store" — the Postgres instance that holds operational events
keyed to Backstage catalog entity refs — and define the schema and the synthetic
**seed dataset contract** that the demo's data is generated against. This is the
architectural heart of the POC: *the Backstage catalog is the spine; every
operational signal is keyed to a catalog entity ref and indexed in Postgres for
exact, time-ranged retrieval* (project spec §10).

This slice delivers the data layer the MCP server (separate slice) will query.

## Scope

**In scope**
- Postgres deployment in the `data` namespace (`deploy/postgres/`).
- `brain/schema.sql` — the `events` and `dossiers` tables and their indexes.
- Makefile targets `db-up` and `db-init`.
- The connection contract consumed by the seeder and MCP server.
- The **seed dataset contract**: entity refs, event/payload shapes, the planted
  protagonist timeline, time-anchoring and determinism rules. This defines *what
  data must exist*; the `seed.py` implementation is a later slice.

**Out of scope** (separate specs/plans)
- `brain/seeder/seed.py` implementation.
- The MCP server (`brain/mcp_server/`) and its tools.
- Backstage catalog changes — none are needed; we key to existing example refs.

## Decisions locked in (from brainstorming)

| Decision | Choice | Consequence |
|---|---|---|
| Payload richness | Realistic-but-lean, source-flavoured fields | Citable dossiers, no API-fidelity time-sink |
| Planted correlation | Single clean pairing + two contrasts | Agent reasons, not pattern-matches |
| Entity refs | Reuse existing Backstage catalog; protagonist `component:default/payment-gateway` | Zero Backstage changes; catalog off the critical path |
| Postgres | Plain `postgres:16` Deployment + NodePort 30432, `emptyDir` | No Bitnami risk; ephemeral, rebuilt by `make seed` |
| Schema application | `schema.sql` in a ConfigMap, applied via `kubectl exec … psql` (`make db-init`) | Simple, idempotent, no initdb-script coupling |
| Dossier materialisation | Rebuilt **on read** by the MCP server; seeder writes only `events` | `dossiers` is a cache, not seeded |
| Time anchoring | `occurred_at = now() − offsets`; fixed RNG (seed 42); protagonist key events use fixed offsets | "Right now" always works; demo is byte-identical every run |

## Architecture

A pinned `postgres:16` runs in the `data` namespace, exposing a single database
`brain`. It is reachable from the host at `localhost:5432` via a NodePort Service
on `30432` — the port mapping already exists in `kind/cluster.yaml`
(`containerPort: 30432 → hostPort: 5432`). The seeder and the MCP server run
**locally** on the host against this DSN (project spec §6 recommendation — one
fewer image to build, identical architecture story):

```
postgresql://brain:brain@localhost:5432/brain
```

Storage is `emptyDir` (ephemeral): the brain store is rebuildable at any time with
`make db-init && make seed`, so there is no PVC to babysit (project spec §1
reliability constraint, §3 "persistence.enabled=false — it's a demo").

Retrieval is exact-match on `entity_ref` plus a time-range on `occurred_at`,
served by a composite B-tree index. No embeddings, no ANN index maintenance, no
write amplification — this is the deliberate "boring tech, defended" talking point
(project spec §4, §9 technical-depth).

## Components

### `deploy/postgres/postgres.yaml`

A single manifest file (applied by `make db-up`) containing:

- **ConfigMap `brain-schema`** — holds `schema.sql` (the DDL below) so it can be
  mounted/`exec`-applied in-cluster without baking a custom image.
- **Deployment `postgres`** (namespace `data`):
  - image `postgres:16` (pinned tag), `imagePullPolicy: IfNotPresent`.
  - env `POSTGRES_DB=brain`, `POSTGRES_USER=brain`, `POSTGRES_PASSWORD=brain`
    (demo-only credentials; the store holds only synthetic data).
  - `emptyDir` volume at `/var/lib/postgresql/data`.
  - a readiness probe using `pg_isready -U brain -d brain`.
  - modest resources (requests `cpu: 100m, memory: 256Mi`).
- **Service `postgres`** (namespace `data`): `type: NodePort`, port `5432`,
  `nodePort: 30432`, selector on the Deployment's pod labels.

The connection contract is the public interface of this component: anything that
needs the brain store dials `localhost:5432`, db/user/password `brain`. Consumers
do not depend on in-cluster DNS.

### `brain/schema.sql`

```sql
-- Brain store schema. Applied by `make db-init`. Idempotent.

CREATE TABLE IF NOT EXISTS events (
    id          bigserial   PRIMARY KEY,
    entity_ref  text        NOT NULL,   -- component:default/payment-gateway
    source      text        NOT NULL,   -- pagerduty | prometheus | sonarqube | argocd
    kind        text        NOT NULL,   -- incident | alert | scan | deploy
    severity    text,                   -- sev1..sev4 | critical | warning | null
    payload     jsonb       NOT NULL,   -- source-shaped detail (see payload shapes)
    occurred_at timestamptz NOT NULL
);

-- Exact entity lookup, optionally narrowed by source, newest first.
CREATE INDEX IF NOT EXISTS idx_events_lookup
    ON events (entity_ref, source, occurred_at DESC);

-- Exact entity lookup narrowed by signal kind, newest first.
CREATE INDEX IF NOT EXISTS idx_events_kind
    ON events (entity_ref, kind, occurred_at DESC);

-- Materialised per-service rollup. Cache: rebuilt on read by the MCP server,
-- NOT written by the seeder.
CREATE TABLE IF NOT EXISTS dossiers (
    entity_ref   text        PRIMARY KEY,
    generated_at timestamptz NOT NULL,
    summary      jsonb       NOT NULL   -- counts, open items, trend, last deploy
);
```

### Makefile targets

Follow the existing `→`/`✓` echo style; idempotent.

- `db-up` — `kubectl apply -f deploy/postgres/postgres.yaml`, then
  `kubectl -n data rollout status deploy/postgres` and wait for the pod Ready.
- `db-init` — copy/apply the schema into the running pod, e.g.
  `kubectl -n data exec deploy/postgres -- psql -U brain -d brain -f /schema/schema.sql`
  (the ConfigMap is mounted at `/schema/`). Idempotent because the DDL uses
  `IF NOT EXISTS`.

(`make seed` and folding `db-up`/`db-init` into `make up` belong to later slices.)

## Data contract: event & payload shapes

`kind` ∈ `{incident, alert, scan, deploy}`; `source` ∈
`{pagerduty, prometheus, sonarqube, argocd}`. `severity` and `occurred_at` are
columns (not duplicated into payload). `occurred_at` is the event's real moment:
incident start, alert firing-start, scan run time, deploy completion.

**`pagerduty` / `incident`** — `severity` ∈ `sev1..sev4`
```json
{
  "title": "p99 latency breach on /charge",
  "status": "open",                       // open | resolved
  "started_at": "<iso8601>",
  "resolved_at": null,                    // null while open
  "service": "payment-gateway",
  "url": "https://pd.example/incidents/PD-2041"
}
```

**`prometheus` / `alert`** — `severity` ∈ `critical | warning`
```json
{
  "alertname": "HighErrorRate",
  "expr": "rate(http_requests_total{code=~\"5..\"}[5m]) > 0.05",
  "value": 0.087,
  "threshold": 0.05,
  "state": "firing",                      // firing | resolved
  "started_at": "<iso8601>",
  "resolved_at": null
}
```

**`sonarqube` / `scan`** — `severity` is `null`
```json
{
  "coverage": 66.0,
  "code_smells": 214,
  "bugs": 7,
  "vulnerabilities": 2,
  "quality_gate": "ERROR",                // OK | ERROR
  "project_key": "payment-gateway"
}
```

**`argocd` / `deploy`** — `severity` is `null`
```json
{
  "revision": "v2.4.0",
  "image_tag": "1.9.0->1.10.0",
  "status": "Succeeded",                  // Succeeded | Failed
  "sync_status": "Synced",
  "author": "platform-bot",
  "url": "https://argo.example/applications/payment-gateway"
}
```

## Data contract: entity refs

Keyed to **existing** Backstage catalog entities (no catalog changes). Refs use
the `default` namespace (the example entities specify none, so Backstage assigns
`default`):

- **Protagonist:** `component:default/payment-gateway` — carries the open sev2,
  the firing alerts, the downward quality trend, and the planted deploy.
- **Blast-radius beat:** `component:default/fraud-detection-service` — already
  `dependsOn` `payment-gateway` in the catalog; healthy itself, but lets the agent
  note downstream exposure.
- **Background (breadth for `list_services`):**
  `component:default/notification-dispatcher`,
  `component:default/inventory-tracker`,
  `component:default/auth-service`.

All five must exist in the running Backstage catalog (they do, in
`backstage/examples/services.yaml`). The seeder treats this list as configuration.

## Data contract: the planted protagonist timeline

All offsets are relative to `now()` at seed time and are **fixed** (not random) so
the demo is byte-identical on every run. The shape gives the agent one
unmistakable correlation plus two contrasts (an incident with no causal deploy,
and a healthy deploy that caused no incident):

```
now-30h   pagerduty  incident  sev3  "intermittent timeout on /charge"   RESOLVED (resolved ~now-29h)
                                                  └─ no deploy in the preceding ~6h  → incident WITHOUT a cause
now-26h   argocd     deploy          v2.3.1  img 1.8.4->1.9.0  Succeeded
                                                  └─ caused no incident             → healthy deploy
now-2h    argocd     deploy          v2.4.0  img 1.9.0->1.10.0 Succeeded            ← THE change
now-90m   pagerduty  incident  sev2  "p99 latency breach on /charge"      OPEN
                                                  └─ ~30 min after v2.4.0           → the planted correlation
now-90m   prometheus alert     critical "HighErrorRate"                   FIRING
now-90m   prometheus alert     warning  "HighLatencyP99"                  FIRING
weekly×5  sonarqube  scan      —     coverage 78 -> 74 -> 71 -> 69 -> 66; code_smells rising;
                                       last scan quality_gate ERROR        → the downward trend
```

This satisfies the demo's four rehearsed questions (project spec §6): *what
services exist* (`list_services`), *what's going on with payment-gateway right
now* (open incident + firing alerts + dossier), *could the incident relate to a
recent change* (the `now-2h` deploy), and *whose code quality is trending the
wrong way* (payment-gateway's coverage decline vs. stable background).

## Data contract: background services

Each background service gets sparse, believable, **healthy** history over ~30 days
so the platform feels real without competing with the protagonist:

- 1–3 `pagerduty/incident` events, all `resolved` (none open now).
- `prometheus/alert` events all `resolved` (no firing alerts now → `payment-gateway`
  is unambiguously the one in trouble).
- weekly `sonarqube/scan` with coverage stable (~78–85%) and `quality_gate: OK`.
- 1–4 `argocd/deploy` per week, all `Succeeded`.

Counts, titles, and within-day jitter are drawn from a fixed RNG (`seed = 42`); the
protagonist's key events above use fixed offsets and are not subject to jitter.

## Determinism & rebuild

- Seeder uses `random.Random(42)` for all variable content.
- Timestamps are computed as `now() − offset`; the protagonist's headline events
  use fixed offsets so "right now" framing is identical every rehearsal and the
  live run.
- The store is fully rebuildable: `make db-init && make seed` recreates the schema
  (idempotent) and regenerates all events. `make down`/`make up` is the cold path.

## Verification

1. `make db-up` → the `postgres` pod in `data` reaches Ready.
2. `make db-init` → `psql -U brain -d brain -c '\dt'` lists `events` and `dossiers`;
   `\d events` shows both indexes.
3. From the host: `psql postgresql://brain:brain@localhost:5432/brain -c 'select 1'`
   succeeds (NodePort 30432 → host 5432 reachable).
4. After the seeder slice lands, the contract is checkable with, e.g.,
   `select count(*) from events where entity_ref='component:default/payment-gateway'`
   returning a non-zero count, and exactly one `open` incident + two `firing` alerts
   for the protagonist.
