# 统一课程 IR 与高效多模态流水线执行方案

状态：用户已批准为 `V3.0` 大版本，已按 P0–P6 完成实施与本地验收（2026-08-12）
调查日期：2026-08-12
版本：`V3.0` 大版本升级

直接实施入口：[V3.0 升级 Agent 执行任务书](V3.0升级Agent执行任务书.md)。本文保留产品判断与总体架构，具体阶段顺序、收敛门、本机环境和测试次数以任务书为准。实际交付结果见 [执行事实.yaml](执行事实.yaml) `v3_0_delivery_2026_08_12`。

## 1. 本方案解决的“3+1+2”问题

| 编号 | 用户问题 | 根因 | 方案核心 |
|---|---|---|---|
| 1 | VLM 逐条处理、速度慢 | 每题重启 Python/CUDA 并重新加载模型；问题数膨胀 | 任务收敛 + 单次模型会话 + 逐任务缓存 |
| 2 | VLM 过度“失效”警告 | 正常 no_match 曾被误报；上游又制造了低质量视觉问题 | 合同校验 + 三态结果 + 任务级单次告警 |
| 3 | 剩余时间严重不合理 | 用离散百分比线性外推，耗时阶段没有进度单元 | 阶段/任务 ETA + 本机历史 + deadline 倒计时 |
| 4 | 整体工作流效率低 | 规划、视觉、正文和渲染之间缺少统一中间合同 | 建立 CourseIR，所有阶段只消费必要投影 |
| 5 | 本地产物重复句子多 | 标题、paragraph、rule_list/steps 和兼容字段重复消费同一原文 | 原子 Claim 台账 + 单一内容所有权 + 去重质量门 |
| 6 | 云端输入格式和图片处理不清晰 | 当前使用冗长文字 prompt，计划和视觉信息重复；图片只在本地 | 紧凑 CloudPayload JSON；只传已核实视觉证据 ID/事实，不传图片像素 |

本方案不是继续添加模型，而是减少无效工作、建立唯一事实源。默认继续使用现有 ASR、Qwen3-VL-2B 和云端文本模型，不引入 LangGraph、向量库、大模型并发或本地服务。

## 2. 已核实的当前事实

### 2.1 视觉样例

`test4-1` 时长 1302.167 秒：

- 11 个视觉知识点生成 19 个视觉问题。
- 19 次 compare 分别启动 runner 并加载模型，视觉阶段约 364 秒。
- 推理阶段先 select 9 项，全局场景仲裁后只保留 7 项；2 次调用在推理后被丢弃。
- 10 项 `vlm_criteria_rejected` 主要来自问题残缺、成功条件与单图接口不一致。
- 进度到 78% 时已用 76 秒，旧 ETA 估计剩余 21.4 秒，实际剩余 365 秒。

### 2.2 本地重复并非渲染器偶发现象

当前离线组织路径把同一 `combined_text` 同时用于：

- `definition_or_conclusion`；
- 第一个 paragraph content block；
- 从相同文本抽取的 rule_list/steps；
- adapter 派生的 explanation/details/steps 兼容字段。

新渲染器通常优先 content_blocks，因此 explanation/details 的副本未必再次显示；但 paragraph 已包含完整原句，rule_list/steps 又原样摘取其中句子，标题还经常是正文开头的截断版本，所以用户仍能直接看到重复。

现有 `_clean_offline_fragments()` 只去除相邻且完全相同的片段，无法处理：

- “我再说一遍”后的近重复；
- 一整段正文与其中规则条目的包含重复；
- 标题与正文开头高度重合；
- ASR 轻微错字导致的近重复。

样例中 source segment 没有被多个知识点重复引用，因此本轮不要把重点放在“强制片段唯一归属”；主要重复发生在单个知识点的内容组装内部。

### 2.3 当前云端实际发送什么

当前云端不接收 Markdown、PDF 或图片像素：

