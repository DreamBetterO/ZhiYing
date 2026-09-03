# AI 执行入口

> 项目文档链路、最新状态与文档索引入口见 [项目索引](项目索引.md)。

## 当前技术状态

- 用户可见产品名为“知影”；源码命名空间与发行包均为 `zhiying`，便携可执行文件沿用 `ZhiYing` 工程标识，旧 `video-study` 命令仅作兼容别名，`video-study://` 本地协议保持不变。
- 发行入口位于源码仓库根目录 `release/`：Git 只保存用户文档、官方组件链接和机器清单；`scripts/release/` 只负责轻量核心 ZIP，`release/output/` 被 Git 忽略。
- 生产路径是 23 节点 `Source/Job/Video/Aggregate Graph → NodeExecutor → ArtifactStore/WorkspaceCache`；`GraphRuntime` 是唯一生产编排器。
- CourseIR、单视频 VLM 会话、完整候选池视觉检索、紧凑 CloudPayload、DocumentPlan/Canonical Document v3、任务 ETA、Application/Desktop 分层均为当前合同。
- V5.0 新增「视频链接源获取」：`SourcePort`（`src/zhiying/source.py`）预检/下载/ffprobe 时长一致性校验；`run_compatible_pipeline_from_url`/`acquire_source_from_url` 方式一入口；`WorkspaceCatalog.find_by_url` 下载前缓存命中；桌面「＋ 添加链接」对话框 + 队列来源列 + 下载中/已就绪状态与 `source_ready` 事件；D1–D7 决策全部落地。
- 实测事实：W3C trailer（默认 UA，52s/4.37MB/aac）✅、test-videos.co.uk（10s/无音轨）✅、Mux HLS ✅；B 站 BV1cmTu6mEL3 阶段性 412/握手超时（preflight 分类 `DOWNLOAD_TIMEOUT`）；抖音明确「暂不支持」（TC-020）。

## V6.1 正式版本与后续质量确认

- 用户已于 2026-08-20 批准 `V6.0 LangGraph 全链路架构升级`；实施版本从 `1.0.0-alpha` 开始，通过发布门后升为 `1.0.0`。
- [主架构方案](迭代升级/V6.0%20LangGraph全链路架构升级方案.md)定义目标和不可违反合同；[执行计划](迭代升级/V6.0%20LangGraph全链路升级执行计划.md)是唯一阶段/恢复点来源；[目标合同 YAML](迭代升级/V6.0%20LangGraph全链路目标合同.yaml)供 Agent/测试机器核对。
- V6 目标是 LangGraph 接管 Source、单/多视频、知识、文档、Markdown/Word/PDF、聚合和 Job 终态，成为唯一生产编排器；先等价接管，后升级模型、视觉和 Document v3。
- 当前代码版本为 `1.0.0`；用户已于 2026-08-24 明确确认正式版本号。active `docs/architecture/*.yaml` 已切换到 Graph/Document v3.1。
- 用户已于 2026-08-21 批准 [V6.1 主方案](迭代升级/V6.1%20Function%20Calling编辑Harness升级方案.md)、[执行计划](迭代升级/V6.1%20Function%20Calling编辑Harness执行计划.md)和[目标合同](迭代升级/V6.1%20Function%20Calling编辑Harness目标合同.yaml)，并于 2026-09-03 确认 V6.1 实施与真实云验收完成；CP61-0～CP61-8 均已关闭。
- V6.1 已以 `1.0.0` 作为当前产品版本：保留确定性主图，在 `EditorialAgentSubgraph` 内以阶段限定 Function Calling 赋予 LLM 主动观察和局部编辑权；原生渲染 Document v3.1，并以 `tool_native → structured_only → local_deterministic` 完成可审计降级。

## 待执行

1. V6.1 当前无待完成检查点；后续只处理新的独立需求、回归问题和 3 项既有发行基线测试失败。
2. 当前保存模型 `qwen3.7-flash-2026-07-15` 已通过真实 structured_only 课程链路；`glm-5.2` 曾通过原生工具探针。下一次真实课程请求仍须重新披露并逐次确认。
3. ZhiYing 1.0.0 发行采用“同版本、双通道”；下一次便携重建和远程发布仍需重新授权。
4. B 站锚点再次在线验证前先运行链接健康检查。

## 直接行动

