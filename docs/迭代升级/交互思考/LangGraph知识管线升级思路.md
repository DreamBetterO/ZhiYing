# LangGraph 知识管线升级 · 交互思考

> 状态：思路草案，待二次讨论。非实施计划，非版本记录。

## 背景问题

1. ASR 转写存在大量同音误识别（激风→积分、赛引→sin），现有管线无纠错环节。
2. 截图链路对高数类板书密集课程效果差：10s 等间隔采样 + 信息熵筛选，选不出公式帧，板书白底黑字熵值低被过滤。
3. 云端 LLM 仅在 planning 和 organizer 各调用 1 次（共 2 次），做的是课程规划和内容整理，无 ASR 纠错、公式还原、跨块去重等精炼环节。
4. 单次大上下文调用对长视频不友好，多视频并发不可能。

## 升级方向：用 LangGraph 替换中段管线

现有 `PipelineRunner` 是线性 DAG，无并行 fan-out/fan-in。LangGraph 的 `Send` + `checkpoint` + 条件边天然适合 Map-Reduce 分块并行。

### 替换范围

- **保留段 A**：ASR 引擎、ffmpeg 采样、config/runtime/desktop — 纯 I/O，不动。
- **替换段 B**：从 `transcript.normalize` 产物开始，到 `document.assemble` 产物结束，全部用 LangGraph 重写。现有 `execution/steps/knowledge.py` 的 8 个 Step → 1 个 LangGraph 图。
- **保留段 C**：render、python-docx 排版、video-study:// 协议、桌面 UI — 消费最终产物，不动。

### 图结构概要

```
ASR Refine (Map-Reduce)
  分块并行纠错 → 合并 → 字典持久化积累

Keyframe Predict (串行, 1次LLM)
  读纠错后文本 → 预测板书时间窗口 → 定向密集采样 → 合并候选池

Planning (串行, 复用现有 planning.py)
  输入换成纠错后 transcript

Visual Evidence (Map-Reduce)
  每个视觉问题并行处理 → 可选 VLM select + 公式交叉验证 → 全局仲裁

Organizer (串行, 复用现有 organizer.py)

Document Refine (Map-Reduce)
  按 chapter 分块精炼 → 全局校准(去重+本节要点+公式一致性)
```

### 核心复用

`planning.py` / `organizer.py` / `visual_retrieval.py` / `vision.py` 的函数签名几乎不改，调用者从 Step 变 Node。

### 真正新写的

- ASR 纠错 prompt + dictionary store（跨视频积累）
- 关键帧预测 prompt + dense sample
- 文档精炼 prompt + global calibrate
- 视觉公式交叉验证（截图板书反哺 ASR 字典）

### ASR 字典

- 存 `workspace/asr_dictionary.json`，按领域分文件
- 每次纠错发现新条目通过 function call 提交，高置信度自动入库
- 下一个视频自动加载，逐步积累

### Token 预算概估

3 小时高数视频约 35 次云端调用，单次 2K-8K token，总输入 ~120K、输出 ~60K。对比现有 2 次大调用（120K+10K），调用次数多但每次更精准。