1. planning：发送带 block ID/时间的压缩转写文本。
2. organizing：再次发送压缩转写文本，加上文字版 LessonPlan、FrameSemantic 和 VisualEvidence。
3. 云端返回结构化 JSON；本地再生成 document.json、Markdown、Word、PDF。

现有样例的 organizing 输入组成（不含固定指令）：

| 部分 | 字符量 |
|---|---:|
| source blocks | 7,732 |
| 文字版 LessonPlan | 18,418 |
| VisualEvidence 文本 | 5,055 |
| FrameSemantic | 486 |
| 合计 | 31,691 |

同一缓存构造的紧凑 CourseIR JSON 原型为 14,971 字符，约为上述组成的 47%。这只是字符比较，真实 token 取决于模型 tokenizer；但已经证明主要浪费来自冗长、重复的计划和视觉上下文，而不是 Markdown 与 JSON 的文件扩展名。

## 3. 目标架构

```text
原始视频
→ ASR + 不可变原始转写
→ SourceBlock / 重复标记
→ 课程计划、知识深度、视觉任务合同
→ 候选召回 + OCR + 单会话 VLM
→ CourseIR（Source + Unit + Claim + VisualEvidence）
→ 本地 Composer 或云端 Refiner
→ 确定性去重/证据校验
→ Canonical Document v2
→ Markdown → Word → PDF
```

核心原则：

- 原始转写只保留和追踪，不直接当成最终正文。
- 一个事实先成为 Claim，再且只进入一个主要内容块。
- 图片由本地像素证据决定，云端只决定已核实图片放在哪里、怎样简洁解释。
- Markdown/PDF 是渲染结果，不是模型输入。
- 进度、缓存和告警都围绕实际任务单元，而不是模糊百分比。

## 4. CourseIR：统一中间合同

建议新增内部 schema，不直接把现有 `document.json` 当作模型载荷：

```json
{
  "course": {"id": "...", "domain": "...", "form": "chart_analysis", "duration": 1302.2},
  "sources": [
    {"id": "b1", "start": 12.0, "end": 33.0, "text": "...", "segment_ids": ["seg_00001"]}
  ],
  "units": [
    {"id": "p1", "title": "...", "type": "rule", "depth": "deep", "source_ids": ["b1"]}
  ],
  "claims": [
    {"id": "c1", "unit_id": "p1", "kind": "condition", "text": "...", "source_ids": ["b1"], "origin": "audio_backed"}
  ],
  "visuals": [
    {"id": "v1", "unit_id": "p1", "frame_id": "f38", "role": "explain", "facts": ["..."], "answer": "..."}
  ]
}
```

CourseIR 是本地完整权威；CloudPayload 是它的最小投影，不包含：

- 本地绝对路径；
- 图片 hash、候选分数、no_match 详情；
- runtime events、设备信息、缓存签名；
- Markdown、DOCX、PDF；
- 重复的 explanation/details/content_blocks；
- base64 图片数据。

## 5. 本地正文去重方案

### 5.1 原始转写不删除，只标记重复

重复检测不能破坏来源追踪。SourceBlock 层只增加：

- `repeat_group_id`；
- `canonical_source_id`；
- `adds_new_information`。

完全重复或高相似复述可以在写作投影中只消费 canonical block，但原始 segment、SRT 和回看链接全部保留。

不需要新模型。使用资源友好的组合：

- 去空白/口头填充后的 exact hash；
- 包含关系；
- 中文字符 3-gram Jaccard；
- `SequenceMatcher`；
- 相邻时间和相同来源单元作为弱先验。

不能仅凭相似度删除带有新条件、例外、数字、步骤或正反例的句子。

### 5.2 建立 Claim 台账

把正文先拆成原子 Claim，类型至少包括：

- conclusion / definition；
- explanation / mechanism；
- condition / boundary；
- step；
- example；
- pitfall；
- visual_fact；
- model_aid。

每个 Claim 具有稳定 ID、来源 IDs、origin 和文本指纹。一个 Claim 只能有一个主要展示位置。

### 5.3 内容块互斥路由

