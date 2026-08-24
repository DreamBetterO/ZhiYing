from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .utils import TaskCancelled, ensure_not_cancelled

# 稳定错误码
CLOUD_OUTPUT_TRUNCATED = "CLOUD_OUTPUT_TRUNCATED"
CLOUD_OUTPUT_INVALID_JSON = "CLOUD_OUTPUT_INVALID_JSON"
CLOUD_OUTPUT_SCHEMA_REJECTED = "CLOUD_OUTPUT_SCHEMA_REJECTED"
CLOUD_TIMEOUT = "CLOUD_TIMEOUT"
CLOUD_BUDGET_EXCEEDED = "CLOUD_BUDGET_EXCEEDED"

# 结果分类
RESULT_SUCCESS = "success"
RESULT_TRUNCATED = "truncated"
RESULT_INVALID_JSON = "invalid_json"
RESULT_SCHEMA_REJECTED = "schema_rejected"
RESULT_TIMEOUT = "timeout"
RESULT_CONNECTION = "connection"
RESULT_HTTP_STATUS = "http_status"


class CloudOutputTruncated(RuntimeError):
    """输出被截断；不应盲发同一请求给下一个模型。"""
    def __init__(self, message: str, *, finish_reason: str = "", response_chars: int = 0):
        super().__init__(message)
        self.finish_reason = finish_reason
        self.response_chars = response_chars


@dataclass(frozen=True)
class CloudAttemptInfo:
    """单次模型尝试的结构化信息，不含响应正文或密钥。"""
    model: str
    ok: bool
    result_type: str = RESULT_SUCCESS
    finish_reason: str = ""
    prompt_chars: int = 0
    response_chars: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None


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


class CloudBudgetExceeded(RuntimeError):
    """Raised before a request when the per-video cloud budget is exhausted."""


class CloudCircuitOpen(RuntimeError):
    """Raised before a request after repeated provider failures."""


@dataclass
class CloudRequestBudget:
    """Mutable per-video request and usage ledger shared by all cloud stages."""

    max_requests: int
    requests_used: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })
    attempts: list[dict[str, Any]] = field(default_factory=list)
    failure_limit: int = 3
    consecutive_failures: int = 0
    circuit_open: bool = False

    def claim(self, *, stage: str, model: str) -> None:
        if self.circuit_open:
            raise CloudCircuitOpen("云端连续失败熔断已打开，未继续发送请求")
        if self.requests_used >= self.max_requests:
            raise CloudBudgetExceeded(
                f"云端请求预算已用尽（{self.requests_used}/{self.max_requests}），未继续发送请求"
            )
        self.requests_used += 1

    def record(self, *, stage: str, attempt: ModelAttempt, usage: dict[str, int]) -> None:
        self.attempts.append({"stage": stage, **attempt.__dict__})
        if attempt.ok:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= max(1, int(self.failure_limit)):
                self.circuit_open = True
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.usage[key] = int(self.usage.get(key, 0)) + int(usage.get(key, 0) or 0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_requests": self.max_requests,
            "requests_used": self.requests_used,
            "requests_remaining": max(0, self.max_requests - self.requests_used),
            "usage": dict(self.usage),
            "attempts": [dict(item) for item in self.attempts],
            "consecutive_failures": self.consecutive_failures,
            "circuit_open": self.circuit_open,
        }


def cloud_request_limit(settings: dict[str, Any]) -> int:
    """Resolve the user-configured global request cap without treating model count as a cap."""
    budget = settings.get("budget", {}) if isinstance(settings, dict) else {}
    configured = budget.get("max_calls_per_video", 1)
    env_name = str(settings.get("max_calls_env", "") or "") if isinstance(settings, dict) else ""
    env_value = os.getenv(env_name) if env_name else None
    raw = env_value if env_value and env_value.isdigit() else configured
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 1


def ensure_cloud_request_budget(settings: dict[str, Any]) -> CloudRequestBudget:
    existing = settings.get("_runtime_request_budget")
    if isinstance(existing, CloudRequestBudget):
        return existing
    state = CloudRequestBudget(max_requests=cloud_request_limit(settings))
    settings["_runtime_request_budget"] = state
    return state


