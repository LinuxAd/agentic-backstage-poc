# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**SRE Second Brain** — a proof-of-concept for a Backstage-anchored internal developer platform where operational signals (incidents, alerts, code-quality scans, deploys) are keyed to Backstage catalog entity refs, indexed in Postgres for exact time-ranged retrieval, and exposed to an AI agent via MCP tools. The full vision and rationale live in `docs/specs/project-spec.md`; read it before making architectural decisions.

The one-line architecture: *the Backstage catalog is the spine — every operational signal is keyed to a catalog entity ref (e.g. `component:default/payments-api`) and indexed in Postgres for exact, time-ranged retrieval; vectors are deliberately reserved for prose only.*

### Build state vs. spec

The end-to-end demo is built. **Currently implemented:** the kind cluster foundation (`kind/`, `deploy/namespaces.yaml`); the Backstage source app (`backstage/`) deployed via a hand-written Helm chart behind ingress (`charts/backstage/`, `backstage-*` targets); the Postgres "brain store" (`deploy/postgres/`, `brain/schema.sql`, `db-up`/`db-init`); the deterministic synthetic-event seeder (`brain/seeder/seed.py`, `make seed`); and the `second-brain` MCP server exposing six tools to Claude Code (`brain/mcp_server/`, `make mcp-register`). `make demo` cold-starts the whole stack in one command. **Not built (deliberately, per spec):** the async-embedding sidecar for prose (vectors are designed-but-omitted), and the demo apps in the `apps` namespace (first cut).

Note one deviation from the spec already taken: spec constraint #3 said "no custom Backstage image." The repo instead **containerizes the local Backstage source app** and loads it into kind with `kind load docker-image` (no registry). This is intentional — see `docs/superpowers/specs/2026-06-12-kind-cluster-bringup-design.md`.

## Repository layout

- `Makefile` — the operational spine; orchestrates the entire cluster + Backstage + brain-store + MCP lifecycle. `make demo` is the one-command cold start.
- `kind/cluster.yaml` — single control-plane kind cluster, pinned node image, host port mappings (3000→ingress, 7007→Backstage NodePort, 5432→Postgres NodePort 30432).
- `deploy/namespaces.yaml` — the four namespaces: `backstage`, `data`, `apps`, `brain`.
- `deploy/postgres/postgres.yaml` — the brain-store Postgres (Deployment + NodePort Service + emptyDir) in the `data` namespace.
- `charts/backstage/` — a hand-written Helm chart (not the upstream Backstage chart) that deploys the containerized app behind ingress-nginx.
- `backstage/` — the Backstage source app (Yarn 4 workspaces monorepo: `packages/app` frontend, `packages/backend` backend). Has its own README and tooling.
- `brain/` — `schema.sql` (the `events`/`dossiers` tables + composite indexes), `seeder/seed.py` (synthetic events), and `mcp_server/` (`server.py` FastMCP tools, `queries.py` SQL + dossier assembly, `catalog.py` Backstage REST client, `smoke_test.py`).
- `docs/specs/` and `docs/superpowers/specs/` — the product spec and per-slice design docs. Design docs are written before each implementation slice; plans live in `docs/superpowers/plans/`.

## Common commands

All cluster/deploy operations go through the Makefile (run `make help` for the list):

```sh
make preflight       # verify kind, kubectl, docker, helm, uv are installed and the daemon is up
make demo            # COLD-START EVERYTHING: cluster → ingress → brain store → seed → Backstage → register MCP, then print the demo script
make up              # create kind cluster (idempotent) + apply namespaces
make ingress-install # install ingress-nginx (kind provider) and wait for readiness
make backstage-up    # build → load → deploy Backstage end-to-end
make db-up           # deploy the brain-store Postgres and wait for Ready
make db-init         # apply brain/schema.sql into the running Postgres (idempotent)
make seed            # generate ~30 days of deterministic synthetic events (uv runs seed.py)
make mcp-register    # register the 'second-brain' MCP server with Claude Code
make status          # show cluster, nodes, namespaces
make down            # delete the kind cluster
make nuke            # down + prune dangling images for a cold rebuild
```

Cold start for the full demo: `make demo` (then start a **new** Claude Code session to load the MCP tools).

