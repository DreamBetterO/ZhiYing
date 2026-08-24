from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from zhiying.execution.artifacts import ArtifactId, ArtifactRef, WorkspaceLayout
from zhiying.execution.bootstrap import build_runtime_services
from zhiying.execution.cache import CacheDecision, CacheReason
from zhiying.execution.context import (
    CloudCredentials,
    ProcessingContext,
    ProcessingOptions,
    RunPolicy,
    RuntimeServices,
    VideoSource,
)
from zhiying.execution.contracts import FingerprintMaterial, StepOutcome, StepSpec, StepStatus
from zhiying.execution.registry import StepRegistry
from zhiying.execution.graph_runtime import GraphRuntime
from zhiying.providers import CloudRequestBudget, OpenAICloudJsonAdapter
from zhiying.utils import LocalProcessAdapter

from tests.fakes_execution import (
    FakeCloudJsonPort,
    FakeDocumentPort,
    FakeMediaPort,
    FakeProcessPort,
    FakeSpeechPort,
    FakeVisionPort,
)


class PortFakeContractTests(unittest.TestCase):
    def test_all_required_ports_have_offline_fakes(self) -> None:
        self.assertIsNotNone(FakeProcessPort().run(["fake"]))
        self.assertIn("duration", FakeMediaPort().probe(Path("video.mp4"))["format"])
        self.assertTrue(FakeSpeechPort().probe_capability()["available"])
        self.assertEqual(FakeVisionPort().open_session({}).compare({})["decision"], "no_match")
        self.assertTrue(FakeCloudJsonPort().request_json(
            {}, validator=lambda value: self.assertTrue(value["ok"]), stage="test", cancel_check=lambda: False,
        )["ok"])
        self.assertEqual(
            FakeDocumentPort().render_pdf({}, Path("word.docx"), Path("out.pdf")),
            "built_in",
        )

    def test_runtime_services_construct_each_port_lazily_once(self) -> None:
        calls = []
        services = RuntimeServices(port_factories={"media": lambda: calls.append("media") or object()})
        self.assertEqual(calls, [])
        first = services.port("media")
        second = services.port("media")
        self.assertIs(first, second)
        self.assertEqual(calls, ["media"])


