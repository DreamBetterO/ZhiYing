# V4.0 架构解耦与可恢复执行内核升级 Agent 执行清单

状态：`approved_ready_for_execution`

基线：V3.0 / 产品版本 `0.3.0`

变更标识：`architecture-decoupling-pipeline-kernel`

版本：**V4.0（用户已批准）**

版本类型：**大版本架构升级**。本次会改变内部执行合同、缓存协议、Desktop/Application 边界和兼容策略，但不改变桌面产品边界、用户主流程和最终产物链。

## 0. 执行 Agent 先读结论

本次升级不是为项目增加一层“抽象包装”，也不是按文件行数机械拆分。唯一目标是：在保持 V3.0 已验证能力稳定的前提下，使问题定位、局部重跑、功能扩展和后续 Agent 接手都能围绕稳定合同进行，并删除被新合同取代的重复路径。

本次必须同时解决五类问题：

1. **Agent 协作效率**：入口、当前状态、模块边界、故障码和测试索引形成单一导航链。
2. **代码耦合**：停止用动态 `settings` 传递运行服务，消除知识模块对 `summarize.py` 的反向依赖和双编排。
3. **工程中间件耦合**：ffmpeg/ffprobe、ASR/VLM 子进程、云客户端、Node/Word/PDF 只能经明确端口接入执行层，业务步骤不得直接初始化它们。
4. **中间产物耦合**：所有路径、Schema、读写、校验和指纹由 Artifact 合同管理，业务函数不得继续自行拼接 Workspace 路径和维护缓存。
5. **项目清洁度**：新路径稳定后必须删除旧编排、旧开关、旧云端大提示词、无生产调用模块和临时迁移代码，不能永久保留两套框架。

### 0.1 如何使用本清单

V4.0 文档按用途分为三层：

- [V4.0 升级执行导航](V4.0升级执行导航.md)：一页入口，负责把当前 Phase 路由到最少必读内容。
- [V4.0 架构合同](V4.0架构合同.md)：长期稳定的边界、接口、Artifact、缓存、中间件和 DAG 约束，不保存执行进度。
- 本清单：P0–P11 唯一可勾选任务书，只保存实施动作和验收门。

执行规则：

1. 一次只允许一个 Phase 为 `in_progress`。
2. 开工只读：本文件第 0 节、导航中的当前 Phase、本文件对应 Phase、该 Phase 引用的合同章节。
3. `当前架构升级状态.yaml` 只保存 phase、完成项、阻塞、下一步和测试摘要，不复制本文件内容。
4. 合同冲突按架构合同的变更规则暂停和决策；普通实现不得边做边改变目标架构。
5. 每个 Phase 达到收敛门后才进入下一阶段；没有通过完整离线测试的 Phase 不得标记完成。

### 0.2 当前基线证据

2026-08-12 本清单编写前已完成纯离线检查：

- `227` 项单元测试通过。
- `python -m compileall -q src tests` 通过。
- `node --check scripts/render_docx.mjs` 通过。
- `python -m pip check` 无依赖破坏。
- `git diff --check` 通过，仅有既有 LF/CRLF 提示。
- 未读取 `.env`，未发起云端请求，未消耗云端额度。

### 0.3 当前工作区保护

当前仓库包含大量 V3.0 用户既有修改、新文件和删除项。执行 Agent 必须：

- 开工前运行 `git status --short`，保存结果到当前会话，不得假设可重置。
- 不得使用 `git reset --hard`、`git checkout --` 或覆盖用户修改。
- 不得恢复用户已删除的 `.agents/video-study-project.md`、旧 packaging、旧实现/研究草稿或已删除构建脚本。
- 如用户未授权 Git 操作，不得自行提交、建分支或清理工作树。
- 每个阶段只修改清单列出的责任范围；发现超范围问题先记录到当前执行状态，不顺手扩大重构。

### 0.4 永久产品边界

