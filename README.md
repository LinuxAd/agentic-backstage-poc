# SRE Second Brain

A Backstage-anchored internal developer platform POC on a local [kind](https://kind.sigs.k8s.io/) cluster. Operational signals (incidents, alerts, code-quality scans, deploys) are keyed to Backstage catalog entity refs, indexed in Postgres for exact, time-ranged retrieval, and exposed to an AI agent (Claude Code) through precise MCP tools.

> **The one-line architecture:** the Backstage catalog is the spine — every operational signal is keyed to a catalog entity ref (e.g. `component:default/payment-gateway`) and indexed in Postgres for exact, time-ranged retrieval. Agents get precise tools over a well-indexed store, not a fuzzy memory. Vectors are reserved for prose (designed, deliberately not built).

See `docs/specs/project-spec.md` for the full design and `docs/superpowers/` for per-slice design docs and plans.

## Prerequisites

On your PATH, with the Docker daemon running:

- `kind`, `kubectl`, `docker`, `helm`
- `uv` (runs the Python seeder + MCP server; `brew install uv`)
- **Node 22 or 24 with Corepack** (the Backstage build uses `yarn@4.4.1` via Corepack). If you use nvm, make sure the right node is on PATH for the build — `make`'s shell does not source nvm (see below).
- `claude` CLI (to register the MCP server)

Verify the core tools:

```sh
make preflight
```

## One-command demo

```sh
make demo
```

This cold-starts everything — kind cluster → ingress-nginx → brain-store Postgres + schema → synthetic seed → Backstage → MCP registration — then prints the demo script.

> **nvm note:** the Backstage build runs `yarn` on the host, and `make`'s non-login shell won't pick up nvm. Run with the right node prepended, e.g.:
> ```sh
> PATH="$HOME/.local/share/nvm/v22.22.3/bin:$PATH" make demo
> ```

When it finishes, **start a new Claude Code session in this repo** (MCP tools load at session start), then ask:

1. *What services exist on this platform and who owns them?*
2. *What's going on with payment-gateway right now?*
3. *Could the open incident be related to a recent change?*
4. *Which service's code quality is trending the wrong way?*

The agent calls the `second-brain` MCP tools, pulls a dossier assembled from the brain store, and answers with grounded, cited context — including discovering the deploy (`v2.4.0`, ~30 min before the open incident) that correlates with the `payment-gateway` outage. Closer: open **http://localhost:3000** and show `payment-gateway` in the Backstage catalog — the same entity ref the agent keyed everything to.

## Step-by-step (instead of `make demo`)

```sh
make up               # create the kind cluster + namespaces
make ingress-install  # install ingress-nginx and wait for it
make db-up            # deploy the brain-store Postgres
make db-init          # apply brain/schema.sql
make seed             # generate ~30 days of deterministic synthetic events
make backstage-up     # build, load and deploy Backstage  (needs Node 22/24 + Corepack)
make mcp-register     # register the 'second-brain' MCP server with Claude Code
```

Backstage is at **http://localhost:3000** (guest sign-in). The brain store is at `postgresql://brain:brain@localhost:5432/brain`.

> The first `make backstage-up` is slow — it runs a full `yarn install` and Backstage build before building the image.

## What's where

- `Makefile` — the operational spine (`make help` lists targets).
- `brain/schema.sql` — `events` + `dossiers` tables and composite indexes.
- `brain/seeder/seed.py` — deterministic synthetic-event generator.
- `brain/mcp_server/` — the `second-brain` MCP server (`server.py`) and its `queries.py` / `catalog.py` / `smoke_test.py`.
- `deploy/postgres/` — the brain-store Postgres manifest.
- `charts/backstage/` — hand-written Helm chart for the containerized Backstage app.

## Other commands

```sh
make help     # list all targets
make status   # show cluster, nodes and namespaces
make down     # delete the cluster
make nuke     # delete the cluster and prune images for a cold rebuild
```

## GitHub auth (optional)

Guest login works out of the box. For GitHub OAuth, create `backstage/.env` with `AUTH_GITHUB_CLIENT_ID` and `AUTH_GITHUB_CLIENT_SECRET`, then:

```sh
make backstage-secret
make backstage-deploy
```