Composer 按 Claim 类型生成 blocks：

- paragraph 只放概念关系和必要解释；
- rule_list 只放条件/规则，不再把同一句留在 paragraph；
- steps 只放动作序列；
- example/pitfall 各自独占；
- visual_group 只引用 VisualEvidence ID；
- understanding_tip 与课堂正文分离且限制长度。

不要为了满足 detail_level 数量机械生成多个 block。某个 deep 知识点来源只有一条规则时，可以只有 rule_list + visual_group，不必再复制一段 paragraph。

### 5.4 标题不再截断正文

TitleValidator：

- 6–28 个汉字优先；
- 应是名词短语、规则结论或明确问题；
- 不以“这个、它、所以、然后、来看”等指代/口语开头；
- 不以逗号、残缺连词结尾；
- 与正文开头的规范化重合率不应超过 0.65。

已有合法云端/计划标题应保留。本地 fallback 从显式领域词 + 规则/步骤线索生成短标题；无法生成时使用带时间的中性标题，不复制正文前 40 字。

### 5.5 输出前确定性 DedupGate

在云端或本地 Composer 之后统一执行，不能只依赖提示词：

1. 同 block exact duplicate：保留一条并合并来源。
2. list item 被 paragraph 完整包含：按 Claim 类型决定从 paragraph 删除该句或删除 list item。
3. 跨 block 相似度 ≥0.88 且无新增数字/条件/否定词：保留信息结构更明确的一条。
4. title-body 重合超阈值：重写/降级标题，不删正文证据。
5. 相邻知识点核心 Claim 高度重复且主题相同：合并知识点；否则仅报告质量问题，不自动跨主题删除。

质量报告增加：

- `duplicate_claim_count`；
- `containment_duplicate_count`；
- `title_body_overlap_count`；
- `near_duplicate_pairs`；
- `claims_without_source`。

## 6. Canonical Document v2 与兼容策略

当前 schema 同时保存 content_blocks 和 explanation/details/steps 等派生副本。建议 v2 以 content_blocks + source_refs 为唯一正文：

- document.json v2 不再持久化重复正文副本；
- Markdown/Word/PDF 三端只消费同一 canonical blocks；
- 旧 explanation/details 等仅由 `v2_to_legacy_view()` 在需要兼容时临时生成；
- v1 加载器先规范化为 v2，再渲染；不得维护两套独立写作逻辑。

这改变了主内容合同，因此建议按大版本处理，并提供迁移与回滚。

## 7. 云端载荷：不是 Markdown、PDF，也不是完整原始 JSON

### 7.1 格式选择结论

- PDF：最不适合。包含布局噪声，无法可靠引用 source/visual ID，且可能引入 OCR/解析开销。
- Markdown：适合人读和最终产出，但标题、列表标记、图片语法和重复正文会浪费输入；结构约束较弱。
- 当前完整 document.json：也不适合，文件包含大量本地路径、完整 transcript、figures、hash、runtime、兼容字段和重复 VisualEvidence。
- **紧凑 JSON CourseIR 投影：推荐。** 它的优势是精确字段、稳定 ID、可裁剪和易校验，不是因为“JSON 天然比 Markdown 少 token”。

### 7.2 CloudPayload

建议使用短而可读的字段，并删除空/default 字段：

```json
{
  "src": [{"id": "b1", "t": [12.0, 33.0], "text": "..."}],
  "units": [{"id": "p1", "title": "...", "type": "rule", "depth": "deep", "src": ["b1"]}],
  "visuals": [{"id": "v1", "unit": "p1", "role": "explain", "facts": ["..."], "answer": "..."}]
}
```

不要把字段缩成难以理解的单字母数组协议；节省少量 token 不值得降低模型遵约率。

### 7.3 两次云端阶段仍有必要

如果启用云端规划，合理顺序仍是：

1. planning：浏览压缩 sources，返回 LessonPlan 和视觉任务。
2. 本地 VLM：执行像素核验。
3. organizing：消费计划、相关 sources 和已选 visuals，返回 canonical content blocks。