**Toolchain gotchas (both bit us during bring-up):** `make backstage-up` runs a host `yarn` build, which needs **Node 22 or 24 with Corepack-provided `yarn@4.4.1`** on PATH — `make`'s non-login shell does not source nvm, so run with the right node prepended (e.g. `PATH="$HOME/.local/share/nvm/v22.22.3/bin:$PATH" make demo`). And after a fresh `make up`, Backstage 404s until `make ingress-install` has run (the `demo` target sequences this for you). The seeder and MCP server run locally via `uv` (PEP 723 inline deps; nothing is installed into system Python).

GitHub OAuth (optional): `make backstage-secret` reads `backstage/.env` (gitignored; expects `AUTH_GITHUB_CLIENT_ID` / `AUTH_GITHUB_CLIENT_SECRET`) into the `backstage-github-auth` Secret. With `auth.github.enabled: false` in chart values, dummy values are injected and guest login is used instead.

### Backstage app development

Work inside `backstage/` (Yarn 4 via Corepack, packageManager pinned in `package.json`):

```sh
yarn install          # --immutable in CI/builds
yarn start            # dev: frontend :3000, backend :7007, sqlite
yarn tsc              # typecheck
yarn build:backend    # build the backend bundle (what the Docker image needs)
yarn test             # all workspaces; pass a path to scope to one test
yarn lint             # lint changed files (lint:all for everything)
yarn test:e2e         # Playwright e2e
```

## How the pieces fit (non-obvious wiring)

- **Image flow:** `make backstage-build` runs `host-build` (yarn install/tsc/build:backend on the host) then `docker build` the backend `Dockerfile`, tagging `backstage:latest`. `make backstage-load` does `kind load docker-image` — there is no registry, so the chart uses `pullPolicy: IfNotPresent` to avoid trying to pull `:latest`.

- **App-config layering** is the fiddliest part (called out in the spec's risk register). The image bakes `app-config.yaml` + `app-config.production.yaml`; the chart mounts a third overlay (`app-config.kind.yaml`) from a ConfigMap at `/app/config/`, and the deployment's container command passes all three via repeated `--config` flags. The ConfigMap content comes from `appConfig:` in `charts/backstage/values.yaml`. The deployment annotates the pod with a `sha256sum` of that config so changing it rolls the pod. **Backstage's own catalog DB is in-memory SQLite** — the catalog rebuilds from the baked `./examples` on every start. (The brain-store Postgres is a *separate* concern keyed off the catalog, not Backstage's database.) The overlay also sets `backend.auth.dangerouslyDisableDefaultAuthPolicy: true` so the MCP server can read the catalog API.

- **Networking:** ingress-nginx is the entry point on `http://localhost:3000` (kind maps host 3000 → container 80). The ingress is a host-less catch-all so any Host header matches. Backstage's `app.baseUrl` and `backend.baseUrl` are both set to `http://localhost:3000` (single origin). The brain-store Postgres is reached at `localhost:5432` via a NodePort Service on `30432` (mapped in `kind/cluster.yaml`).

- **Brain store + entity refs:** every seeded event is keyed to an existing Backstage catalog entity ref (e.g. `component:default/payment-gateway`) — the catalog is the spine. Retrieval is exact-match on `entity_ref` + time-range on `occurred_at` over composite B-tree indexes (no vectors). The seeder is deterministic (`random.Random(42)`) but anchors timestamps to `now()`, so the protagonist `payment-gateway` always shows an open incident "right now"; re-running `make seed` truncates and regenerates.

- **MCP server wiring:** `brain/mcp_server/server.py` is a stdio FastMCP server run locally via `uv run` (PEP 723 inline deps: `mcp`, `psycopg2-binary`); `make mcp-register` adds it to Claude Code as `second-brain`. Five of its six tools read Postgres at `localhost:5432`; `list_services` reads the Backstage catalog REST API at `localhost:3000`. That catalog read requires `backend.auth.dangerouslyDisableDefaultAuthPolicy: true` in the chart's `appConfig` (the new Backstage backend 401s unauthenticated requests by default) — demo-only. MCP tools load at session start, so registering requires a **new** Claude Code session before the tools appear.

## Conventions

- Each implementation slice gets a design doc in `docs/superpowers/specs/` (dated, scoped, with a verification section) before code. Follow that pattern for new slices.
- Makefile targets each echo a short progress line (`→`/`✓`) so a live demo is legible; keep that style. Targets are idempotent where possible (`make up` skips an existing cluster).
- Reliability over scope is the guiding constraint: prefer a smaller thing that comes up cold with one command over a bigger thing that might.
