from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from zhiying.execution.artifacts import WorkspaceLayout
from zhiying.execution.context import (
    CloudCredentials,
    ProcessingContext,
    ProcessingOptions,
    RunPolicy,
    RuntimeServices,
    VideoSource,
)


class ExecutionContextTests(unittest.TestCase):
    def test_context_snapshot_excludes_runtime_services_and_api_key(self) -> None:
        with TemporaryDirectory() as directory:
            context = ProcessingContext(
                run_id="run-1",
                source=VideoSource(
                    Path(directory) / "lesson.mp4", "lesson-id", "sha256:fixture", 12.0, 42,
                ),
                workspace=WorkspaceLayout(Path(directory) / "workspace", "lesson-id"),
                options=ProcessingOptions(asr={"engine": "fake"}, render={"schema_version": 2}),
                policy=RunPolicy(cloud_authorized=True, target_steps=("document.assemble",)),
                services=RuntimeServices(
                    credentials=CloudCredentials(
                        api_key="never-serialize-this-key",
                        base_url="https://example.invalid/v1",
                        models=("fake-model",),
                    ),
                ),
            )
            serialized = json.dumps(context.public_snapshot(), ensure_ascii=False)
        self.assertNotIn("never-serialize-this-key", serialized)
        self.assertNotIn("credentials", serialized)
        self.assertNotIn("services", serialized)
        self.assertNotIn("never-serialize-this-key", repr(context))
        self.assertEqual(context.source.path, context.source.path.resolve())

    def test_processing_options_are_recursively_immutable(self) -> None:
        raw = {"engine": "fake", "nested": {"models": ["a", "b"]}}
        options = ProcessingOptions(asr=raw)
        raw["engine"] = "changed"
        raw["nested"]["models"].append("c")
        self.assertEqual(options.asr["engine"], "fake")
        self.assertEqual(options.asr["nested"]["models"], ("a", "b"))
        with self.assertRaises(TypeError):
            options.asr["engine"] = "forbidden"

    def test_run_policy_normalizes_targets_and_rejects_negative_retry(self) -> None:
        policy = RunPolicy(target_steps=("a", "a", "b"), force_steps=frozenset({"a"}))
        self.assertEqual(policy.target_steps, ("a", "b"))
        with self.assertRaises(ValueError):
            RunPolicy(retry_limit=-1)

    def test_processing_options_reject_secret_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "不得包含秘密字段"):
            ProcessingOptions(knowledge={"provider": {"_runtime_api_key": "secret"}})


if __name__ == "__main__":
    unittest.main()