不能简单合并为一次调用，因为视觉证据必须在 planning 之后、本地生成，再参与最终写作。

优化点：

- organizing 不再发送 verbose LessonPlan 文本，改为 compact units JSON。
- 只发送每个 unit 实际引用的 source blocks。
- VisualEvidence 只发送 `decision=select`；当前 12 个 no_match 不进入 writer prompt。
- 有 VisualEvidence 时不再同时发送全部 FrameSemantic；两者不能成为竞争图片来源。
- `facts`、`answer`、`role`、`visual_id` 足够写作；OCR 只有在不与 facts 重复且确有教学意义时发送。
- 云端只能引用既有 visual ID，不能创建 frame、改图或凭时间选择图片。

### 7.4 预先分批，禁止先失败再回退

当前可能先发送整篇 organizing，失败后再逐章发送，造成重复请求。新流程在请求前按估算预算决定：

- 整体 prompt/output 均在预算内：一次 organizing。
- 超预算：按章节或连续 unit group 预先拆分；每个 source block 在 organizing 阶段原则上只发送一次。
- 不先尝试注定超预算/易截断的整篇请求。

请求前生成 `workspace/.../cloud-payload.json` 供审计，记录 chars、source/unit/visual 数量和预计分批，但不包含密钥。UI 授权说明展示实际投影内容范围。

## 8. 图片信息如何与云端协作

默认保持本地视觉、云端写作：

```text
本地帧 → OCR/VLM → VisualEvidence(facts/answer/role/id)
→ CloudPayload.visuals
→ 云端只返回 visual_id 的放置位置
→ 本地 adapter 用 visual_id 找回图片路径并渲染
```

优点：

- 不上传截图，不增加视觉 token；
- 云端不会重新推翻本地像素证据；
- 本地绝对路径不离开设备；
- JSON 中 visual ID 可以稳定连接 Markdown、Word、PDF。

局限：云端只理解本地 VLM 已提取的事实，不能独立检查图片。当前项目资源与隐私边界下这是合理职责分工。

如未来确实需要云端二次看图，应另设显式 opt-in 的 multimodal review：只上传已经入选且仍存在歧义的 1–2 张压缩图，单独显示图片数量、发送内容和额度。它不是本版本默认路径。

## 9. 视觉任务与 VLM 执行优化

### 9.1 任务规划

- 保留合法的模型 visual_need；启发式只补缺失字段，不无条件覆盖。
- 视觉问题必须包含明确可见对象/关系；指代词、截断句和抽象成功条件在规划阶段拒绝。
- 每个知识点默认一个 primary 任务。
- 单图 provider 未支持多选前，禁用自动 `progression_grid=3`。
- 15–45 分钟 enhanced 视频 compare 上限 8，detail 上限 2。
- 已入选场景在后续任务中先 reservation，再调用 VLM，避免推理后丢弃。

### 9.2 单次模型会话

每个视频只启动一个本地 runner：

- 一次加载 processor/model；
- 默认 `micro_batch_size=1` 顺序执行 jobs；
- 每个 job 原子落盘；
- 中断保留已完成结果；
- 只对未完成 transient job 重启一次；
- 完成后退出并释放 GPU。

8 GB 显存下首版不同时执行两组四图 compare。真正的 batch 只可在后续对单图 detail 做受控实验。

### 9.3 逐任务缓存

cache key 包含 question/contract、candidate 指纹、OCR、模型/runner/校验版本。正常 no_match 也是缓存结果。计划小改不再导致全部视觉问题重跑。

## 10. “失效”重新定义

| 状态 | 示例 | 对用户 |
|---|---|---|
| expected_no_match | 规划拒绝、候选不足、criteria 未满足、场景已占用 | info；正常完成 |
| recovered_quality | detail 失败但 compare 证据完整 | info；保留证据 |
| inference_failure | timeout、OOM 重试失败、runner 崩溃、协议非法 | 任务级仅一个 warning |

