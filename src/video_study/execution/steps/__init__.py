"""内置 PipelineStep；注册只发生在 execution.bootstrap。"""

from .coarse import build_coarse_steps

__all__ = ["build_coarse_steps"]
