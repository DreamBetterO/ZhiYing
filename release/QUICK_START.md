# 快速开始

## 1. 解压核心包

将 `ZhiYing-Core-<版本>-win-x64.zip` 完整解压，例如：

```text
D:\ZhiYing\
├─ ZhiYing.exe
├─ ZhiYing-Console.exe
├─ doctor.cmd
└─ _internal\
```

不要直接在 ZIP 中运行，也不要只复制两个 EXE。

## 2. 准备模型和工具

- NVIDIA 显卡电脑：使用[推荐的 GPU 配置](GPU_GUIDE.md)；
- 其他电脑：使用[基础 CPU 配置](DOWNLOADS.md#cpu-配置)。

下载完成后，将 `models` 和 `tools` 文件夹放在 `ZhiYing.exe` 同一层。

## 3. 检查配置

双击 `doctor.cmd`。这是检查工具，出现黑色窗口属于正常现象。

如果有项目显示缺失，请按窗口提示和[下载说明](DOWNLOADS.md)检查对应目录。

## 4. 启动知影

双击 `ZhiYing.exe`。日常使用不要打开 `ZhiYing-Console.exe`。

进入软件后：

1. 添加本地视频或视频链接；
2. 勾选视频；
3. 点击“生成本地文档”；
4. 等待 Markdown、Word 和 PDF 生成完成。

如启动失败，请查看[故障排查](TROUBLESHOOTING.md)。
