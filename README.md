# 知影 ZhiYing

> 本地优先的 Windows 视频知识工作台：把教学视频整理为可回看来源的 Markdown、Word 和 PDF 学习文档。

**当前产品版本：1.0.0（V6.1） · 平台：Windows 10/11 x64 · 形态：原生桌面应用 · 最新离线测试：622 项通过**

## 项目概览

知影面向学生、自学者、课程制作者和需要整理长视频资料的用户。它把视频中的声音、画面、时间位置和知识结构串成一条可追踪链路，最终生成适合阅读、复习和二次编辑的文档。

项目坚持三个边界：

- **本地优先**：默认在本机完成媒体解析、语音识别、视觉分析和文档生成。
- **来源可追溯**：知识点保留 `video-study://` 时间链接，可从文档回到原视频位置。
- **桌面应用**：不启动 Web UI、本地 HTTP 服务或端口监听器。

完整界面、操作示例和输出效果见[软件使用说明](docs/项目文档/软件介绍.md)。

## 下载与发行

### 普通使用者

正式发布后，从项目的 [GitHub Releases](https://github.com/DreamBetterO/ZhiYing/releases) 下载：

1. `ZhiYing-Core-<版本>-win-x64.zip`；
2. 同版本 `SHA256SUMS.txt`；
3. 按[模型与工具下载说明](release/DOWNLOADS.md)获取所需外部组件。

解压核心 ZIP 后，包内会直接提供快速开始、组件下载、GPU、隐私和故障排查文档。日常启动 `ZhiYing.exe`；`doctor.cmd` 和 `ZhiYing-Console.exe` 只用于诊断。

### ZIP 与源码如何一起发布

| 内容 | 发布位置 | 原因 |
|---|---|---|
| 源码、测试、项目文档、`release/` 清单 | Git 仓库与版本标签 | 便于审阅、克隆和贡献 |
| 核心 ZIP、对应 SHA-256 | 同版本 GitHub Release 附件 | 用户可直接下载，同时避免把大文件写入 Git 历史 |
| 模型、FFmpeg、Node.js、yt-dlp、CUDA/Python 运行时 | 官方上游链接 | 体积大、许可证和更新节奏独立 |
| `release/output/` 本机构建内容 | 不进入 Git 提交 | 只作为待上传的 Release 附件暂存区 |

因此，ZIP 会与版本一起上传，但不会提交为普通 Git 文件。维护者门禁和上传清单见[发行流程](scripts/release/PUBLISHING.md)。

## 核心能力

| 能力 | 说明 |
|---|---|
| 本地视频整理 | 读取本地视频，完成媒体探测、语音识别、关键帧抽取和知识整理 |
| 视频链接输入 | 对受支持的公开视频链接执行预检、下载和一致性校验 |
| 本地与云端双路径 | 本地确定性链可完整产出；云端优化默认关闭并受逐次授权与预算控制 |
| 多模态证据 | 将转写、关键画面和视觉事实绑定到对应知识点 |
| 多视频聚合 | 将同一主题下的多个视频整理为聚合文档 |
| 三格式输出 | 同时生成 Markdown、Word 和 PDF |
| 数学内容 | Word 数学公式使用原生 OMML 渲染 |
| 来源回看 | 文档中的时间链接可调用本地播放器定位原视频 |
| 缓存与恢复 | 复用已完成的中间结果，支持取消、恢复和安全清理 |
| 质量与降级 | 模型或云能力不可用时按确定性路径降级，并保留原因与实际能力链 |

## 使用流程

```text
本地视频 / 视频链接
        ↓
媒体探测与音频提取
        ↓
ASR 转写 + 关键帧/视觉事实
        ↓
Course IR 与知识组织
        ↓
受控编辑子图与质量审计
        ↓
Markdown → Word → PDF
        ↓
点击来源链接回看原视频
```

核心包采用组件化模式：程序本身保持轻量，模型和工具按设备能力选择。无 NVIDIA GPU 可使用 CPU 路径，但速度较慢；NVIDIA 用户建议阅读 [GPU 指南](release/GPU_GUIDE.md)。

## 运行要求

- Windows 10/11 64 位；
- 推荐 NVIDIA GPU，8 GB 或更多显存；
- 较新的 NVIDIA 驱动；
- 足够容纳模型、运行时、源视频和中间产物的磁盘空间；
- 视频链接功能需要网络，本地视频整理可在组件就绪后离线运行。

组件版本、安装目录和完整文件要求见 [`release/manifests/components.json`](release/manifests/components.json)。

## 从源码运行

开发环境使用 Conda 环境 `ImageT10`，Python 环境位于 `D:\Anaconda\envs\envs\ImageT10`。

```powershell
conda activate ImageT10
$env:PYTHONPATH = "$PWD\src"
python -m zhiying desktop --config config.yaml
```

也可以在已配置好的开发电脑上双击根目录的 `启动桌面版.cmd`。

请注意：源码运行需要项目依赖、模型和工具已经按配置就绪；不要把本机 `.env`、模型或用户数据提交到仓库。

## 输出与可溯源性

每个任务在独立 Workspace 中保存结构化 Artifact，再从 Canonical Document v3.1 原生渲染三种格式：

- Markdown：便于版本比较、知识库导入和二次处理；
- Word：适合编辑、打印，支持原生公式；
- PDF：适合阅读、分享和归档。

文档中的来源链接使用稳定的 `video-study://` 本地协议。该协议只负责调用本地播放器定位视频，不会打开网页。

## 技术架构

生产链使用 LangGraph 作为唯一编排器：

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

稳定合同包括：一视频一个 VLM 会话、Course IR、紧凑 CloudPayload、Document v3.1、受控 Function Calling、任务进度和 Application/Desktop 分层。详细内容见[技术架构详解](docs/项目文档/架构详解.md)。

## 项目结构

```text
src/zhiying/       应用源码
tests/             离线测试与回归门禁
scripts/           诊断、模型工作进程、渲染与发行脚本
release/           面向使用者的下载、安装、GPU 与故障说明
docs/              使用、开发、架构、诊断与迭代资料
models/            本机模型数据，不提交
tools/             本机第三方工具，不提交
workspace/         可恢复任务状态，不提交
output/            用户产物，不提交
packing/           历史打包实现，不进入新发行体系
```

机器可检查的目录边界见 [`docs/architecture/source-layout.yaml`](docs/architecture/source-layout.yaml)。

## 隐私与安全

- `.env` 保存本机密钥，永不提交；
- 本地模式不主动上传视频、音频、关键帧或转写；
- 云端优化默认关闭，启用前必须确认发送内容、端点、模型链和预算；
- 视频链接功能会访问用户提供的地址并调用 yt-dlp；
- 发行包不得包含 API Key、用户视频、Workspace、历史输出或下载来源标记；
- 未签名版本可能在首次启动时出现 Windows 安全提示，正常处理过程中不应反复弹出命令窗口。

更多信息见[隐私说明](release/PRIVACY.md)、[安全策略](SECURITY.md)和[故障排查](release/TROUBLESHOOTING.md)。

## 文档导航

| 读者 | 文档 |
|---|---|
| 第一次了解项目 | [软件介绍](docs/项目文档/软件介绍.md) |
| 下载和配置组件 | [发行说明](release/README.md) · [快速开始](release/QUICK_START.md) · [组件下载](release/DOWNLOADS.md) |
| NVIDIA 用户 | [GPU 指南](release/GPU_GUIDE.md) |
| 遇到运行问题 | [故障排查](release/TROUBLESHOOTING.md) |
| 开发者 | [开发文档](docs/项目文档/开发文档.md) · [架构详解](docs/项目文档/架构详解.md) |
| 维护者 | [项目索引](docs/项目索引.md) · [发行流程](scripts/release/PUBLISHING.md) |

## 开发与质量

当前完整离线验收为 **622 项通过**，另有 1 项跳过和 5 项预期失败。标准检查：

```powershell
conda activate ImageT10
python -m unittest discover -s tests
python -m compileall -q src tests
python -m pip check
git diff --check
```

发行相关修改还必须通过 `tests/test_release_distribution.py`，包括文档链接、Git 边界、核心包内容和 ZIP 定稿门禁。

## 参与贡献

欢迎通过 Issue 反馈可复现的问题或提出改进建议。完整约定见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。提交代码前请：

1. 阅读[开发文档](docs/项目文档/开发文档.md)和现行架构合同；
2. 不提交 `.env`、模型、工具、视频、Workspace 或生成产物；
3. 为行为变化添加回归测试；
4. 运行完整离线验收并说明真实失败、跳过项和外部依赖；
5. 保持 JSON → Markdown → Word → PDF 数据流和时间戳可追溯性。

安全漏洞请按 [`SECURITY.md`](SECURITY.md) 使用私密渠道报告，不要在公开 Issue 中披露敏感信息。

有任何疑问和信息欢迎联系：fangxiang202009@yeah.net

## 许可证

本项目源码采用 [MIT License](LICENSE)。模型、工具、Python 依赖及其他第三方组件适用各自许可证；MIT License 不替代第三方许可证义务。

正式面向大量用户推广前，仍应完成人工第三方许可证审计、NOTICE、SBOM 和 Windows 代码签名。
