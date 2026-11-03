"""Hosted free-tier provider (Groq, Google AI Studio, OpenRouter). Section 3.5.

Any OpenAI-compatible endpoint works: POST {HOSTED_BASE_URL}/chat/completions.
The key arrives as LLM_API_KEY from a Kubernetes Secret created on the command
line. It never appears in a file you commit, and never in a log line.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...schemas import Answer, Passage
from .common import build_messages, parse_answer, safe_fallback

log = logging.getLogger("rahbar.providers.hosted")

ATTEMPTS = 2                      # first try, then exactly one retry (section 3.4)
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings   # hosted_base_url, llm_model, llm_api_key, temperature

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        if not passages:
            return safe_fallback("no passages to ground the answer in")
        if not self.settings.hosted_base_url:
            return safe_fallback("HOSTED_BASE_URL is not configured")
        if not self.settings.llm_api_key:
            return safe_fallback("LLM_API_KEY is missing (create it with `make secret`)")

        messages = build_messages(question, passages)
        last_error = "unknown"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                raw = self._post(messages)
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
            usage = self._usage
            if usage:
                from ...routes.metrics import TOKENS_IN, TOKENS_OUT

                TOKENS_IN.inc(usage[0])
                TOKENS_OUT.inc(usage[1])
            return answer
        return safe_fallback(last_error)

    _usage: tuple[int, int] = (0, 0)

    def _post(self, messages: list[dict[str, str]]) -> str:
        url = f"{self.settings.hosted_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        payload: dict = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "Answer",
                                                "schema": Answer.model_json_schema(),
                                                "strict": False}},
        }
        response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        if response.status_code == 400:
            # Not every free tier accepts json_schema; fall back to JSON mode,
            # and Pydantic still validates whatever comes back.
            payload["response_format"] = {"type": "json_object"}
            response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage") or {}
        self._usage = (int(usage.get("prompt_tokens") or 0),
                       int(usage.get("completion_tokens") or 0))
        return str(body["choices"][0]["message"]["content"])
