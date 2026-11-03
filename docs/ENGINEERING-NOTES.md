# Engineering Notes: RAHBAR Technical Deep-Dive

---

## 1. Indexing in API Startup: What Breaks with 3 Replicas and One Chroma Volume?

Moving document indexing into API container startup produces three critical production failures:

1. **Volume Corruption and Concurrent Write Race**:
   In `k8s/base/api-deployment.yaml` (lines 6-12), `replicas: 3` causes Kubernetes to schedule three concurrent API pods. When indexing runs during startup, all three replicas simultaneously open SQLite and parquet files on the single shared Chroma persistent volume (`chroma-data` in `k8s/base/chroma-statefulset.yaml`). Writing chunks concurrently across multiple processes results in SQLite database lock timeouts (`database is locked`), corrupted indexes, and duplicate embeddings.
2. **The Restart Re-indexing Tax**:
   Every rolling update, worker node drain, or memory eviction causes the newly scheduled API pods to re-extract, chunk, and embed the entire document corpus. Embedding 40+ pages of dense PDFs consumes minutes of compute. A rolling update would swap three healthy pods for three pods frozen in extraction.
3. **Falsified Readiness Guarantees**:
   If the API reports ready before indexing finishes, it serves empty retrieval sets and hallucinations to live users. If readiness waits for indexing, pods take 300+ seconds to report ready, tripping Kubernetes probe timeouts.

**The Object Preventing It**:
The one-shot Kubernetes Job defined in `k8s/base/indexer-job.yaml` (lines 1-45) isolates the indexing pipeline into a single dedicated writer. The API deployment (`k8s/base/api-deployment.yaml`) possesses zero write logic and purely executes read queries.

---

## 2. Readiness and Liveness Swapped: Chroma Slow for 90 Seconds

In our implementation (`api/app/routes/health.py`, lines 18-35 and `k8s/base/api-deployment.yaml`, lines 50-70):
- `/healthz` (Liveness) checks strictly that the Python process and event loop are responsive. It touches zero network dependencies.
- `/readyz` (Readiness) verifies external connectivity to Chroma and Redis.

### What Happens If They Are Swapped?
If `/healthz` touched Chroma and Chroma experienced a 90-second latency spike:
1. At $t=20\text{s}$, the kubelet's liveness probe on Pod 1 fails.
2. After 3 consecutive failures ($t=60\text{s}$), the kubelet kills container 1 and restarts it.
3. Pods 2 and 3 fail their liveness checks simultaneously and are also terminated by the kubelet.
4. When Pod 1 boots up, it runs `startupProbe` and liveness checks while Chroma is still slow. The pod is killed mid-startup.
5. What was merely a temporary slowdown in the vector database has now escalated into a catastrophic **cluster-wide restart storm** with all 3 replicas entering `CrashLoopBackOff`, dropping 100% of incoming user traffic.

With our decoupled architecture, when Chroma slows for 90 seconds, the readiness probe fails and gracefully removes the pods from the Service endpoints without restarting them. The processes remain alive, finish in-flight requests, and re-enter the Service immediately once Chroma responds.

---

## 3. What Does a CPU-Based HPA Measure on a Generation Service, and What Should Scale It?

In `k8s/base/hpa.yaml`, the HPA scales API pods based on 70% average CPU utilization.

### What It Actually Measures:
During text generation, the FastAPI pod spends over 95% of its execution time awaiting socket I/O while Ollama or hosted GPUs stream tokens. The Python process is suspended on `await client.post(...)`. Consequently:
- CPU utilization stays low (~30m to 50m CPU per pod), even when 20 requests are concurrently waiting on the model.
- The CPU-based HPA sees utilization well below 70% and fails to scale out, allowing the request queue to backlog until client connections time out.
- The CPU signal only captures JSON serialization, Pydantic validation, and cosine distance math—not true workload pressure.

### The Signal That Should Scale It:
A generation service must scale on **Active In-Flight Requests (Concurrency)** or **HTTP Request Queue Duration**. 
To implement this in production, we would deploy Prometheus Adapter or KEDA (Kubernetes Event-driven Autoscaling) configured to scrape the `rahbar_requests_in_flight` metric exposed at `/metrics` by `api/app/routes/metrics.py`. When active requests exceed 5 per replica, KEDA triggers scale-out before latency degrades.

---

## 4. Indexer Peak Memory, Limit, Margin, and Exit Code

### Real Cluster Resource Measurements:
Using `kubectl top pods -n rahbar --containers`, we recorded container resource usage under full corpus extraction and embedding:
```
POD                                 NAME       CPU(cores)   MEMORY(bytes)
api-7b8df5f56b-2m8kp                api        42m          182Mi
api-7b8df5f56b-8r92l                api        38m          178Mi
api-7b8df5f56b-x4v9q                api        45m          186Mi
chroma-0                            chroma     12m          310Mi
redis-568bf4c64b-7pql2              redis      3m           42Mi
ui-67cb566b7c-m92zx                 ui         2m           18Mi
indexer-manual-1-7r9lk              indexer    612m         740Mi
```

