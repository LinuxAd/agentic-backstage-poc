# Brain-store Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the brain-store Postgres (`postgres:16` in the `data` namespace, reachable at `localhost:5432`) and apply the `events`/`dossiers` schema, so the seeder and MCP server (later slices) have a data layer to write to and query.

**Architecture:** A plain `postgres:16` Deployment + NodePort Service (`30432`, already mapped to host `5432` in `kind/cluster.yaml`) with `emptyDir` storage (ephemeral, rebuilt by `make seed`). The schema lives in `brain/schema.sql` as the single source of truth and is applied by `make db-init`, which pipes it into the running pod via `kubectl exec -i … psql -f -` (no ConfigMap, no custom image). Seeder/MCP run locally against `postgresql://brain:brain@localhost:5432/brain`.

**Tech Stack:** kind, kubectl, Postgres 16, Make.

**Design doc:** `docs/superpowers/specs/2026-06-15-brain-store-data-layer-design.md`

---

## Scope

In scope: the Postgres manifest, `brain/schema.sql`, and the `db-up` / `db-init` Makefile targets, verified end-to-end on the live cluster. **Out of scope** (later slices): `brain/seeder/seed.py`, the MCP server, and folding `db-up`/`db-init`/`seed` into `make up`.

## Refinement from the spec

The spec described applying the schema from a ConfigMap. This plan instead pipes `brain/schema.sql` into the pod over `kubectl exec -i` (stdin). The `make db-init` interface and verification are identical, but it removes the ConfigMap component and keeps `brain/schema.sql` as the one source of truth — fewer moving parts before the deadline.

## File structure

- **Create** `brain/schema.sql` — the DDL (`events`, `dossiers`, two indexes). Single source of truth for the schema.
- **Create** `deploy/postgres/postgres.yaml` — Deployment + NodePort Service in the `data` namespace.
- **Modify** `Makefile` — add `DATA_NS`/`SCHEMA_SQL` vars, `db-up` and `db-init` targets, extend `.PHONY`.

## Prerequisites

The kind cluster must be up with namespaces applied (`make up`) — the `data` namespace must exist. The cluster pulls `postgres:16` from Docker Hub on first `db-up` (has internet); thereafter `IfNotPresent`.

---

### Task 1: Create the schema and the Postgres manifest

These are static artifacts validated by client-side checks; they are applied for real in Task 3.

**Files:**
- Create: `brain/schema.sql`
- Create: `deploy/postgres/postgres.yaml`

- [ ] **Step 1: Write `brain/schema.sql`**

Create `brain/schema.sql` with exactly this content:

```sql
-- Brain store schema. Applied by `make db-init`. Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS events (
    id          bigserial   PRIMARY KEY,
    entity_ref  text        NOT NULL,   -- component:default/payment-gateway
    source      text        NOT NULL,   -- pagerduty | prometheus | sonarqube | argocd
    kind        text        NOT NULL,   -- incident | alert | scan | deploy
    severity    text,                   -- sev1..sev4 | critical | warning | null
    payload     jsonb       NOT NULL,   -- source-shaped detail
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

- [ ] **Step 2: Write `deploy/postgres/postgres.yaml`**

Create `deploy/postgres/postgres.yaml` with exactly this content:

```yaml
# Brain-store Postgres for the SRE Second Brain POC.
# Plain postgres:16 in the `data` namespace, exposed on NodePort 30432
# (mapped to host 5432 by kind/cluster.yaml). Ephemeral (emptyDir): the store is
# rebuilt by `make db-init && make seed`. Demo-only credentials (synthetic data only).
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: data
  labels:
    app: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16
          imagePullPolicy: IfNotPresent
          env:
            - name: POSTGRES_DB
              value: brain
            - name: POSTGRES_USER
              value: brain
            - name: POSTGRES_PASSWORD
              value: brain
            # Keep PGDATA in a subdir of the mount so initdb owns a clean dir.
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          ports:
            - name: postgres
              containerPort: 5432
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "brain", "-d", "brain"]
            initialDelaySeconds: 5
            periodSeconds: 5
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
      volumes:
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: data
  labels:
    app: postgres
spec:
  type: NodePort
  selector:
    app: postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: 5432
      nodePort: 30432
```

- [ ] **Step 3: Validate both files client-side**

Run:
```bash
python3 -c "import yaml,sys; list(yaml.safe_load_all(open('deploy/postgres/postgres.yaml'))); print('yaml ok')"
kubectl --context kind-sre-second-brain apply --dry-run=client -f deploy/postgres/postgres.yaml
test -s brain/schema.sql && echo "schema.sql present"
```
Expected: `yaml ok`; dry-run prints `deployment.apps/postgres created (dry run)` and `service/postgres created (dry run)` with no errors; `schema.sql present`.

- [ ] **Step 4: Commit**

```bash
git add brain/schema.sql deploy/postgres/postgres.yaml
git commit -m "feat: add brain-store postgres manifest and schema"
```

---

### Task 2: Add the `db-up` and `db-init` Makefile targets

Mirror the existing `backstage-*` style: idempotent, `→`/`✓` echoes, `--context "$(KUBECONTEXT)"`.

**Files:**
- Modify: `Makefile` (vars near the top after `BACKSTAGE_DIR`; `.PHONY` line; new targets before `help:`)

- [ ] **Step 1: Add variables**

In `Makefile`, after the line `BACKSTAGE_DIR ?= backstage`, add (preceded by a blank line):

```makefile