1. V6.1 已完成；若处理回归，先读取 V6.1 主方案、执行计划、目标合同与 `当前架构升级状态.yaml` 的完成基线，只修改责任节点及其下游。
2. 若诊断现有故障 Workspace，先运行 `.venv\Scripts\python.exe scripts\diagnostics\diagnose_workspace.py workspace\<video_id>`。
3. 按 `step_id/error_code` 查 [故障索引](diagnostics/problem-index.yaml)。
4. 只读 [模块边界](architecture/module-boundaries.yaml) 指定的 owner、Artifact 与测试；先补失败回归测试，再修改最小责任模块。
5. 当前影响范围以 [步骤目录](architecture/pipeline-steps.yaml)、GraphRuntime 和 NodeExecutor 为运行事实；不得恢复旧 Runner。V6.1 切换后不得保留生产 `v3 → v2` 降级渲染或旧固定模板绕行。
6. 完成检查点时运行责任测试并记录原始结果；正式切换/发布运行完整离线验收。

## 约束

- 桌面优先：无 Web UI、本机 HTTP 服务、端口监听、`serve` 命令。
- 密钥安全：不读取、不打印、不提交 `.env`。
- 云推理 opt-in：真实云调用前逐次说明数据/端点/模型链/预算并获得明确授权；不得仅为测试配置发起真实请求。
- `video-study://` 本地协议：点击调用本地播放器定位，不打开网页。
- 数据/源码分离：`Resource/ workspace/ output/ models/ 视频/` 是数据产物；无关临时脚本放 `./tmp`。
- 保留用户 dirty worktree；不假设仓库可重置或提交。
- 工作时 python 为 conda 环境 `ImageT10`；非必要不动核心依赖。
- 使用视频进行测试时，需要严格按照 UI 的逻辑运行，保证理论链路和实际链路的一致性。

## 便携打包迭代约束

- 后续打包默认采用增量更新：保持发行目录 `models/`、`tools/` 的相对路径和既有内容不变，仅更新程序及其他非模型、非工具文件，以便快速传输到测试电脑；必要时允许更改，更改需要明确指出。
- 复用前必须按上一版清单校验模型和工具完整性；不完整时停止，不得用缺件目录生成完整包。
- 未经用户明确批准，不重新下载、重拷、覆盖、移动或升级模型、CUDA 运行时及工具文件。
- 详细执行规则见 [`打包方案`](../packing/scheme/打包方案.md#后续迭代的增量打包约束)。
- 面向 GitHub 的发行体系使用根目录 `release/` 与 `scripts/release/`；不再创建独立 `ZhiYing-Releases` 仓库，也不再构建完整离线/工具/运行时分卷。旧 `packing/` 只作为历史便携实现，不进入新发行目录。

## 验收

- 完成时运行完整离线验收：`unittest discover -s tests` / `compileall -q src tests` / `pip check` / `git diff --check`。
- 结构化收尾：完成了什么（含路径）、关键发现、剩余缺口/阻塞、需用户决策事项。
- 小的功能点升级时，测试函数测升级部分，注意测试和验收边界。
- 用户要求全链路测试时，测试务必对齐 UI 逻辑，进行 UI 挂载的全链路测试，期间不可美化链路产物。

## 必须保持

- 云推理 opt-in；未授权时 CloudPort 构造和请求均为 0。
- 当前 V6.0 运行基线新写 Document v3、v2 只读；V6.1 生产切换后新写 Document v3.1 且三端原生消费，历史 v2/v3 继续只读兼容，历史 Workspace 始终不主动删除。
- V6 首版图片云端默认禁止；文本授权不得隐式授权图片。
- V6.1 必须保留无需 LLM 的本地确定性完成路径；现有本地模型通过能力门后可做语义建议，但不得成为完成链前置条件。
- Function Calling 只允许批准的阶段工具、schema、预算和调用上限；不得赋予任意文件、Shell、网络、MCP、图片上传或无限循环权限。
- 云端/工具失败允许自动产出，但状态必须为 `degraded`，并真实记录执行层级、模型链、失败原因和未满足质量项。
- 保留用户 dirty worktree；不读取、打印或提交 `.env`。
- `迭代记录与问题.md` 的追加需要用户明确版本之后再进行写入，写入格式为 `版本号-日期-问题描述`。
- `项目索引.md` 的版本需同步最新，在进行升级或交付时进行必要跟新；
- `architecture`、`AI执行入口.md`和`当前架构升级状态.yaml`的版本需要保持最新，每次版本升级时都需要更新；
