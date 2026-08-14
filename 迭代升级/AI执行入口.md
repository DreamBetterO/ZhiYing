# AI 执行入口

## 当前基线

- 用户批准版本：**V4.0 大版本架构升级**；P0–P11 已完成，产品版本为 `0.4.0`。
- 生产路径是 15 步 `StepRegistry → PipelineRunner → ArtifactStore/WorkspaceCache`；`pipeline.py` 仅是公开兼容门面。
- CourseIR、单视频 VLM 会话、紧凑 CloudPayload、Canonical Document v2、任务 ETA、Application/Desktop 分层均为唯一默认合同。
- 最终验收见 `执行事实.yaml -> v4_0_architecture_delivery_2026_08_13`；动作与验收门见已完成的 [V4.0 执行清单](架构解耦与可恢复执行内核升级Agent执行清单.md)。历史证据不要默认通读。

## 已批准待执行升级

- 用户已批准并命名 **V4.1 云端精炼与桌面任务控制小升级**；产品代码尚未开始修改。
- 唯一实施依据：[V4.1 执行计划](V4.1云端精炼与桌面任务控制小升级执行计划.md)。
- 执行必须按 P0–P7 顺序逐门收敛；模板—编辑决策—精炼正文交互属于核心合同，不得降格为提示词拼接。
- 测试按计划第 8 节唯一归属执行；完整离线套件只在最终集成门运行，不重复长视频、真实 VLM 或无授权云请求。

## 直接行动

1. 对故障 Workspace 运行只读诊断：`.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`。
2. 按 `step_id/error_code` 查 [故障索引](../docs/diagnostics/problem-index.yaml)。
3. 只读 [模块边界](../docs/architecture/module-boundaries.yaml) 指定的 owner、Artifact 与测试；先补失败回归测试，再修改最小责任模块。
4. 步骤影响范围以 [步骤目录](../docs/architecture/pipeline-steps.yaml) 和 Registry 为准；目录由合同测试校验。
5. 完成时运行完整离线验收；真实云、模型下载、重装或长视频重跑必须另获授权。
6. 开始 V4.1 产品修改前，将 `当前架构升级状态.yaml` 切到 V4.1/P0；未收到执行指令时只维护计划，不提前编码。

## 必须保持

- 原生 Windows 桌面；无 Web UI、HTTP 服务、端口或 `serve`。
- JSON → Markdown → Word → PDF、时间戳与 `video-study://` 本地回看。
- 云推理 opt-in；未授权时 CloudPort 构造和请求均为 0。
- v1 Artifact 只读迁移；Document 只写 v2；历史 Workspace 不主动删除。
- 保留用户 dirty worktree；不读取、打印或提交 `.env`。
- `迭代记录与问题.md` 已有唯一 V4.0 用户批准记录，实施完成不得追加或改写。
