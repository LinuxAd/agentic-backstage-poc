# SRE Second Brain — local kind cluster bring-up.
# Foundation slice: cluster lifecycle + namespaces. Postgres, Backstage,
# seeder and the MCP server land in later slices.

CLUSTER_NAME ?= sre-second-brain
KIND_CONFIG  ?= kind/cluster.yaml
KUBECONTEXT  := kind-$(CLUSTER_NAME)

BACKSTAGE_DIR ?= backstage

SONARQUBE_NS         ?= apps
SONARQUBE_VALUES     ?= deploy/sonarqube-values.yaml
SONARQUBE_CHART_REPO ?= https://SonarSource.github.io/helm-chart-sonarqube
SONARQUBE_CHART_VER  ?= 2026.4.0

INGRESS_NGINX_URL ?= https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml

.DEFAULT_GOAL := help
.PHONY: help preflight up down nuke status host-build backstage-build backstage-load ingress-install backstage-secret backstage-deploy backstage-up sonarqube-up sonarqube-down

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
		--for=condition=Ready pod \
		--selector=app.kubernetes.io/component=controller --timeout=180s
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
