from __future__ import annotations

import unittest
from dataclasses import dataclass

from video_study.execution.artifacts import ArtifactId
from video_study.execution.contracts import RemoteCost, StepSpec
from video_study.execution.registry import StepRegistry


@dataclass
class StubStep:
    spec: StepSpec


def step(
    step_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    inputs: tuple[ArtifactId, ...] = (),
    outputs: tuple[ArtifactId, ...] = (),
) -> StubStep:
    return StubStep(StepSpec(
        step_id=step_id,
        implementation_version=1,
        dependencies=dependencies,
        inputs=inputs,
        outputs=outputs,
        remote_cost=RemoteCost.NONE,
        owner="tests",
        tests=("tests/test_execution_registry.py",),
        error_code_prefix=step_id.upper().replace(".", "_"),
    ))


class StepRegistryTests(unittest.TestCase):
    def test_explicit_registration_and_topological_target_closure(self) -> None:
        registry = StepRegistry([
            step("source", outputs=(ArtifactId("source", ("manifest.json",)),)),
            step("audio", dependencies=("source",), outputs=(ArtifactId("audio", ("audio/audio.flac",)),)),
            step("document", dependencies=("source",), outputs=(ArtifactId("document", ("knowledge/document.json",)),)),
        ])
        self.assertEqual(registry.required_order(("audio",)), ("source", "audio"))
        self.assertEqual(registry.ids(), ("source", "audio", "document"))

    def test_branching_dag_order_follows_explicit_registration(self) -> None:
        registry = StepRegistry([
            step("source"),
            step("audio", dependencies=("source",)),
            step("decode", dependencies=("audio",)),
            step("frames", dependencies=("source",)),
            step("result", dependencies=("decode", "frames")),
        ])
        self.assertEqual(
            registry.required_order(("result",)),
            ("source", "audio", "frames", "decode", "result"),
        )

    def test_duplicate_id_is_rejected(self) -> None:
        registry = StepRegistry([step("source")])
        with self.assertRaisesRegex(ValueError, "重复 step_id"):
            registry.register(step("source"))

    def test_missing_dependency_and_cycle_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "依赖不存在"):
            StepRegistry([step("a", dependencies=("missing",))]).validate()
        with self.assertRaisesRegex(ValueError, "存在环"):
            StepRegistry([
                step("a", dependencies=("b",)),
                step("b", dependencies=("a",)),
            ]).validate()

    def test_artifact_has_single_producer(self) -> None:
        shared = ArtifactId("shared", ("knowledge/shared.json",))
        with self.assertRaisesRegex(ValueError, "多个生产者"):
            StepRegistry([step("a", outputs=(shared,)), step("b", outputs=(shared,))]).validate()

    def test_distinct_artifacts_cannot_share_output_path(self) -> None:
        first = ArtifactId("first", ("knowledge/shared.json",))
        second = ArtifactId("second", ("knowledge/shared.json",))
        with self.assertRaisesRegex(ValueError, "输出路径冲突"):
            StepRegistry([step("a", outputs=(first,)), step("b", outputs=(second,))]).validate()

    def test_input_artifact_requires_declared_producer_dependency(self) -> None:
        source = ArtifactId("source", ("manifest.json",))
        with self.assertRaisesRegex(ValueError, "未声明输入"):
            StepRegistry([
                step("source", outputs=(source,)),
                step("consumer", inputs=(source,)),
            ]).validate()


if __name__ == "__main__":
    unittest.main()