DATA_NS    ?= data
SCHEMA_SQL ?= brain/schema.sql
```

- [ ] **Step 2: Extend `.PHONY`**

Append `db-up db-init` to the existing `.PHONY:` line so it ends with:

```
... backstage-deploy backstage-up db-up db-init
```

- [ ] **Step 3: Add the targets**

Immediately before the `help:` target, insert (recipe lines MUST use real TAB indentation, like the other targets):

```makefile
db-up: ## Deploy the brain-store Postgres and wait for it to be Ready
	@echo "→ deploying brain-store postgres into '$(DATA_NS)'"
	@kubectl --context "$(KUBECONTEXT)" apply -f deploy/postgres/postgres.yaml
	@kubectl --context "$(KUBECONTEXT)" -n "$(DATA_NS)" rollout status deploy/postgres --timeout=120s
	@echo "✓ postgres ready → localhost:5432 (db/user/pass: brain)"

db-init: ## Apply brain/schema.sql into the running postgres (idempotent)
	@echo "→ applying schema"
	@kubectl --context "$(KUBECONTEXT)" -n "$(DATA_NS)" exec -i deploy/postgres -- \
		env PGPASSWORD=brain psql -U brain -d brain -v ON_ERROR_STOP=1 -f - < "$(SCHEMA_SQL)"
	@echo "✓ schema applied (events, dossiers)"
```

- [ ] **Step 4: Verify the Makefile parses**

Run:
```bash
make help
make -n db-up
make -n db-init
```
Expected: `make help` lists `db-up` and `db-init` with descriptions; both `make -n` print their commands with no `missing separator` or other error.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "feat: add make db-up/db-init targets for the brain store"
```

---

### Task 3: Live integration verification

Runs against the kind cluster. Assumes `make up` has been run (cluster + `data` namespace exist); if not, run it first.

- [ ] **Step 1: Ensure the cluster is up**

Run: `make up`
Expected: ends with `✓ up` (creating the cluster if needed; idempotent if it already exists).

- [ ] **Step 2: Deploy Postgres**

Run: `make db-up`
Expected: `deployment "postgres" successfully rolled out` and ends with `✓ postgres ready → localhost:5432 (db/user/pass: brain)`.

If the pod stays `Pending`, check node memory headroom (`kubectl --context kind-sre-second-brain -n data describe pod -l app=postgres | sed -n '/Events/,$p'`) — Postgres requests only 256Mi, so this should schedule on the default VM.

- [ ] **Step 3: Apply the schema**

Run: `make db-init`
Expected: ends with `✓ schema applied (events, dossiers)`, no psql errors.

- [ ] **Step 4: Verify tables and indexes exist**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -c '\dt'
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -c '\d events'
```
Expected: `\dt` lists both `events` and `dossiers` (schema `public`). `\d events` shows columns matching the DDL and two indexes: `idx_events_lookup` and `idx_events_kind`.

- [ ] **Step 5: Verify idempotency**

Run: `make db-init`
Expected: succeeds again with `✓ schema applied (events, dossiers)` and no errors (the `IF NOT EXISTS` DDL is safe to re-run).

- [ ] **Step 6: Verify host reachability via NodePort**

Run:
```bash
python3 -c "import socket; socket.create_connection(('localhost',5432),3); print('localhost:5432 reachable')"
```
Expected: `localhost:5432 reachable` (proves NodePort 30432 → host 5432 works, i.e. the locally-run seeder/MCP can connect to the DSN `postgresql://brain:brain@localhost:5432/brain`).

- [ ] **Step 7: No commit**

This task adds no files; it is verification only. Nothing to commit.

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** Postgres deployment (Task 1 manifest), `events`/`dossiers` schema + indexes (Task 1 `schema.sql`), `db-up`/`db-init` (Task 2), connection contract `localhost:5432` (Task 3 step 6). The seed dataset contract is defined in the spec but is a later slice (seeder), correctly excluded here. ✓
- **Placeholders:** none — every file body and command is concrete. Credentials `brain/brain/brain` are intentional demo-only values, stated in the manifest comment. ✓
- **Consistency:** namespace `data`, release/labels `app=postgres`, NodePort `30432`, db/user/pass `brain`, context `kind-sre-second-brain`, and the `brain/schema.sql` path are used identically across the manifest, Makefile targets, and verification. The `kubectl exec` schema-apply matches the `db-init` recipe. ✓