- 保持原生 Windows 桌面产品，不增加 Web UI、本机 HTTP 服务、端口监听或 `serve`。
- 保持 JSON → Markdown → Word → PDF 数据流。
- 保持时间戳、source segment、视觉证据和 `video-study://` 本地回看。
- Document v2 的 `content_blocks` 继续作为持久化正文唯一事实源。
- 云端保持 opt-in；真实调用前仍须说明数据、端点、模型链、次数和预算并获得明确授权。
- 视频和截图不得因本次重构上传；本地 VLM 仍不消耗云端 token。
- 不读取、输出、缓存或提交 `.env` 中的秘密。
- `Resource/`、`workspace/`、`output/`、`models/` 继续视为本地数据/产物而非源码。
- 不向 `迭代记录与问题.md` 写实施状态、测试、文件列表或 Agent 推理。

## 1. 执行依据

- 长期稳定的目标边界、核心接口、Artifact/Workspace、缓存、中间件端口、DAG 与 `summarize` 整理规则，以 [V4.0 架构合同](V4.0架构合同.md) 为唯一依据。
- 本文件只保存 P0–P11 的可勾选动作、阶段收敛门、测试预算、停止条件、回滚与交付格式。
- 执行 Agent 按 [V4.0 升级执行导航](V4.0升级执行导航.md) 进入当前 Phase，只读取该 Phase 及其引用的合同章节。
- 普通实现不得改变架构合同；若发现合同无法实现，先停止当前 Phase，记录证据并请求决策，不得在代码中静默引入第二套路径。

## 2. 分阶段执行清单

每个阶段必须满足收敛门后再进入下一阶段。若阶段失败，回退该阶段的增量修改，不回退用户既有 V3.0 工作树。

## P0：冻结行为合同和建立执行状态

### 修改范围

- `迭代升级/当前架构升级状态.yaml`（新增）
- `tests/test_pipeline_characterization.py`（新增）
- `tests/test_architecture_boundaries.py`（先建基础版）
- 本清单只勾选状态，不重写用户问题或版本历史

### 工作

- [x] 创建当前状态 YAML：change_id、phase、completed、blockers、next、validation、touched_files。
- [x] 记录开工时 `git status --short` 和 227 项测试基线，不把完整命令输出复制进时间线。
- [x] 为 `process_video/run_all` 的公开参数、返回字段、事件级别、取消行为建立 characterization tests。
- [x] 固定 Document v2、`video-study://`、CloudPayload 阻断旧大 prompt、VLM no-match/真实 failure 区分。
- [x] 固定现有 Workspace 读取兼容行为和桌面 cached result 行为。
- [x] 测试全部使用临时目录、fake provider 和离线 fixture。

### 收敛门

- [x] 当前实现不改行为。
- [x] 完整测试不少于 227 项且通过。
- [x] 没有真实 ASR/VLM/cloud/Word 调用。

## P1：执行合同骨架，不切生产路径

### 新增文件

```text
src/video_study/execution/__init__.py
src/video_study/execution/context.py
src/video_study/execution/contracts.py
src/video_study/execution/artifacts.py
src/video_study/execution/cache.py
src/video_study/execution/ports.py
src/video_study/execution/registry.py
src/video_study/execution/runner.py
src/video_study/execution/bootstrap.py
tests/test_execution_context.py
tests/test_execution_registry.py
tests/test_workspace_artifacts.py
tests/test_workspace_cache.py
tests/test_pipeline_runner.py
```

### 工作

- [x] 实现 3–5 节定义的最小合同。
- [x] `StepRegistry` 显式注册，导入时不得扫描文件系统或动态加载插件。
- [x] DAG 校验：ID 唯一、依赖存在、无环、Artifact 单一生产者、输出路径不冲突。
- [x] Runner 测试覆盖 cached/succeeded/degraded/skipped/failed/cancelled。
- [x] Context 序列化测试断言 API Key 不出现。
- [x] 先使用 fake Step/fake ArtifactStore；本阶段不切换 `pipeline.py`。

### 收敛门

- [x] 新合同测试全部通过。
- [x] 现有生产路径和 227 项基线仍通过。
- [x] 新目录没有依赖 tkinter、OpenAI、subprocess 或具体模型模块。

