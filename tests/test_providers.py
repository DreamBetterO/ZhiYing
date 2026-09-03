from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
import httpx
from openai import APIStatusError

from zhiying.providers import (
    AllModelsFailed,
    CloudBudgetExceeded,
    CloudRequestBudget,
    FallbackChatClient,
    _safe_error,
    _extract_json,
    cloud_output_limit,
    cloud_request_limit,
    cloud_timeout_limit,
    test_openai_connection,
)
from zhiying.utils import TaskCancelled, cloud_optional_output_limit


def response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class ProviderFallbackTests(unittest.TestCase):
    def test_status_error_keeps_only_safe_provider_code_and_parameter(self) -> None:
        exc = APIStatusError(
            "provider response may contain secrets",
            response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid/v1")),
            body={"error": {"code": "invalid_parameter", "param": "messages[1].content", "message": "sensitive"}},
        )

        detail = _safe_error(exc)

        self.assertEqual(detail, "HTTP 400 (code=invalid_parameter, param=messages[1].content)")
        self.assertNotIn("sensitive", detail)

    def test_json_request_can_omit_max_tokens_for_provider_compatibility(self) -> None:
        client = FallbackChatClient(
            api_key="test-key", base_url="https://example.invalid/v1", models=["model-a"],
        )
        create = Mock(return_value=response({"ok": True}))
        client.client.chat.completions.create = create

        client.create_json(
            messages=[{"role": "user", "content": "return JSON"}], max_tokens=None,
        )

        self.assertNotIn("max_tokens", create.call_args.kwargs)

    def test_json_request_can_use_prompt_json_without_response_format(self) -> None:
        client = FallbackChatClient(
            api_key="test-key", base_url="https://example.invalid/v1", models=["model-a"],
        )
        create = Mock(return_value=response({"ok": True}))
        client.client.chat.completions.create = create

        client.create_json(
            messages=[{"role": "user", "content": "return JSON"}], json_mode=False,
        )

        self.assertNotIn("response_format", create.call_args.kwargs)

    def test_json_parser_recovers_latex_backspace_escape(self) -> None:
        parsed = _extract_json(r'{"text":"$\binom{n}{k}$"}')
        self.assertEqual(parsed["text"], r"$\binom{n}{k}$")

    def test_json_parser_recovers_unescaped_latex_commands(self) -> None:
        parsed = _extract_json(
            r'{"text":"$\frac{1}{2}\sum_i x_i\left(a\right)$","note":"line\nnext"}'
        )

        self.assertEqual(parsed["text"], r"$\frac{1}{2}\sum_i x_i\left(a\right)$")
        self.assertEqual(parsed["note"], "line\nnext")

    def test_json_parser_repairs_trailing_commas_without_touching_string_text(self) -> None:
        parsed = _extract_json(
            '{"text":"保留,]文本","items":["第一项",],"meta":{"ok":true,},}'
        )

        self.assertEqual(parsed["text"], "保留,]文本")
        self.assertEqual(parsed["items"], ["第一项"])
        self.assertEqual(parsed["meta"], {"ok": True})

    def test_output_and_request_limits_are_configurable_by_stage_environment(self) -> None:
        settings = {
            "budget": {
                "max_calls_per_video": 7,
                "max_output_tokens": 9000,
                "planning_max_output_tokens": 11000,
            },
            "max_calls_env": "TEST_CLOUD_MAX_CALLS",
            "max_output_tokens_env": "TEST_CLOUD_MAX_OUTPUT",
            "planning_max_output_tokens_env": "TEST_CLOUD_PLANNING_OUTPUT",
            "timeout_seconds": 180,
            "timeout_seconds_env": "TEST_CLOUD_TIMEOUT",
        }
        with patch.dict("os.environ", {
            "TEST_CLOUD_MAX_CALLS": "12",
            "TEST_CLOUD_MAX_OUTPUT": "14000",
            "TEST_CLOUD_PLANNING_OUTPUT": "16000",
            "TEST_CLOUD_TIMEOUT": "240",
        }):
            self.assertEqual(cloud_request_limit(settings), 12)
            self.assertEqual(cloud_output_limit(settings), 14000)
            self.assertEqual(cloud_output_limit(settings, "planning_max_output_tokens"), 16000)
            self.assertEqual(cloud_timeout_limit(settings), 240.0)

    def test_optional_output_limit_can_disable_provider_hard_cap(self) -> None:
        settings = {
            "budget": {"organizer_request_max_output_tokens": 0},
            "organizer_request_max_output_tokens_env": "TEST_ORGANIZER_OUTPUT_LIMIT",
        }
        self.assertIsNone(cloud_optional_output_limit(settings, "organizer_request_max_output_tokens"))
        with patch.dict("os.environ", {"TEST_ORGANIZER_OUTPUT_LIMIT": "18000"}):
            self.assertEqual(
                cloud_optional_output_limit(settings, "organizer_request_max_output_tokens"),
                18000,
            )

    def test_release_config_has_long_course_cloud_headroom(self) -> None:
        api = yaml.safe_load((Path(__file__).resolve().parents[1] / "api.yaml").read_text(encoding="utf-8"))
        qwen = api["qwen"]
        self.assertGreaterEqual(qwen["budget"]["max_calls_per_video"], 10)
        self.assertGreaterEqual(qwen["budget"]["planning_max_output_tokens"], 10000)
        self.assertGreaterEqual(qwen["budget"]["max_output_tokens"], 10000)

    def test_connection_probe_lists_models_without_chat_completion(self) -> None:
        client = Mock()
        client.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id="model-a")])
        with patch("zhiying.providers.OpenAI", return_value=client):
            result = test_openai_connection(api_key="temporary", base_url="https://example.com/v1")
        client.models.list.assert_called_once_with()
        client.chat.completions.create.assert_not_called()
        self.assertEqual(result["model_count"], 1)
    def test_semantic_validator_can_reject_first_model_and_try_next(self) -> None:
        client = FallbackChatClient(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            models=["first", "second"],
        )
        create = Mock(side_effect=[response({"quality": "bad"}), response({"quality": "good"})])
        client.client.chat.completions.create = create

        observed = []
        parsed, model, attempts, usage = client.create_json(
            messages=[{"role": "user", "content": "test"}],
            validator=lambda value: (
                None if value.get("quality") == "good" else (_ for _ in ()).throw(ValueError("bad quality"))
            ),
            on_attempt=observed.append,
        )

        self.assertEqual(parsed["quality"], "good")
        self.assertEqual(model, "second")
        self.assertEqual([item.ok for item in attempts], [False, True])
        self.assertEqual(usage["total_tokens"], 30)
        self.assertEqual(create.call_count, 2)
        self.assertEqual([item.model for item in observed], ["first", "second"])

    def test_semantic_validator_normalized_value_is_returned(self) -> None:
        client = FallbackChatClient(
            api_key="test-key", base_url="https://example.invalid/v1", models=["model-a"],
        )
        client.client.chat.completions.create = Mock(return_value=response({"raw": True}))

        parsed, _model, _attempts, _usage = client.create_json(
            messages=[{"role": "user", "content": "test"}],
            validator=lambda _value: {"normalized": True},
        )

        self.assertEqual(parsed, {"normalized": True})

    def test_semantic_rejections_do_not_open_provider_circuit(self) -> None:
        client = FallbackChatClient(
            api_key="test-key", base_url="https://example.invalid/v1",
            models=["one", "two", "three", "four"],
        )
        create = Mock(side_effect=[response({"quality": "bad"}) for _ in range(4)])
        client.client.chat.completions.create = create
        budget = CloudRequestBudget(max_requests=8, failure_limit=2)

        with self.assertRaises(AllModelsFailed) as raised:
            client.create_json(
                messages=[{"role": "user", "content": "test"}],
                validator=lambda _value: (_ for _ in ()).throw(ValueError("schema mismatch")),
                request_budget=budget,
                stage="blueprint",
            )

        self.assertEqual(create.call_count, 4)
        self.assertEqual(raised.exception.usage["total_tokens"], 60)
        self.assertEqual([item.model for item in raised.exception.attempts], ["one", "two", "three", "four"])
        self.assertFalse(budget.circuit_open)
        self.assertEqual(budget.consecutive_failures, 0)

    def test_request_budget_is_shared_across_stages_and_records_usage(self) -> None:
        client = FallbackChatClient(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            models=["model-a"],
        )
        create = Mock(side_effect=[response({"ok": True}), response({"ok": True})])
        client.client.chat.completions.create = create
        budget = CloudRequestBudget(max_requests=2)

        client.create_json(messages=[{"role": "user", "content": "plan"}], request_budget=budget, stage="planning")
        client.create_json(messages=[{"role": "user", "content": "organize"}], request_budget=budget, stage="organizing")
        with self.assertRaises(CloudBudgetExceeded):
            client.create_json(messages=[{"role": "user", "content": "retry"}], request_budget=budget, stage="fallback")

        snapshot = budget.snapshot()
        self.assertEqual(snapshot["requests_used"], 2)
        self.assertEqual(snapshot["requests_remaining"], 0)
        self.assertEqual(snapshot["usage"]["total_tokens"], 30)
        self.assertEqual([item["stage"] for item in snapshot["attempts"]], ["planning", "organizing"])
        self.assertEqual(create.call_count, 2)

    def test_blocking_cloud_request_can_be_cancelled_promptly(self) -> None:
        client = FallbackChatClient(
            api_key="test-key", base_url="https://example.invalid/v1", models=["model-a"],
        )
        release = threading.Event()
        client.client.chat.completions.create = Mock(side_effect=lambda **_kwargs: release.wait(5))
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        started = time.monotonic()
        try:
            with self.assertRaises(TaskCancelled):
                client.create_json(
                    messages=[{"role": "user", "content": "test"}], cancel_check=cancelled,
                )
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