class CompositionRootTests(unittest.TestCase):
    def test_offline_composition_has_no_cloud_factory_and_constructs_nothing(self) -> None:
        with TemporaryDirectory() as directory, \
                patch("zhiying.media.processing.MediaAdapter") as media, \
                patch("zhiying.media.speech.SpeechAdapter") as speech, \
                patch("zhiying.execution.adapters.vision.VisionAdapter") as vision, \
                patch("zhiying.render.DocumentAdapter") as document, \
                patch("zhiying.utils.LocalProcessAdapter") as process:
            services = build_runtime_services(
                project_root=Path(directory),
                model_dir=Path(directory) / "models",
                options=ProcessingOptions(),
                policy=RunPolicy(cloud_authorized=False),
            )
            self.assertNotIn("cloud", services.port_factories)
            for constructor in (media, speech, vision, document, process):
                constructor.assert_not_called()

    def test_cloud_factory_requires_authorization_credentials_and_shared_budget(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = RunPolicy(cloud_authorized=True)
            with self.assertRaisesRegex(ValueError, "缺少本次运行所需凭据"):
                build_runtime_services(
                    project_root=root, model_dir=root / "models",
                    options=ProcessingOptions(), policy=policy,
                )
            credentials = CloudCredentials("secret-key", "https://example.invalid/v1", ("model-a",))
            budget = CloudRequestBudget(3)
            services = build_runtime_services(
                project_root=root, model_dir=root / "models",
                options=ProcessingOptions(knowledge={"max_output_tokens": 1234}),
                policy=policy, credentials=credentials, cloud_budget=budget,
            )
            with patch("zhiying.providers.FallbackChatClient") as client:
                cloud = services.port("cloud")
                client.assert_not_called()
            self.assertIs(cloud.budget, budget)
            self.assertNotIn("secret-key", repr(cloud))
            self.assertNotIn("secret-key", repr(services))

    def test_cache_hit_never_constructs_port_factory(self) -> None:
        class PortUsingStep:
            spec = StepSpec(
                "fixture.step", 1,
                outputs=(ArtifactId("fixture.output", ("output.json",)),),
                error_code_prefix="FIXTURE",
            )

            def fingerprint(self, _context, _inputs):
                return FingerprintMaterial({})

            def execute(self, context, _inputs, _staging):
                context.services.port("media")
                raise AssertionError("cache hit must not execute")

            def validate(self, _context, _outcome):
                return None

        class Store:
            def validate(self, _context, _artifact): return True
            def staging_dir(self, *_args): raise AssertionError("must not stage")
            def commit(self, *_args): raise AssertionError("must not commit")
            def cleanup_staging(self, *_args): return None

        class Cache:
            def decide(self, *_args):
                artifact = PortUsingStep.spec.outputs[0]
                return CacheDecision(True, CacheReason.CACHE_HIT, (ArtifactRef(artifact, Path("cached")),))
            def record(self, *_args): raise AssertionError("must not record")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            constructed = []
            context = ProcessingContext(
                "run-id", VideoSource(root / "v.mp4", "v", "sha256:v", 1, 1),
                WorkspaceLayout(root / "workspace", "v"), ProcessingOptions(), RunPolicy(),
                RuntimeServices(port_factories={"media": lambda: constructed.append(True) or object()}),
            )
            state = GraphRuntime().run_compatible_state(type("Kernel", (), {
                "context": context,
                "registry": StepRegistry([PortUsingStep()]),
                "artifacts": Store(),
                "cache": Cache(),
            })())
        self.assertEqual(state.statuses["fixture.step"], StepStatus.CACHED)
        self.assertEqual(constructed, [])


class ConcreteAdapterTests(unittest.TestCase):
    def test_cloud_adapter_is_lazy_redacted_and_uses_injected_budget(self) -> None:
        budget = CloudRequestBudget(2)
        fake_client = Mock()
        fake_client.create_json.return_value = ({"ok": True}, "model-a", [], {})
        with patch("zhiying.providers.FallbackChatClient", return_value=fake_client) as constructor:
            adapter = OpenAICloudJsonAdapter(
                api_key="top-secret", base_url="https://example.invalid/v1",
                models=["model-a"], budget=budget,
            )
            constructor.assert_not_called()
            result = adapter.request_json(
                {"messages": [{"role": "user", "content": "fixture"}]},
                validator=lambda value: None, stage="planning", cancel_check=lambda: False,
            )
        self.assertTrue(result["ok"])
        constructor.assert_called_once()
        self.assertIs(fake_client.create_json.call_args.kwargs["request_budget"], budget)
        self.assertNotIn("top-secret", repr(adapter))

    def test_process_diagnostics_redact_common_secret_forms(self) -> None:
        text = "Authorization: Bearer-abc api_key=secret password:hello token value"
        redacted = LocalProcessAdapter._redact(text)
        for secret in ("Bearer-abc", "secret", "hello", "value"):
            self.assertNotIn(secret, redacted)

    def test_document_speech_and_vision_adapters_delegate_without_real_middleware(self) -> None:
        from zhiying.media.speech import SpeechAdapter
        from zhiying.execution.adapters.vision import VisionAdapter
        from zhiying.render import DocumentAdapter

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("zhiying.media.speech.decode_audio", return_value={"segments": []}) as decode_audio:
                speech = SpeechAdapter(root / "models", config_root=root, cancel_check=lambda: False)
                speech.decode(root / "audio.flac", root / "transcript.json", {"engine": "fake"}, cancel_check=lambda: False)
            self.assertLessEqual(decode_audio.call_count, 1)

            document = DocumentAdapter(root)
            with patch("zhiying.render.render_markdown") as markdown, \
                    patch("zhiying.render.render_docx") as word, \
                    patch("zhiying.render.convert_docx_to_pdf", return_value="built_in") as pdf:
                document.render_markdown({}, root / "out.md")
                document.render_word(root / "document.json", root / "out.docx", cancel_check=lambda: False)
                self.assertEqual(document.render_pdf({}, root / "out.docx", root / "out.pdf", cancel_check=lambda: False), "built_in")
            markdown.assert_called_once(); word.assert_called_once(); pdf.assert_called_once()

            provider = Mock()
            provider.start_session.return_value = 0.1
            provider.compare_candidates.return_value = {"decision": "no_match"}
            with patch("zhiying.execution.adapters.vision.create_visual_provider", return_value=(provider, "")) as create:
                vision = VisionAdapter({}, cancel_check=lambda: False, event_sink=lambda _event: None, progress_sink=lambda _event: None)
                session = vision.open_session({})
                self.assertEqual(session.compare({"question": {}, "candidates": [], "contract": {}})["decision"], "no_match")
                session.close()
            settings = create.call_args.args[0]
            self.assertIn("_cancel_check", settings)
            self.assertIn("_event_callback", settings)
            provider.start_session.assert_called_once()


if __name__ == "__main__":
    unittest.main()