## P2：ArtifactStore、WorkspaceCatalog 和 WorkspaceCache

### 修改范围

- `execution/artifacts.py`
- `execution/cache.py`
- `media.py` 中 Manifest 创建逻辑逐步移入 WorkspaceCatalog
- `desktop.py` 的 cached/clear/cleanup 辅助逻辑
- `localplay.py`
- `aggregate.py` 的 document 定位

### 工作

- [x] 建立 4.2 Artifact 注册表和 validator。
- [x] 建立 JSON 规范化 hash；大文件保存首次摘要并以 size/mtime 快速复核，变化时重算。
- [x] 建立 staging、atomic commit、CacheRecord 和稳定 miss reason。
- [x] 建立 Workspace lease 和安全路径检查。
- [x] 建立 LegacyArtifactAdapter，读取旧 manifest/transcript/frames/document。
- [x] `cached_result_for_video`、`clear_workspace_cache`、Desktop `_cleanup`、localplay 查找、aggregate document 定位改用 WorkspaceCatalog；随后从 Desktop 删除重复实现。
- [x] 增加损坏 JSON、缺失图片、缺失输出、陈旧锁、危险清理路径测试。

### 收敛门

- [x] Desktop/Localplay/Aggregate 不再硬编码 `workspace/*/manifest.json` 和 `knowledge/document.json`。
- [x] 旧 Workspace fixture 能被只读认领，不重跑模型。
- [x] 失败提交不会破坏旧 Artifact。
- [x] 清理操作继续保留源视频和最终 output，拒绝项目根、用户目录和磁盘根。

## P3：中间件端口和 composition root

### 修改范围

- `execution/ports.py`
- `execution/bootstrap.py`
- `media.py`、`asr.py`、`providers.py`、`knowledge/vision_providers.py`、`render.py` 作为具体 adapter
- `utils.py` 的进程执行能力

### 工作

- [x] 为现有函数增加薄 adapter，不先大规模移动算法代码。
- [x] 把 subprocess 的取消、超时、stderr 脱敏和进程树终止统一到 ProcessPort 实现。
- [x] Cloud client 由 lazy factory 创建，CloudRequestBudget 从 Context 注入。
- [x] VLM provider/session 由 lazy factory 创建，不再从 settings 隐式寻找 callback/state。
- [x] Document adapter 封装 Node、Word 和内置 PDF 选择。
- [x] 为每个 port 建 fake，并建立“缓存命中不构造 adapter”测试。

### 收敛门

- [x] execution 和领域层没有第三方 SDK/系统进程直接依赖。
- [x] API Key 不进入日志、异常、repr 或 CacheRecord。
- [x] 不发真实云请求。

## P4：粗粒度 Runner 切换

### 新增 Step

```text
source.probe
audio.extract
transcript.process（临时组合 Step）
frames.process（临时组合 Step）
knowledge.process（临时组合 Step）
render.bundle（临时组合 Step）
```

### 工作

- [x] Step 先包装当前稳定函数，保持结果不变。
- [x] `pipeline.py` 改为：构建 Context → 调用 Runner → 返回兼容 DTO。
- [x] Manifest、事件、ETA 和最终结果汇总移交 Runner。
- [x] CLI/Desktop 继续调用 `run_all`，外部参数暂不改变。
- [x] 运行事件带 `run_id/step_id/code`，桌面中文文案保持现状。
- [x] 切换成功后立即删除 `process_video()` 内已迁移的重复编排，不保留 `use_new_runner` 长期开关。

### 收敛门

- [x] characterization tests 全部通过。
- [x] 完全缓存路径不初始化中间件。
- [x] 取消只清本次 staging/lease，不删除已成功 Artifact。
- [x] 主流程仍输出相同公开结果键。

## P5：音频、转写和关键帧细粒度化

### 工作

