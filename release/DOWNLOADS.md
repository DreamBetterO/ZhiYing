# 模型与工具下载

核心包不包含大体积组件。请先下载“公共工具”，再根据电脑选择 NVIDIA GPU 或 CPU 配置。

## 公共工具（都需要）

| 组件 | 官方下载 | 放置位置 |
|---|---|---|
| FFmpeg | [FFmpeg 下载页](https://ffmpeg.org/download.html) | `tools/ffmpeg/` |
| Node.js 24.19.0 x64 | [下载 ZIP](https://nodejs.org/dist/v24.19.0/node-v24.19.0-win-x64.zip) | `tools/node/` |
| yt-dlp | [下载 EXE](https://github.com/yt-dlp/yt-dlp/releases/download/2026.07.04/yt-dlp.exe) | `tools/yt-dlp/` |

放置完成后应至少存在：

```text
tools/
├─ ffmpeg/ffmpeg.exe
├─ ffmpeg/ffprobe.exe
├─ node/node.exe
└─ yt-dlp/yt-dlp.exe
```

## NVIDIA GPU 配置（推荐）

需要以下三项：

| 组件 | 官方来源 | 放置位置 |
|---|---|---|
| Qwen3-ASR 0.6B | [模型固定版本](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf/tree/6aa69c382e2b426eee1f5870d4c95859a74b6445) | `models/qwen3-asr-0.6b-hf/` |
| Qwen3-VL 2B | [模型固定版本](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct/tree/89644892e4d85e24eaac8bacfd4f463576704203) | `models/qwen3-vl-2b-instruct/` |
| CUDA 推理运行时 | [PyTorch CUDA 12.8](https://download.pytorch.org/whl/cu128) · [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | `models/qwen3-asr-runtime/` |

模型必须下载完整仓库，不能只下载单个权重文件。GPU 的完整目录示例见 [GPU 指南](GPU_GUIDE.md)。

## CPU 配置

没有 NVIDIA 显卡时，可使用 faster-whisper small：

| 组件 | 官方来源 | 放置位置 |
|---|---|---|
| faster-whisper small | [模型固定版本](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120) | `models/faster-whisper-small/` |

CPU 处理速度通常明显慢于 GPU。

## 最后检查

把 `tools` 和 `models` 放到 `ZhiYing.exe` 同一层，然后双击 `doctor.cmd`。全部准备完成后再处理长视频。

精确版本和必需文件记录在 [`manifests/components.json`](manifests/components.json)；普通使用无需手工修改该文件。
