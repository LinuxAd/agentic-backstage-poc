# SonarQube (Community Build) Bring-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy SonarQube Community Build into the existing kind cluster and serve it through ingress-nginx at `http://sonarqube.localhost:3000`, alongside Backstage.

**Architecture:** Use the upstream SonarSource Helm chart (`sonarqube/sonarqube`, pinned to chart `2026.4.0`) driven by a repo-local values override (`deploy/sonarqube-values.yaml`) and new idempotent `make sonarqube-*` targets — mirroring how `make backstage-*` orchestrates Helm. SonarQube runs in the existing `apps` namespace with the embedded **H2** database and **ephemeral** storage (no Postgres dependency; data is lost on pod restart — acceptable for the POC). Ingress is **host-based**: a rule scoped to `sonarqube.localhost` wins over Backstage's host-less catch-all, so `localhost:3000` stays Backstage and `sonarqube.localhost:3000` reaches SonarQube. `*.localhost` resolves to loopback on macOS/modern browsers, so no `/etc/hosts` edit is needed.

**Tech Stack:** kind, Helm 3, ingress-nginx, SonarSource SonarQube Helm chart, SonarQube Community Build (Elasticsearch + web, Java).

---

## Decisions locked in (from brainstorming)

| Decision | Choice | Consequence |
|---|---|---|
| Routing | Host-based subdomain `sonarqube.localhost` | No SonarQube context-path config; specific-host ingress rule beats Backstage catch-all |
| Database | Embedded H2, `persistence.enabled: false` | No Postgres to deploy; **data is ephemeral** (lost on pod restart) |
| Packaging | Upstream `sonarqube/sonarqube` chart + values override | Less hand-written YAML; pin the chart version for reproducibility |
| Namespace | `apps` (already created by `make up`) | No new namespace |
| Elasticsearch under kind | `initSysctl.enabled: false` + `node.store.allow_mmap=false` | Avoids privileged init container / host `vm.max_map_count` tuning — most reliable on kind/Colima/Docker Desktop |

## Prerequisites / environment note

SonarQube runs Elasticsearch + a web server in one pod; it needs roughly **3–4 GiB** of memory available to the cluster. The Docker/Colima VM backing kind must have at least ~6 GiB allocated, or the pod will be OOMKilled / stuck `Pending`. Call this out in the verification step if the pod does not become Ready.

## File structure

- **Create** `deploy/sonarqube-values.yaml` — the Helm values override (community build, H2, ephemeral, ingress host, mmap workaround, modest resources). Lives in `deploy/` next to `namespaces.yaml`.
- **Create** `docs/superpowers/specs/2026-06-15-sonarqube-bringup-design.md` — the slice design doc (repo convention: design doc before code).
- **Modify** `Makefile` — add `sonarqube-up` / `sonarqube-down` targets, extend `.PHONY` and the help list.
- **Modify** `CLAUDE.md` — update the "Build state vs. spec" and "Common commands" sections so the docs match reality.

There is intentionally **no** `charts/sonarqube/` directory — we consume the upstream chart, unlike the hand-written `charts/backstage/`.

---

### Task 1: Write the slice design doc

The repo convention (CLAUDE.md → Conventions) is a dated design doc in `docs/superpowers/specs/` before implementation. This task records the design and its verification contract.

**Files:**
- Create: `docs/superpowers/specs/2026-06-15-sonarqube-bringup-design.md`

- [ ] **Step 1: Create the design doc**

Create `docs/superpowers/specs/2026-06-15-sonarqube-bringup-design.md` with exactly this content:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-15-sonarqube-bringup-design.md
git commit -m "docs: design for SonarQube community build bring-up"
```

---

### Task 2: Add the SonarQube Helm values override

**Files:**
- Create: `deploy/sonarqube-values.yaml`

- [ ] **Step 1: Create the values file**

Create `deploy/sonarqube-values.yaml` with exactly this content:

```yaml
# Helm values override for the upstream sonarqube/sonarqube chart (pinned to
# chart 2026.4.0). Applied by `make sonarqube-up`. Intentionally minimal — all
# local intent lives here so the upstream chart stays untouched.

# Use the Community Build image (free edition). The chart derives the
# `<buildNumber>-community` image tag from this and ignores commercial editions.
community:
  enabled: true

# Embedded H2 database (no jdbcOverwrite => H2). For evaluation only: it does not
# survive a pod restart and cannot be upgraded. The cluster's Postgres lands in a
# later slice.
jdbcOverwrite:
  enabled: false

# Ephemeral storage — emptyDir, no PVC. Pairs with the H2 decision above.
persistence:
  enabled: false