- [x] 将临时 `transcript.process` 拆为 `transcript.decode` 与 `transcript.normalize`。
- [x] 新增 `transcript/raw.json`，迁移旧 transcript 的 raw_text/规范化规则。
- [x] ASR provider 只负责解码；术语纠正、时间戳 clamp、SRT 写入进入 normalize Step/纯函数。
- [x] 将 `frames.process` 拆为 candidates/select，新增 candidates index。
- [x] `frames.py` 的选择算法保持纯函数，ffmpeg 采样经 MediaPort。
- [x] 从 `asr.py`、`frames.py` 删除全局缓存读取/签名/写回；只保留 adapter/领域能力所需 I/O。
- [x] ETA 由 Runner 根据 Step/任务组注册，缓存状态以 CacheDecision 为权威。

### 收敛门

- [x] 术语规则变化不重跑 decode。
- [x] 选择阈值变化不重跑候选帧采样。
- [x] engine/model 变化不重提音频。
- [x] ASR/Frames 旧缓存 fixture 可认领或只重跑最小范围。

## P6：知识 DAG、动态视觉任务和 `summarize` 拆分

### 新增/调整

```text
src/video_study/transcript.py
src/video_study/knowledge/content_profile.py
src/video_study/knowledge/text_analysis.py
src/video_study/knowledge/source_blocks.py
src/video_study/knowledge/offline_document.py
src/video_study/knowledge/document.py
src/video_study/execution/steps/knowledge.py
```

### 工作

- [x] 按架构合同第 7 节迁移 summarize 职责和所有调用方。
- [x] 把 knowledge.process 拆成 plan、visual jobs/evidence、frame semantics、CourseIR、units、selfcheck、document。
- [x] visual jobs 使用 Runner 动态任务组和逐任务 CacheRecord；复现 V3.0 的“首个 miss 才加载、批次熔断、历史失败不重告警”。
- [x] planning/organizing 的云端 provider 和预算从 Context 注入；不再通过 settings 放入 `_runtime_*`。
- [x] CourseIR/units/document 的 Artifact 指纹只包含声明过的输入和配置。
- [x] 用 DAG 重放替代 `refresh_cached_local_derivatives()`。
- [x] 所有调用方切到 DAG 后，删除 `knowledge/pipeline.py` 中的完整编排与 `refresh_cached_local_derivatives()`；文件无独立职责时直接删除。
- [x] 默认 CourseIR 云端异常只回退离线，不进入 `_qwen_summary`。
- [x] 默认路径停止使用 `_qwen_summary`、旧 validator/prompt；源码的最终删除统一留给 P9 的兼容债务门。
- [x] 移除知识领域函数内部的 cache_path 参数；缓存由 Step 层统一处理。
- [x] `rg 'summarize import|summarize\.' src tests` 只剩待迁移测试后，迁移测试目标并删除 `summarize.py`。

### 收敛门

- [x] `rg 'summarize import|summarize\.' src` 无结果。
- [x] `Test-Path src/video_study/summarize.py` 为 false，且不存在仅为旧文件保活的测试。
- [x] `rg '_runtime_|_event_callback|_progress_event_callback' src/video_study/knowledge` 无核心生产路径结果。
- [x] 本地视觉完全命中不 preflight、不 cold load。
- [x] cloud 全流程仍共享请求上限，离线测试请求数为 0。
- [x] Document v2、视觉证据、来源 ID 和 token 账本保持。

## P7：Canonical Document v2、渲染和聚合解耦

### 工作

- [x] Markdown、Word、PDF renderer 直接消费 Document v2/content_blocks。
- [x] 更新 `scripts/render_docx.mjs` 读取 v2，移除生成临时 v1 `document.json` 的路径。
- [x] Aggregate 使用 canonical Document reader/view，不先整体转为 legacy dict。
- [x] Aggregate 的 document 写入和三端渲染复用 ArtifactStore/DocumentPort，不再手写第二套 bundle 编排。
- [x] 所有新 document 固定写 Schema v2；保留旧 v1 的只读迁移测试。
- [x] 验证旧 Workspace 后删除 Document v1 生成开关和不再需要的 legacy view 写路径。

