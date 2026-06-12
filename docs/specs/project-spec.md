# BSA POC Spec — "SRE Second Brain" on a Local Kind Cluster

**Goal:** A working demo that proves the architecture you'll defend in the BSA: a Backstage-anchored internal developer platform where operational signals are indexed against the service catalog in Postgres (not a vector DB), exposed to an AI agent through precise tools, and queried live via Claude Code.

**Demo punchline (what the interviewer sees):** You type *"What's going on with payments-api right now?"* into Claude Code. It calls your MCP tools, pulls a dossier assembled from incidents, alerts, code-quality and deploy events keyed to the Backstage entity, and answers with grounded, cited operational context. Then you open Backstage and show the same service in the catalog.

**Hard deadline:** Demo-ready Monday evening. Interview Tuesday 16 June.

---

## 1. Guiding constraints

1. **Reliability over scope.** Everything must come up with one command on a cold laptop. A smaller demo that works beats a bigger one that might.
2. **Boring tech, defended.** Postgres with a composite index, not a vector DB. This is a talking point, not a shortcut — say so in the interview.
3. **No custom Backstage image.** Use the published Backstage image + Helm chart; load catalog entities from raw GitHub URLs. Building Backstage from source is the single biggest schedule risk — avoid it.
4. **Synthetic data is fine.** The interviewer is evaluating architecture and reasoning, not your PagerDuty bill. A seeder generating plausible events is honest and sufficient — label it as simulated.
5. **Every component maps to a BSA scoring axis** (enthusiasm, technical depth, problem-solving, autonomy, quality). See §9.

---

## 2. Repository layout (monorepo)

```
sre-second-brain/
├── README.md                  # quickstart + architecture diagram
├── Makefile                   # up / seed / demo / down / nuke
├── kind/
│   └── cluster.yaml           # kind config (1 control plane, port mappings)
├── deploy/
│   ├── namespaces.yaml        # backstage, data, apps, brain
│   ├── backstage/
│   │   └── values.yaml        # Backstage Helm chart values
│   ├── postgres/
│   │   └── values.yaml        # Bitnami PostgreSQL chart values
│   └── brain/
│       ├── deployment.yaml    # MCP/tool API deployment + service
│       └── seeder-job.yaml    # one-shot K8s Job (optional; can run locally)
├── catalog/                   # Backstage catalog-info files (served via raw GitHub URLs)
│   ├── all.yaml               # Location entity pointing at the rest
│   ├── systems.yaml           # 1 system: "payments-platform"
│   ├── payments-api.yaml
│   ├── ledger-service.yaml
│   ├── fraud-screener.yaml
│   ├── notifications.yaml
│   └── checkout-web.yaml
├── apps/                      # cheap demo services (Python/FastAPI, built with Claude)
│   ├── payments-api/          # Dockerfile + ~50 lines: /health, /metrics stub
│   └── ledger-service/
├── brain/
│   ├── schema.sql             # events + dossiers tables, indexes
│   ├── seeder/
│   │   └── seed.py            # synthetic event generator
│   └── mcp_server/
│       ├── server.py          # MCP server exposing the tools (python mcp sdk)
│       └── queries.py         # SQL, dossier assembly
└── docs/
    └── architecture.md        # the diagram + trade-off notes you'll talk to
```

Decision recorded: **one Postgres instance, two databases** (`backstage`, `brain`) in the `data` namespace. Backstage's chart is pointed at it as an external DB. One stateful component to babysit instead of two.

---

## 3. Cluster + platform bring-up

