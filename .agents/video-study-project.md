# 知影项目交接

更新时间：2026-07-22（Asia/Shanghai）

## 权威产品方向

- 唯一交互入口：\`.venv\\Scripts\\video-study.exe desktop --config config.yaml\` 或 \`启动桌面版.cmd\`。
- 主实现是 \`src/video_study/desktop.py\`。启动列表为空，不扫描 Resource；视频由用户多选，使用原绝对路径，不复制、不上传。
- 不存在 Web UI、本机 HTTP 服务、端口监听或 \`serve\` 命令；不要重新引入。
- 桌面没有独立来源回看按钮。Word/PDF 来源链接使用 \`video-study://play/<video_id>?t=<seconds>\`，直接调用本地播放器，不打开网页。
- \`src/video_study/localplay.py\` 在当前用户 HKCU 登记协议；优先 \`ffplay -ss\` 精确定位，缺失时回退系统默认播放器。

## 当前最高优先级（2026-08-06）

- 下一阶段实施依据为 `docs/implementation/知识整理升级实施任务书.md`，方案背景见 `docs/research/用户知识文档需求与工程优化讨论稿.md`。
- 用户提供的手工整理文档只用于提取呈现偏好和思考方式，不作为具体知识的标准答案或重型评测基准。
- 优先增加知识内容鉴别与选择：判断内容角色、知识类型、重要程度，以及“展开 / 简写 / 仅索引 / 忽略”。
- 再按概念、规则、流程、原理、对比、案例、边界和图表等知识类型组合专业整理提示词，避免一条通用提示词处理所有内容。
- 只做低成本的来源、方向词、冲突和缺图自检；不建设需要大量资源的质量门。
- 第一轮从 `summarize.py` 抽离 knowledge 层，保持旧 JSON 兼容和完整渲染链；不要趁机重写桌面 UI。

## 已实现

- JSON → Markdown → Word → PDF 可恢复流水线与时间戳溯源。
- Qwen3-ASR-0.6B / faster-whisper 顺序降级；本地整理、云端优化、自动处理。
- 逐视频阶段、进度、耗时、ETA、Token；精确取消只清当前视频缓存，不删除源 MP4。
- 内容保留量“精简 / 推荐 / 丰富”同时控制云端讲义详略、输出预算、离线知识点密度和截图数量。
- 云端知识文档已升级为课程讲义结构：学习目标、章节、详细解释、细节/步骤/案例/边界/易错点、整理说明、复习提示和课程复习清单；整理说明只能重组来源内信息，不补充外部事实。
- 关键画面按时间绑定到具体知识点，图注使用“与某知识点讲解同期”的保守表述；Markdown、Word、PDF 均把图片紧邻对应知识点渲染，同时兼容旧章节级图片。
- 多视频聚合模式默认关闭；开启后只调用一次云端，但可生成多个逻辑章节，保留结构化细节、知识点级配图和多来源链接。
- 聚合来源 URL 会统一重建为 `video-study://play/...`；聚合 ID 纳入生成器版本和来源章节指纹，避免继续复用旧 HTTP 产物。
- 设置折叠、模型链顺序、绿色成功/红色失败；“记住密钥”和“聚合模式”仅用颜色表达状态。
- 队列“全选 / 取消全选”合并为一个随状态切换的按钮；新增二次确认的“清理全部缓存”，只清空工作区中间缓存，保留工作区目录、最终文档和原视频，并拒绝清理项目根目录、用户目录或磁盘根目录。确认框明确提示：清理后本地溯源链接与 Markdown 截图需重新处理视频才能恢复。
- 产品名“知影 · 视频知识工作台”；水印由 \`config.yaml > desktop.watermark\` 配置，默认 \`powed by Fx\`、透明度 0.14。
- \`source_link_base\` 已进入知识缓存签名，协议变化会重建知识文档但不重跑 ASR。

## 验证基线（2026-07-22）

- 41 项单元测试、compileall、Node Word 渲染脚本语法检查、pip check、git diff --check 通过。
- \`src/video_study/player.py\`、FastAPI、uvicorn、8765 配置及旧前端测试/文档已删除。
- 已在用户授权下复用三个视频的 ASR 与截图缓存完成“推荐 / 丰富”云端 A/B；未重跑 ASR、未重新抽帧、未上传视频或截图、未下载模型、未提交 Git。
- Git 尚无首次提交，多数源码显示未跟踪。修改前启动的旧桌面进程可能仍驻留，人工验收前关闭旧窗口并重新启动。

## 课程讲义质量实验结论

1. A/B 产物位于 `output/experiments/course-notes-ab/推荐/` 与 `output/experiments/course-notes-ab/丰富/`，各含 JSON、Markdown、Word、PDF；`report.json` 保存结构指标与调用记录。
2. 三个视频均直接复用 manifest、转写和关键帧缓存；云端只接收压缩转写与来源块 ID。每个视频分别精炼，再在本地确定性合并，避免聚合请求二次压缩课程细节。
3. “丰富”样本全部使用 `qwen3.7-plus`；“推荐”早期样本中第一个视频因当时限额降级到 `kimi-k2.6`，其余两个使用 `qwen3.7-plus`，因此该比较不是严格的同模型基准。
4. 推荐：10 节 / 29 知识点 / 24,562 Markdown 字符；丰富：13 节 / 23 知识点 / 25,366 字符。丰富档步骤、案例、适用条件和整理说明更完整，更接近学完课程后的系统讲义；推荐档知识点更多但单点较浅。
5. 最终 PDF 已抽查首页、中页、末页和联系表：无裁切、重叠或缺图；链接均为 `video-study://`。已增加规则，知识点配图跳过长视频前 60 秒的桌面/片头候选，修复两处图文脱节。
6. `qwen3.7-plus` 已用极小 JSON 请求验证接受 6000 输出预算（42 tokens）；`api.yaml` 明确配置推荐档 `max_output_tokens: 5000`、丰富档 `rich_max_output_tokens: 6000`，丰富档超时 240 秒。8000 曾被当前兼容端点 HTTP 403 拒绝，不再使用。
7. 若时间对齐在其他课程仍明显错图，再评估可选视觉模型对候选缩略图二次对齐；该功能必须额外提示会上传截图并消耗额度，默认关闭。

