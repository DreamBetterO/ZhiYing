from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from video_study.providers import FallbackChatClient, test_openai_connection


def response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class ProviderFallbackTests(unittest.TestCase):
    def test_connection_probe_lists_models_without_chat_completion(self) -> None:
        client = Mock()
        client.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id="model-a")])
        with patch("video_study.providers.OpenAI", return_value=client):
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
        self.assertEqual(usage["total_tokens"], 15)
        self.assertEqual(create.call_count, 2)
        self.assertEqual([item.model for item in observed], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
