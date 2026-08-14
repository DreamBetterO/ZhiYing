# CODELY.md — 知影 · 视频知识工作台

> 本文件是 Codely CLI 的项目级指令上下文。Agent 在执行任何任务前应先读取本文件。

## 项目概述

**知影**是一款原生 Windows 桌面视频整理工具，将教学视频转化为可溯源的学习文档。

核心处理链路：

```
MP4 → 音频/关键帧 → 带时间戳转写 JSON → 知识 JSON (Course IR)
    → Markdown → Word → PDF
```

- 视频始终使用本地绝对路径，不上传、不复制。
- 云端整理默认关闭，启用前需展示发送内容、模型和预算并获得用户明确授权。
- 文档中的来源链接使用 `video-study://` 本地协议，点击后调用本地播放器定位，不启动 Web 服务。

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python ≥ 3.11 |
| ASR | faster-whisper (本地), Qwen3-ASR-0.6B (本地, 可选) |
| 视觉 | Qwen3-VL-2B-Instruct (本地 VLM, auto 模式) |
| 云端 | OpenAI 兼容 API (DashScope, 默认关闭) |
| 文档渲染 | python-docx / docx (Node.js), reportlab, pypdf |
| 图像 | Pillow |
| 配置 | PyYAML, python-dotenv |
| 桌面 | 原生 Windows GUI (tkinter) |
| 构建 | setuptools, pyproject.toml |
| Node 依赖 | docx ^9.5.1 (render_docx.mjs) |

## 构建与运行

```powershell
# 激活虚拟环境
.venv\Scripts\activate

# 启动桌面版
.venv\Scripts\video-study.exe desktop --config config.yaml
# 或双击 启动桌面版.cmd

# 本地 VLM / ASR 运行时（conda 环境）
conda activate ImageT10
```

## 开发验证

```powershell
# 单元测试
.venv\Scripts\python.exe -m unittest discover -s tests

# 编译检查
.venv\Scripts\python.exe -m compileall -q src tests

# 依赖完整性
.venv\Scripts\python.exe -m pip check

# Git 空白检查
git diff --check
```

## 项目结构

```
src/video_study/          产品源码
  cli.py                  命令行入口 (video-study = video_study.cli:main)
  pipeline.py             CLI/旧调用方公开兼容门面
  application/            ProcessingService、请求/结果 DTO 与执行句柄
  desktop/                无 Tk Controller、状态模型、设置服务与 Tk View
  execution/              15 步 Registry/Runner、Artifact/Cache、端口与适配器
  asr.py                  语音识别
  frames.py               关键帧提取
  render.py               Markdown/Word/PDF 渲染
  localplay.py            video-study:// 协议与本地播放器
  providers.py            云端模型 provider
  progress.py             ETA 与进度跟踪
  transcript.py           转写规范化纯函数
  config.py / runtime.py / media.py / utils.py / aggregate.py / single_instance.py   knowledge/              LessonPlan、视觉证据、Course IR、单元与 Document v2     planning.py           课程规划     visual_retrieval.py   视觉证据检索     course_ir.py          Course IR 生成     organizer.py          知识单元整理     selfcheck.py          知识自检     document.py           Canonical Document v2     cloud_payload.py      紧凑云端载荷     editorial.py          编辑意图加载器与编辑决策合同







tests/                    单元测试 (unittest)
scripts/                  开发与渲染辅助脚本
  qwen_asr_runner.py      Qwen3-ASR 运行器
  qwen_vl_runner.py       Qwen3-VL 运行器
  render_docx.mjs         Node.js Word 渲染
  verify_outputs.py       产物验证
迭代升级/                  执行事实、已批准版本与现行架构任务书
docs/                     文档索引
icon/                     桌面窗口图标
config.yaml               本地处理、渲染、桌面水印等配置
api.yaml                  云端端点、模型链与预算配置
.env / .env.example       API Key (仅用户启用"记住密钥"后保存)
```

## 关键配置文件

- **`config.yaml`**：ASR 引擎与参数、关键帧采样、视觉教学、视觉证据、Course IR、文档 schema、桌面 UI、水印。
- **`api.yaml`**：云端模型链 (默认 deepseek-v4-flash-0731 → glm-5.2 → kimi-k2.6 → qwen3.7-plus)、调用预算 (`max_calls_per_video: 5`)、`planning_max_output_tokens: 5000`、输入/输出 token 上限。所有云端参数通过环境变量覆盖，不硬编码密钥。

- **`.env`**：`QWEN_API_KEY`, `QWEN_BASE_URL`, `CLOUD_LLM_ENABLED`（默认 false）。已被 `.gitignore` 忽略，禁止输出或提交。

## 工作约定

### 不可违反的边界

1. **桌面优先**：不增加 Web UI、本机 HTTP 服务、端口监听或 `serve` 命令。
2. **密钥安全**：不读取、输出或提交 `.env` 中的密钥。
3. **云端授权**：云端推理默认关闭。真实云调用前必须逐次说明发送数据、端点、模型链和预算，并获得用户明确授权。不得仅为测试配置而发起真实 API 请求。
4. **数据目录**：`workspace/`、`output/`、`models/`、`视频/` 是本地数据或产物，不是源码，不提交到 Git。
5. **流水线保持**：保持 JSON → Markdown → Word → PDF 数据流和时间戳可溯源性，不破坏 `video-study://` 协议。
6. **迭代记录写入门控**：`迭代升级/迭代记录与问题.md` 只在用户明确批准命名版本后追加一条记录，不写实施状态、测试、环境或 Agent 推理。
7. **业务代码撰写**：干净不冗余、可维护、可扩展的代码，不包含硬编码、魔法值、全局变量等。

### 开发规范

- 与用户交流使用中文，除非用户另有要求。
- 修改项目前先读 `MEMORY.md` 和 `迭代升级/AI执行入口.md`。
- 与主项目无关的临时脚本放在 `./tmp/` 目录。
- 保留用户已有的 dirty worktree 修改，不假设仓库可重置或提交。
- 默认使用缓存和离线测试；真实云调用需单独授权。
- 本地 VLM 为 `auto` 模式：仅在本机模型、运行时和 CUDA 预检通过时启用，不上传截图、不消耗云端 token。

### 执行入口与审计

| 文件 | 用途 |
|------|------|
| `迭代升级/AI执行入口.md` | Agent 执行入口，先读 |
| `迭代升级/执行事实.yaml` | 最新实施、验证与审计事实 |
| `迭代升级/迭代记录与问题.md` | 用户批准的版本时间线（严格写入门控）|
| `MEMORY.md` | 项目记忆索引 |
| `docs/README.md` | 文档索引 |



### 迭代规范

- **Small/minor**：向后兼容的修复/功能，保持主阶段顺序、核心契约和桌面边界。
- **Major**：改变核心阶段顺序、核心契约、模型责任边界、兼容策略或桌面产品边界；执行文档需包含迁移和回滚方案。
- 版本分类可由 Agent 建议，但最终由用户确定。
