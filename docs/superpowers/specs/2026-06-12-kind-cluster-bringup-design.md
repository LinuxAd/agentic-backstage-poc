# Design: Local kind cluster bring-up

**Date:** 2026-06-12
**Scope:** Cluster foundation only — `kind/cluster.yaml`, `deploy/namespaces.yaml`, and a `Makefile` with cluster-lifecycle targets. Postgres, Backstage deployment, the brain store, the seeder, and the MCP server are out of scope for this slice and land later.

Parent spec: `docs/specs/project-spec.md` (BSA POC — "SRE Second Brain").

## Context & decisions

The repo already contains a **Backstage source app** in `backstage/` (created via `create-app`; frontend `:3000`, backend `:7007`, sqlite dev DB, GitHub auth). This contradicts spec constraint #3 ("no custom Backstage image; use the published image + Helm chart"). Two decisions resolve the path:

1. **Backstage delivery:** containerize the source app and load it into kind via `kind load docker-image` (no local registry). The image build + load is deferred to a later slice; this slice only prepares the cluster it will land on.
2. **Makefile scope now:** cluster foundation only — bring the cluster up/down reliably with namespaces in place. Other spec targets (`seed`, `demo`, Postgres/Backstage deploy) are added as those components get built.

Guiding constraint from the spec: **reliability over scope** — everything must come up with one command on a cold laptop.

## Files introduced

| File | Purpose |
|---|---|
| `kind/cluster.yaml` | kind cluster definition — 1 control-plane node, pinned node image, host port mappings |
| `deploy/namespaces.yaml` | the four namespaces: `backstage`, `data`, `apps`, `brain` |
| `Makefile` | `help` / `preflight` / `up` / `down` / `nuke` / `status` |

## `kind/cluster.yaml`

- Single control-plane node, no workers (demo; reliability over scope).
- **Pinned node image** (`kindest/node:v1.31.x@sha256:…`) so cold rebuilds are reproducible — a Quality/Autonomy point (spec §9). Pin to a current kind-supported node image at implementation time.
- `extraPortMappings` so services are reachable on `localhost` without managing port-forwards:
  - host **7007 → nodePort 30007** — Backstage (the containerized source app, once deployed).
  - host **5432 → nodePort 30432** — Postgres, so the locally-run MCP server (spec §6 recommendation) and `psql` connect to `localhost:5432` directly.
  - These mappings are harmless before the matching NodePort Services exist; nothing listens until then.

## `deploy/namespaces.yaml`

Plain `kind: Namespace` manifests for `backstage`, `data`, `apps`, `brain`, in a single multi-document YAML file. Applied by `make up` with `kubectl apply` (idempotent).

## `Makefile`

Variables:
- `CLUSTER_NAME ?= sre-second-brain`
- `KIND_CONFIG ?= kind/cluster.yaml`
- kube-context is `kind-$(CLUSTER_NAME)`.

Targets (foundation scope):

| Target | Behaviour |
|---|---|
| `help` | Default target. Self-documenting list of targets. |
| `preflight` | Assert `kind`, `kubectl`, `docker` are on PATH and the Docker daemon is reachable. Fail with a clear, actionable message otherwise. |
| `up` | Depends on `preflight`. **Idempotent:** if a cluster named `$(CLUSTER_NAME)` already exists, skip create; otherwise `kind create cluster --name $(CLUSTER_NAME) --config $(KIND_CONFIG)`. Then `kubectl apply -f deploy/namespaces.yaml`. |
| `down` | `kind delete cluster --name $(CLUSTER_NAME)`. |
| `nuke` | `down`, plus remove the loaded Backstage image and `docker image prune` of dangling images, for a truly cold next run. |
| `status` | Show cluster existence, nodes, and namespaces at a glance. |

Conventions:
- `.PHONY` for all targets (no file outputs).
- `.DEFAULT_GOAL := help`.
- Each target echoes a short progress line so the demo is legible.

## Deferred (design accommodates, not built now)

- A `load-backstage` target: `docker build` the source app, then `kind load docker-image` it into the cluster.
- `seed` / `demo` / Postgres install / Backstage deploy / brain API targets, added as those components land. The port mappings (7007, 5432) and namespaces above are exactly what they plug into.

## Out of scope / noted

- Root `node_modules/` and `package-lock.json` are untracked and there is no root `.gitignore`. Left alone unless separately requested.

## Verification

- `make up` on a machine with no existing cluster creates `sre-second-brain` and the four namespaces; `kubectl get ns` shows them.
- `make up` run a second time is a no-op for cluster creation (idempotent) and re-applies namespaces cleanly.
- `make status` reports the cluster and namespaces.
- `make down` deletes the cluster; `make nuke` additionally clears images for a cold rebuild.
- `make preflight` fails clearly when Docker is not running.
