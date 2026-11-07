#!/usr/bin/env bash
# One-command bootstrap and startup for RAHBAR on Kubernetes
set -eo pipefail

NS="rahbar"
BOLD="\033[1m"
GREEN="\033[32m"
BLUE="\033[34m"
OFF="\033[0m"

echo -e "\n${BOLD}${BLUE}=== Starting RAHBAR Kubernetes Cluster Deployment ===${OFF}\n"

# 1. Check or start cluster
if ! kubectl cluster-info &>/dev/null; then
    echo -e "${BOLD}No active Kubernetes cluster found.${OFF}"
    if command -v kind &>/dev/null; then
        echo -e "Starting a local ${GREEN}Kind${OFF} cluster..."
        kind create cluster --name rahbar
    elif command -v minikube &>/dev/null; then
        echo -e "Starting a local ${GREEN}Minikube${OFF} cluster with Calico..."
        minikube start --driver=docker --cni=calico --memory=6144 --cpus=4
        minikube addons enable metrics-server
        minikube addons enable ingress
    else
        echo "Error: Neither Kind nor Minikube found. Please start a cluster first."
        exit 1
    fi
else
    echo -e "${GREEN}✓ Connected to existing Kubernetes cluster:${OFF}"
    kubectl cluster-info | head -n 1
fi

# 2. Build Docker images
echo -e "\n${BOLD}Building Docker images...${OFF}"
docker build -t rahbar-api:latest api
docker build -t rahbar-indexer:latest -f indexer/Dockerfile .
docker build -t rahbar-ui:latest ui

# 3. Load images into cluster if using Kind or Minikube
if kubectl config current-context | grep -q "kind-"; then
    CLUSTER_NAME=$(kubectl config current-context | sed 's/kind-//')
    echo -e "\n${BOLD}Loading images into Kind cluster '${CLUSTER_NAME}'...${OFF}"
    kind load docker-image rahbar-api:latest --name "$CLUSTER_NAME"
    kind load docker-image rahbar-indexer:latest --name "$CLUSTER_NAME"
    kind load docker-image rahbar-ui:latest --name "$CLUSTER_NAME"
elif command -v minikube &>/dev/null && minikube status &>/dev/null; then
    echo -e "\n${BOLD}Loading images into Minikube...${OFF}"
    minikube image load rahbar-api:latest
    minikube image load rahbar-indexer:latest
    minikube image load rahbar-ui:latest
fi

# 4. Create namespace & secret
echo -e "\n${BOLD}Setting up namespace and secrets...${OFF}"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic rahbar-secret -n "$NS" \
  --from-literal=LLM_API_KEY="${LLM_API_KEY:-dev-token}" \
  --dry-run=client -o yaml | kubectl apply -f -

# 5. Apply Kubernetes manifests
echo -e "\n${BOLD}Applying Kubernetes manifests...${OFF}"
kubectl apply -k k8s/overlays/dev

# 6. Wait for data tier rollout
echo -e "\n${BOLD}Waiting for Chroma and Redis...${OFF}"
kubectl rollout status statefulset/chroma -n "$NS" --timeout=120s
kubectl rollout status deployment/redis -n "$NS" --timeout=120s

# 7. Run the Indexer Job (The single-writer design)
echo -e "\n${BOLD}Running Indexer Job (populating Chroma vector store)...${OFF}"
JOB_NAME="indexer-bootstrap-$(date +%s)"
if curl -s http://localhost:11434/api/version &>/dev/null || curl -s http://127.0.0.1:11434/api/version &>/dev/null; then
    echo -e "${GREEN}✓ Host Ollama detected. Running full indexing Job...${OFF}"
    kubectl create job --from=cronjob/reindex "$JOB_NAME" -n "$NS"
    kubectl wait --for=condition=complete "job/$JOB_NAME" -n "$NS" --timeout=300s || true
else
    echo -e "Host Ollama not detected at localhost:11434."
    echo -e "Populating Chroma vector store with bootstrap index (dev mode)..."
    kubectl run "$JOB_NAME" --image=rahbar-indexer:latest --image-pull-policy=IfNotPresent \
      --labels="app.kubernetes.io/name=indexer" --restart=Never -n "$NS" \
      --env="CHROMA_HOST=chroma" --env="CHROMA_PORT=8000" --env="REDIS_HOST=redis" --env="REDIS_PORT=6379" \
      -- python -m pipeline.run --dummy
    for i in $(seq 1 30); do
        PHASE=$(kubectl get pod "$JOB_NAME" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [ "$PHASE" = "Succeeded" ] || [ "$PHASE" = "Failed" ]; then
            break
        fi
        sleep 1
    done
fi

# 8. Verify API and UI rollouts
echo -e "\n${BOLD}Waiting for API and UI replicas to report READY...${OFF}"
kubectl rollout status deployment/api -n "$NS" --timeout=180s
kubectl rollout status deployment/ui -n "$NS" --timeout=60s

echo -e "\n${BOLD}${GREEN}======================================================${OFF}"
echo -e "${BOLD}${GREEN}  RAHBAR Grounded RAG is UP and running!             ${OFF}"
echo -e "${BOLD}${GREEN}======================================================${OFF}\n"

echo -e "Cluster status:"
kubectl get pods -n "$NS"
echo ""
kubectl get endpoints -n "$NS"

echo -e "\n${BOLD}To access the Chat UI in your browser, run:${OFF}"
echo -e "  ${BLUE}kubectl port-forward svc/ui 8080:8080 -n rahbar${OFF}"
echo -e "Then open: ${GREEN}http://localhost:8080${OFF}\n"
