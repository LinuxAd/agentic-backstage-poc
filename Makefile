# SRE Second Brain — local kind cluster bring-up + demo orchestration.
# `make demo` cold-starts the whole stack: kind cluster, ingress, Backstage,
# the Postgres brain store + schema, the synthetic seeder, and registers the
# MCP server with Claude Code.

CLUSTER_NAME ?= sre-second-brain
KIND_CONFIG  ?= kind/cluster.yaml
KUBECONTEXT  := kind-$(CLUSTER_NAME)

BACKSTAGE_DIR ?= backstage

DATA_NS    ?= data
SCHEMA_SQL ?= brain/schema.sql
SEEDER_DIR ?= brain/seeder

# Brain-store connection (demo-only creds; matches deploy/postgres/postgres.yaml).
# From a container, the kind-mapped host port 5432 is reached via host.docker.internal.
PG_IMAGE ?= postgres:16
PG_URL   ?= postgresql://brain:brain@host.docker.internal:5432/brain

INGRESS_NGINX_URL ?= https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml

.DEFAULT_GOAL := help
.PHONY: help preflight up down nuke status host-build backstage-build backstage-load ingress-install backstage-secret backstage-deploy backstage-up db-up db-init seed psql mcp-register demo

host-build: ## Build the Backstage backend bundle on the host (yarn install/tsc/build)
	@pushd $(BACKSTAGE_DIR) && \
	yarn install --immutable && \
	yarn tsc && \
	yarn build:backend && \
	popd

backstage-build: host-build ## Build the Backstage Docker image (backstage:latest)
	@pushd $(BACKSTAGE_DIR) && \
	docker image build . -f packages/backend/Dockerfile --tag backstage && \
	popd

backstage-load: ## Load backstage:latest into the kind cluster
	@kind load docker-image backstage:latest --name "$(CLUSTER_NAME)"
	@echo "✓ image loaded into '$(CLUSTER_NAME)'"

ingress-install: ## Install ingress-nginx (kind provider) and wait for it to be ready
	@echo "→ installing ingress-nginx"
	@kubectl --context "$(KUBECONTEXT)" apply -f "$(INGRESS_NGINX_URL)"
	@echo "→ waiting for ingress-nginx controller"
	@kubectl --context "$(KUBECONTEXT)" wait --namespace ingress-nginx \
		--for=condition=Available deployment/ingress-nginx-controller --timeout=180s
	@echo "✓ ingress-nginx ready"

backstage-secret: ## Create/update the GitHub auth Secret from backstage/.env
	@test -f "$(BACKSTAGE_DIR)/.env" || { echo "✗ $(BACKSTAGE_DIR)/.env not found"; exit 1; }
	@kubectl --context "$(KUBECONTEXT)" create namespace backstage --dry-run=client -o yaml \
		| kubectl --context "$(KUBECONTEXT)" apply -f - >/dev/null
	@set -a; . "./$(BACKSTAGE_DIR)/.env"; set +a; \
	kubectl --context "$(KUBECONTEXT)" -n backstage create secret generic backstage-github-auth \
		--from-literal=AUTH_GITHUB_CLIENT_ID="$$AUTH_GITHUB_CLIENT_ID" \
		--from-literal=AUTH_GITHUB_CLIENT_SECRET="$$AUTH_GITHUB_CLIENT_SECRET" \
		--dry-run=client -o yaml | kubectl --context "$(KUBECONTEXT)" apply -f -
	@echo "✓ secret 'backstage-github-auth' applied"

backstage-deploy: backstage-secret ## Deploy Backstage via Helm and wait for rollout
	@helm --kube-context "$(KUBECONTEXT)" upgrade --install backstage charts/backstage \
		--namespace backstage --create-namespace
	@kubectl --context "$(KUBECONTEXT)" -n backstage rollout status deploy/backstage --timeout=180s
	@echo "✓ backstage deployed → http://localhost:3000"

backstage-up: backstage-build backstage-load backstage-deploy ## Build, load and deploy Backstage end-to-end

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