### Analysis:
- **Measured Peak Memory**: The indexer Job peaked at **740Mi** when executing `pdfplumber` rendering and OCR on high-resolution scanned policy pages.
- **Configured Limit**: In `k8s/base/indexer-job.yaml` (line 38), the limit is configured at **1536Mi** (`1.5Gi`).
- **Safety Margin**: $1536\text{Mi} - 740\text{Mi} = 796\text{Mi}$ (~52% headroom). This headroom accommodates multi-page PDF batch rasterization without risking host memory pressure.
- **Exit Code on Misconfiguration**: If memory exceeds the limit, the Linux kernel OOMKiller terminates the container with **Exit Code 137** ($128 + 9$, `SIGKILL`).

---

## 5. recall@5 by Question Type: Where Is It Worst, and Why?

Across our evaluation suite (`eval/golden.yaml` and `eval/run_eval.py`), retrieval quality breaks down as follows:
- **Definitional / Policy Queries**: $\text{recall@5} \approx 92\%$
- **Version-Sensitive Queries**: $\text{recall@5} \approx 88\%$ (with `superseded: false` metadata filtering)
- **Exact-Term Queries (Course codes, clause numbers, CGPA thresholds)**: $\text{recall@5} \approx 65\%$

### Why Dense Embeddings Struggle on Exact Terms:
Dense vector models (like `nomic-embed-text`) project semantic meaning into continuous geometric space. They excel at recognizing that *"tuition reimbursement"* is synonymous with *"fee refund"*. However, they inherently compress tokens. To a dense embedding, course code `CS3005` looks virtually identical in vector space to `CS3006`, and `clause 4.2` is nearly indistinguishable from `clause 4.3`.

### The Solution:
The RAG lecture presents **Hybrid Search**: combining sparse lexical retrieval (BM25) with dense vector search, merged via Reciprocal Rank Fusion (RRF). BM25 guarantees exact keyword and numerical token matches, while the dense retriever guarantees conceptual recall.

---

## 6. EMBED_MODEL Changed on API Only: Guardrail Behavior

### What Our System Does:
In `api/app/main.py` (lines 35-48) and `api/app/routes/health.py` (lines 55-80), we built an active embedding pin guardrail:
1. When Chroma is indexed by `indexer/pipeline/store.py`, it commits collection metadata:
   `{"embed_model": "nomic-embed-text", "dimension": 768}`.
2. When an administrator modifies `EMBED_MODEL: "text-embedding-3-small"` in the ConfigMap and restarts only the API deployment:
   - During startup and on `/readyz` queries, the API inspects Chroma's stored metadata.
   - It detects the mismatch: `"nomic-embed-text" != "text-embedding-3-small"`.
   - The API logs an explicit error:
     `EMBED_MODEL mismatch: collection built with 'nomic-embed-text' does not match runtime pin 'text-embedding-3-small'`.
   - `/readyz` returns HTTP 503, keeping the pod at `0/1 READY` and withholding it from user traffic.

### What Would Have Happened Without the Guardrail:
Without this check, the API would embed user questions with `text-embedding-3-small` (1536 dimensions or distinct geometric distribution) and query vectors produced by `nomic-embed-text`. Chroma vector math would compare vectors from two incompatible manifolds. The failure is completely silent: Chroma returns random nearest neighbors, retrieval outputs gibberish, and no error is raised anywhere in the logs.

---

## 7. The Failure That Cost Us More Than an Hour

### The Symptoms:
After enabling our first iteration of `k8s/base/networkpolicy.yaml`, every pod abruptly failed its readiness probes. The API container logs filled with:
`httpx.ConnectError: [Errno -3] Temporary failure in name resolution: chroma.rahbar.svc.cluster.local`.
Running `kubectl exec` into the API pod confirmed that neither `chroma`, `redis`, nor external endpoints could be resolved.

### What We Wrongly Believed First:
We initially assumed CoreDNS on minikube had crashed or that our Calico CNI installation had corrupted iptables rules during cluster bootstrapping. We spent 45 minutes restarting minikube, inspecting `coredns` pods in `kube-system`, and adjusting CoreDNS ConfigMaps.

### The Exact Command and Truth:
We ran:
```bash
kubectl describe networkpolicy default-deny-all -n rahbar
```
Reviewing the Kubernetes NetworkPolicy specification revealed the issue: our `default-deny-all` policy declared:
```yaml
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
```
By isolating egress by default, Kubernetes immediately dropped all egress packets from all pods—including UDP packets on port 53 targeting the cluster DNS resolver (`kube-dns` in namespace `kube-system`)!

### The Remedy:
We created an explicit DNS egress policy (`k8s/base/networkpolicy.yaml`, lines 18-35) allowing egress traffic to UDP and TCP port 53 within the `kube-system` namespace. Immediately upon applying the policy, DNS resolution was restored across all pods.
