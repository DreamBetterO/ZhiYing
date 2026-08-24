# 故障排查

## 双击后没有反应

1. 确认已经完整解压 ZIP；
2. 确认 `_internal` 与 `ZhiYing.exe` 在同一目录；
3. 双击 `doctor.cmd` 查看缺少的组件；
4. 仍无法启动时，再运行 `ZhiYing-Console.exe` 查看错误信息。

## 出现黑色窗口

- 打开 `doctor.cmd` 或 `ZhiYing-Console.exe` 时出现黑色窗口是正常的；
- 日常使用请只打开 `ZhiYing.exe`；
- 正常处理视频时不应反复弹出黑色窗口。

## Windows 或安全软件警告

未签名版本第一次启动时可能显示“未知发布者”。请确认文件来自项目正式发布页并核对 SHA-256。正常使用过程中不应反复要求批准多个子程序。

## 提示缺少模型或工具

查看[下载说明](DOWNLOADS.md)，重点检查文件夹名称和层级。`models`、`tools` 必须与 `ZhiYing.exe` 放在同一层。

## GPU 未识别

1. 更新 NVIDIA 驱动并重启；
2. 确认 `nvidia-smi` 能显示显卡；
3. 运行 `doctor.cmd`；
4. 不要混用其他 Python 环境或来源不一致的 CUDA 文件。

## 视频链接无法下载

确认网络正常且 `tools/yt-dlp/yt-dlp.exe` 存在。部分网站可能限制下载；可先将视频保存到本地，再使用“＋ 添加本地视频”。
