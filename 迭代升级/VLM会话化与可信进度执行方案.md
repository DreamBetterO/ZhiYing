# VLM 会话化、视觉任务收敛与可信进度执行方案

状态：已被综合方案吸收，保留作为 VLM/ETA 专项调查依据；后续实施以 [统一课程 IR 与高效多模态流水线执行方案](统一课程IR与高效多模态流水线执行方案.md) 为准
调查日期：2026-08-12
建议版本：小版本（建议名 `V2.2.1`，最终版本名和类型由用户确认）

## 1. 结论

当前三个问题不是单纯调参能解决：

1. VLM 慢的主要原因不是“一次处理四张候选图”，而是**每个视觉问题都新建 Python/CUDA 子进程并重新加载 Qwen3-VL**。应先改为“视觉阶段单进程、模型只加载一次、任务逐个落盘”，而不是在 8 GB 显存上直接增加真正的并行 batch。
2. 大量“失效”包含两类情况：旧代码把正常证据拒绝记成 warning；更深层的问题是视觉问题本身数量膨胀、文本残缺、成功条件与单图选择接口不一致。当前源码已修正第一类告警，但必须继续修正视觉任务规划。
3. ETA 使用 `elapsed * (100-progress) / progress`，把离散百分比当作均匀工作量；视觉阶段又没有百分比更新，导致 ETA 既严重偏小又长期冻结。应改为阶段/任务单元估算。

本次不需要更换大模型、不引入 LangGraph、不并行启动多个 VLM、不增加本机服务或端口。

## 2. 已核实证据

样例：`workspace/test4-1-da2230dd85c7`，视频时长 1302.167 秒。

| 现象 | 实际证据 | 根因 |
|---|---:|---|
| VLM 耗时高 | 19 次 compare，18:05:03–18:10:58，约 355 秒 | `LocalQwenVLProvider._invoke()` 每次启动 runner；runner 的 `_generate()` 每次调用 `_load_model()` |
| 调用数膨胀 | 11 个视觉知识点生成 19 个问题 | `_limit_visual_questions()` 限制的是知识点数，不是问题/推理数；增强档把 4 个“procedure”知识点各扩成 3 问 |
| 无效工作 | 推理时 9 个问题先 select，课程级仲裁后只保留 7 个 | 相同场景在推理后才去重，2 次完整调用结果被丢弃 |
| no_match 偏多 | 10 个 `vlm_criteria_rejected` | 问题直接包含残缺 ASR 标题；单图选择却要求“区分关键步骤或前后状态” |
| 旧告警过量 | 既有日志把 10 个正常 `criteria_rejected` 写成 warning | 旧事件分级错误；当前源码已改为 info，两个定点测试通过 |
| ETA 严重失真 | 78% 时已用 76 秒，旧公式估 21.4 秒，实际剩余 365 秒 | 百分比不是工时权重；视觉阶段 364 秒内无 percent 更新 |

额外的合同冲突：当前 VLM compare 只返回一个 `selected_candidate_id`，但 `progression_grid` 会要求 3 张图，并把它实现成三个近似问题。这既不是序列选择，也无法保证三张图分别对应前、中、后状态。

## 3. 目标与不做事项

### 3.1 目标

- 同一视频的视觉阶段只加载一次 Qwen3-VL。
- 视觉问题总量受“实际 VLM 作业数”约束，而不是受知识点数间接约束。
- 只把系统推理失败记为 warning；模型按证据门拒绝图片是正常结果。
- ETA 能随时间倒计时；没有足够依据时显示“估算中”，不提供虚假精确值。
- 保持 JSON → Markdown → Word → PDF、时间戳、`video-study://` 与原生桌面边界。

### 3.2 不做

- 不升级 4B/8B 模型，不加量化栈。
- 不运行多个并行 VLM worker；RTX 4060 Laptop 8 GB 不适合多任务四图并行。
- 首版不做 Transformers 真 batch。真 batch 会把多问题的视觉 token 和 KV cache 同时压入显存，收益不确定而 OOM 风险明确。
- 不做常驻 HTTP 服务、端口监听或后台守护进程。
- 不让 VLM 重新承担课程规划、知识扩写或最终写作。