## 安全约束

- 不得读取、打印、复制或提交 \`.env\`、\`QwenAPI.txt\` 的密钥。
- 云端必须显式授权。真实调用前说明发送的压缩转写和来源块 ID、候选模型顺序、预计次数/Token，等待用户确认。
- Resource、workspace、output、models 是本地数据/产物，不是源码。
- 不重建环境、不重新下载模型、不无理由重跑 94.5/144.7 分钟长视频。
- 除非用户要求，不启动长期进程、不提交 Git。

## onedir 打包现状（2026-07-23）

Windows x64 便携发行已经完成，最终产物位于 `pack/`：

1. `video-study.spec` 使用独立冻结入口，生成 `pack/知影/知影.exe`；无参数双击直接启动桌面。
2. 发行包随附 Python、Node.js、docx-js 和约 0.78 GB 的 faster-whisper-small 模型；Qwen3-ASR
   约 7 GB 的专用模型/运行库不进入标准包。
3. FFmpeg/ffprobe/ffplay 支持系统 PATH，也支持放入 `知影/tools/ffmpeg/`；缺少 ffplay 时仍回退
   系统默认播放器。
4. 配置、工作区和输出均以 EXE 目录为根；内部只读资源从 PyInstaller `_internal` 定位，不依赖
   启动时工作目录。
5. 已用中文和空格路径的 6 秒合成视频完成冻结版离线冒烟，JSON → Markdown → Word → PDF 全链
   成功；DOCX ZIP 结构、PDF 页数和 Markdown 均通过读取验证。
6. 已验证无参数桌面启动，以及 HKCU 的 `video-study://` 协议命令安全引用发行 EXE、发行配置与
   `"%1"`；现有单测继续覆盖 ffplay 精确时间参数和缺失时回退。
7. 构建入口为 `scripts/build_windows.ps1`，会运行测试（除非显式 `-SkipTests`）、整理发行目录并
   生成便携 ZIP。用户说明位于 `pack/README-安装与使用.md`，环境检查脚本位于发行目录。
