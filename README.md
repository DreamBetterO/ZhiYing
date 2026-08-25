<div align="center">

# 知影 · ZhiYing

**本地优先的 Windows 视频知识工作台**

把教学视频整理为**可回看来源**的 Markdown、Word 和 PDF 学习文档。

[![Version](https://img.shields.io/badge/version-1.0.0-crimson)](https://github.com/DreamBetterO/ZhiYing/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-blue)](#运行要求)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](#开发者快速开始)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/offline_tests-623%20passed-brightgreen)](#)

![知影主界面](docs/screenshot/首页.png)

---

## 它是什么

知影把教学视频里的**声音、画面、时间位置**和**知识结构**串成一条可追踪的链路，最终生成适合阅读、复习和二次编辑的学习文档。

- **多模态证据**：转写、关键画面、视觉事实绑定到对应知识点；
- **多视频聚合**：同一主题的多个视频合并为综合文档，保留各自来源；
- **三种输出**：Markdown、Word（原生 OMML 公式）、PDF 共享同一份内容；
- **回看来源**：文档时间链接一键调起本地播放器，定位到原视频对应位置；
- **本地 + 云端双路径**：本地确定性链可独立完成；云端优化默认关闭，受逐次授权与预算控制。
- [软件介绍](docs/项目文档/软件介绍.md)：本软件的使用细节更多细节请移步此处。

---

## 快速开始

### 普通用户

1. 前往 [Releases](https://github.com/DreamBetterO/ZhiYing/releases/tag/v1.0.0) 下载 `ZhiYing-Core-1.0.0-win-x64.zip` 与 `SHA256SUMS.txt`；
2. 解压后按 [快速开始](release/QUICK_START.md) 补齐组件；
3. 双击 `ZhiYing.exe` 启动，按 [软件介绍](docs/项目文档/软件介绍.md) 操作。

> 组件、GPU 与故障排查见 [release/README.md](release/README.md)。

### 开发者

```bash
# 1. 克隆
git clone https://github.com/DreamBetterO/ZhiYing.git
cd ZhiYing

# 2. 创建并激活 conda 环境
conda create -n ImageT10 python=3.10 -y
conda activate ImageT10

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动桌面端
zhiying desktop --config config.yaml
# 或双击根目录的 启动桌面版.cmd
```

阅读 [开发文档](docs/项目文档/开发文档.md) 与 [架构详解](docs/项目文档/架构详解.md) 了解代码与设计。

---

## 核心能力

| 能力 | 说明 |
|---|---|
| **本地视频整理** | 读取 MP4，自动完成媒体探测、语音识别、关键帧抽取和知识整理 |
| **视频链接输入** | 支持页面链接、直链、HLS 流与 B 站 BV/av 号；预检 → 下载 → 时长校验一条龙 |
| **多模态证据** | 转写、关键画面、视觉事实绑定到对应知识点 |
| **多视频聚合** | 同一主题多个视频合并为综合文档，保留各自来源 |
| **三格式输出** | Markdown / Word（原生 OMML 公式）/ PDF 共享同一份内容 |
| **来源回看** | 文档时间链接 `video-study://` 协议调起本地播放器定位 |
| **缓存与恢复** | 复用已校验的中间产物，支持取消、清理、安全恢复 |
| **本地 + 云端双路径** | 本地确定性链可独立完成；云端优化受逐次授权与预算控制 |
| **质量降级** | 模型或云能力不可用时按确定性路径降级，保留真实能力链与失败原因 |

---

## 文档导航

| 我想了解什么 | 建议阅读 |
|---|---|
| 第一次使用、怎么操作 | [软件介绍](docs/项目文档/软件介绍.md) |
| 产品定位与边界 | [业务文档](docs/项目文档/业务文档.md) |
| 定位代码、调试、提交 | [开发文档](docs/项目文档/开发文档.md) |
| 系统设计、数据流 | [架构详解](docs/项目文档/架构详解.md) |
| 下载、组件、GPU、故障 | [发行说明](release/README.md) · [快速开始](release/QUICK_START.md) |
| 当前执行状态 | [项目索引](docs/项目索引.md) · [AI 执行入口](docs/AI执行入口.md) |
| 机器合同与故障定位 | [architecture](docs/architecture) · [problem-index](docs/diagnostics/problem-index.yaml) |

---

## 架构一览

生产链以 **LangGraph** 为唯一编排器：

```text
Desktop UI
  ↓
Application 用例层
  ↓
Source / Job / Video / Aggregate Graph
  ↓
NodeExecutor → ArtifactStore / WorkspaceCache
  ↓
Media + Knowledge + Editorial + Documents
```

**稳定合同**：单视频单 VLM 会话 · Course IR · 紧凑 CloudPayload · Document v3.1 · 受控 Function Calling · 任务进度 · Application/Desktop 分层。

---

## 运行要求

- **操作系统**：Windows 10/11 64 位；
- **GPU**：推荐 NVIDIA GPU，8 GB 或更多显存（仅本地模型推理需要）；
- **驱动**：较新的 NVIDIA 驱动；
- **磁盘**：足够容纳模型、运行时、源视频和中间产物；
- **网络**：视频链接功能需要网络；本地视频整理可在组件就绪后离线运行。

组件版本与完整文件清单见 [`release/manifests/components.json`](release/manifests/components.json)。

---

## 参与贡献

欢迎通过 Issue 反馈可复现的问题或提出改进建议。完整约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交代码前请：

1. 阅读 [开发文档](docs/项目文档/开发文档.md) 和现行架构合同；
2. **不要**提交 `.env`、模型、工具、视频、Workspace 或生成产物；
3. 为行为变化添加回归测试；
4. 运行 `unittest discover -s tests` 并如实说明真实失败、跳过项与外部依赖；
5. 保持 JSON → Markdown → Word → PDF 数据流和时间戳可追溯性。

```bash
# 完整离线验收
conda activate ImageT10
unittest discover -s tests
compileall -q src tests
pip check
git diff --check
```

安全漏洞请按 [SECURITY.md](SECURITY.md) 使用私密渠道报告，不要在公开 Issue 中披露敏感信息。

---

## 许可

本项目基于 [MIT License](LICENSE) 开放源码。

技术交流：fangxiang202009@yeah.net