### 收敛门

- [x] 持久化 document 中旧 explanation/details/steps 等正文副本不再出现。
- [x] 三端内容块映射一致，图片紧邻、来源链接和时间戳一致。
- [x] v1 fixture 可读，但生产路径只写 v2。

## P8：Desktop/Application 边界与 Desktop 旧编排删除

### P8.1 Application 用例层

新增：

```text
src/video_study/application/__init__.py
src/video_study/application/requests.py
src/video_study/application/processing.py
tests/test_processing_service.py
```

- [x] 定义 `ProcessingRequest/ProcessingResult/ProcessingHandle/AggregateRequest/CloudAuthorization`，字段稳定、不可变、无 Tk 类型。
- [x] `ProcessingService` 提供：缓存查询、单/批视频处理、聚合、取消、单视频 Workspace 删除和全量安全清理。
- [x] 把 Desktop 与 CLI 重复的 cloud runtime 解析收敛到 Application/composition root；API Key 保持 `repr=False` 且不进入 DTO 输出。
- [x] 把 `force/force_asr/force_summary/cloud_summary` 兼容参数翻译为显式目标 Step/RerunPolicy；新 Controller 不再拼动态 settings。
- [x] Aggregate 变为 Application use case，复用 Document/Artifact/Render 服务；Desktop 不再直接调用 `aggregate_documents` 或拼 cloud settings。
- [x] `pipeline.py` 继续为 CLI/旧调用方提供兼容 facade，但 Desktop 直接依赖 `ProcessingService`。

### P8.2 Desktop Controller 与状态机

新增：

```text
src/video_study/desktop/controller.py
src/video_study/desktop/models.py
tests/test_desktop_controller.py
```

- [x] 把 `QueueItem` 移到 `desktop/models.py`，仅保留 path、selected、status、stage、progress、message 和公开 result；删除 Runner/ETA/provider/Thread 引用。
- [x] 定义唯一 `DesktopState` 和 command：add/remove/select/toggle/start/cancel/delete/clear/aggregate。
- [x] 用状态转换表实现 `idle/preparing/running/cancelling/completed/failed/cancelled`；非法 command 返回可展示错误，不让 View 自己判断业务互斥。
- [x] 把现有 `DesktopApp.start/_run/cancel/_finish/_cleanup` 中的队列和执行逻辑迁入 Controller/Application Service。
- [x] Controller 保存 `ProcessingHandle` 并通过它取消；取消请求幂等，`cancelling` 状态不能再次启动或重复清理。
- [x] ETA/任务进度属于 ProcessingHandle 或 Controller session；View 的 QueueItem 不再持有 `EtaEstimator`。
- [x] 聚合只读取已完成的 `ProcessingResult`；单视频失败、取消和缓存命中的状态转换有独立测试。
- [x] Controller 测试全部使用 fake service/fake handle，在没有 Tk root、模型、ffmpeg、Node、Word 和网络的环境下运行。

### P8.3 Tk View 拆分

目标：

```text
src/video_study/desktop/__init__.py
src/video_study/desktop/view.py
```

- [x] 将现有 `desktop.py` 转换为 package；`desktop/__init__.py` 继续导出 `launch_desktop`，CLI import 不变。
- [x] `view.py` 只保留 Tk 控件构建、样式、布局、文件选择、messagebox、`root.after` dispatcher 和状态渲染。
- [x] View 的按钮事件只调用 Controller command，不直接调用 `run_all`、Aggregate、WorkspaceCatalog、provider、Manifest/document JSON 或配置文件 I/O。
- [x] 后台线程/ProcessingHandle 只能产生 `UiEvent`；任何 Tk 变量、widget 或 messagebox 调用都必须回到主线程。
- [x] `format_duration/format_eta`、颜色和短日志格式可保留为纯 presenter 函数，但不得读取业务 Artifact。
- [x] 保持现有人民币红/米白、绿色成功、两行日志、响应式滚动、高 DPI、标题版本和控件文案；本 Phase 不顺手重做 UI。
- [x] 通过固定 ViewModel/UiEvent fixture 测试展示映射，避免对像素布局做脆弱单测。