额外规则：

- `local_vlm_enabled=auto` 且能力不存在，只通知一次“未启用视觉增强”，不弹完成降级。
- 用户显式要求 VLM 且能力不可用，才是 warning。
- 日志显示 `视觉任务 3/8 · 入选 2 · 正常拒绝 1`，不逐条写“VLM 失效”。
- 事件附 app/pipeline/cache/runner 版本，避免把旧日志误认为当前行为。

## 11. ETA 与工作计划

移除 `elapsed * (100-progress) / progress`。新增 ProgressEvent 和 EtaEstimator：

- ASR：已处理音频秒数/总秒数；
- frames：已处理候选/预计候选；
- visual：session load、compare/detail 完成数/总数；
- cloud：payload build、planning/organizing batch 完成数；
- render：Markdown/Word/PDF 子阶段。

估算规则：

1. 当前阶段剩余 + 后续阶段历史中位数。
2. VLM cold load 与 warm job 分开统计。
3. 最近 5 个 job 中位数；本机历史最多保存 20 组。
4. 无样本返回 None，显示“估算中”。
5. 有估算后保存 monotonic deadline，桌面每秒倒计时。
6. cache hit 和任务数变化触发重算。
7. `auto` 明确拆成 local_prepare/cloud_refine，不再机械 50/50 估时。

历史只保存阶段耗时、硬件/模型/档位和 cache 状态，不保存视频名、转写、图片或密钥。

## 12. 实施顺序

### 阶段 A：建立 CourseIR 和正文唯一所有权

1. 新增 SourceBlock/Claim/CourseIR schema。
2. 本地 organizer 改为 Claim → block，取消 paragraph/list 重复消费。
3. 新增 TitleValidator 和 DedupGate。
4. 三端渲染统一消费 canonical blocks。
5. v1 文档可迁移读取，旧产物不原地覆盖。

全部使用 fixture/缓存测试，不调用 VLM 或云端。

### 阶段 B：收敛视觉任务并会话化

1. visual contract validator。
2. compare/detail 总预算和场景 reservation。
3. VisualJob/Result、单次 runner、逐 job cache。
4. 三态告警与版本指纹。

先 mock `_load_model()` 只调用一次，最后才进行一次真实本地 VLM 验收。

### 阶段 C：CloudPayloadBuilder

1. 从 CourseIR 构造 compact JSON 投影。
2. selected visuals only；禁止路径/base64/no_match/runtime 字段。
3. 请求前预算和预分批。
4. 云端响应只能引用 source/claim/visual ID。
5. 记录实际 prompt/completion usage。

先做离线 payload 快照和字符比较；只有用户再次授权后做一次真实云端精炼。

### 阶段 D：ETA 和桌面状态

1. ProgressEvent/EtaEstimator。
2. UI deadline 倒计时和“估算中”。
3. visual/cloud job 进度。
4. 取消、缓存恢复与单次汇总告警。

## 13. 文件级执行建议

| 文件 | 工作 |
|---|---|
| `src/video_study/knowledge/schema.py` | SourceBlock/Claim/CourseIR/VisualJob、Document v2 |
| `src/video_study/knowledge/planning.py` | 标题/视觉合同校验、任务总预算 |
| `src/video_study/knowledge/organizer.py` | Claim 提取、互斥 block composer、云端返回解析 |
| `src/video_study/knowledge/dedup.py`（新） | normalize、相似度、Claim merge、DedupGate |
| `src/video_study/knowledge/cloud_payload.py`（新） | CourseIR → compact CloudPayload、预算和分批 |
| `src/video_study/knowledge/visual_retrieval.py` | 场景 reservation、VisualJob 调度、逐 job cache |
| `src/video_study/knowledge/vision_providers.py` | session run_jobs，保留 per_call 回滚 |
| `scripts/qwen_vl_runner.py` | 一次加载、逐 job 原子输出、session ready/done |
| `src/video_study/knowledge/pipeline.py` | CourseIR 编排、三态状态、版本指纹 |
| `src/video_study/progress.py`（新） | ProgressEvent、EtaEstimator、耗时历史 |
| `src/video_study/adapter.py` 或现有 knowledge adapter | v1→v2、v2→legacy view、visual ID 本地解析 |
| `src/video_study/render.py` / `scripts/render_docx.mjs` | 只渲染 canonical blocks，v1 先迁移 |
| `src/video_study/desktop.py` | 工作单元进度、ETA deadline、单次告警 |

