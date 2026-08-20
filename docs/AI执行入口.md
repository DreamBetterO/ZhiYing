# AI 执行入口

> 项目文档链路、最新状态与文档索引入口见 [项目索引](项目索引.md)。

## 当前技术状态

- 生产路径是 15 步 `StepRegistry → PipelineRunner → ArtifactStore/WorkspaceCache`；`pipeline.py` 仅是公开兼容门面。
- CourseIR、单视频 VLM 会话、紧凑 CloudPayload、Canonical Document v2、任务 ETA、Application/Desktop 分层均为唯一默认合同。
- V5.0 新增「视频链接源获取」：`SourcePort`（`src/video_study/source.py`）预检/下载/ffprobe 时长一致性校验；`run_compatible_pipeline_from_url`/`acquire_source_from_url` 方式一入口；`WorkspaceCatalog.find_by_url` 下载前缓存命中；桌面「＋ 添加链接」对话框 + 队列来源列 + 下载中/已就绪状态与 `source_ready` 事件；D1–D7 决策全部落地。
- 实测事实：W3C trailer（默认 UA，52s/4.37MB/aac）✅、test-videos.co.uk（10s/无音轨）✅、Mux HLS ✅；B 站 BV1cmTu6mEL3 阶段性 412/握手超时（preflight 分类 `DOWNLOAD_TIMEOUT`）；抖音明确「暂不支持」（TC-020）。

## 待执行

1. 如需发布便携包，按 `packing/script` 重新构建并运行便携验证（增量构建，`tools/` 新增 `yt-dlp/yt-dlp.exe` 项，按清单校验）；**便携包重建需单独授权**。
2. 若再次验证长视频，优先诊断既有 workspace，避免无意义重复重跑；确需真实 ASR 重跑时再清理相关缓存。
3. 云端链路仍保持 opt-in；任何真实云端请求前必须重新说明数据、端点、模型链和预算并获得授权。
4. B 站锚点（BV1cmTu6mEL3）再次在线验证前先跑 `scripts/check_test_links.py` 健康检查。

## 直接行动

1. 对故障 Workspace 运行只读诊断：`.venv\Scripts\python.exe scripts\diagnose_workspace.py workspace\<video_id>`。
2. 按 `step_id/error_code` 查 [故障索引](diagnostics/problem-index.yaml)。
3. 只读 [模块边界](architecture/module-boundaries.yaml) 指定的 owner、Artifact 与测试；先补失败回归测试，再修改最小责任模块。
4. 步骤影响范围以 [步骤目录](architecture/pipeline-steps.yaml) 和 Registry 为准；目录由合同测试校验。
5. 完成时运行完整离线验收；真实云、模型下载、重装或长视频重跑必须另获授权。

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

## 验收

- 完成时运行完整离线验收：`unittest discover -s tests` / `compileall -q src tests` / `pip check` / `git diff --check`。
- 结构化收尾：完成了什么（含路径）、关键发现、剩余缺口/阻塞、需用户决策事项。
- 小的功能点升级时，测试函数测升级部分，注意测试和验收边界。
- 用户要求全链路测试时，测试务必对齐 UI 逻辑，进行 UI 挂载的全链路测试，期间不可美化链路产物。

## 必须保持

- 云推理 opt-in；未授权时 CloudPort 构造和请求均为 0。
- v1 Artifact 只读迁移；Document 只写 v2；历史 Workspace 不主动删除。
- 保留用户 dirty worktree；不读取、打印或提交 `.env`。
- `迭代记录与问题.md` 的追加需要用户明确版本之后再进行写入，写入格式为 `版本号-日期-问题描述`。
- `项目索引.md` 的版本需同步最新，在进行升级或交付时进行必要跟新；
- `AI执行入口.md`及`当前架构升级状态.yaml`的版本需要保持最新，每次版本升级时都需要更新；
