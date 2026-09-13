# JZ2440 通用应用平台

本项目已从单一 Codex Monitor 设备演进为可注册应用的嵌入式平台。RunBoard
与 Codex Monitor 均已按平台应用方式安装并完成现场生命周期回归；两者都不
开机自启，开发板 Reset 后默认进入 Qtopia。后续应用切换由 Windows Control
通过 `appctl` 接管。

## 运行时布局

```text
/opt/jz2440/
  bin/
    appctl
  apps/
    <app>/app.conf
    <app>/app
/tmp/jz2440/
  current-app
  current-pid
```

`/opt/jz2440` 是未来现场部署的独立 sibling 目录。应用注册信息和可执行文件位于 `/opt`；PID、当前应用和临时状态只位于 `/tmp`，不依赖持久化状态文件。

## appctl 约定

板端入口为 POSIX `/bin/sh` 脚本 `platform/board/appctl/appctl`，未来部署目标为 `/opt/jz2440/bin/appctl`，兼容 BusyBox 1.7.0：

```text
appctl list
appctl status
appctl start <app>
appctl stop
appctl qtopia
```

应用配置使用 `APP_NAME`、`APP_EXEC`、`APP_ARGS` 和 `APP_PROTOCOL`。配置被按键读取，不执行或 source 外部文件；执行路径必须是绝对路径，参数仅允许简单的路径、选项和值字符。

应用以**前台、同步**方式运行。`appctl start` 只在应用退出后清理状态并恢复 Qtopia，因此不会留下多个应用实例或误判启动成功。启动前发送 `<APPREADY|app>`，退出后发送 `<APPSTOP|app|RC=N>`。

## 模式与所有权

主机 Bridge 使用 Board Controller 识别三种模式：

```text
UNKNOWN       无法确认当前串口所有者，禁止改变状态
CONSOLE       shell/Qtopia 控制台，可执行受控 appctl 命令
APPLICATION   应用已通过 APPREADY 接管 UART；Bridge 只处理应用协议
```

`UNKNOWN` 不猜测、不发送启动或停止命令。切换到应用模式前，Controller 先通过控制台 marker 完成同步，再发送 `/opt/jz2440/bin/appctl start codex-monitor` 并等待 APPREADY。切回控制台时，明确的主机退出操作发送 `<CQMQUIT>`，等待 APPSTOP，再重新同步 shell marker。

Codex Monitor 的应用配置仍使用其现有 direct serial backend 和 CQM1 协议；Bridge 仍采用 Board Request → Host Response：只有收到 `<CQMREQ|V=1>` 才回复一帧真实 CQM1，没有 request 时 quota TX 为零。

## 主机命令

在现场串口已确认且应用目录已部署后，Bridge 提供：

```text
CodexQuotaBridge.exe board list [--port COMx]
CodexQuotaBridge.exe board status [--port COMx]
CodexQuotaBridge.exe board start codex-monitor [--port COMx]
CodexQuotaBridge.exe board stop [--port COMx]
```

这些命令由 `BoardController` 管理串口控制台/应用状态，由 `BoardApplicationSession` 处理 APPREADY、APPSTOP、CQMREQ 和 CQM1。Controller 通过 `/opt/jz2440/bin/appctl` 调用板端控制器。当前单元测试使用确定性 fake transport 覆盖同步、列表、启动、停止、请求响应、分片帧、畸形生命周期帧和多次请求。

## 已验收的平台应用

当前 `appctl list` 至少包含：

```text
codex-monitor
runboard
```

RunBoard 已安装在平台标准应用目录并保留 755 可执行权限；最终 target
为 44876 bytes，Reset 后文件仍存在且校验一致。Codex Monitor 的既有 target
与配置未被覆盖。现场已验证 Qtopia → RunBoard → Qtopia、Qtopia → Codex
Monitor → Qtopia，以及 Codex Monitor → RunBoard 的串行切换；任意时刻只允许
一个应用拥有 LCD/UART 和应用生命周期。

最终默认状态为：

```text
appctl status = QTOPIA
RunBoard       = not running
Codex Monitor  = not running
```

RunBoard 与 Codex Monitor 均不是 startup/rcS 自启动项；本次安装没有修改
Flash、NAND、bootloader、kernel、U-Boot 环境或全局启动脚本。

## 启动策略

本轮拒绝沿用直接改 `rcS` 的 Board Autostart 方案。此前实机验证表明，旧系统的 `askfirst` shell 与 Monitor 争用同一 UART RX，会破坏退出和恢复路径。为保护可启动性，`/etc/init.d/rcS` 已恢复原状，Qtopia 保持原启动链；`docs/history/rejected-board-autostart/boot-monitor.sh` 等旧草案仅保留作历史/回滚参考并标记为 deprecated。

## Windows Control handoff

Windows Control 下一阶段只通过平台契约管理应用：连接串口、读取
`appctl list/status`、停止当前应用并等待 `APPSTOP`、启动目标应用并等待
`APPREADY`，然后更新主机 UI。Control 不应直接 kill target、写 LCD、覆盖 PID
或绕过 launcher/appctl。

推荐切换状态机：

```text
QTOPIA → STARTING → APP_RUNNING → STOPPING → QTOPIA
APP_A  → STOPPING → APPSTOP → QTOPIA → STARTING APP_B → APPREADY → APP_B
```

串口断开时停止当前切换动作并进入可观测的 reconnect 状态，不自动执行无限
重启或永久恢复动作。
