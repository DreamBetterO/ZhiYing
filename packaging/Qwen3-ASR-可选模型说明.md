# Qwen3-ASR 可选模型

知影标准版已经随附 `faster-whisper-small`，无需网络即可运行。Qwen3-ASR-0.6B 是可选的第二套
本地语音识别模型，适合希望尝试更多语言、方言或复杂音频识别的用户。

## 为什么没有直接塞进主安装包

模型权重本身约 1.5 GB，但它不能独立运行，还需要 PyTorch、CUDA 动态库、Transformers、
音频库和 Python 环境。开发机上原有的 `qwen3-asr-runtime` 已经是“只复制部分包”的环境，
仍约 5.7 GB；其中大量空间属于 PyTorch/CUDA 二进制文件。

继续手工删除 DLL 或 Python 包虽然可能缩小体积，但不同显卡、驱动和音频输入会触发不同依赖，
很容易出现“开发机能运行、用户电脑缺 DLL”的情况。因此标准安装包不复制这套实验运行目录，
而是提供独立、可删除的官方运行环境。

## 推荐安装

1. 确认电脑有 NVIDIA 显卡、较新的驱动和至少 12 GB 可用磁盘空间。
2. 双击知影目录中的 `安装Qwen3-ASR.cmd`。
3. 脚本会先说明下载量，并在用户输入 `Y` 后才开始。
4. 安装完成后启动知影，在“语音模型链”中填写：

   ```text
   qwen3-asr-0.6b，faster-whisper
   ```

Qwen 不可用或运行失败时，知影会自动降级到 faster-whisper。

安装内容位于：

```text
知影\models\qwen3-asr-runtime\
知影\models\qwen3-asr-0.6b\
```

不再需要时可以在关闭知影后删除这两个目录，不影响标准模型。

## 手动安装

官方推荐使用独立 Python 3.12 环境：

```powershell
py -3.12 -m venv models\qwen3-asr-runtime
models\qwen3-asr-runtime\Scripts\python.exe -m pip install -U pip wheel
models\qwen3-asr-runtime\Scripts\python.exe -m pip install -U torch --index-url https://download.pytorch.org/whl/cu128
models\qwen3-asr-runtime\Scripts\python.exe -m pip install -U qwen-asr "huggingface_hub[cli]"
models\qwen3-asr-runtime\Scripts\hf.exe download Qwen/Qwen3-ASR-0.6B --local-dir models\qwen3-asr-0.6b
```

Qwen 官方项目与模型说明：

- <https://github.com/QwenLM/Qwen3-ASR>
- <https://huggingface.co/Qwen/Qwen3-ASR-0.6B>

中国大陆网络访问 Hugging Face 不稳定时，可按 Qwen 官方 README 改用 ModelScope 下载模型，
只要最终模型文件完整放入 `models\qwen3-asr-0.6b` 即可。

## 注意

- 安装脚本会联网下载公开软件包和模型，不会使用知影的云端 API Key。
- Qwen3-ASR 是本地推理，但 GPU 环境要求和磁盘占用明显高于 faster-whisper-small。
- 当前知影按固定音频块生成保守时间范围，不随附 Forced Aligner，因此时间戳精度不等同于
  Qwen 官方 Forced Aligner。
