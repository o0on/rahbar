# Evidence 6: GitHub Actions CI Pipeline Verification

Repository: o0on/rahbar
Workflow: CI Pipeline (`.github/workflows/ci.yml`)
Run ID: 42158825402
Branch: dev

## Job Breakdown and Execution Timing

| Job | Status | Duration | Description |
| :--- | :--- | :--- | :--- |
| **lint** | SUCCESS | 14s | `ruff check .` and `mypy` strict typing checks |
| **test** | SUCCESS | 30s | `pytest` executing 94 unit tests across API and indexer ($\ge 60\%$ coverage) |
| **manifests** | SUCCESS | 7s | `kustomize build k8s/overlays/prod \| kubeconform -strict` |
| **build** | SUCCESS | 1m 12s | Builds Docker images for `api`, `indexer`, and `ui` |
| **e2e-eval** | SUCCESS | 5m 59s | Ephemeral Kind cluster: deploys Chroma, Redis, in-cluster Ollama, runs Indexer Job on fixture corpus, and verifies evaluation gate |
| **scan** | SUCCESS | 1m 7s | `trivy` container image vulnerability scan |

## Artifacts Produced
- `fixture-eval-results`: Evaluation metrics JSON output generated during the ephemeral cluster evaluation run.
