# ADR 0004: LLM Provider Interface, Data Governance and Security

## Status
Accepted · November 2026

## Context
A production RAG service operating over university rules must safeguard data privacy, support flexible infrastructure constraints (local laptop execution vs cloud GPUs vs in-cluster hosting), and defend against prompt injections embedded within corpus documents or user prompts.

Sending unredacted queries or proprietary documents to external cloud APIs may violate institutional data governance policies. Conversely, running large LLMs directly on resource-constrained Kubernetes nodes can trigger OOM kills.

## Decision
1. **Pluggable LLM Provider Pattern**:
   - The generation layer is placed behind an abstract `LLMProvider` interface selectable via `LLM_PROVIDER` in the ConfigMap (`stub`, `ollama-host`, `ollama-incluster`, or `hosted`).
   - Default mode is `ollama-host`, connecting from the cluster to host laptop Ollama via `host.minikube.internal:11434`.
   - In-cluster Ollama is supported via a dedicated bonus Deployment with model persistence.
2. **Untrusted Data Boundary & Schema Enforcement**:
   - Retrieved text is wrapped in strict delimiters and treated purely as inert data.
   - Output generation is strictly constrained using JSON schema enforcement (Ollama format / JSON mode) validated against the Pydantic `Answer` schema.
   - Injections such as `"ignore previous instructions"` are neutralized by schema constraints and deterministic prompt framing.
3. **Strict Credential Governance**:
   - API keys are never stored in ConfigMaps, committed manifests, or Dockerfiles.
   - Any external API keys reside solely in Kubernetes Secrets created out-of-band via CLI.

## Options Considered
1. **Hardcoded Hosted Model (e.g. OpenAI / Groq)**:
   - *Rejected*: Violates air-gapped / local execution goals and leaks student queries to third parties.
2. **Direct Unstructured Free-Text Generation**:
   - *Rejected*: Incurred frequent hallucinations, unparseable citations, and high vulnerability to indirect prompt injections.
3. **Pluggable Interface with JSON Schema Constraints (Chosen)**:
   - *Accepted*: Fully portable, zero data leakage in local mode, guaranteed citation schema conformance, and single-file provider swapping.

## Consequences
- **Positive**: Strict data privacy, zero vendor lock-in, automated schema parsing, and robust prompt injection resistance.
- **Negative**: Requires maintaining adapter code for different provider serialization formats.