### P8.4 设置、凭据和 Windows 平台边界

新增：

```text
src/video_study/desktop/settings.py
tests/test_desktop_settings.py
```

- [x] 把 `validate_desktop_settings`、内容/视觉档位配置编译、设置原子保存和 API 凭据保存移入窄接口。
- [x] View 只提交用户输入 DTO；settings service 返回已校验的显示/请求配置，不向 Controller 暴露 `.env` 内容。
- [x] “记住密钥”保持用户显式选择；日志、异常、UiEvent 和测试快照不得包含真实密钥。
- [x] `os.startfile`、文件选择和 messagebox 留在 View/Windows 平台边界；Application/Controller 不直接调用。
- [x] `video-study://` 注册和播放继续由 `localplay.py`/WorkspaceCatalog 负责，不迁入 Controller。

### P8.5 删除旧职责和重复路径

- [x] `cached_result_for_video`、`clear_workspace_cache`、Desktop `_cleanup` 的业务实现删除，调用改为 ProcessingService/WorkspaceCatalog。
- [x] 删除 Desktop 中的 `_runtime_cloud_config`、直接 `run_all`/aggregate 调用和处理线程业务逻辑。
- [x] 完成 import 搜索，确认不存在 `desktop.py` 与 `desktop/` 双实现、旧 `DesktopApp` 复制或无调用 Controller。

### 收敛门

- [x] `tests/test_desktop_controller.py` 覆盖正常、缓存命中、失败、取消、重复取消、聚合和清理状态。
- [x] Desktop View 不导入 `knowledge.*`、providers、Runner、Step、Workspace JSON 实现或 `subprocess`。
- [x] Controller 不导入 tkinter、messagebox、filedialog、`os.startfile` 或供应商 SDK。
- [x] Application/Controller 不从后台线程直接操作任何 Tk 对象。
- [x] CLI 和 `from video_study.desktop import launch_desktop` 保持可用。
- [x] 现有 Desktop 功能和视觉表现通过手工验收，不引入 UI 功能重写。
- [x] 项目只有一个生产 Runner 和一个 StepRegistry。
- [x] 没有无删除阶段的 migration flag/adapter。

## P9：兼容债务和僵尸代码清理

### 删除候选及门槛

- [x] `knowledge/profiling.py`、`selection.py` 及对应 prompt/test：AST/运行调用图确认仅剩自身测试后删除；LessonPlan 测试覆盖对应默认能力。
- [x] `legacy` cloud payload 和 `_qwen_summary`：CourseIR 失败离线保留测试通过后删除。
- [x] VLM `per_call`：session/cache/OOM/取消测试及必要的一次本地验收通过后删除。
- [x] legacy ETA：Runner 任务图和桌面 ETA 测试通过后删除。
- [x] Document v1 写模式：v1 只读迁移和 v2 三端验收通过后删除。
- [x] 旧 `runtime-events.json` 写路径：JSONL/run state 已覆盖且无读取方后删除。
- [x] `LegacyArtifactAdapter`：完成约定的旧 Workspace fixture/真实缓存认领并在执行事实记录迁移版本后，可缩减为必要只读迁移；不得删除仍需读取的 v1 artifact 支持。

### 配置与元数据

- [x] `config.py` 删除已退役枚举值，配置缺省直接采用唯一默认合同。
- [x] `config.yaml/api.yaml` 删除无选择意义的 legacy mode 开关，只保留真实用户可配置项。
- [x] `pyproject.toml` 的 distribution name/description 去掉 `demo`；保留 Python package `video_study` 和 CLI `video-study`。
- [x] 如需重新 editable install，先说明会修改本地开发环境；本轮无需重装，未下载模型或升级依赖。

### 收敛门

- [x] `rg 'legacy|per_call|eta_mode|schema_version: 1|video-study-demo|summary.*demo'` 的剩余结果逐条解释为必要只读兼容、测试 fixture 或删除。
- [x] 不以“以后也许有用”为理由保留不可达源码。
- [x] 不删除 no-speech、本地离线、v1 读取、PDF fallback 等真实能力。

