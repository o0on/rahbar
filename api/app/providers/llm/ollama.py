"""Ollama provider: ollama-host or ollama-incluster (section 3.5).

Enforcement ladder, strongest first:
1. `format` carries the Answer JSON schema, so Ollama constrains decoding to
   valid instances of the contract (structured outputs).
2. Pydantic still validates the reply - structure can be guaranteed, truth
   cannot.
3. One retry on a validation failure, then the canned fallback: a user must
   never see a 500 because the model wrote prose instead of JSON.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...schemas import Answer, Passage
from .common import build_messages, parse_answer, safe_fallback

log = logging.getLogger("rahbar.providers.ollama")

ATTEMPTS = 2                      # first try, then exactly one retry (section 3.4)
TIMEOUT = httpx.Timeout(180.0, connect=10.0)


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings   # ollama_url, llm_model, temperature

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        if not passages:
            return safe_fallback("no passages to ground the answer in")
        payload = {
            "model": self.settings.llm_model,
            "messages": build_messages(question, passages),
            "format": Answer.model_json_schema(),   # constrained decoding (section 3.4)
            "stream": False,
            "options": {"temperature": self.settings.temperature},
        }
        last_error = "unknown"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = self._post(payload)
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < ATTEMPTS:
                    log.warning("generation attempt failed, retrying once",
                                extra={"attempt": attempt, "error": last_error})
                    continue
                break
            try:
                answer = parse_answer(raw)
            except (ValidationError, ValueError) as exc:
                last_error = f"validation: {exc}"
                log.warning("model reply failed validation, retrying once",
                            extra={"attempt": attempt, "error": last_error})
                continue
            self._record_tokens()
            return answer
        return safe_fallback(last_error)

    def _post(self, payload: dict) -> str:
        url = f"{self.settings.ollama_url.rstrip('/')}/api/chat"
        response = httpx.post(url, json=payload, timeout=TIMEOUT)
        if response.status_code == 400 and "format" in payload:
            # Very old Ollama: no structured outputs. JSON mode, then validation.
            degraded = dict(payload)
            degraded["format"] = "json"
            response = httpx.post(url, json=degraded, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
        self._tokens = (int(body.get("prompt_eval_count") or 0),
                        int(body.get("eval_count") or 0))
        return str(body["message"]["content"])

    # -- tokens in/out per request, for /metrics (section 4.8)
    _tokens: tuple[int, int] = (0, 0)

    def _record_tokens(self) -> None:
        from ...routes.metrics import TOKENS_IN, TOKENS_OUT

        tokens_in, tokens_out = self._tokens
        if tokens_in:
            TOKENS_IN.inc(tokens_in)
        if tokens_out:
            TOKENS_OUT.inc(tokens_out)
