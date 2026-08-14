from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .contracts import PipelineStep


class StepRegistry:
    """显式 DAG 注册表；不会扫描文件系统或动态发现插件。"""

    def __init__(self, steps: Iterable[PipelineStep] = ()) -> None:
        self._steps: dict[str, PipelineStep] = {}
        for step in steps:
            self.register(step)

    def register(self, step: PipelineStep) -> None:
        step_id = step.spec.step_id
        if step_id in self._steps:
            raise ValueError(f"重复 step_id：{step_id}")
        self._steps[step_id] = step

    def get(self, step_id: str) -> PipelineStep:
        try:
            return self._steps[step_id]
        except KeyError as exc:
            raise KeyError(f"未知 Step：{step_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._steps)

    def validate(self) -> None:
        producers: dict[object, str] = {}
        paths: dict[str, tuple[str, object]] = {}
        for step_id, step in self._steps.items():
            for dependency in step.spec.dependencies:
                if dependency not in self._steps:
                    raise ValueError(f"Step {step_id} 依赖不存在：{dependency}")
            for artifact in step.spec.outputs:
                if artifact in producers:
                    raise ValueError(
                        f"Artifact {artifact.name} 有多个生产者：{producers[artifact]}、{step_id}"
                    )
                producers[artifact] = step_id
                for path in artifact.relative_paths:
                    if path in paths:
                        other_step, other_artifact = paths[path]
                        raise ValueError(
                            f"Artifact 输出路径冲突：{path}（{other_step}/{other_artifact.name}、"
                            f"{step_id}/{artifact.name}）"
                        )
                    paths[path] = (step_id, artifact)
        for step_id, step in self._steps.items():
            for artifact in step.spec.inputs:
                producer = producers.get(artifact)
                if producer is None:
                    raise ValueError(f"Step {step_id} 的输入 Artifact 无生产者：{artifact.name}")
                if producer not in step.spec.dependencies:
                    raise ValueError(
                        f"Step {step_id} 未声明输入 {artifact.name} 的生产者依赖：{producer}"
                    )
        self._topological_order(tuple(self._steps))

    def required_order(self, targets: Iterable[str] | None = None) -> tuple[str, ...]:
        self.validate()
        selected = tuple(targets or self._steps)
        unknown = [step_id for step_id in selected if step_id not in self._steps]
        if unknown:
            raise ValueError(f"请求包含未知 Step：{', '.join(unknown)}")
        required: set[str] = set()

        def collect(step_id: str) -> None:
            if step_id in required:
                return
            required.add(step_id)
            for dependency in self._steps[step_id].spec.dependencies:
                collect(dependency)

        for target in selected:
            collect(target)
        # set 只用于成员判断；实际排序始终遵循显式注册顺序，避免进程间哈希随机化。
        return self._topological_order(tuple(step_id for step_id in self._steps if step_id in required))

    def _topological_order(self, selected: tuple[str, ...]) -> tuple[str, ...]:
        selected_set = set(selected)
        indegree = {step_id: 0 for step_id in selected}
        downstream: dict[str, list[str]] = defaultdict(list)
        for step_id in selected:
            for dependency in self._steps[step_id].spec.dependencies:
                if dependency in selected_set:
                    indegree[step_id] += 1
                    downstream[dependency].append(step_id)
        ready = [step_id for step_id in self._steps if step_id in selected_set and indegree[step_id] == 0]
        ordered: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for child in downstream[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(ordered) != len(selected_set):
            cycle = sorted(step_id for step_id, degree in indegree.items() if degree > 0)
            raise ValueError(f"Step DAG 存在环：{', '.join(cycle)}")
        return tuple(ordered)
