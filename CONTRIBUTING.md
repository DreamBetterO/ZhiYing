# 参与贡献

感谢你改进知影。项目是 Windows 原生桌面应用，稳定边界包括 JSON → Markdown → Word → PDF 数据流、时间戳可溯源、单视频单 VLM 会话和本地优先策略。

## 开始之前

1. 先搜索现有 Issue，确认问题尚未被记录；
2. 阅读 [`README.md`](README.md)、[开发文档](docs/项目文档/开发文档.md)和[架构详解](docs/项目文档/架构详解.md)；
3. 功能变化先补充失败的回归测试，再修改最小责任模块；
4. 不提交 `.env`、模型、工具、视频、Workspace、输出、缓存或发行 ZIP。

## 开发环境

```powershell
conda activate ImageT10
$env:PYTHONPATH = "$PWD\src"
python -m zhiying desktop --config config.yaml
```

## 提交前检查

```powershell
python -m unittest discover -s tests
python -m compileall -q src tests
python -m pip check
git diff --check
```

发行相关修改还需运行：

```powershell
python -m unittest tests.test_release_distribution
```

## Pull Request 说明

请在 PR 中写明：

- 用户可见的问题与预期行为；
- 修改的责任模块；
- 新增或更新的测试；
- 完整验收结果及跳过项；
- 是否涉及模型、依赖、云请求、便携构建或用户数据；
- 截图或文档变化（如适用）。

不要用格式美化掩盖真实失败。架构合同变化应同步更新 `docs/architecture/`，发行变化应同步更新 `release/`。
