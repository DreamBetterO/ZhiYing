# 文件校验

`SHA256SUMS.txt` 用于确认下载的核心 ZIP 是否完整、是否被意外修改。

在 ZIP 所在目录打开 PowerShell，运行：

```powershell
Get-FileHash .\ZhiYing-Core-1.0.0-win-x64.zip -Algorithm SHA256
```

显示的哈希值应与 `SHA256SUMS.txt` 完全一致。模型和工具请使用各自官方下载页提供的校验信息。