## 4. 视觉任务先收敛，再优化执行

### 4.1 保留有效的模型计划，不无条件覆盖

当前 `_assign_depth_contracts()` 会创建新的 `_visual_contract()`，只保留原 `question`，从而丢失云端计划中可能有效的 role、success_criteria、target_count 和 sequence_mode。

改为：

1. 对已有 `visual_need` 做字段级校验。
2. 合法字段保留；只补齐缺失或不合法字段。
3. 离线启发式合同仅作为 fallback，不得无条件覆盖模型已生成且通过校验的像素问题。

合同校验最低要求：

- question 应包含明确可见对象或关系，建议 8–80 个汉字。
- 不接受主要由“这个、它、所以、然后、看了没有”等指代词组成的问题。
- success_criteria 必须能从像素/OCR 直接判定，不能是“能够理解”“能够回答”等抽象目标。
- `single` 只能要求单图可见事实；需要前后状态时必须有两个明确、互斥的状态问题，不能用一张图满足“前后状态”。

无法可靠修复的问题直接标为 `planning_rejected`，不调用 VLM，也不记 warning。

### 4.2 target_count 不再等于推理问题数

默认每个知识点只生成一个 primary 视觉任务。只有满足以下条件才生成第二个任务：

- 课程计划明确要求 comparison_pair 或真实步骤序列；
- 第二张图有独立、可核验、与第一张不重复的成功条件；
- primary 已找到证据，且第二张图确实能增加教学信息；
- 尚未耗尽全视频作业预算。

首版禁用自动 `progression_grid=3`。在接口仍只支持单个 candidate ID 时，最多退化为一个关键状态图；后续若要恢复网格，需先增加多图序列合同与顺序校验。

### 4.3 限制“VLM 作业数”，不是视觉知识点数

建议自动预算（compare 作业，detail 另计）：

| 视频时长 | minimal | balanced | enhanced |
|---|---:|---:|---:|
| ≤15 分钟 | 2 | 4 | 6 |
| 15–45 分钟 | 3 | 6 | 8 |
| >45 分钟 | 4 | 8 | 12 |

detail 作业上限为 compare 预算的 25%，至少 1、最多 3。所有 compare/detail 都计入实际调用统计。

优先级建议：

`视觉依赖强度 × 知识点重要度 × 像素可回答性 × 候选质量/多样性 - 合同含糊惩罚 - 已占用场景惩罚`

1302 秒增强档样例的 compare 上限应为 8，而不是当前 19。

### 4.4 在推理前做场景调度

现有全局仲裁发生在推理之后，导致已经付费的结果被丢弃。调整为：

1. 先为所有有效任务召回、聚类候选。
2. 对候选集合完全相同或高度重叠的任务做价值排序。
3. 已入选的场景进入 reservation；后续任务优先移除该场景并补入下一候选。
4. 移除后候选不足时正常 no_match，不再调用 VLM。
5. 最终全局仲裁仍保留，作为正确性保护而不是主要去重手段。

不允许仅凭时间戳复用同一图片到多个知识点。若一个场景确实能服务多个知识点，只在一个主知识点处放图，并在结构化数据中保留关联知识点 ID。

## 5. 用“单次会话”替代盲目大 batch

### 5.1 首选实现

每个视频的视觉阶段启动一个短生命周期 runner：

```text
主进程准备全部 VisualJob
→ 启动一次 qwen_vl_runner
→ runner 加载一次 processor/model
→ 按优先级逐个处理 job（默认 micro_batch_size=1）
→ 每个 job 原子写入独立结果文件
→ 全部完成后写 session.done 并 os._exit(0)
```

这仍是本地子进程，不是服务，不监听端口。ASR 与 VLM 继续串行；完成视觉阶段后立即释放显存。

### 5.2 为什么首版不直接真 batch

