from __future__ import annotations

import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from zhiying.providers import (
    CloudBudgetExceeded,
    CloudRequestBudget,
    FallbackChatClient,
    test_openai_connection,
)
from zhiying.utils import TaskCancelled


def response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class ProviderFallbackTests(unittest.TestCase):
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
