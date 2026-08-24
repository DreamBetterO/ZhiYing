# NVIDIA GPU 配置

推荐环境：Windows 10/11 64 位、较新的 NVIDIA 驱动、8 GB 或更多显存。

## 准备步骤

1. 更新 NVIDIA 驱动并重启电脑。
2. 打开命令提示符运行 `nvidia-smi`，确认能看到显卡信息。
3. 按[下载说明](DOWNLOADS.md#公共工具都需要)准备 FFmpeg、Node.js 和 yt-dlp。
4. 下载 Qwen3-ASR、Qwen3-VL 和 CUDA 推理运行时。
5. 按下面的目录结构放置文件。
6. 双击 `doctor.cmd` 检查。

```text
ZhiYing-Core-<版本>-win-x64/
├─ ZhiYing.exe
├─ tools/
│  ├─ ffmpeg/
│  ├─ node/
│  └─ yt-dlp/
└─ models/
   ├─ qwen3-asr-runtime/
   ├─ qwen3-asr-0.6b-hf/
   └─ qwen3-vl-2b-instruct/
```

不要把其他 Python 环境或系统 CUDA 文件直接复制进知影目录。显存不足时，可在软件设置中降低视觉处理需求，或改用 CPU 配置。
