# ZhiYing 发行工程

本目录只保存核心程序的可复现构建与校验逻辑。面向使用者的发行说明和下载清单位于源码仓库根目录 [`release/`](../../release/)。

## 核心包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release/build_core.ps1
```

输出默认进入 `release/output/`。该目录被 Git 忽略，只用于本机检查和后续上传附件。核心包明确排除 `models/` 与 `tools/` 的实际内容；构建完成后会执行 CLI 帮助、自检、秘密文件、空数据目录和完整性清单验证。

离线测试与校验使用项目规定的 Conda `ImageT10`；PyInstaller 冻结复用项目现有 `.venv` 工具链，不会向 Conda 环境重装依赖。

模型、工具和 CUDA 推理运行时不再由发行脚本复制或组合。它们只通过 `release/manifests/components.json` 和 `release/DOWNLOADS.md` 指向官方来源。模型下载、依赖重装、云请求和长视频测试不属于核心构建脚本的隐式行为。

版本定稿和 GitHub Release 上传边界见[维护者发布流程](PUBLISHING.md)。
