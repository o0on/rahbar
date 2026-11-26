# RAHBAR Kubernetes Manifests (Assignment 03 Release & Evaluation)

## Release & Gate Objects (Part F2, F3, F5)

- `k8s/base/gate-configmap.yaml` (`rahbar-gate-config`): Stores `RECALL5_MAX_DROP`, `FAITHFULNESS_MIN`, `P95_MS_MAX`, `ABSTENTION_MIN_BASELINE`, and `COVERAGE_MIN`. Editing this ConfigMap flips the Evaluation Job outcome without rebuilding container images.
- `k8s/base/eval-job.yaml` (`job/rahbar-eval`): One-shot evaluation `Job` (`requests: 100m/128Mi`, `limits: 500m/512Mi`) querying the target API service and exiting non-zero when any gate constraint is violated.
- `k8s/base/candidate-deployment.yaml` & `k8s/base/candidate-service.yaml` (`deploy/api-candidate`, `svc/api-candidate`): Isolated canary release serving collection `rahbar-v2-tuned` (`EMBED_VERSION=v2-tuned`, `TOP_K=6`) with `maxSurge: 1, maxUnavailable: 0`.

## Running the Release Gate & Canary Rollout

```bash
# 1. Apply updated configmaps & canary manifests
kubectl apply -k k8s/overlays/dev

# 2. Run the in-cluster evaluation Job
kubectl -n rahbar delete job rahbar-eval --ignore-not-found
kubectl apply -f k8s/base/eval-job.yaml
kubectl -n rahbar wait --for=condition=complete job/rahbar-eval --timeout=180s
kubectl -n rahbar logs job/rahbar-eval

# 3. Demonstrate ConfigMap-only gate flip (no image rebuild)
kubectl -n rahbar patch configmap rahbar-gate-config --type merge -p '{"data":{"P95_MS_MAX":"100"}}'
kubectl -n rahbar delete job rahbar-eval --ignore-not-found
kubectl apply -f k8s/base/eval-job.yaml
kubectl -n rahbar logs -f job/rahbar-eval   # Expected: GATE FAIL (exit 1)

# Restore threshold
kubectl apply -f k8s/base/gate-configmap.yaml

# 4. Canary rollout & rollback verification
kubectl -n rahbar set env deploy/api-candidate EMBED_VERSION=v2-bad CHROMA_COLLECTION=rahbar-v2-bad
kubectl -n rahbar rollout status deploy/api-candidate --timeout=120s
kubectl -n rahbar rollout undo deploy/api-candidate
kubectl -n rahbar rollout status deploy/api-candidate --timeout=120s
```