# Required by the chart; arbitrary local value (only used by the /api/monitoring
# Prometheus endpoint, which we do not expose).
monitoringPasscode: "local-poc-passcode"

# Elasticsearch on kind: avoid the privileged sysctl init container and host
# vm.max_map_count tuning by telling ES not to use mmap directories.
initSysctl:
  enabled: false
sonarProperties:
  sonar.search.javaAdditionalOpts: "-Dnode.store.allow_mmap=false"

# Host-based routing. A rule scoped to sonarqube.localhost is more specific than
# Backstage's host-less catch-all, so it wins for this host only.
ingress:
  enabled: true
  ingressClassName: nginx
  hosts:
    - name: sonarqube.localhost
      path: /
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    # SonarQube report uploads can be large; lift nginx's default body cap.
    nginx.ingress.kubernetes.io/proxy-body-size: "64m"

# Trim the chart's heavy defaults (limit memory 6Gi, ephemeral-storage 512000M)
# to something a local kind/Colima VM can schedule.
resources:
  requests:
    cpu: 400m
    memory: 2Gi
  limits:
    cpu: "1"
    memory: 4Gi
```

- [ ] **Step 2: Validate it is well-formed YAML**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('deploy/sonarqube-values.yaml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add deploy/sonarqube-values.yaml
git commit -m "feat: add SonarQube community build helm values override"
```

---

### Task 3: Add Makefile lifecycle targets

Mirror the style of the existing `backstage-*` targets: idempotent, with `→`/`✓` progress echoes, using `--kube-context`/`--context "$(KUBECONTEXT)"`.

**Files:**
- Modify: `Makefile` (the `.PHONY` line at `Makefile:14`; add targets near the backstage targets; help is generated from `## ` comments so new targets appear automatically)

- [ ] **Step 1: Add chart coordinate variables**

In `Makefile`, after the `BACKSTAGE_DIR ?= backstage` line (currently `Makefile:9`), add:

```makefile

SONARQUBE_NS         ?= apps
SONARQUBE_VALUES     ?= deploy/sonarqube-values.yaml
SONARQUBE_CHART_REPO ?= https://SonarSource.github.io/helm-chart-sonarqube
SONARQUBE_CHART_VER  ?= 2026.4.0
```

- [ ] **Step 2: Extend the `.PHONY` list**

Replace the `.PHONY` line (currently `Makefile:14`):

```makefile
.PHONY: help preflight up down nuke status host-build backstage-build backstage-load ingress-install backstage-secret backstage-deploy backstage-up
```

with:

```makefile
.PHONY: help preflight up down nuke status host-build backstage-build backstage-load ingress-install backstage-secret backstage-deploy backstage-up sonarqube-up sonarqube-down
```

- [ ] **Step 3: Add the targets**

In `Makefile`, immediately after the `backstage-up:` target (currently ends at `Makefile:58`) and before the `help:` target, insert:

```makefile
sonarqube-up: ## Deploy SonarQube Community Build via Helm and wait for it to be Ready
	@echo "→ adding/updating SonarSource helm repo"
	@helm repo add sonarqube "$(SONARQUBE_CHART_REPO)" >/dev/null 2>&1 || true
	@helm repo update sonarqube >/dev/null
	@echo "→ deploying SonarQube (chart $(SONARQUBE_CHART_VER)) into '$(SONARQUBE_NS)'"
	@helm --kube-context "$(KUBECONTEXT)" upgrade --install sonarqube sonarqube/sonarqube \
		--version "$(SONARQUBE_CHART_VER)" \
		--namespace "$(SONARQUBE_NS)" --create-namespace \
		-f "$(SONARQUBE_VALUES)"
	@echo "→ waiting for SonarQube to start (Elasticsearch + web; up to 10m)"
	@kubectl --context "$(KUBECONTEXT)" -n "$(SONARQUBE_NS)" wait \
		--for=condition=Ready pod \
		--selector=app=sonarqube --timeout=600s
	@echo "✓ sonarqube deployed → http://sonarqube.localhost:3000 (login admin/admin)"

sonarqube-down: ## Uninstall the SonarQube Helm release
	@helm --kube-context "$(KUBECONTEXT)" uninstall sonarqube --namespace "$(SONARQUBE_NS)" || true
	@echo "✓ sonarqube removed"
```

- [ ] **Step 4: Verify the Makefile parses and help lists the new targets**

