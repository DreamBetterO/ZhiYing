# 第三方组件说明

ZhiYing 核心包包含由 Python 打包器冻结的 Python 解释器和应用依赖。具体依赖版本以构建环境锁定结果、`pyproject.toml`、`package-lock.json` 与 `BUILD-MANIFEST.json` 为准。

模型、FFmpeg、Node.js、yt-dlp、PyTorch 和 CUDA 推理运行时不属于核心包；使用者从 `release/DOWNLOADS.md` 列出的上游来源获取，并遵守各上游项目的许可证与 NOTICE。

正式公开发布前必须完成人工许可证审计、生成 SBOM，并为所有自研 Windows 可执行文件配置可信代码签名。
