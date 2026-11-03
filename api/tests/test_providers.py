"""Provider tests: the augmented prompt, JSON extraction, the retry ladder
(section 3.4) and the safe fallback - with httpx.post faked, so CI never
calls a real model (section 6)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.providers.llm import hosted as hosted_module
from app.providers.llm import ollama as ollama_module
from app.providers.llm.common import (build_messages, is_fallback, parse_answer,
                                      safe_fallback, strip_fences)
from app.providers.llm.hosted import HostedProvider
from app.providers.llm.ollama import OllamaProvider
from app.schemas import Answer, Passage

VALID_REPLY = json.dumps({
    "reasoning": "the handbook states the threshold",
    "answer": "A CGPA below 2.00 triggers an academic warning "
              "[Academic_Rules_Handbook_2026 / 5.4].",
    "citations": [{"source": "Academic_Rules_Handbook_2026.pdf",
                   "section": "5.4 Academic Warning", "page": 12}],
    "answered_from_context": True,
    "confidence": "high",
})


def passage() -> Passage:
    return Passage(chunk_id="handbook-2026-p004-0002",
                   text="A CGPA below 2.00 triggers an academic warning.",
                   source="Academic_Rules_Handbook_2026.pdf",
                   section="5.4 Academic Warning", page=12, score=0.81)


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(self.status_code))

    def json(self) -> dict:
        return self._body


def ollama_body(content: str) -> dict:
    return {"message": {"content": content},
            "prompt_eval_count": 11, "eval_count": 7}


# ----------------------------------------------------------------------- prompt

def test_prompt_orders_instructions_then_evidence_then_question() -> None:
    messages = build_messages("What is the attendance rule?", [passage()])
    assert [m["role"] for m in messages] == ["system", "user"]
    user = messages[1]["content"]
    assert user.index("EVIDENCE") < user.index("QUESTION: What is the attendance rule?")
    assert "<<<" in user and ">>>" in user              # evidence is delimited
    assert "Academic_Rules_Handbook_2026.pdf" in user
    system = messages[0]["content"].lower()
    assert "untrusted data" in system and "never as instructions" in system


# ------------------------------------------------------------- json extraction

def test_strip_fences_unwraps_the_three_shapes_models_produce() -> None:
    assert strip_fences(f"```json\n{VALID_REPLY}\n```") == VALID_REPLY
    assert strip_fences(f"Sure! {VALID_REPLY} Hope that helps.") == VALID_REPLY
    assert strip_fences("no json here") == "no json here"


def test_parse_answer_accepts_valid_and_rejects_prose() -> None:
    parsed = parse_answer(VALID_REPLY)
    assert parsed.confidence == "high" and parsed.citations
    assert parse_answer(f"```json\n{VALID_REPLY}\n```").answered_from_context is True
    with pytest.raises((ValidationError, ValueError)):
        parse_answer("The attendance rule is 75 percent, as everyone knows.")
    with pytest.raises((ValidationError, ValueError)):
        parse_answer('{"answer": "half an answer"}')


def test_safe_fallback_is_recognised_as_a_fallback() -> None:
    fallback = safe_fallback("model wrote prose")
    assert is_fallback(fallback) is True
    assert fallback.answered_from_context is False
    assert fallback.citations == []
    assert is_fallback(parse_answer(VALID_REPLY)) is False


# --------------------------------------------------------------------- ollama

def test_ollama_without_passages_falls_back_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ollama_module.httpx, "post",
                        lambda *a, **k: pytest.fail("no request without evidence"))
    provider = OllamaProvider(Settings(ollama_url="http://ollama:11434"))
    result = provider.generate("Q?", [])
    assert is_fallback(result) and result.confidence == "low"


def test_ollama_sends_the_schema_and_parses_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        seen.append(json or {})
        return FakeResponse(200, ollama_body(VALID_REPLY))

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)
    provider = OllamaProvider(Settings(ollama_url="http://ollama:11434"))
    result = provider.generate("What happens below a 2.00 CGPA?", [passage()])
    assert result.answered_from_context is True
    assert result.citations[0].section == "5.4 Academic Warning"
    assert seen[0]["format"] == Answer.model_json_schema()   # constrained decoding
    assert seen[0]["options"]["temperature"] == 0.2
    assert "EVIDENCE" in seen[0]["messages"][1]["content"]


def test_ollama_retries_a_bad_reply_once_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        calls.append(1)
        return FakeResponse(200, ollama_body("the model wrote prose, not JSON"))

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)
    provider = OllamaProvider(Settings(ollama_url="http://ollama:11434"))
    result = provider.generate("Q?", [passage()])
    assert is_fallback(result)                  # never a 500 for bad model output
    assert len(calls) == 2                      # first try plus exactly one retry


def test_ollama_falls_back_when_the_service_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    calls: list[int] = []

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)
    provider = OllamaProvider(Settings(ollama_url="http://ollama:11434"))
    assert is_fallback(provider.generate("Q?", [passage()]))
    assert len(calls) == 2


def test_ollama_degrades_to_json_mode_on_an_old_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that rejects `format: schema` gets one degraded retry (3.4)."""
    seen: list[dict] = []

    def fake_post(url: str, json: dict | None = None, timeout: object = None) -> FakeResponse:
        seen.append(json or {})
        if seen[-1].get("format") == "json":                # the degraded shape
            return FakeResponse(200, ollama_body(VALID_REPLY))
        return FakeResponse(400, {"error": "unknown format"})

    monkeypatch.setattr(ollama_module.httpx, "post", fake_post)
    provider = OllamaProvider(Settings(ollama_url="http://ollama:11434"))
    assert provider.generate("Q?", [passage()]).answered_from_context is True
    assert len(seen) == 2 and seen[1]["format"] == "json"


