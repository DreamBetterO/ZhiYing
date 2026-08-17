# AI 执行入口

## 当前基线

- 当前可运行产品版本为 `0.4.2`；**V4.2 本地 ASR 可靠性与日志可观测性修复已完成源码侧实机验收**，便携发行包重建仍需单独执行。
- 生产路径是 15 步 `StepRegistry → PipelineRunner → ArtifactStore/WorkspaceCache`；`pipeline.py` 仅是公开兼容门面。
- CourseIR、单视频 VLM 会话、紧凑 CloudPayload、Canonical Document v2、任务 ETA、Application/Desktop 分层均为唯一默认合同。
- V4.2 修复了 faster-whisper `mappingproxy` 序列化失败、`engine_chain` 降级断链、Qwen3-ASR 假进度、UI 四行重复、诊断器失败定位偏差和复选框视觉问题；接入 Qwen3-ASR 官方 Transformers 后端、真实块进度、动态设备/dtype/batch 和速度熔断。
- 本轮收尾补齐 Qwen3-ASR 官方依赖预检、中文路径下 `nagisa` runtime 镜像、扁平模型配置适配、tokenizer audio token 兼容、动态 batch、`qwen_max_rtf`、runner 错误保真、UI 单调进度和诊断器跨缓存回溯。
- 2026-08-16 热修：长视频 Qwen3-ASR 完成后 RTF 超建议阈值不再触发降级重跑，只记录 `asr_speed_warning`；桌面端舍去预计剩余时间并平滑进度；云端优化前弹出可编辑“本次整理要求”，文本随本次授权进入规划/整理并参与缓存指纹。
- 2026-08-16 热修：`audio.extract` 会校验提取音频是否覆盖源视频时长；源视频超过 60 秒且音频覆盖不足 95% 时记录 `audio_extract_incomplete` 并停止 ASR/文档生成。`scripts/diagnose_workspace.py` 的 `media_checks` 可直接显示源视频时长、音频时长、覆盖率和截断判断。
- `test4-2.mp4` 已按 UI 逻辑等价路径完成一次真实全链路：run `2f1fde8cbfa544a7a0b77c2d776c9bb5` 使用 Qwen3-ASR CUDA/bfloat16 成功，后续 Markdown/Word/PDF 均完成；缓存复跑 run `80589ddddf8b4210b29d0120e6c3a0fb` 可由诊断器回溯 ASR 来源。全过程云端请求为 0。

## 待执行

1. 如需发布便携包，按 `packing/script` 重新构建并运行便携验证；不得复用旧 `0.4.1` 发行目录作为 V4.2 完整发布证据。
2. 若再次验证长视频，优先诊断既有 `workspace/test4-2-c380e216e27c`，避免无意义重复重跑；确需真实 ASR 重跑时再清理该视频相关缓存。
3. 云端链路仍保持 opt-in；任何真实云端请求前必须重新说明数据、端点、模型链和预算并获得授权。

## 待用户审阅的升级方案

- 暂无。

## 直接行动

1. 对故障 Workspace 运行只读诊断：`.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`。
2. 按 `step_id/error_code` 查 [故障索引](../docs/diagnostics/problem-index.yaml)。
3. 只读 [模块边界](../docs/architecture/module-boundaries.yaml) 指定的 owner、Artifact 与测试；先补失败回归测试，再修改最小责任模块。
4. 步骤影响范围以 [步骤目录](../docs/architecture/pipeline-steps.yaml) 和 Registry 为准；目录由合同测试校验。
5. 完成时运行完整离线验收；真实云、模型下载、重装或长视频重跑必须另获授权。

## 便携打包迭代约束

- 后续打包默认采用增量更新：保持发行目录 `models/`、`tools/` 的相对路径和既有内容不变，
  仅更新程序及其他非模型、非工具文件，以便快速传输到测试电脑。
- 复用前必须按上一版清单校验模型和工具完整性；不完整时停止，不得用缺件目录生成完整包。
- 未经用户明确批准，不重新下载、重拷、覆盖、移动或升级模型、CUDA 运行时及工具文件。
- 详细执行规则见 [`packing/scheme/打包方案.md`](../packing/scheme/打包方案.md#后续迭代的增量打包约束)。

## 必须保持

- 原生 Windows 桌面；无 Web UI、HTTP 服务、端口或 `serve`。
- JSON → Markdown → Word → PDF、时间戳与 `video-study://` 本地回看。
- 云推理 opt-in；未授权时 CloudPort 构造和请求均为 0。
- v1 Artifact 只读迁移；Document 只写 v2；历史 Workspace 不主动删除。
- 保留用户 dirty worktree；不读取、打印或提交 `.env`。
- `迭代记录与问题.md` 已有 V4.0、V4.1 和 V4.2 用户批准记录；V4.2 实施、验收或交付完成不得再次追加或改写。
