# RAHBAR: Submission Guide & Deliverables Checklist

This document details the final steps to complete the Google Classroom submission for Assignment 02: **RAHBAR Grounded RAG on Kubernetes**.

---

## 1. Google Classroom Deliverables

When submitting on Google Classroom, submit the following 4 items:

1. **GitHub Repository URL**: 
   `https://github.com/o0on/rahbar` (Public repository with both partners added).
2. **Link to Successful CD Pipeline Run**:
   Link to the passing `cd.yml` run on `main` (e.g. `https://github.com/o0on/rahbar/actions/workflows/cd.yml`).
3. **Link to Docker Hub Images**:
   Links to published images on Docker Hub tagged by Git commit SHA (`${{ github.sha }}`):
   - `rahbar-api:<commit-sha>`
   - `rahbar-indexer:<commit-sha>`
   - `rahbar-ui:<commit-sha>`
4. **Committed Evaluation Results**:
   `eval/results.json` and `eval/baseline.json` committed in the repository root.

---

## 2. Docker Hub Account & GitHub Secrets Setup

The CD pipeline (`.github/workflows/cd.yml`) pushes images to Docker Hub when changes are merged to `main`. 

If you do not have a Docker Hub account yet:
1. **Create an Account**:
   - Go to [https://hub.docker.com/signup](https://hub.docker.com/signup).
   - Sign up for a free Docker ID using your email (takes ~60 seconds, no credit card required).
2. **Create an Access Token**:
   - Log in to Docker Hub, click your profile icon in the top right $\rightarrow$ **Account Settings** $\rightarrow$ **Personal Access Tokens**.
   - Click **Generate new token**. Description: `rahbar-github-actions`, Access permissions: `Read & Write`.
   - Copy the generated token string.
3. **Add Secrets to GitHub Repository**:
   - Go to your repository on GitHub: `https://github.com/o0on/rahbar/settings/secrets/actions`.
   - Click **New repository secret**:
     - Name: `DOCKERHUB_USERNAME`, Value: `<your-dockerhub-username>`
     - Name: `DOCKERHUB_TOKEN`, Value: `<your-dockerhub-personal-access-token>`
     - Name: `LLM_API_KEY`, Value: `test-key`

---

## 3. Pull Request & Demonstrating the Blocked Gate

The rubric requires demonstrating the CI gate blocking a pull request:
1. Open a Pull Request from `dev` to `main`:
   `https://github.com/o0on/rahbar/compare/main...dev`
2. Title: `feat: release grounded RAG service on Kubernetes`
3. Screenshot the green status checks on the PR showing `ci.yml` passed.
4. Merge the Pull Request into `main`.

---

## 4. Final Submission Checklist

- [x] All 7 corpus properties satisfied and documented in `docs/CORPUS.md`.
- [x] Indexing decoupled into Kubernetes Job / CronJob (never on API startup).
- [x] All 4 ADRs (`0001` through `0004`) committed.
- [x] Probes properly wired (`/healthz` for liveness, `/readyz` for readiness).
- [x] Embedding model pin guardrail enforced in code.
- [x] Semantic cache with cosine threshold and re-index invalidation.
- [x] Over 90 unit tests passing with $\ge 60\%$ coverage.
- [x] `eval/results.json` and `eval/baseline.json` committed.
- [x] Zero mechanical failures reported by `python scripts/check_submission.py`.
- [x] Commit authorship distributed $\ge 35\%$ between partners.
