# Project memory index

当前可运行基线：产品版本 `0.4.2`。**V4.2 本地 ASR 可靠性与日志可观测性修复已完成源码侧实机验收**；Qwen3-ASR 已通过正式 UI 逻辑等价链路完成 `test4-2.mp4`，未触发云端请求。

2026-08-16 追加热修：Qwen3-ASR 完成后 RTF 超建议阈值只记录速度告警，不再判失败并触发 faster-whisper 重跑；桌面端不再展示预计剩余时间，进度条采用平滑递增；云端优化授权改为可编辑“本次整理要求”窗口。

2026-08-16 追加热修：`audio.extract` 增加提取音频覆盖率校验，源视频超过 60 秒时要求音频覆盖至少 95%；若 ffmpeg 因源音频流损坏提前中断，将在 ASR 和文档生成前失败并记录 `audio_extract_incomplete`。诊断器新增 `media_checks` 输出源视频/音频时长与覆盖率。

交接入口：[V4.2 本地 ASR 实机复核与收尾修复清单](迭代升级/V4.2本地ASR实机复核与收尾修复清单.md)。本轮只安装缺失 Python 依赖到 `models/qwen3-asr-runtime` overlay，未重新下载模型权重；便携发行包重建仍需单独执行。

- [AI 执行入口](迭代升级/AI执行入口.md) — 当前基线、直接行动与边界。
- [当前架构升级状态](迭代升级/当前架构升级状态.yaml) — 只含 active phase、blocker、next 与验证摘要。
- [模块边界](docs/architecture/module-boundaries.yaml) / [步骤目录](docs/architecture/pipeline-steps.yaml) / [故障索引](docs/diagnostics/problem-index.yaml) — 定位问题的机器可校验入口。
- [V4.0 架构合同](迭代升级/V4.0架构合同.md) — 长期稳定约束。
- [执行事实](迭代升级/执行事实.yaml) — 完成历史与审计证据；不要默认通读。
- [用户版本时间线](迭代升级/迭代记录与问题.md) — 只保存用户批准的问题和最终方案。
- [便携打包方案](packing/scheme/打包方案.md#后续迭代的增量打包约束) — 后续迭代默认复用
  `models/` 与 `tools/`，仅传输非模型、非工具更新；复用前必须校验完整性。

**注意：**

1. 故障定位：`.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`。

2. 本机 Torch 环境：`conda activate ImageT10`。云端默认不调用；不得读取或输出 `.env`。

3. 使用视频进行测试时，需要严格按照ui的逻辑运行，保证理论链路和实际链路的一致性！
