# 知影windows 发行版

知影可以把本地视频或视频链接整理成 Markdown、Word 和 PDF 学习文档。

## 开始使用

1. 下载 `ZhiYing-Core-<版本>-win-x64.zip`。
2. 将 ZIP **完整解压**到一个单独文件夹，不要只复制 EXE。
3. 按[模型与工具下载说明](DOWNLOADS.md)补齐所需组件。
4. 双击 `doctor.cmd` 检查配置。
5. 检查通过后，双击 `ZhiYing.exe` 启动。

第一次安装建议继续阅读[快速开始](QUICK_START.md)。

## 两个启动文件

- `ZhiYing.exe`：日常使用入口，不显示黑色命令窗口。
- `ZhiYing-Console.exe`：排查故障时使用，会显示命令窗口。

平时只需要打开 `ZhiYing.exe`。

## 模型与工具

核心包不包含体积较大的模型、FFmpeg、Node.js、yt-dlp 和 GPU 推理运行时。请根据电脑选择：

- NVIDIA 显卡：按照 [GPU 指南](GPU_GUIDE.md)准备 GPU 组件；
- 没有 NVIDIA 显卡：按照[下载说明](DOWNLOADS.md)准备 CPU 组件，速度会慢一些。

## 使用软件

启动后：

1. 点击“＋ 添加本地视频”或“＋ 添加视频链接”；
2. 勾选需要处理的视频；
3. 点击“生成本地文档”；
4. 完成后在软件中打开 Markdown、Word、PDF 或输出目录。

## 遇到问题

先运行 `doctor.cmd`，再查看[故障排查](TROUBLESHOOTING.md)。

隐私说明见 [PRIVACY.md](PRIVACY.md)。知影源码采用 [MIT License](../LICENSE)，模型和工具遵循各自的上游许可证。
