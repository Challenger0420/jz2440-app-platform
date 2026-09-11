# Deployment preparation

> Historical Codex Monitor lifecycle reference. The current long-term
> direction is the generic application platform documented in
> [APP_PLATFORM.md](APP_PLATFORM.md). Direct `rcS` Board Autostart is
> deprecated/rejected because the legacy `askfirst` console reader contends
> with a foreground application on the same UART. This document does not
> authorize enabling autostart.

本阶段只准备长期运行所需的接口和恢复设计；最终自动启动尚未启用。

## 已确认的串口映射

USB serial adapter → JZ2440 UART0 → `/dev/s3c2410_serial0`。依据是：

1. USB serial adapter 的 shell 已验证可交互；
2. kernel command line 使用 `console=ttySAC0`；
3. `/proc/tty/driver/serial` 只有 UART 0 具有有效 MMIO/IRQ 信息；
4. `/dev/ttySAC*` 不存在；`/dev/ttyS0` 虽存在但 direct backend 的 `TCGETS` 配置失败；实际可打开并接收的 UART0 节点是 `/dev/s3c2410_serial0`。

正式部署前仍应在停止 Qtopia、shell 前台等待的受控窗口中用 direct backend 做实测；不能仅根据节点名称猜测。

## Monitor 输入模式

```text
调试：  codex-monitor --stdin
  正式：  codex-monitor --serial /dev/s3c2410_serial0
```

两种模式共享同一个 CQM1 parser 和 framebuffer renderer。direct 模式使用现有 freestanding OABI runtime 的 `open`、`ioctl`、`fcntl`、`read`、`close`：

- 115200 baud
- 8 data bits
- 1 stop bit
- no parity
- no hardware/software flow control

direct backend 保存并恢复原始 termios，避免 `<CQMQUIT>` 后 shell 留在 raw/non-echo 状态。

## Windows Bridge

显式 `--port COMx` 时使用用户指定端口；未指定时按 USB serial adapter 的 VID/PID 在 WMI PnP 信息中查找唯一 COM 端口。零个候选会在下一周期重试，多个候选会拒绝猜测并等待显式端口。SerialPort 打开、写入或 provider 失败均释放旧连接并在下一周期重试。

Bridge 采用 request-driven 模式：只监听 `<CQMREQ|V=1>\\n`，收到一个有效请求后读取一次真实 quota 并回复一帧 CQM1；没有请求时不发送 quota。`--quit` 仅用于明确的用户退出操作。

## 主机与板端辅助脚本

- `scripts/start-monitor.sh`：板端 supervisor，幂等检查 monitor，临时停止 Qtopia，再执行 `--serial /dev/s3c2410_serial0`。
- `scripts/stop-monitor.sh`：板端本地 PID fallback；正常退出仍由主机明确发送 `<CQMQUIT>` 完成。
- `scripts/run-bridge.ps1`：Windows 登录后使用的隐藏、可重启 Bridge 包装器。
- `scripts/stop-board-monitor.ps1`：停止常驻 Bridge 后发送一次明确的 `<CQMQUIT>`。

USB 拔插后的最终重连验证仍需现场完成：`USB_RECONNECT_FINAL_GATE = PENDING_PHYSICAL_TEST`。当前状态功能 RC 尚未覆盖持久 ELF，也未修改启动链。

## 验收顺序

1. 上传到 `/tmp` 的临时 RC；
2. direct serial 连续至少五个真实 refresh；
3. Bridge 拔插/COM 号变化后自动重新枚举；
4. stale → reconnect → normal；
5. `<CQMQUIT>` 后恢复 Qtopia/Today；
6. 全部通过后再单独评审 FINAL_DEPLOYMENT。

## 当前软件准备状态

通用应用平台的控制面已在主机上实现并通过模拟测试：`appctl` 负责注册
应用的前台生命周期，`BoardController` 负责 Console/Application/Unknown
模式和安全同步，`BoardApplicationSession` 保留 request-driven CQM1。
未来现场应先执行 `scripts/verify/verify-app-platform.ps1` 的控制台检查，再单独
验证应用串口所有权；本轮不运行部署脚本，不访问开发板。
