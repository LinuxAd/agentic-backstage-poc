# SonarQube (Community Build) bring-up — design

**Date:** 2026-06-15
**Status:** planned

## Goal

Add SonarQube Community Build to the kind cluster and expose it via ingress-nginx
at `http://sonarqube.localhost:3000`, without disturbing Backstage on
`http://localhost:3000`.

## Why this shape

- **Why SonarQube:** the project spec treats code-quality scans as a first-class
  operational signal keyed to Backstage catalog entity refs. A running SonarQube
  is the source of those scans.
- **Upstream Helm chart, not hand-written:** SonarQube has a non-trivial runtime
  (Elasticsearch + web). The maintained `sonarqube/sonarqube` chart is more
  reliable than re-deriving it; we pin the chart version and keep all local
  intent in a single `deploy/sonarqube-values.yaml`.
- **Embedded H2, ephemeral:** the cluster's Postgres "brain store" is not built
  yet. H2 keeps this slice self-contained. Data loss on restart is acceptable for
  a POC and mirrors the existing in-memory-SQLite stance for Backstage.
- **Host-based ingress:** Backstage owns the host-less catch-all `/` on
  `localhost:3000`. An ingress rule scoped to host `sonarqube.localhost` is more
  specific and wins for that host, leaving Backstage untouched. `*.localhost`
  resolves to loopback, so no `/etc/hosts` edit.
- **Elasticsearch on kind:** instead of a privileged init container raising
  `vm.max_map_count`, set `sonar.search.javaAdditionalOpts=-Dnode.store.allow_mmap=false`
  and disable `initSysctl`. This is the documented restricted-environment path and
  avoids host-kernel tuning that is fragile across Docker Desktop / Colima.

## Components

- `deploy/sonarqube-values.yaml` — Helm values override.
- `make sonarqube-up` / `make sonarqube-down` — lifecycle targets.
- Namespace: existing `apps`.
- Chart: `sonarqube/sonarqube` pinned to `2026.4.0`.

## Verification

1. `make sonarqube-up` completes and the pod reaches Ready.
2. `curl -sS -H 'Host: sonarqube.localhost' http://localhost:3000/api/system/status`
   returns JSON with `"status":"UP"`.
3. Backstage still loads at `http://localhost:3000`.
4. `make sonarqube-down` removes the release; `kubectl get all -n apps` is clean.

## Out of scope

- Postgres-backed persistence (future slice, alongside the brain store).
- Wiring SonarQube findings into the brain store / MCP tools.
- Registering SonarQube as a Backstage catalog entity or plugin.
- Auth/SSO; the default `admin/admin` first-login flow is fine for local POC.
