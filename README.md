# 知影｜高质量、可回溯的影像知识点整理平台

[![Version](https://img.shields.io/badge/version-1.0.0-crimson)](https://github.com/DreamBetterO/ZhiYing/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-blue)](#运行要求)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)](#从源码启动)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

![知影](docs/screenshot/主页.gif)

知影将课程、讲座、教程等视频整理成结构清晰的学习资料。它不只转写语音，还会结合画面和时间位置提炼知识点，最终生成可阅读、可编辑、可返回原视频核对的 Markdown、Word 和 PDF 文档。

## 核心能力

| 能力 | 说明 |
|---|---|
| 视频内容整理 | 从语音、画面和时间信息中提炼章节与知识点 |
| 本地视频与视频链接 | 支持本地 MP4，也支持常见网页链接、直链和流媒体地址 |
| 内容来源回溯 | 在文档中保留时间位置，可返回原视频查看上下文 |
| 多视频合并 | 将同一主题的多段视频整理为一份综合文档 |
| 多格式输出 | 同时生成 Markdown、Word 和 PDF |
| 本地与云端两种方式 | 可以完全本地处理，也可以选择云端模型进一步优化内容 |
| 缓存与恢复 | 自动复用已经完成的步骤，中断后不必全部重来 |

## 性能与消耗参考

实测参考：

- **整理速度**：约 17 分钟完成，折算为 **1 小时视频约 6 分钟**。
- **云端 Token 消耗**：合计约 13 万，折算为 **1 小时视频约 4.3 万 Tokens**（输入约 3.1 万、输出约 1.3 万）。

实际耗时和用量会随视频内容、硬件性能、模型及处理方式变化，以上数据用于提供量级参考。原始运行记录见[软件介绍](docs/项目文档/软件介绍.md#任务完成)。

## 快速开始

### 使用 Windows 发行版

1. 前往 [GitHub Releases](https://github.com/DreamBetterO/ZhiYing/releases/tag/v1.0.0)，下载 `ZhiYing-Core-1.0.0-win-x64.zip`。
2. 将 ZIP 完整解压到一个单独文件夹。
3. 双击 `ZhiYing.exe` 启动。
4. 准备处理视频时，按照[模型与工具下载说明](release/DOWNLOADS.md)补齐需要的组件。
5. 如需检查电脑是否准备完成，双击 `doctor.cmd`。

模型或工具不完整也不影响打开界面，只会让对应的处理能力暂时不可用。日常使用请打开 `ZhiYing.exe`；`ZhiYing-Console.exe` 仅用于排查启动问题。

完整安装说明见[发行版使用说明](release/README.md)和[快速安装指南](release/QUICK_START.md)。

### 从源码启动

需要准备 Git、Python 3.11+ 和 Node.js 18+。

```powershell
git clone https://github.com/DreamBetterO/ZhiYing.git
cd ZhiYing

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
npm ci

.\.venv\Scripts\python.exe -m zhiying desktop --config config.yaml
```

最后一条命令会直接打开桌面界面。完成安装后，也可以双击根目录的 `启动桌面版.cmd`。

FFmpeg、模型和 PyTorch 推理环境用于实际处理视频，不是打开界面的前置条件。具体准备方式见[模型与工具下载说明](release/DOWNLOADS.md)。

## 基本使用

1. 添加本地视频或视频链接。
2. 勾选需要处理的视频。
3. 选择“生成本地文档”或“使用云端优化”。
4. 完成后直接打开 Markdown、Word、PDF 或输出目录。

处理过程中可以看到当前阶段、进度、耗时、预计剩余时间和云端 Token 用量。更多界面说明见[软件介绍](docs/项目文档/软件介绍.md)。

## 运行要求

- Windows 10 或 Windows 11，64 位；
- 推荐使用 NVIDIA GPU，8 GB 或更多显存；
- 本地处理需要 FFmpeg 和相应模型；
- 视频链接下载以及云端优化需要网络；
- 没有 NVIDIA GPU 时可以使用 CPU 方案，但速度会慢一些。

显卡与运行环境的选择见 [GPU 配置指南](release/GPU_GUIDE.md)。

## 开发

知影是使用 Python 开发的 Windows 桌面应用。界面、视频处理、知识整理和文档生成相互分离，便于独立调试和测试。

常用检查命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

提交修改时，请同时提供必要的测试和验证结果。不要提交 `.env`、模型、工具、用户视频、工作缓存或生成文件。代码结构和调试入口见[开发文档](docs/项目文档/开发文档.md)，系统设计见[架构详解](docs/项目文档/架构详解.md)。

## 项目结构

```text
src/zhiying/       应用源码
├─ desktop/        桌面界面
├─ application/    应用流程
├─ execution/      任务执行、进度与恢复
├─ media/          视频、音频与画面处理
├─ knowledge/      内容理解与知识整理
└─ documents/      Markdown、Word、PDF 输出

tests/             自动化测试
scripts/           诊断、渲染和发行脚本
release/           发行版安装与组件说明
docs/              产品和开发文档
```



## 文档

| 内容 | 文档 |
|---|---|
| 软件界面与使用方法 | [软件介绍](docs/项目文档/软件介绍.md) |
| 发行版安装 | [发行版使用说明](release/README.md) |
| 模型和工具下载 | [下载说明](release/DOWNLOADS.md) |
| NVIDIA GPU 配置 | [GPU 配置指南](release/GPU_GUIDE.md) |
| 常见问题 | [故障排查](release/TROUBLESHOOTING.md) |
| 开发与调试 | [开发文档](docs/项目文档/开发文档.md) |
| 系统设计 | [架构详解](docs/项目文档/架构详解.md) |

## 许可与联系

项目源码采用 [MIT License](LICENSE)。模型、媒体工具和其他第三方组件遵循各自的许可证。

技术交流：fangxiang202009@yeah.net