**Makefile targets (the demo's spine):**

| Target | Does |
|---|---|
| `make up` | kind create cluster → create namespaces → helm install postgres → apply schema → helm install backstage → deploy brain API → (optional) deploy apps |
| `make seed` | runs `seed.py` against Postgres — 30 days of synthetic events across 5 services |
| `make demo` | port-forwards Backstage (7007) and the MCP server, prints the URLs and the Claude Code connect command |
| `make down` | kind delete cluster |

**Backstage via Helm:**
- Chart: `backstage/backstage` (the official chart).
- `values.yaml` essentials: published `ghcr.io/backstage/backstage` image; `app-config` overrides inline in values for: `catalog.locations` → raw GitHub URL of `catalog/all.yaml`; guest auth; external Postgres pointed at `postgres.data.svc.cluster.local`.
- Catalog rule: allow `Component, System, API, Location`.
- **Risk note:** the stock image's app-config layering via the chart is the fiddliest part of the whole build. Do this FIRST (Friday evening / Saturday morning), not last. Fallback in §8.

**PostgreSQL via Helm:**
- Chart: `bitnami/postgresql`, namespace `data`, single primary, small PVC (or `persistence.enabled=false` — it's a demo; rebuildable via `make seed`).
- Init: apply `brain/schema.sql` via a `kubectl exec` step in `make up` (simplest) rather than initdb scripts in values.

**Catalog entities (5 components + 1 system):**
- `payments-api` (Go, owner: platform-team), `ledger-service` (Python), `fraud-screener` (Python), `notifications` (Go), `checkout-web` (TypeScript) — all part of system `payments-platform`, with `dependsOn` relations between them so the Backstage graph view has something to show.
- Entity refs (`component:default/payments-api`) are the **canonical keys** for everything in the brain store. This is the architectural sentence of the demo.

**Demo apps (stretch, not MVP):** two FastAPI containers with `/health` returning service metadata, deployed to `apps` namespace, referenced by annotation in their catalog entries. They make the cluster feel real but nothing downstream depends on them. Cut first if time is short.

---

## 4. The brain store (Postgres)

```sql
CREATE TABLE events (
    id          bigserial PRIMARY KEY,
    entity_ref  text        NOT NULL,   -- component:default/payments-api
    source      text        NOT NULL,   -- pagerduty | prometheus | sonarqube | argocd
    kind        text        NOT NULL,   -- incident | alert | scan | deploy
    severity    text,                   -- sev1..sev4 | critical|warning | null
    payload     jsonb       NOT NULL,   -- source-shaped detail
    occurred_at timestamptz NOT NULL
);

CREATE INDEX idx_events_lookup
    ON events (entity_ref, source, occurred_at DESC);

CREATE INDEX idx_events_kind
    ON events (entity_ref, kind, occurred_at DESC);

CREATE TABLE dossiers (
    entity_ref   text PRIMARY KEY,
    generated_at timestamptz NOT NULL,
    summary      jsonb       NOT NULL   -- counts, open items, trends, last deploy
);
```

**Interview point baked into the schema:** retrieval is exact-match + time-range on a composite B-tree index — microseconds, precise, no embeddings, no ANN index maintenance, no write amplification. The vector pattern is reserved (in the real design, not this POC) for the prose corpus only: postmortems and runbooks, embedded asynchronously off the write path.

**Dossier materialisation:** `queries.py` includes `rebuild_dossier(entity_ref)` — counts of open incidents/alerts, quality trend direction, last deploy, top recent events. In the POC, rebuild on demand (cheap); in the real design this is a periodic rollup job. One pre-assembled read instead of N queries per agent question = lower latency and token cost.

---

## 5. Seeder (synthetic feeds)

`seed.py`: for each of the 5 entity refs, generate ~30 days of events with believable shape:
- **pagerduty/incident:** 2–6 per service, mixed severities, one *open* sev2 on `payments-api` (the demo's protagonist), realistic titles ("p99 latency breach on /charge", "connection pool exhaustion").
- **prometheus/alert:** firing + resolved alerts; 2 currently firing on `payments-api`.
- **sonarqube/scan:** weekly scans; coverage and code-smell counts trending slightly *down* on `payments-api` (gives the agent a trend to notice), stable elsewhere.
- **argocd/deploy:** 1–4 deploys/week; crucially, a deploy on `payments-api` ~2 hours before the open incident started. **This is the planted correlation the agent can "discover" live** — deploy → incident proximity is the moment the demo stops being a CRUD app and starts being an SRE tool.

Deterministic seed (fixed RNG) so every rehearsal and the live run produce identical data.

---

## 6. MCP server (the tool layer)

Python `mcp` SDK, stdio or HTTP transport, running in-cluster (deployment in `brain` ns) or — simpler and perfectly defensible — locally against the port-forwarded Postgres. **Recommendation: run it locally for the demo.** One fewer image to build; the architecture story is identical.

Tools (precise, boring, fast — say that out loud):

| Tool | Signature | Backing |
|---|---|---|
| `list_services` | () → entity refs + owners + system | Backstage REST API (`/api/catalog/entities`) — proves catalog-as-spine |
| `get_service_dossier` | (entity_ref) → rollup | `dossiers` table (rebuild on read) |
| `get_recent_incidents` | (entity_ref, days=7) → rows | indexed query |
| `get_active_alerts` | (entity_ref) → firing alerts | indexed query |
| `get_quality_trend` | (entity_ref, weeks=4) → series | indexed query |
| `get_recent_deploys` | (entity_ref, days=7) → rows | indexed query |

Claude Code config: register the MCP server (`claude mcp add second-brain -- python brain/mcp_server/server.py`). Rehearse the exact questions:
1. "What services exist on this platform and who owns them?"
2. "What's going on with payments-api right now?"
3. "Could the open incident be related to a recent change?" ← the planted deploy correlation pays off here.
4. "Which service's code quality is trending the wrong way?"

---

## 7. Build order & timeboxes

| When | Task | Cut line |
|---|---|---|
| Fri eve (≤2h) | Repo scaffold, kind config, Makefile skeleton, catalog YAMLs pushed to GitHub | — |
| Sat AM | Postgres + Backstage on kind, catalog loading from raw URLs. **The risk burns down here.** | If Backstage-on-Helm fights past midday → §8 fallback, move on |
| Sat PM | schema.sql, seeder, verify queries by hand in psql | MVP line: everything above must exist |
| Sun AM | MCP server + Claude Code wiring, first end-to-end question answered | — |
| Sun PM | Dossier rollup, planted-correlation polish, demo apps **only if everything else is green** | Demo apps are the first cut |
| Mon | Full rehearsal ×2 from `make down && make up && make seed`; record backup video; write `docs/architecture.md` talking notes | No new features Monday. None. |

---

## 8. Risk register & fallbacks

1. **Backstage Helm/app-config pain (high likelihood).** Fallback: run Backstage via `docker run` with a mounted app-config, keep Postgres + brain + apps on kind. The catalog-as-spine story survives intact; mention the trade-off honestly if asked — that's an autonomy/problem-solving point, not a confession.
2. **Live demo failure (always possible).** Record a 3–4 min screen capture of the full happy path on Monday. Open the interview with live; if anything wobbles, switch to the recording without apologising and keep talking architecture.
3. **Corporate-laptop / network surprises.** Demo from the personal machine it was built on. Pre-pull all images (`kind load docker-image` or warm cache) so `make up` works offline.
4. **Time collapse (kids, life, interview prep for the other rounds).** The MVP line is end of Saturday. If you're behind it Sunday morning, the demo becomes: Backstage in docker, Postgres in docker-compose, MCP local. Still a working demo, still the same architecture.

---

## 9. Mapping to the five BSA axes

- **Enthusiasm & engagement:** this is your own production pain (scattered operational context) turned into a tool — tell it that way, not as a homework exercise.
- **Technical depth:** the vector-DB rejection with reasons; composite-index retrieval; entity-ref-as-canonical-key; async-embedding sidecar reserved for prose (designed, deliberately not built).
- **Problem-solving:** the planted deploy→incident correlation found live by the agent; the Helm fallback decision if you had to take it.
- **Autonomy:** monorepo, one-command bring-up, deterministic seed, schema you wrote — all yours, all reproducible.
- **Quality:** `make down && make up && make seed` works cold; README quickstart; recorded backup; the demo was rehearsed.

---

## 10. The one-line architecture (say it early, say it twice)

> "The Backstage catalog is the spine: every operational signal is keyed to a catalog entity ref and indexed in Postgres for exact, time-ranged retrieval. Agents don't get a fuzzy memory — they get precise tools over a well-indexed store. Vectors are reserved for the only corpus where similarity is the right semantics: prose."