- 当前每个 compare 最多 4 图、单图视觉预算较高；多问题一起 padding 会显著提高峰值显存。
- 既有实测峰值已接近 7 GB，8 GB 显存没有足够余量安全运行 batch 2×4 图。
- 当前最大浪费是反复加载模型和过多问题，不是单次 generate 没有并行。

后续只有在会话化实测后，才可对“单图 detail”尝试 `micro_batch_size=2`，并要求总视觉 token 预算、峰值显存和 JSON 有效率均通过门槛。默认仍为 1。

### 5.3 会话协议

建议增加内部 `VisualJob`：

```json
{
  "job_id": "vq_019_01:compare",
  "action": "compare",
  "question": {},
  "contract": {},
  "candidates": [],
  "timeout_seconds": 60,
  "cache_key": "..."
}
```

runner 接收一个 jobs manifest，结果写到指定 output_dir：

- `session.ready.json`：模型、设备、加载耗时、可用显存。
- `job_<id>.json`：单任务结果与 duration/peak_vram。
- `job_<id>.error.json`：OOM、timeout、协议错误。
- `session.done.json`：完成/失败/取消计数。

使用临时文件写完后 `replace`，防止主进程读到半个 JSON。主进程继续负责取消和总超时；取消时终止整个会话进程树。

runner 崩溃时保留已完成结果，只对未完成且属于 transient 的任务重启一次会话；不得从第一题全部重跑。

### 5.4 provider 兼容与回滚

扩展 provider 为批任务接口，例如：

```text
run_jobs(jobs) -> iterator[VisualJobResult]
```

保留现有 `compare_candidates()` / `extract_selected()` 适配器用于单测和回滚。配置增加：

```yaml
visual_evidence:
  execution_mode: session       # session | per_call
  max_compare_jobs: auto
  max_detail_jobs: auto
  micro_batch_size: 1
```

`per_call` 只作为回滚路径，不作为默认性能方案。

## 6. 细粒度缓存

当前 visual-evidence 使用全局签名，任何计划或设置变化都可能重跑全部问题。升级为逐 job cache：

cache key 至少包含：

- 清洗后的 question、contract、action；
- candidate ID、文件指纹和场景 ID；
- OCR 文本摘要；
- 模型目录/模型标识、runner 语义版本、视觉 token 配置；
- 结果校验版本。

顶层文件继续输出最终 `visual_evidence`，同时保存 `job_results` 映射。只重跑 cache miss 或 transient failure；正常 no_match 也是可缓存结果。

旧缓存加载时按当前 source taxonomy 重新分类，不因旧事件曾使用 warning 就再次弹降级；事件中写入 app/pipeline/cache/runner 版本指纹，便于识别“旧运行产物与当前源码不一致”。

## 7. 失效与告警重新定义

### 7.1 三类状态

| 类别 | 例子 | UI/任务结果 |
|---|---|---|
| expected_no_match | 候选不足、问题合同被拒、VLM 主动拒绝、criteria 不满足、场景去重 | info；不进入 degradations |
| recovered_quality | detail 失败但 compare 已有完整像素证据 | info；保留证据 |
| inference_failure | runner 崩溃/超时、OOM 重试仍失败、非法 candidate ID、无法解析协议 | 单问题日志可为 info/error detail；任务级只汇总一个 warning |

`local_vlm_enabled=auto` 时能力不存在属于“自动能力未启用”，只显示一次 info；只有用户显式要求 `true` 且能力不可用，才作为 warning。

### 7.2 当前源码与旧产物

当前源码已经把 `vlm_criteria_rejected`、`vlm_rejected`、`vlm_no_candidate` 等正常拒绝从 warning 中移除，并有定点测试。既有 `test4-1` 日志中的 10 条 warning 来自修复前运行，不能用它判断当前 warning 分类仍未修复。

如果用户在新运行中仍看到同类 warning，先核对事件版本指纹和启动入口，不要重新跑 VLM；优先判断是否运行了旧复制目录、旧打包产物或读取了旧 runtime-events。

