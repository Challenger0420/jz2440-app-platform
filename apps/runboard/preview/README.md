# RunBoard Windows Preview

这是 480x272 JZ2440 屏幕的 Windows Preview。默认读取 `shared/mocks/*.json`；`--live` 模式通过本地 `config/config.local.json` 使用 OpenSSH 执行只读服务器查询，不连接串口或开发板。

在仓库根目录的 Windows PowerShell 中运行：

```powershell
python apps\runboard\preview\runboard_preview.py --scenario single
```

读取一次真实服务器快照并打开 Live Preview：

```powershell
python apps\runboard\preview\runboard_preview.py --live
```

Live 模式由 Host Aggregator 合并 server 和 Codex Usage provider。当前 Codex Usage
仍使用明确标注的 mock fallback；失败时保留窗口，显示 `OFFLINE` / `ERROR` 或
`STALE`，并写入本地 `build/runboard/live.log`。Live Preview 按 UI 周期重绘，
但 provider 按 `config.example.json` 中的采样周期缓存，避免高频 SSH。

RunBoard 的 compact board frame encoder/decoder 和 MemoryTransport 只在本地
运行测试，不打开 COM 口。

启动后可用顶部按钮切换：

- `IDLE`：0 个实验，服务器空闲。
- `SINGLE`：主实验 + Codex Usage。
- `DOUBLE`：主实验 + 第二实验，底部自动替换为 Second Experiment。
- `COMPLETED` / `ERROR`：额外状态检查。

本 Preview 使用标准库 Tkinter，不要求安装第三方 UI 框架。
