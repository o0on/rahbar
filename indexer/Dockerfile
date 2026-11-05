# Multi-stage build for RAHBAR Indexer Job.
# Build from the repo root so the corpus can be baked into the image:
#   docker build -t rahbar-indexer:latest -f indexer/Dockerfile .
# (docs/PIPELINE.md, "How documents reach the indexer Job")
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY indexer/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final runner stage
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:$PATH" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

# Install system dependencies for OCR and PDF image rendering
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /install

# Create non-root user
RUN groupadd -g 10002 indexeruser && \
    useradd -u 10002 -g indexeruser -s /sbin/nologin -M indexeruser

COPY indexer/pipeline/ ./pipeline/

# The corpus reaches the Job baked into this image. corpus/raw is gitignored,
# so fetch whatever this machine is missing; FETCH_CORPUS=0 skips the download
# entirely (CI passes it: a build must not depend on a third-party site, and CI
# indexes eval/fixtures from a ConfigMap mounted at /corpus instead). The fetch
# is guarded so a site outage warns rather than failing the build - the Job
# then exits loudly with "no documents found in /corpus" at deploy time.
ARG FETCH_CORPUS=1
COPY corpus/ ./corpus/
RUN if [ "$FETCH_CORPUS" = "1" ]; then python corpus/fetch.py || echo "WARN: corpus fetch failed; image ships without raw documents"; fi; \
    mkdir -p /corpus && cp -r corpus/raw/. /corpus/ 2>/dev/null || true; \
    rm -rf corpus/raw; \
    chown -R indexeruser:indexeruser /corpus

USER indexeruser

CMD ["python", "-m", "pipeline.run"]