### 7.3 日志展示

视觉阶段使用进度型消息：

- `视觉任务 3/8：候选核验完成，入选 2，正常拒绝 1`
- 不再逐条显示“VLM 失效”。
- 真失败时最终只显示：`视觉推理 1/8 项未完成（timeout），其余结果已保留`。

## 8. ETA 改为阶段/任务估算

### 8.1 删除旧公式

删除桌面端基于 `shown percent` 的：

```text
eta = elapsed * (100 - shown) / shown
```

百分比只用于大致进度展示，不再参与 ETA。

### 8.2 新的 ProgressEvent

为耗时阶段提供结构化进度：

```json
{
  "stage": "visual",
  "phase": "compare",
  "completed_units": 3,
  "total_units": 8,
  "unit_duration_seconds": 6.8,
  "cache_hit": false,
  "cold_start": false
}
```

至少覆盖：

- ASR：已处理音频秒数 / 总音频秒数；
- frames：已处理候选 / 预计候选；
- visual：session load、compare 完成数、detail 完成数；
- cloud planning/organizing：开始/完成与本机历史中位数；
- render：开始/完成与渲染方式。

### 8.3 估算器

新增独立 `EtaEstimator`，规则：

1. 当前阶段剩余时间 + 后续阶段历史中位数。
2. VLM 冷启动与 warm job 分开统计；不能用第一题总耗时代表后续每题。
3. warm job 使用最近 5 次中位数；有本机历史时使用相同硬件/模型/档位的最近 20 次样本。
4. 少于一个有效样本且无历史时返回 `None`，UI 显示“估算中”。
5. 估算产生后保存 `eta_deadline`；桌面 `_tick()` 每秒用 deadline 减当前 monotonic time，ETA 必须倒计时。
6. 新阶段、缓存命中、任务数变化时允许重算；用轻量平滑避免每题大幅跳动。
7. 多次样本后可显示 P25–P75 范围；样本不足只显示“约 mm:ss”。

本机历史只保存耗时、硬件/模型/阶段指纹和 cache 状态，不保存视频名、转写、图片或用户内容。建议存入已 ignore 的 `workspace/.runtime-metrics.json`，限制最近 20 组并原子写入。

### 8.4 自动处理双阶段

当前 `auto` 把 local/cloud 机械各占 50%，但第二次运行会命中本地缓存，工作量并不相等。改为显式 phase plan：

```text
local_prepare: audio/asr/frames/offline document
cloud_refine: cached inputs + planning/organizing + render
```

每个 phase 独立估算；没有历史时显示当前阶段“估算中”，不得从 50% 推算剩余时间。

## 9. 实施顺序

### 阶段 A：先修任务计划，不运行真实 VLM

1. 增加视觉合同 validator/normalizer。
2. 保留合法模型合同，只补缺失字段。
3. 把预算改成 compare/detail 作业总数。
4. 默认每个知识点一个 primary；禁用自动三图 progression。
5. 加入推理前场景 reservation。
6. 用现有 lesson-plan/visual-evidence 作为 fixture 做离线测试。

### 阶段 B：会话化 runner

1. 增加 VisualJob/VisualJobResult 与 `run_jobs`。
2. runner 一次加载模型、逐 job 原子落盘。
3. 完成结果可恢复；未完成 transient job 最多重启一次。
4. 增加逐 job cache。
5. 保留 `per_call` 回滚开关。

### 阶段 C：状态与 ETA

1. 固化 expected/recovered/failure taxonomy。
2. 事件增加版本指纹和 visual job 计数。
3. 新增 EtaEstimator 与本机耗时历史。
4. 桌面 ETA 改为 deadline 倒计时；未知时显示“估算中”。
5. auto 使用显式 phase，不再机械 50/50 估时。

### 阶段 D：一次受控实测

前三阶段的离线测试通过后，只对一个已有样例运行一次真实本地视觉恢复：

