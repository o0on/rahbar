"""Readiness guardrail tests (section 4.4): the pod reports not-ready when
Chroma is unreachable, the index is empty, Redis is down, or the collection
was built by a different embedding model - with chromadb and redis faked."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient



class FakeCollection:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.metadata = ({"embed_model": state["model"]} if state["model"] else {})

    def count(self) -> int:
        return self.state["count"]


class FakeHttpClient:
    def __init__(self, state: dict, *args: object, **kwargs: object) -> None:
        self.state = state

    def heartbeat(self) -> None:
        if self.state["chroma_error"]:
            raise RuntimeError("connection refused")

    def get_collection(self, name: str) -> FakeCollection:
        if self.state["chroma_error"]:
            raise RuntimeError("connection refused")
        if self.state["missing"]:
            raise RuntimeError(f"collection {name} does not exist")
        return FakeCollection(self.state)


class FakeRedisClient:
    def __init__(self, state: dict, *args: object, **kwargs: object) -> None:
        self.state = state

    def ping(self) -> bool:
        if self.state["redis_error"]:
            raise ConnectionError("redis refused")
        return True


def install_fakes(monkeypatch: pytest.MonkeyPatch, **over: object) -> dict:
    """Put fake chromadb and redis modules on sys.modules for one test."""
    state: dict = {"model": "nomic-embed-text", "count": 42, "missing": False,
                   "chroma_error": False, "redis_error": False}
    state.update(over)

    chroma = ModuleType("chromadb")
    chroma.__path__ = []                       # mark it as a package
    config = ModuleType("chromadb.config")

    class ChromaSettings:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    config.Settings = ChromaSettings            # type: ignore[attr-defined]
    chroma.config = config                      # type: ignore[attr-defined]
    chroma.HttpClient = lambda *a, **k: FakeHttpClient(state, *a, **k)  # type: ignore[attr-defined]

    redis_module = ModuleType("redis")
    redis_module.Redis = lambda *a, **k: FakeRedisClient(state, *a, **k)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "chromadb", chroma)
    monkeypatch.setitem(sys.modules, "chromadb.config", config)
    monkeypatch.setitem(sys.modules, "redis", redis_module)
    return state


@pytest.fixture()
def readyz_client(monkeypatch: pytest.MonkeyPatch):
    """A chroma-mode app wired to the fake cluster."""
    def build(**over: object) -> TestClient:
        install_fakes(monkeypatch, **over)
        monkeypatch.setenv("RETRIEVER", "chroma")
        monkeypatch.setenv("LLM_PROVIDER", "stub")
        from app.main import app
        return TestClient(app)

    return build


def test_readyz_is_ready_when_the_index_matches_the_pin(readyz_client) -> None:
    with readyz_client() as client:
        r = client.get("/readyz")
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["chroma"] == "ok"
    assert checks["collection_count"] == 42
    assert checks["embedding_pin"] == "ok"
    assert checks["redis"] == "ok"


def test_readyz_refuses_an_index_built_by_another_model(readyz_client) -> None:
    """A collection built by another model must not serve traffic (4.4)."""
    with readyz_client(model="all-MiniLM-L6-v2") as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["embedding_pin"] == "mismatch"
    assert "all-MiniLM-L6-v2" in checks["error"]
    assert "nomic-embed-text" in checks["error"]


def test_readyz_fails_while_the_collection_is_empty(readyz_client) -> None:
    with readyz_client(count=0) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["chroma"] == "empty_collection"


def test_readyz_names_an_unreachable_chroma(readyz_client) -> None:
    with readyz_client(chroma_error=True) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["chroma"].startswith("unreachable")


def test_readyz_names_an_unreachable_redis(readyz_client) -> None:
    with readyz_client(redis_error=True) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["redis"].startswith("unreachable")


def test_startup_survives_a_pin_mismatch_and_stays_not_ready(
    readyz_client,
) -> None:
    """The API logs the mismatch at startup instead of crashing, then keeps
    refusing traffic until the index is rebuilt (section 4.4)."""
    with readyz_client(model="someone-elses-model") as client:
        assert client.get("/healthz").status_code == 200   # liveness never probes
        assert client.get("/readyz").status_code == 503


def test_liveness_never_touches_a_dependency(readyz_client) -> None:
    with readyz_client(chroma_error=True, redis_error=True) as client:
        assert client.get("/healthz").json() == {"status": "alive"}
