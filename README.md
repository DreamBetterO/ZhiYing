# 知影 · 视频知识工作台

原生 Windows 桌面视频整理工具，处理链路为：

```text
MP4 → 音频/关键帧 → 带时间戳转写 JSON → 知识 JSON
    → Markdown → Word → PDF
```

视频由用户在桌面软件中选择，始终使用真实绝对路径，不上传、不复制。启动列表为空，不扫描
`Resource`。云端整理默认关闭，启用前会展示发送内容、模型和预计额度并要求用户确认。

## 启动

```powershell
.venv\Scripts\video-study.exe desktop --config config.yaml
```

也可双击 `启动桌面版.cmd`。

桌面端支持批量队列、本地整理、云端优化、自动处理、模型降级、内容保留量、聚合模式、精确取消、
缓存恢复、Token/耗时/ETA，以及打开原视频、Word、PDF 和产物目录。队列使用一个“全选 / 取消全选”切换按钮；
“清理全部缓存”经二次确认后只清空工作区中间文件，不删除最终文档和原视频。清理后，本地溯源链接与 Markdown
截图需要重新处理对应视频才能恢复。

云端优化按“精简 / 推荐 / 丰富”生成不同详略的课程讲义，可保留学习目标、详细解释、步骤、
课程案例、适用边界、易错点和复习清单。关键画面按来源时间绑定到具体知识点并紧邻正文；多视频
聚合会生成多个逻辑章节，并继续保留每个知识点的本地来源回看链接。

## Word/PDF 时间戳溯源

文档中的来源链接使用 `video-study://` 本地协议。桌面程序启动时会为当前 Windows 用户登记该协议；
点击 Word/PDF 中的来源时间后，系统直接调用本地播放器并定位，不会启动 Web 服务或打开网页。
当前优先使用 `ffplay -ss` 精确定位；找不到 ffplay 时回退系统默认播放器，此时可能需要手动定位。

## 配置与安全

- `config.yaml`：本地处理、渲染、桌面水印等配置。
- `api.yaml`：非敏感云端端点、模型链与预算配置。
- `.env`：仅在用户启用“记住密钥”后保存 API Key，已被 Git 忽略。
- `workspace/`、`output/`、`models/`、`Resource/`：本地缓存、产物、模型和用户数据，不作为源码提交。

禁止把 `.env` 中的密钥输出或提交。删除缓存与产物不会删除源 MP4。

## 目录约定

```text
src/video_study/   产品源码
tests/             自动化测试
scripts/           开发与渲染辅助脚本
docs/              项目索引与执行入口
docs/迭代升级/     执行事实、已批准版本与现行架构任务书
icon/              桌面窗口图标
Resource/ 视频/    用户输入数据
workspace/ output/ 可恢复中间数据与最终文档
models/            本地模型和运行时
```

根目录只保留启动入口、项目配置、依赖清单和协作入口。一次性实验脚本、测试输出与工具会话不得放在
根目录；可复用工具进入 `scripts/`，临时结果进入已忽略的 `tmp/`，产品数据继续使用既有数据目录。

## 开发验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\python.exe -m pip check
git diff --check
```

详细的项目交接、执行事实和现行架构说明见 [项目索引](docs/项目索引.md)。
