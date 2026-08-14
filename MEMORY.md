# Project memory index

当前基线：**V4.0 架构升级已完成交付**；产品版本 `0.4.0`。

- [AI 执行入口](迭代升级/AI执行入口.md) — 当前基线、直接行动与边界。
- [当前架构升级状态](迭代升级/当前架构升级状态.yaml) — 只含 active phase、blocker、next 与验证摘要。
- [模块边界](docs/architecture/module-boundaries.yaml) / [步骤目录](docs/architecture/pipeline-steps.yaml) / [故障索引](docs/diagnostics/problem-index.yaml) — 定位问题的机器可校验入口。
- [V4.0 架构合同](迭代升级/V4.0架构合同.md) — 长期稳定约束。
- [V4.0 执行清单](迭代升级/架构解耦与可恢复执行内核升级Agent执行清单.md) — P0–P11 验收门。
- [执行事实](迭代升级/执行事实.yaml) — 完成历史与审计证据；不要默认通读。
- [用户版本时间线](迭代升级/迭代记录与问题.md) — 只保存用户批准的问题和最终方案。

故障定位：`.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`。

本机 Torch 环境：`conda activate ImageT10`。云端默认不调用；不得读取或输出 `.env`。
