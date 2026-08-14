# AI 执行入口

## 当前基线

- 用户批准版本：**V4.1 云端精炼与桌面任务控制小升级**；P0–P7 全部门禁通过（含真实云端验收），产品版本为 `0.4.1`。
- 生产路径是 15 步 `StepRegistry → PipelineRunner → ArtifactStore/WorkspaceCache`；`pipeline.py` 仅是公开兼容门面。
- CourseIR、单视频 VLM 会话、紧凑 CloudPayload、Canonical Document v2、任务 ETA、Application/Desktop 分层均为唯一默认合同。
- V4.1 新增编辑意图（`课程资料整理偏好.md`）、编辑决策、云端截断分批检查点、本地聚合降级、拖动排序、全局单实例互斥和产物可见性控制。
- 真实云端验收已通过：单视频知识阶段（2 次请求，16798 tokens）和两视频聚合（1 次请求，10383 tokens）。
- 最终验收见 `执行事实.yaml -> v4_1_delivery_2026_08_14`；历史证据不要默认通读。

## 已批准待执行升级

- 暂无新的已批准升级。

## 直接行动

1. 对故障 Workspace 运行只读诊断：`.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`。
2. 按 `step_id/error_code` 查 [故障索引](../docs/diagnostics/problem-index.yaml)。
3. 只读 [模块边界](../docs/architecture/module-boundaries.yaml) 指定的 owner、Artifact 与测试；先补失败回归测试，再修改最小责任模块。
4. 步骤影响范围以 [步骤目录](../docs/architecture/pipeline-steps.yaml) 和 Registry 为准；目录由合同测试校验。
5. 完成时运行完整离线验收；真实云、模型下载、重装或长视频重跑必须另获授权。

## 必须保持

- 原生 Windows 桌面；无 Web UI、HTTP 服务、端口或 `serve`。
- JSON → Markdown → Word → PDF、时间戳与 `video-study://` 本地回看。
- 云推理 opt-in；未授权时 CloudPort 构造和请求均为 0。
- v1 Artifact 只读迁移；Document 只写 v2；历史 Workspace 不主动删除。
- 保留用户 dirty worktree；不读取、打印或提交 `.env`。
- `迭代记录与问题.md` 已有 V4.0 和 V4.1 用户批准记录，实施完成不得追加或改写。
