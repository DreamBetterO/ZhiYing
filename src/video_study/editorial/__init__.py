"""V6.1 EditorialAgentSubgraph 的本地编辑协议（CP61-1）。

policy     — EditorialPolicy v1（约束与机器可验证谓词）
intent     — LocalIntentCompiler（自然语言 → EditorialPolicy）
evidence   — EvidenceCorrectionOverlay v1（不可变 transcript 上的纠错覆盖层）
blueprint  — DocumentBlueprint v2（Schema/validator）
document   — Document v3.1 组件树模型与 validator
local      — LocalBlueprintPolicy / LocalDocumentComposer / LocalDeterministicRepair
"""