## P10：Agent 协作、故障定位和架构守卫

### 新增/更新

```text
docs/architecture/module-boundaries.yaml
docs/architecture/pipeline-steps.yaml
docs/diagnostics/problem-index.yaml
scripts/diagnose_workspace.py
tests/test_architecture_boundaries.py
tests/test_documentation_contracts.py
MEMORY.md
迭代升级/AI执行入口.md
docs/开发文档.md
docs/业务文档.md（仅用户可见行为发生变化时）
```

### 工作

- [x] `module-boundaries.yaml` 记录 owner、公开接口、允许/禁止依赖、Artifact、Step、测试。
- [x] `pipeline-steps.yaml` 从 StepRegistry 导出或由合同测试校验，不能成为另一份手工真相。
- [x] `problem-index.yaml` 以稳定 error code 映射 owner、先查 Artifact、测试和安全重跑范围。
- [x] `diagnose_workspace.py` 只读输出：run_id、最后失败 Step、cache reason、Artifact 状态、建议重跑 Step；不读取秘密、不调用模型、不修改 Workspace。
- [x] 当前执行状态只保留 active phase/blocker/next，不复制推理过程。
- [x] AI执行入口收敛为当前基线、活动清单、直接行动和边界；完成历史放执行事实。
- [x] 文档合同测试验证链接、文件、Step、error code、owner、测试路径都存在。
- [x] AST 边界测试阻止 knowledge→summarize/pipeline/desktop、desktop→knowledge/provider、业务模块→subprocess/OpenAI 的回归。

### Agent 标准定位流程

1. 读 `MEMORY.md` 和 AI执行入口。
2. 读当前架构升级状态。
3. 运行只读 workspace diagnose。
4. 按 `step_id + error_code` 查问题索引。
5. 只读 owner、依赖和对应测试。
6. 先添加失败回归测试。
7. 修改最小责任模块。
8. 按 Registry 影响范围运行目标测试。
9. 最后运行完整离线验收。

### 收敛门

- [x] 新 Agent 不需读完整执行事实或全仓源码就能定位一个已编码故障。
- [x] 文档没有复制当前版本实现细节造成多处同步。
- [x] 架构违规由测试阻止，而不是只靠约定。

## P11：最终验收、迁移和交付

### 自动化验收

- [x] `.venv\Scripts\python.exe -m unittest discover -s tests`
- [x] 对关键架构、缓存、选择性失效测试启用 warnings-as-errors。
- [x] `.venv\Scripts\python.exe -m compileall -q src tests`
- [x] `node --check scripts/render_docx.mjs`
- [x] `.venv\Scripts\python.exe -m pip check`
- [x] `git diff --check`
- [x] AST/module/document contracts 全部通过。
- [x] 运行 `rg` 清理门检查并逐条审核剩余兼容项。

### 离线集成验收

- [x] 新临时 Workspace 完成一次 fake/小 fixture 全链路。
- [x] 现有 V3.0 Workspace 完成一次无模型初始化的认领和缓存重渲染。
- [x] 验证 JSON → Markdown → Word → PDF。
- [x] 验证 Document v2/content_blocks 三端一致。
- [x] 验证 `video-study://` 仍解析到真实视频和时间戳，不打开网页。
- [x] 验证完全缓存、局部失效、损坏缓存、取消、失败恢复和 Workspace lease。
- [x] 验证云未授权时 CloudPort 构造次数为 0、请求次数为 0。

### 真实运行限制

- 默认不发真实云端请求；若最终确需云端验收，必须重新取得用户对数据、端点、模型链、次数和预算的明确授权。
- 不下载/重装模型。
- 如本次代码实际修改 VLM session/runner 边界，可在用户同意后最多做一次真实本地 VLM 验收；否则复用 V3.0 已有结果和离线 fake 测试。
- 不无理由重跑长视频。

### 手工桌面验收

