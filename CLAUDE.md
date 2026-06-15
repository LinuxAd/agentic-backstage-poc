# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**SRE Second Brain** — a proof-of-concept for a Backstage-anchored internal developer platform where operational signals (incidents, alerts, code-quality scans, deploys) are keyed to Backstage catalog entity refs, indexed in Postgres for exact time-ranged retrieval, and exposed to an AI agent via MCP tools. The full vision and rationale live in `docs/specs/project-spec.md`; read it before making architectural decisions.

The one-line architecture: *the Backstage catalog is the spine — every operational signal is keyed to a catalog entity ref (e.g. `component:default/payments-api`) and indexed in Postgres for exact, time-ranged retrieval; vectors are deliberately reserved for prose only.*

### Build state vs. spec

The spec describes the full system, but only part is built. **Currently implemented:** the kind cluster foundation (`kind/`, `deploy/namespaces.yaml`), the Backstage source app (`backstage/`), a Helm chart deploying it via ingress (`charts/backstage/`, Makefile `backstage-*` targets), and SonarQube Community Build deployed from the upstream chart via `deploy/sonarqube-values.yaml` (Makefile `sonarqube-*` targets), served at `http://sonarqube.localhost:3000`. **Not yet built:** Postgres "brain store" (`brain/schema.sql`), the synthetic-event seeder (`seed.py`), the MCP server, and the `make seed` / `make demo` targets. The four namespaces and the port mappings (5432 for Postgres) are pre-wired for those components to land into.

Note one deviation from the spec already taken: spec constraint #3 said "no custom Backstage image." The repo instead **containerizes the local Backstage source app** and loads it into kind with `kind load docker-image` (no registry). This is intentional — see `docs/superpowers/specs/2026-06-12-kind-cluster-bringup-design.md`.

## Repository layout

- `Makefile` — the operational spine; orchestrates the entire cluster + Backstage lifecycle.
- `kind/cluster.yaml` — single control-plane kind cluster, pinned node image, host port mappings (3000→ingress, 7007→Backstage NodePort, 5432→Postgres).
- `deploy/namespaces.yaml` — the four namespaces: `backstage`, `data`, `apps`, `brain`.
- `charts/backstage/` — a hand-written Helm chart (not the upstream Backstage chart) that deploys the containerized app behind ingress-nginx.
- `backstage/` — the Backstage source app (Yarn 4 workspaces monorepo: `packages/app` frontend, `packages/backend` backend). Has its own README and tooling.
- `docs/specs/` and `docs/superpowers/specs/` — the product spec and per-slice design docs. Design docs are written before each implementation slice.

## Common commands

All cluster/deploy operations go through the Makefile (run `make help` for the list):

```sh
make preflight       # verify kind, kubectl, docker are installed and the daemon is up
make up              # create kind cluster (idempotent) + apply namespaces
make ingress-install # install ingress-nginx (kind provider) and wait for readiness
make backstage-up    # build → load → deploy Backstage end-to-end
make status          # show cluster, nodes, namespaces
make down            # delete the kind cluster
make sonarqube-up    # deploy SonarQube Community Build (upstream chart) into the apps ns
make sonarqube-down  # uninstall SonarQube
make nuke            # down + prune dangling images for a cold rebuild
```

Cold-start sequence for a working Backstage on `http://localhost:3000`:
`make up && make ingress-install && make backstage-up`. Add `make sonarqube-up` to
also bring up SonarQube at `http://sonarqube.localhost:3000`.

**Memory:** SonarQube (Elasticsearch + web) requests 2 GiB and needs ~2–3 GiB to run.
The Docker/Colima VM backing kind must have **≥ ~6 GiB** allocated — with less, the
`sonarqube-sonarqube-0` pod stays `Pending` with `Insufficient memory` (Docker Desktop:
Settings → Resources → Memory; Colima: `colima start --memory 6`). Backstage alone is
fine on the default ~2 GiB.

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

- **App-config layering** is the fiddliest part (called out in the spec's risk register). The image bakes `app-config.yaml` + `app-config.production.yaml`; the chart mounts a third overlay (`app-config.kind.yaml`) from a ConfigMap at `/app/config/`, and the deployment's container command passes all three via repeated `--config` flags. The ConfigMap content comes from `appConfig:` in `charts/backstage/values.yaml`. The deployment annotates the pod with a `sha256sum` of that config so changing it rolls the pod. **Currently the kind overlay sets the database to in-memory SQLite** — the catalog rebuilds from the baked `./examples` on every start; there is no Postgres dependency yet.

- **Networking:** ingress-nginx is the entry point on `http://localhost:3000` (kind maps host 3000 → container 80). The ingress is a host-less catch-all so any Host header matches. Backstage's `app.baseUrl` and `backend.baseUrl` are both set to `http://localhost:3000` (single origin). 7007 (Backstage NodePort) and 5432 (future Postgres) are also mapped but optional/unused until their Services exist. SonarQube is reached at `http://sonarqube.localhost:3000`: its ingress rule is scoped to the `sonarqube.localhost` host, which is more specific than Backstage's host-less catch-all and so wins for that host only. `*.localhost` resolves to loopback, so no `/etc/hosts` edit is required.

## Conventions

- Each implementation slice gets a design doc in `docs/superpowers/specs/` (dated, scoped, with a verification section) before code. Follow that pattern for new slices.
- Makefile targets each echo a short progress line (`→`/`✓`) so a live demo is legible; keep that style. Targets are idempotent where possible (`make up` skips an existing cluster).
- Reliability over scope is the guiding constraint: prefer a smaller thing that comes up cold with one command over a bigger thing that might.