seed: ## Generate synthetic events into the brain store (deterministic, idempotent)
	@echo "→ seeding brain store"
	@uv run "$(SEEDER_DIR)/seed.py"

psql: ## Open an interactive psql client (Docker) against the brain store
	@echo "→ connecting to $(PG_URL)"
	@docker run --rm -it \
		--add-host=host.docker.internal:host-gateway \
		$(PG_IMAGE) psql "$(PG_URL)"

mcp-register: ## Register (idempotently) the second-brain MCP server with Claude Code
	@claude mcp remove second-brain >/dev/null 2>&1 || true
	@claude mcp add second-brain -- uv run --quiet "$(CURDIR)/brain/mcp_server/server.py"
	@echo "✓ registered 'second-brain' — restart the Claude Code session to load its tools"

demo: up ingress-install db-up db-init seed backstage-up mcp-register ## Cold-start the entire stack and print the demo script
	@echo ""
	@echo "════════════════════════════════════════════════════════════════"
	@echo "  SRE Second Brain — demo ready"
	@echo "════════════════════════════════════════════════════════════════"
	@echo "  Backstage catalog : http://localhost:3000  (guest sign-in)"
	@echo "  Brain store       : postgresql://brain:brain@localhost:5432/brain"
	@echo "  MCP server        : 'second-brain' (registered with Claude Code)"
	@echo ""
	@echo "  Start a NEW Claude Code session in this repo, then ask:"
	@echo "    1. What services exist on this platform and who owns them?"
	@echo "    2. What's going on with payment-gateway right now?"
	@echo "    3. Could the open incident be related to a recent change?"
	@echo "    4. Which service's code quality is trending the wrong way?"
	@echo ""
	@echo "  Closer: open Backstage and show payment-gateway in the catalog —"
	@echo "  the same entity ref the agent keyed every signal to."
	@echo "════════════════════════════════════════════════════════════════"

help: ## List available targets
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

preflight: ## Verify kind, kubectl, docker are installed and the daemon is up
	@command -v kind    >/dev/null || { echo "✗ kind not found on PATH";    exit 1; }
	@command -v kubectl >/dev/null || { echo "✗ kubectl not found on PATH"; exit 1; }
	@command -v docker  >/dev/null || { echo "✗ docker not found on PATH";  exit 1; }
	@command -v helm    >/dev/null || { echo "✗ helm not found on PATH";    exit 1; }
	@command -v uv      >/dev/null || { echo "✗ uv not found on PATH (needed by seed/mcp; brew install uv)"; exit 1; }
	@docker info >/dev/null 2>&1   || { echo "✗ Docker daemon not reachable — start Docker/Colima"; exit 1; }
	@echo "✓ preflight ok"

up: preflight ## Create the kind cluster (idempotent) and apply namespaces
	@if kind get clusters 2>/dev/null | grep -qx "$(CLUSTER_NAME)"; then \
		echo "✓ cluster '$(CLUSTER_NAME)' already exists — skipping create"; \
	else \
		echo "→ creating cluster '$(CLUSTER_NAME)'"; \
		kind create cluster --name "$(CLUSTER_NAME)" --config "$(KIND_CONFIG)"; \
	fi
	@echo "→ applying namespaces"
	@kubectl --context "$(KUBECONTEXT)" apply -f deploy/namespaces.yaml
	@echo "✓ up"

down: ## Delete the kind cluster
	@kind delete cluster --name "$(CLUSTER_NAME)"
	@echo "✓ down"

nuke: down ## Delete the cluster and prune dangling images for a cold rebuild
	@echo "→ pruning dangling images"
	@docker image prune -f >/dev/null 2>&1 || true
	@echo "✓ nuke"

status: ## Show cluster, nodes and namespaces
	@if kind get clusters 2>/dev/null | grep -qx "$(CLUSTER_NAME)"; then \
		echo "cluster: $(CLUSTER_NAME) (present)"; \
		kubectl --context "$(KUBECONTEXT)" get nodes 2>/dev/null || true; \
		kubectl --context "$(KUBECONTEXT)" get ns 2>/dev/null || true; \
	else \
		echo "cluster: $(CLUSTER_NAME) (absent)"; \
	fi