class FallbackChatClient:
    """OpenAI-compatible JSON client with ordered model failover."""

    def __init__(self, *, api_key: str, base_url: str, models: list[str], timeout: float = 90.0):
        if not api_key or not base_url or not models:
            raise ValueError("FallbackChatClient requires api_key, base_url and at least one model")
        self.models = list(dict.fromkeys(model.strip() for model in models if model.strip()))
        self.timeout = max(0.01, float(timeout))
        self.client = OpenAI(
            api_key=api_key, base_url=base_url.rstrip("/"), timeout=self.timeout, max_retries=0,
        )

    def _create_response(self, *, cancel_check=None, **kwargs):
        """让阻塞的 SDK 调用可被桌面线程快速放弃，并主动关闭 HTTP 客户端。"""
        if not cancel_check:
            return self.client.chat.completions.create(**kwargs)
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def request() -> None:
            try:
                result_queue.put(("ok", self.client.chat.completions.create(**kwargs)))
            except BaseException as exc:  # 在线程边界保留 SDK 原始异常类型
                result_queue.put(("error", exc))

        threading.Thread(target=request, daemon=True, name="zhiying-cloud-request").start()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                ensure_not_cancelled(cancel_check)
            except TaskCancelled:
                try:
                    self.client.close()
                except Exception:
                    pass
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self.client.close()
                except Exception:
                    pass
                raise TimeoutError(f"云端请求超过运行上限（{self.timeout:g} 秒）")
            try:
                state, value = result_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if state == "error":
                raise value
            return value

    def create_json(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2500,
        validator: Callable[[dict[str, Any]], None] | None = None,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        request_budget: CloudRequestBudget | None = None,
        stage: str = "cloud",
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, Any], str, list[ModelAttempt], dict[str, int]]:
        attempts: list[ModelAttempt] = []
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        for model in self.models:
            ensure_not_cancelled(cancel_check)
            if request_budget is not None:
                request_budget.claim(stage=stage, model=model)
            response_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            try:
                response = self._create_response(
                    cancel_check=cancel_check,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    extra_body={"enable_thinking": False},
                )
                usage = response.usage
                response_usage = {
                    "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                }
                for key in usage_total:
                    usage_total[key] += response_usage[key]
                content = response.choices[0].message.content or ""
                response_chars = len(content)
                finish_reason = str(getattr(response.choices[0], "finish_reason", "") or "")

                # 截断检测：finish_reason=length 时不再盲发下一模型
                if finish_reason == "length":
                    truncated_error = CloudOutputTruncated(
                        f"模型 {model} 输出被截断（finish_reason=length，"
                        f"response_chars={response_chars}）",
                        finish_reason=finish_reason,
                        response_chars=response_chars,
                    )
                    attempts.append(ModelAttempt(model=model, ok=False, error=CLOUD_OUTPUT_TRUNCATED))
                    if request_budget is not None:
                        request_budget.record(stage=stage, attempt=attempts[-1], usage=response_usage)
                    if on_attempt:
                        on_attempt(attempts[-1])
                    raise truncated_error

                parsed = _extract_json(content)
                if validator is not None:
                    validator(parsed)
                attempts.append(ModelAttempt(model=model, ok=True))
                if request_budget is not None:
                    request_budget.record(stage=stage, attempt=attempts[-1], usage=response_usage)
                if on_attempt:
                    on_attempt(attempts[-1])
                return parsed, model, attempts, usage_total
            except CloudOutputTruncated:
                raise
            except (APIConnectionError, APITimeoutError, APIStatusError, ValueError, json.JSONDecodeError) as exc:
                detail = _safe_error(exc)
                attempts.append(ModelAttempt(model=model, ok=False, error=detail))
                if request_budget is not None:
                    request_budget.record(stage=stage, attempt=attempts[-1], usage=response_usage)
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


class OpenAICloudJsonAdapter:
    """受共享预算控制的 CloudJsonPort adapter；秘密不会进入 repr。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        models: list[str],
        budget: CloudRequestBudget,
        timeout: float = 90.0,
        max_tokens: int = 5000,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url
        self.models = tuple(models)
        self.budget = budget
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client: FallbackChatClient | None = None

    def __repr__(self) -> str:
        return (
            f"OpenAICloudJsonAdapter(base_url={self.base_url!r}, models={self.models!r}, "
            "api_key=<redacted>)"
        )

    def _get_client(self) -> FallbackChatClient:
        if self._client is None:
            self._client = FallbackChatClient(
                api_key=self._api_key,
                base_url=self.base_url,
                models=list(self.models),
                timeout=self.timeout,
            )
        return self._client

    def request_json(
        self,
        payload: dict[str, Any],
        *,
        validator,
        stage: str,
        cancel_check,
    ) -> dict[str, Any]:
        result, _info = self.request_json_with_info(
            payload, validator=validator, stage=stage, cancel_check=cancel_check,
        )
        return result

    def request_json_with_info(
        self,
        payload: dict[str, Any],
        *,
        validator,
        stage: str,
        cancel_check,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            messages = [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        result, model, attempts, usage = self._get_client().create_json(
            messages=messages,
            temperature=float(payload.get("temperature", 0.1)),
            max_tokens=int(payload.get("max_tokens", self.max_tokens)),
            validator=validator,
            request_budget=self.budget,
            stage=stage,
            cancel_check=cancel_check,
        )
        return result, {
            "model": model,
            "attempts": [attempt.__dict__ for attempt in attempts],
            "usage": usage,
        }


class OpenAICloudToolAdapter:
    """V6.1 原生工具调用 adapter；仅由已授权且能力门为 tool_native 的模型惰性构造。"""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float = 90.0, max_tokens: int = 2000) -> None:
        self._api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client = None

    def __repr__(self) -> str:
        return f"OpenAICloudToolAdapter(base_url={self.base_url!r}, model={self.model!r}, api_key=<redacted>)"

    def _get_client(self):
        if self._client is None:
            self._client = OpenAI(api_key=self._api_key, base_url=self.base_url, timeout=self.timeout)
        return self._client

    def invoke_turn(self, *, messages, tools, tool_choice, stage, budget, cancel_check):
        from .execution.tool_calling import invoke_tool_turn_openai
        ensure_not_cancelled(cancel_check)
        result = invoke_tool_turn_openai(
            self._get_client(), model=self.model, messages=messages, tools=tools,
            tool_choice=tool_choice, stage=stage, budget=budget,
            cancel_check=cancel_check, max_tokens=self.max_tokens,
        )
        ensure_not_cancelled(cancel_check)
        return result