- 复用已有 ASR、帧、课程计划和云端正文；不新增云端请求。
- 不重复完整长视频链路。
- 记录 load_count、job_count、每 job 耗时、峰值显存、select/no_match/failure、最终重复场景数和 ETA 误差。

## 10. 文件级任务

| 文件 | 修改 |
|---|---|
| `src/video_study/knowledge/planning.py` | 合同保留/校验、单 primary、总 job 预算、禁用伪 progression |
| `src/video_study/knowledge/schema.py` | 增加可序列化的 job/结果或必要的任务字段；保持旧 JSON 兼容 |
| `src/video_study/knowledge/visual_retrieval.py` | 全局任务调度、场景 reservation、逐 job cache、最终仲裁 |
| `src/video_study/knowledge/vision_providers.py` | 增加 session `run_jobs`，保留单调用适配器 |
| `scripts/qwen_vl_runner.py` | jobs manifest、单次加载、逐任务原子结果、session ready/done |
| `src/video_study/knowledge/pipeline.py` | visual job 进度、统一告警汇总、版本指纹 |
| `src/video_study/pipeline.py` | 结构化阶段进度与 cache 命中信息 |
| `src/video_study/progress.py`（新） | EtaEstimator、历史样本和 deadline |
| `src/video_study/desktop.py` | 移除线性公式，消费 estimator，未知显示“估算中” |
| `config.yaml` / `.env.example` | 增加非密钥 execution/budget 配置；默认不改变云端权限 |

## 11. 测试与验收

### 11.1 只跑离线定点测试

- 1302 秒 enhanced fixture 的 compare job ≤ 8。
- `target_count=3` 不再自动产生三个近似问题。
- 指代词/截断标题不能进入 VLM；合法的模型视觉合同不会被启发式覆盖。
- 已预留场景从后续候选中移除，最终重复场景为 0。
- mock 3 个 job 时 `_load_model()` 只调用 1 次；第二个 job 失败不丢第一个结果。
- 正常 no_match 无 warning；多个真实 provider error 只产生一个任务级 warning。
- ETA 无样本时为 None；完成两个 warm job 后按剩余 job 数计算；`_tick()` 会倒计时；阶段切换正确重算。
- auto phase 不再从 50% 线性估时。

### 11.2 一次真实本地验收

在 `test4-1` 缓存样例上：

- `model_load_count = 1`，compare jobs ≤ 8，detail jobs ≤ 2。
- 无 OOM；若会话崩溃，已完成 job 不重跑。
- 视觉阶段相对既有 364 秒至少缩短 50%，或总时长 ≤ 180 秒。
- 正常证据拒绝不出现完成降级弹窗；真实失败仍有一次汇总警告。
- 不因减少调用恢复时间戳最近帧；最终图片仍必须通过像素证据门。
- 同一场景不重复插入；人工查看高优先级知识点的图文对应关系不低于旧产物。
- 无历史时 UI 显示“估算中”；有至少两个 warm job 后，ETA 会每秒下降并在 job 边界校正。

不要用“多跑几次看平均值”验收。一次真实本地运行足够；性能稳定性由 mock/离线 fixture 和逐 job 指标验证。

## 12. 回滚与停止条件

- session runner 异常可切回 `execution_mode: per_call`；正确性仍由 no_match 保护。
- ETA 估算器异常时显示“—/估算中”，不得恢复旧线性公式。
- 若会话化后仍频繁 OOM，保持 micro_batch_size=1，降低候选/视觉 token，不增加并行 worker。
- 若问题规范化后有效图明显减少，先检查合同 validator 是否过严；不得用时间最近帧补数量。
- 若一次模型加载仍占总视觉耗时的大头，可评估跨同一批视频复用进程，但这属于后续小版本，当前不做。

## 13. 版本判断

建议归类为小版本：主阶段顺序、模型职责、文档 schema 主体、桌面边界和兼容策略不变；变化集中在视觉内部调度、runner 生命周期、告警语义和进度估算。若实施时决定引入多图序列主合同或改变核心阶段顺序，应停止并重新按大版本评估迁移与回滚。
