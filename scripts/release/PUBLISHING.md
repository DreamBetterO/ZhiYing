# ZhiYing 发布流程（维护者）

本文定义源码、用户文档和核心 ZIP 如何组成同一个公开版本。创建提交、标签、推送和上传附件仍需用户明确批准。

## 发布位置

### Git 仓库

- `src/`、`tests/`、`scripts/`；
- 根 `README.md` 与开发文档；
- `release/` 下的用户文档、`manifests/*.json`、`checksums/`；
- `release/output/.gitkeep`。

### GitHub Release 附件

- `ZhiYing-Core-<版本>-win-x64.zip`；
- 与 ZIP 同次定稿生成的 `SHA256SUMS.txt`。

核心 ZIP 不得执行 `git add release/output/*.zip`，应与源码标签一起上传到同版本 GitHub Release。

### 永不上传

- `.env`、API Key 和本机私有配置；
- `models/`、`tools/`、CUDA/Python 运行时；
- 用户视频、`workspace/`、`output/`、`Resource/`；
- `packing/` 历史便携目录；
- 构建缓存、解压检查目录和调试残留。

## 发布前检查

1. 产品版本、ZIP 文件名、[`stable.json`](../../release/manifests/stable.json) 和源码版本一致；
2. 获得本次便携构建的单独授权；
3. 完整离线测试、编译、依赖与差异检查通过；
4. 核心包不含模型、工具、密钥、用户数据和调试残留；
5. ZIP 内包含 README、快速开始、下载、GPU、隐私、故障排查和组件清单；
6. `ZhiYing.exe` 为普通入口，`ZhiYing-Console.exe` 只作诊断；
7. `finalize_release_repo.py` 生成与 ZIP 一致的 `SHA256SUMS.txt`；
8. 人工在干净目录解压，运行 `doctor.cmd`，再启动 `ZhiYing.exe`；
9. 用户检查产出物并批准后，才允许创建提交、标签、推送和上传附件。

上传后应重新下载附件并核对文件大小与 SHA-256。
