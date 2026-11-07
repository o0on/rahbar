"""Extended unit tests for Prometheus metrics observation and request counters."""

from __future__ import annotations

import pytest
from app.routes.metrics import LATENCY
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RETRIEVER", "stub")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_metrics_endpoint_returns_prometheus_format(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    assert "rahbar_requests_total" in r.text
    assert "rahbar_request_seconds" in r.text


def test_metrics_tracks_failed_validation_routes(client: TestClient) -> None:
    # 422 Unprocessable Entity for invalid question length
    client.post("/api/ask", json={"question": "no"})
    metrics_text = client.get("/metrics").text
    assert 'status="422"' in metrics_text


def test_metrics_latency_histogram_buckets() -> None:
    # Verify histogram metric configuration
    assert LATENCY._name == "rahbar_request_seconds"
    assert "path" in LATENCY._labelnames