Run: `make help`
Expected: output includes lines for `sonarqube-up` and `sonarqube-down` with their descriptions, and no `make: *** ... Error`.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "feat: add make sonarqube-up/down targets"
```

---

### Task 4: Update repo docs to match reality

**Files:**
- Modify: `CLAUDE.md` (the "Build state vs. spec" paragraph and the "Common commands" block)

- [ ] **Step 1: Update the build-state paragraph**

In `CLAUDE.md`, find the sentence in "Build state vs. spec" that begins **"Currently implemented:"** and append SonarQube to the implemented list. Replace:

```
**Currently implemented:** the kind cluster foundation (`kind/`, `deploy/namespaces.yaml`), the Backstage source app (`backstage/`), and a Helm chart deploying it via ingress (`charts/backstage/`, Makefile `backstage-*` targets).
```

with:

```
**Currently implemented:** the kind cluster foundation (`kind/`, `deploy/namespaces.yaml`), the Backstage source app (`backstage/`), a Helm chart deploying it via ingress (`charts/backstage/`, Makefile `backstage-*` targets), and SonarQube Community Build deployed from the upstream chart via `deploy/sonarqube-values.yaml` (Makefile `sonarqube-*` targets), served at `http://sonarqube.localhost:3000`.
```

- [ ] **Step 2: Add the commands to the Common commands block**

In `CLAUDE.md`, in the fenced command list under "Common commands", after the `make down` line add:

```
make sonarqube-up    # deploy SonarQube Community Build (upstream chart) into the apps ns
make sonarqube-down  # uninstall SonarQube
```

- [ ] **Step 3: Note the routing in the networking section**

In `CLAUDE.md`, at the end of the "Networking:" bullet under "How the pieces fit", append:

```
 SonarQube is reached at `http://sonarqube.localhost:3000`: its ingress rule is scoped to the `sonarqube.localhost` host, which is more specific than Backstage's host-less catch-all and so wins for that host only. `*.localhost` resolves to loopback, so no `/etc/hosts` edit is required.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document SonarQube bring-up in CLAUDE.md"
```

---

### Task 5: End-to-end verification on a live cluster

This task runs the real thing. It assumes the cluster + ingress are already up (`make up && make ingress-install`); if not, run those first.

- [ ] **Step 1: Ensure cluster and ingress are up**

Run: `make up && make ingress-install`
Expected: ends with `✓ up` and `✓ ingress-nginx ready`.

- [ ] **Step 2: Deploy SonarQube**

Run: `make sonarqube-up`
Expected: ends with `✓ sonarqube deployed → http://sonarqube.localhost:3000 (login admin/admin)`.

If the `kubectl wait` times out, diagnose before retrying:
- `kubectl --context kind-sre-second-brain -n apps get pods` — is the pod `Pending` (insufficient memory → raise Docker/Colima RAM to ~6 GiB) or `CrashLoopBackOff`?
- `kubectl --context kind-sre-second-brain -n apps logs -l app=sonarqube --tail=50` — look for Elasticsearch `max virtual memory areas vm.max_map_count` (the mmap workaround in values should prevent this) or OOM.

- [ ] **Step 3: Confirm SonarQube answers through the ingress**

Run: `curl -sS -H 'Host: sonarqube.localhost' http://localhost:3000/api/system/status`
Expected: JSON containing `"status":"UP"` (it may briefly report `"STARTING"` right after rollout — re-run until `UP`).

- [ ] **Step 4: Confirm host-based routing works directly**

Run: `curl -sS http://sonarqube.localhost:3000/api/system/status`
Expected: same `"status":"UP"` JSON (verifies `*.localhost` loopback resolution + the host-scoped ingress rule).

- [ ] **Step 5: Confirm Backstage is undisturbed**

Run: `curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:3000`
Expected: `200` (Backstage's catch-all still serves `localhost`).

- [ ] **Step 6: (Optional) Confirm teardown is clean**

Run: `make sonarqube-down && kubectl --context kind-sre-second-brain -n apps get all`
Expected: `✓ sonarqube removed` and no `sonarqube-*` resources remain. (Skip if you want to leave SonarQube running.)

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** "Add SonarQube" → Tasks 2–3 + 5; "community build" → `community.enabled: true` (Task 2); "wire to kind ingress" → host-based ingress in values (Task 2) verified in Task 5 steps 3–4. ✓
- **Placeholders:** none — every value, command, and expected output is concrete. The only deliberately arbitrary string is `monitoringPasscode`, explained inline. ✓
- **Consistency:** namespace `apps`, release name `sonarqube`, chart `sonarqube/sonarqube@2026.4.0`, host `sonarqube.localhost`, pod selector `app=sonarqube`, and context `kind-sre-second-brain` are used identically across the values file, Makefile targets, and verification. ✓
```