- [x] 启动列表为空，视频由用户选择真实绝对路径。
- [x] 处理、取消、缓存恢复、清理、打开产物、聚合不回归。
- [x] ETA、两行日志和降级提示语义正确。
- [x] 最小窗口、高 DPI、中文路径不因控制器抽离受影响。

### 文档与记录

- [x] 将最终实现和验证摘要追加到 `执行事实.yaml` 的一个新键；不要写 Agent 推理。
- [x] 更新开发文档和架构图为最终默认路径，删除过渡说明。
- [x] 删除 `V4.0架构合同.md` 第 1 节迁移基线并同步导航；实施证据只留在 `执行事实.yaml`，第 2–7 节继续作为有效合同。
- [x] `AI执行入口.md` 将本清单标为完成并指向执行事实。
- [x] `迭代记录与问题.md` 已有唯一 V4.0 用户批准记录；实施完成不得追加或改写 V4.0，未来只有用户批准新的命名版本时才新增记录。

## 3. 每阶段测试预算

为避免执行 Agent 每改一个文件就反复跑全量：

- P0：完整基线一次。
- P1：execution 新测试 + 完整测试一次。
- P2：artifact/cache/desktop/localplay/aggregate 目标测试，阶段末完整一次。
- P3：ports/providers/runtime/vision/render 目标测试，阶段末完整一次。
- P4：pipeline/desktop/characterization，阶段末完整一次。
- P5：asr/frames/cache/progress，阶段末完整一次。
- P6：knowledge/cloud payload/visual/summarize 替代测试，阶段末完整一次。
- P7：adapter/render/aggregate/PDF，阶段末完整一次。
- P8–P10：边界、配置、桌面和删除影响测试，阶段末完整一次。
- P11：最终完整验收一次；失败后只跑直接影响集，修复完成再做一次最终全量。

真实云端始终是 `0` 次默认预算；真实本地 VLM 不纳入普通自动测试。

## 4. 硬停止条件

出现以下情况必须停止当前阶段并先修复/汇报，不能带病进入下一阶段：

- Cache hit 会初始化 ASR/VLM/Cloud/Word provider。
- 取消或失败覆盖上一份有效 Artifact。
- 新旧 Runner 对同一 Workspace 同时写入。
- 云端降级绕过共享请求预算或重新启用旧大 prompt。
- Document v2 持久化重新出现多套正文事实源。
- source ID、时间戳、视觉图片路径或 `video-study://` 丢失。
- 执行状态/日志出现 API Key、请求头或完整秘密环境变量。
- 为修复一个 Step 必须从 Desktop 跨层修改多个无关业务模块，说明边界尚未收敛。
- 新增兼容开关却没有删除阶段和测试。
- 完整测试失败但原因未定位。

## 5. 回滚策略

- 回滚粒度是单个 P 阶段，不是整个用户工作树。
- P1–P3 尚未切生产路径，可删除新增合同代码而不影响 V3.0。
- P4 切换前必须让 characterization tests 覆盖现有公开合同；切换失败只回退门面增量。
- Artifact 迁移始终为单向读取/认领，不批量移动或删除旧 Workspace。
- staging/CacheRecord Schema 带版本；新元数据不可读时旧业务 Artifact 仍可由 LegacyAdapter 读取。
- 不用长期总开关回滚；保留上一阶段可工作的代码状态和小步差异即可。
- 任何云端失败都优先保留本地产物，不用旧大 payload 作为回滚。

## 6. 执行 Agent 交付格式

每完成一个阶段，在当前对话和 `当前架构升级状态.yaml` 中只报告：

1. 完成的 phase 和 checklist 项。
2. 新增/修改/删除的责任模块。
3. 执行的测试及结果。
4. 缓存/Artifact 迁移结果。
5. 是否发生真实本地模型或云端调用及其授权依据。
6. 下一阶段和明确风险。

不要把推理过程、临时调查、环境噪声、文件全文或测试明细写入用户版本时间线。完成本清单前不得宣称架构升级已经交付。