## 14. 测试与验收

### 14.1 离线必测

正文：

- 同一 Claim 不会同时出现在 paragraph 和 rule_list/steps。
- paragraph 包含 list item 时 DedupGate 能确定性消除重复。
- title 与正文开头重合超阈值时被拒绝或修正。
- 含新增数字、否定词、条件的近似句不会被错误删除。
- v1 fixture 可迁移为 v2，Markdown/Word/PDF 内容一致。

CloudPayload：

- 不含 `.md`、`.pdf`、base64、image_path、hash、runtime_events、no_match。
- 每个 source/visual ID 合法，云端返回集合外 ID 会失败而不是猜测。
- 同样例 compact payload 字符量不高于当前动态上下文的 60%。
- 超预算时在请求前分批，不发送一次失败的整篇请求。

视觉/状态/ETA：

- 1302 秒 enhanced compare jobs ≤8，detail ≤2。
- mock 多 job 时模型只加载一次；中途失败不丢已完成结果。
- 正常 no_match 无 warning；多个真失败只有一个任务级 warning。
- ETA 无样本显示估算中；有 warm jobs 后每秒下降并按任务完成校正。

### 14.2 一次真实验收，禁止反复跑

复用同一个缓存样例：

1. 本地渲染：不调用云端/VLM，验证用户可见重复明显消除、来源链接和图片不丢。
2. 本地 VLM：只跑一次视觉恢复，目标 model_load_count=1、视觉阶段 ≤180 秒或较旧基线缩短 ≥50%。
3. 云端精炼：仅在用户明确授权后跑一次，复用相同 CourseIR/视觉缓存；实际 organizer prompt tokens 较旧基线降低目标 ≥30%，且知识点/来源/visual ID 覆盖不下降。

不要通过多次真实调用求平均。性能和异常路径使用 mock、固定 fixture、事件回放验证。

## 15. 迁移、回滚与停止条件

### 15.1 迁移

- 新 cache/schema 版本与旧版并存；旧文件不原地改写。
- v1 document 通过只读 adapter 转成 v2 后渲染。
- 新 document 默认写 v2；如仍需旧消费者，显式导出 legacy view。
- CourseIR/CloudPayload 保存在 workspace，本身属于本地 artifact，不进入 Git。

### 15.2 回滚

- `content_ir.enabled: false`：回到 v1 organizer/adapter。
- `visual_evidence.execution_mode: per_call`：回到旧 runner；正确性仍由 no_match 保护。
- 新 ETA 异常时显示“—/估算中”，不得恢复旧线性公式。
- CloudPayloadBuilder 异常时停止云端请求并保留本地产物，不自动发送旧的超大 prompt。

### 15.3 停止条件

- 去重造成条件、例外、数字或来源丢失：停止自动 near-duplicate merge，仅保留 exact/containment。
- session 模式仍 OOM：保持 batch size 1、降低候选/视觉 token，不增加并行 worker。
- compact payload 降低模型遵约率或来源覆盖：恢复可读字段名，不用更短的晦涩协议换 token。
- 图文质量没有改善：检查视觉问题/成功条件和 Claim/VisualEvidence 连接，不能恢复时间最近帧。

## 16. 版本判断

本方案已由用户确认为 `V3.0` 大版本升级。虽然产品仍保持桌面端、阶段主序和 JSON → Markdown → Word → PDF，但它改变了主内容合同（Document v2/CourseIR）、云端输入合同、VLM provider 生命周期和进度事件合同，因此必须按本文迁移与回滚边界实施。