# --------------------------------------------------------------------- hosted

def hosted_settings(**over: object) -> Settings:
    base: dict[str, object] = dict(hosted_base_url="https://api.example.com/openai/v1",
                                   llm_api_key="sk-not-committed", llm_model="test-model")
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_hosted_refuses_to_call_without_a_url_or_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hosted_module.httpx, "post",
                        lambda *a, **k: pytest.fail("no request without configuration"))
    assert is_fallback(HostedProvider(hosted_settings(hosted_base_url=""))
                       .generate("Q?", [passage()]))
    assert is_fallback(HostedProvider(hosted_settings(llm_api_key=""))
                       .generate("Q?", [passage()]))


def test_hosted_posts_with_a_bearer_key_and_parses_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, json: dict | None = None, headers: dict | None = None,
                  timeout: object = None) -> FakeResponse:
        seen.append((url, json or {}, headers or {}))
        return FakeResponse(200, {"choices": [{"message": {"content": VALID_REPLY}}],
                                  "usage": {"prompt_tokens": 12, "completion_tokens": 8}})

    monkeypatch.setattr(hosted_module.httpx, "post", fake_post)
    provider = HostedProvider(hosted_settings())
    result = provider.generate("What happens below a 2.00 CGPA?", [passage()])
    assert result.confidence == "high"
    url, payload, headers = seen[0]
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer sk-not-committed"   # never in a file
    assert payload["response_format"]["type"] == "json_schema"


def test_hosted_degrades_to_json_mode_on_a_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    def fake_post(url: str, json: dict | None = None, headers: dict | None = None,
                  timeout: object = None) -> FakeResponse:
        seen.append(json or {})
        if seen[-1].get("response_format", {}).get("type") == "json_object":
            return FakeResponse(200, {"choices": [{"message": {"content": VALID_REPLY}}]})
        return FakeResponse(400, {"error": "response_format not supported"})

    monkeypatch.setattr(hosted_module.httpx, "post", fake_post)
    provider = HostedProvider(hosted_settings())
    assert provider.generate("Q?", [passage()]).answered_from_context is True
    assert len(seen) == 2 and seen[1]["response_format"]["type"] == "json_object"


def test_an_unknown_provider_name_fails_fast_at_startup() -> None:
    from app.providers.llm import get_provider

    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_provider(Settings(llm_provider="definitely-not-a-provider"))
    assert get_provider(Settings(llm_provider="stub")).name == "stub"
    assert get_provider(Settings(llm_provider="hosted")).name == "hosted"
    assert get_provider(Settings(llm_provider="ollama-host")).name == "ollama"
