# V6.1 Fixtures（tests/fixtures/v61/）

CP61-0 建立的四类黄金 fixture，供 CP61-1（本地编辑链）、CP61-2（原生渲染）、CP61-7（黄金验收）使用。
不复制任何大文件（无图片、无音频、无完整 Workspace）；只保留结构化输入与黄金期望。

## 文件

| 文件 | kind | 用途 |
|---|---|---|
| `math_concept.json` | math_concept | 数学概念（定义→性质→条件/边界），含真实 ASR 错词黄金样本 |
| `math_example.json` | math_example | 数学例题（题目→思路→推导→结论），case 结构 |
| `strong_visual.json` | strong_visual | 强视觉：明确指图线索 + select 证据 |
| `weak_visual.json` | weak_visual | 弱/无视觉：纯语音 + no_match 证据 |

## 公共结构

```jsonc
{
  "id": "…",                 // 稳定 id
  "kind": "math_concept|math_example|strong_visual|weak_visual",
  "source_sample": "…",      // 可选：真实样本来源（只引用，不复制）
  "description": "…",
  "policy": { "required": [], "preferred": [], "forbidden": [] },
  "transcript": { "segments": [{"segment_id","start_seconds","end_seconds","text"}] },
  "plan": { "units": [{"plan_id","title","role","type","source_segment_ids", …}] },
  "visual_evidence": [ … ],  // 视觉类 fixture 才有；decision ∈ select|no_match
  "known_asr_errors": [ "错词→正确词", … ],
  "golden": { … }            // 黄金期望指标（见各文件）
}
```

## 使用规则

- fixture 输入形状对齐 `transcript/transcript.json` 与 `knowledge/knowledge-units.json` 的既有结构。
- `golden.raw_asr_copy_forbidden` 中的词不得以“精炼后的规则/步骤”形式出现在正文（允许出现在来源引用中）。
- 不修改已冻结的 fixture 形状；扩展时新增字段，不回填旧字段。
