# SRE Second Brain — local kind cluster bring-up.
# Foundation slice: cluster lifecycle + namespaces. Postgres, Backstage,
# seeder and the MCP server land in later slices.

CLUSTER_NAME ?= sre-second-brain
KIND_CONFIG  ?= kind/cluster.yaml
KUBECONTEXT  := kind-$(CLUSTER_NAME)

BACKSTAGE_DIR ?= backstage

.DEFAULT_GOAL := help
.PHONY: help preflight up down nuke status

host-build:
	@pushd $(BACKSTAGE_DIR) && \
	yarn install --immutable && \
	yarn tsc && \
	yarn build:backend && \
	popd

backstage-build: host-build
	@pushd $(BACKSTAGE_DIR) && \
	docker image build . -f packages/backend/Dockerfile --tag backstage && \
	popd

help: ## List available targets
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

preflight: ## Verify kind, kubectl, docker are installed and the daemon is up
	@command -v kind    >/dev/null || { echo "✗ kind not found on PATH";    exit 1; }
	@command -v kubectl >/dev/null || { echo "✗ kubectl not found on PATH"; exit 1; }
	@command -v docker  >/dev/null || { echo "✗ docker not found on PATH";  exit 1; }
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
