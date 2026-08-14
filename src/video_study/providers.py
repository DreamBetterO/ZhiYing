from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


@dataclass(frozen=True)
class ModelAttempt:
    model: str
    ok: bool
    error: str | None = None


class AllModelsFailed(RuntimeError):
    def __init__(self, attempts: list[ModelAttempt]):
        self.attempts = attempts
        summary = "; ".join(f"{item.model}: {item.error}" for item in attempts)
        super().__init__(f"所有候选模型均不可用：{summary}")


class FallbackChatClient:
    """OpenAI-compatible JSON client with ordered model failover."""

    def __init__(self, *, api_key: str, base_url: str, models: list[str], timeout: float = 90.0):
        if not api_key or not base_url or not models:
            raise ValueError("FallbackChatClient requires api_key, base_url and at least one model")
        self.models = list(dict.fromkeys(model.strip() for model in models if model.strip()))
        self.client = OpenAI(api_key=api_key, base_url=base_url.rstrip("/"), timeout=timeout, max_retries=0)

    def create_json(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2500,
        validator: Callable[[dict[str, Any]], None] | None = None,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
    ) -> tuple[dict[str, Any], str, list[ModelAttempt], dict[str, int]]:
        attempts: list[ModelAttempt] = []
        for model in self.models:
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    extra_body={"enable_thinking": False},
                )
                content = response.choices[0].message.content or ""
                parsed = _extract_json(content)
                if validator is not None:
                    validator(parsed)
                attempts.append(ModelAttempt(model=model, ok=True))
                if on_attempt:
                    on_attempt(attempts[-1])
                usage = response.usage
                return parsed, model, attempts, {
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                }
            except (APIConnectionError, APITimeoutError, APIStatusError, ValueError, json.JSONDecodeError) as exc:
                detail = _safe_error(exc)
                attempts.append(ModelAttempt(model=model, ok=False, error=detail))
                if on_attempt:
                    on_attempt(attempts[-1])
        raise AllModelsFailed(attempts)


def test_openai_connection(*, api_key: str, base_url: str, timeout: float = 12.0) -> dict[str, int]:
    """仅请求 /models 验证兼容端点与凭据，不提交推理内容。"""
    if not api_key or not base_url:
        raise ValueError("连接测试需要 API URL 和当前会话 API Key")
    started = time.monotonic()
    try:
        response = OpenAI(
            api_key=api_key, base_url=base_url.rstrip("/"), timeout=timeout, max_retries=0
        ).models.list()
        count = len(getattr(response, "data", []) or [])
    except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
        raise RuntimeError(_safe_error(exc)) from None
    return {"latency_ms": max(0, int((time.monotonic() - started) * 1000)), "model_count": count}


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response contains no JSON object")
    result = json.loads(text[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("response JSON is not an object")
    return result


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, APIStatusError):
        return f"HTTP {exc.status_code}"
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "connection error"
    if isinstance(exc, ValueError):
        # 这里只包含本地 JSON/质量校验原因，不包含请求头、密钥或供应商响应正文。
        return str(exc)[:160] or "ValueError"
    return type(exc).__name__
