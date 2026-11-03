# Multi-stage build for RAHBAR Indexer Job
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final runner stage
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:$PATH" \
    PYTHONPATH="/install/lib/python3.12/site-packages:$PYTHONPATH"

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

COPY pipeline/ ./pipeline/

USER indexeruser

CMD ["python", "-m", "pipeline.run"]