8. 最终交付前必须继续确认发行目录不含 `.env`、`QwenAPI.txt`、用户视频或冒烟缓存；不得把首次
   构建过程中产生的旧 ZIP 作为最终产物。

## 安装版与可选模型（2026-07-24）

1. 产品版本提升至 0.2.0，采用 `icon/小电视.svg` 派生的红色圆角产品图标；图标已写入主程序、
   窗口和 Inno Setup 安装程序，并补充 Windows 文件版本信息。
2. `pack/知影-安装程序-v0.2.0.exe` 是推荐交付物：按当前用户安装到 LocalAppData，无需管理员
   权限，创建开始菜单/可选桌面快捷方式，注册 `video-study://`，卸载时保留“文档/知影”中的用户
   视频、缓存和输出。
3. 标准安装包现在随附 FFmpeg、ffprobe、ffplay 与对应许可证；路径查找固定优先使用
   `tools/ffmpeg/`，无须用户另装 FFmpeg。无 CUDA 运行库时 faster-whisper 自动回退 CPU。
4. Qwen3-ASR-0.6B 不放入标准包。发行目录提供 `安装Qwen3-ASR.cmd/.ps1`，使用独立 Python 3.12
   环境安装官方 `qwen-asr`、CUDA PyTorch 和模型；安装完整后桌面自动把 Qwen 加入语音模型链。
   不手工裁剪现有约 5.7 GB 运行时，因为 Torch/CUDA DLL 与包依赖易随版本变化。
5. 最终验证基线为 44 项单测、冻结版 doctor、内置 FFmpeg 三件套、ZIP 中央目录、Windows 版本
   信息、主程序隐藏启动 8 秒无崩溃；安装器由 Inno Setup 6.7.3 中文界面成功编译。

## UI 后续

- 在普通桌面会话验收高 DPI、缩放、最小窗口、中文字体、文件多选、折叠与状态控件。
- 保持人民币红/米白/绿色状态体系和短文案；优先真实交互缺陷，水印不得遮挡内容。

## 下一对话提示词

\`\`\`text
请阅读 AGENTS.md、MEMORY.md 和其中链接的 .agents/video-study-project.md，继续迭代“知影”，以交接文档为权威。

产品仅保留原生 Windows 桌面入口：.venv\\Scripts\\video-study.exe desktop --config config.yaml，主要实现是 src/video_study/desktop.py。启动列表必须为空，不扫描 Resource；视频使用用户选择的真实绝对路径，不上传、不复制。不要重新引入 Web UI、本机 HTTP 服务、端口监听或 serve 命令。

Word/PDF 来源回看必须保留，使用 video-study://play/<video_id>?t=<seconds>，由 src/video_study/localplay.py 直接调用本地 ffplay 定位，不打开网页。桌面不要增加独立回看按钮。

先检查 Git、进程、CLI、38 项测试、compileall、Node Word 渲染脚本语法、pip check、git diff --check、缓存与 PyInstaller 是否存在。不要重建环境、下载模型、读取/输出 .env 或 QwenAPI.txt 密钥，也不要无理由重跑长视频。

当前课程讲义结构和知识点级配图已经实现，但尚未真实调用云端验收。若继续产品结果优化，先复用已有 ASR/截图缓存，对推荐与丰富模式做一次受控 A/B；调用前说明发送内容、候选模型、次数和 Token 并等待授权。不要上传视频或截图。只有时间对齐仍明显失败时，才讨论可选视觉模型。

PyInstaller onedir 已完成并置于 pack：发行包随附 faster-whisper-small、Node/docx 资源，FFmpeg 支持 PATH 或 tools/ffmpeg，冻结入口、中文空格路径、JSON→Markdown→Word→PDF 和协议注册均已验证。后续改动需复跑构建与冒烟，不要把密钥、用户数据或测试缓存带入 pack；不要发起真实云调用。

UI 继续保持人民币红/米白配色、绿色成功状态、短文案和响应式布局。完成后运行完整测试、compileall、pip check 和 git diff --check。除非我要求，不启动长期服务、不提交 Git。
\`\`\`
