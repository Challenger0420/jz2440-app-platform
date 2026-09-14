# JZ2440 Control Phase 2：真实开发板集成

## 1. 目标与验收边界

Phase 2 将 Phase 1 的 WinForms 骨架接到真实 JZ2440 应用平台，目标是由一个 Windows 宿主进程完成：

- 串口自动发现、打开、探测和断开检测；
- 查询当前应用和板端已注册应用；
- 在 Qtopia、Codex Monitor、RunBoard 之间执行受控切换；
- 在同一串口 transport 上承载应用生命周期协议和应用数据协议；
- 不修改 Flash、bootloader、kernel、rootfs、启动链或自启动配置。

本阶段没有引入板端 `appd`，也没有新增并行 UART mux。板端生命周期 owner 仍是现有 `appctl`。

Codex Monitor 的当前最终构建状态来自现有冻结记录：目标文件为 51400 bytes，SHA256 为
`8C2C20DB7E2C1217B385E4BADADDF1B9769C9C7C2EC96ED6FA26290EBE92EA35`，并已标记为 FEATURE FREEZE。
RunBoard 当前仓库也已有 `app.conf`、target、bridge 和最终构建产物；因此 Phase 1 设计文档中“RunBoard 尚未注册、没有 target”的审计结论已经过时，不能继续作为当前状态依据。

## 2. 最终架构

```text
JZ2440 Control.exe
    |
    +-- ControlViewModel / WinForms UI
    |
    +-- SerialDeviceConnection
          |
          +-- BufferedSerialTransport     (唯一物理 SerialPort reader)
          |       |
          |       +-- BoardController     (probe / appctl / lifecycle)
          |       +-- Codex provider       (CQMREQ -> CQM1)
          |       +-- RunBoard provider    (live worker -> RB1)
          |
          +-- AppRegistry + ApplicationAdapter
          +-- appctl compatibility commands
                    |
                    +-- Qtopia
                    +-- Codex Monitor
                    +-- RunBoard
```

关键原则是“一个 Windows host、一个物理 SerialPort、一个物理读者”。BoardController 和活动应用 provider 不会分别读取 `SerialPort`；它们轮流从 `BufferedSerialTransport` 的缓冲区消费数据。切换时先停止 provider，再发送目标应用的 stop frame，等待生命周期确认后才启动下一个 provider。

旧的 `CodexQuotaBridge` 和 `RunBoardBridge` 仍保留为兼容 CLI、部署和诊断工具，但不能与 Control 并行占用 UART。本次现场接管前检查到一个正在运行的 RunBoardBridge，只停止了该精确宿主进程，随后通过 `<RBQUIT>` 和 `APPSTOP` 安全收回板端应用；没有删除 bridge、配置或源码。

## 3. Windows 端模块

| 模块 | Phase 2 职责 |
|---|---|
| `MainForm` / `ApplicationCardControl` | 展示设备、当前应用和动态应用卡片；只负责定时重发现/重连调度，不承载串口或 lifecycle 逻辑 |
| `ControlViewModel` | UI 与 connection 的边界；使用 switch lock 防止重复 Launch，并在切换期间禁用卡片 |
| `SerialDeviceConnection` | 连接状态、探测、切换顺序、provider 生命周期、断线状态 |
| `BufferedSerialTransport` | 唯一物理 SerialPort reader；缓冲输入并向上层提供 push-back |
| `BoardController` | 复用现有 console/application 模式、echo probe、appctl list/status、APPREADY/APPSTOP 等语义 |
| `AppRegistry` | 动态应用注册和 Available/Unavailable/Starting/Running 等状态 |
| `ApplicationAdapter` | 每个应用的 start/stop frame、ready/stop marker、payload 识别 |
| `CodexApplicationProviderSession` | 复用 `RealCodexProvider` 和 `BoardApplicationSession`，在收到 CQMREQ 后发送 CQM1 |
| `RunBoardApplicationProviderSession` | 启动现有 `runboard_state_cli.py --live-worker`，生成首帧和后续 RB1，并复用同一 transport |
| `ConfigurationStore` / `FileLogger` | 使用 `%LocalAppData%\\JZ2440Control`；不读取或提交仓库中的真实本机配置 |

Windows 工程保持 SDK-style、C#、.NET 8、WinForms、`net8.0-windows`。现有旧 bridge 源码通过 linked compile item 复用，没有进行大范围搬迁。

## 4. 连接和当前应用查询

### 4.1 连接流程

1. `ProlificComPortDiscovery` 按 USB VID/PID 枚举兼容 PL2303 设备；不写死 COM 编号。
2. 只有一个候选端口时自动选择；零个或多个候选时不猜测。
3. 以 115200、8 data bits、no parity、1 stop bit、no flow control 打开。
4. 启动 `BufferedSerialTransport`，让物理串口只有一个后台 reader。
5. 先观察应用 payload：`CQMREQ` 识别 Codex，`RB1` 识别 RunBoard。
6. 未识别为应用时发送唯一 `echo` marker，确认交互 console。
7. console 模式下调用现有 `appctl list`，然后调用 `appctl status`：只有返回 `QTOPIA` 才把当前应用置为 Qtopia；`STOPPED` 或空响应保持 Unknown。
8. 应用模式下只依据真实应用 payload 识别当前应用；没有 payload 时 fail-closed，不猜测当前应用。

`appctl status` 查询增加了有限重试，以覆盖 appctl 停止应用后异步恢复 Qtopia 的窗口。串口进入 `Disconnected/Error` 后，WinForms 层重新发现端口；没有兼容候选时保持 `Disconnected`，不会闪烁 `Connecting -> Error`。只有唯一候选才尝试重新连接，重连只重新探测/查询当前应用，不自动重放上一次启动动作。探测失败时停止自动循环，保留稳定的 `Error` 并允许人工 `Connect` 重试。

### 4.2 RunBoard Available 条件

RunBoard 默认不是无条件 Available。它满足以下任一实际证据后才变为 Available：

- console 查询的 `appctl list` 包含 `runboard`；
- 当前收到真实 `RB1` traffic，说明目标正在运行。

如果 appctl 没有注册 RunBoard，卡片保持 Unavailable，Control 不会尝试启动它。Codex Monitor 使用同一 registry/adapter/card 体系；未来新增应用只需增加 descriptor、adapter 和 provider，不需要改变主界面。

## 5. 生命周期和协议共存

当前真实板端仍使用 console-compatible 的 `appctl` 路径，而不是强行发送 Phase 1 设计文档中的 `<APP|REQ>` 控制帧。

### 5.1 生命周期命令

```text
console -> /opt/jz2440/bin/appctl start codex-monitor
console -> /opt/jz2440/bin/appctl start runboard
board   -> <APPREADY|codex-monitor>
board   -> <APPREADY|runboard>
host    -> <CQMQUIT>
host    -> <RBQUIT>
board   -> <APPSTOP|codex-monitor|RC=N>
board   -> <APPSTOP|runboard|RC=N>
```

停止成功优先要求对应 `APPSTOP` 和后续 console echo 同步。若 provider 恰好在应用退出边界消费了 APPSTOP，Control 只在一个新的 console echo marker 成功时才接受 stop，并记录为 console-probe recovery；如果应用仍占用 UART，操作仍失败并保持 Unknown/Error。

### 5.2 应用数据协议

- Codex Monitor：板端 `CQMREQ`，Windows provider 使用现有 `RealCodexProvider`，发送 `CQM1`。
- RunBoard：Windows provider 使用现有 live worker 生成 `RB1`，板端 target 负责解析、校验、绘制并发出诊断信息。
- `CQM1` 和 `RB1` 都只由当前活动 provider 写入；Qtopia 模式不发送应用 payload。
- `APPREADY`、`APPSTOP`、CQM 和 RB1 由 adapter/会话按前缀分发，不由 Form 事件处理。

通用 `AppControlProtocol` parser/encoder 仍保留给后续结构化控制协议，但 Phase 2 没有要求旧 target 理解 `<APP|REQ>`，也没有引入会和现有应用直接读 UART 冲突的 `appd`。

## 6. 状态机和错误处理

设备状态：

```text
Disconnected -> Connecting -> Connected
                    |             |
                    +----------> Error
Connected -> Disconnected
```

应用状态：

```text
Unknown -> Available -> Starting -> Running -> Stopping -> Available
              |            |          |           |
              +--------> Unavailable  +--------> Failed
```

规则：

- `ControlViewModel` 以 semaphore 拒绝第二个同时进行的切换，返回 `SWITCHING`。
- `Starting` 时不允许重复 Launch；`Stopping` 时不允许启动其他应用。
- 当前 app 卡片显示 `Running`；切换期间显示 `Starting`/`Stopping`。
- `APPREADY` 未确认时进入 Failed，不把目标显示为 Running。
- stop 未确认时不自动猜测 Qtopia；保持 Failed/Unknown，要求重连或重新探测。
- 串口异常异步清理 provider 和 transport、清空当前应用判定并显示 Serial connection lost；重新连接只做探测和查询，不自动恢复上一个应用。
- Current App 为 Unknown 时，UI 禁用所有 Launch/Retry，避免把 `CURRENT_UNKNOWN` 继续发送到板端。
- provider 异常不会让另一个 provider 或旧 bridge 重新打开 UART；用户可以切换或重连。

## 7. 配置与安全

默认配置不包含 COM 编号、用户名、服务器地址、SSH 信息或内部本机绝对路径：

- `BackendMode` 默认是 `serial`；开发时可在 `%LocalAppData%\\JZ2440Control\\config.json` 显式选择 `mock`。
- `PreferredPort` 仅是本地覆盖，不是仓库默认值。
- `RunBoardPython`、`RunBoardStateScript`、frame interval 是本地配置；未配置脚本时 Control 不会伪造 RunBoard 数据。
- RunBoard 的真实 provider 配置仍由受控本地配置读取；UI、日志、README 和截图不展示真实服务器连接信息。
- 日志写入 `%LocalAppData%\\JZ2440Control\\logs`，仓库 `.gitignore` 已覆盖本地配置、日志和 build 输出。

Phase 2 没有创建或提交真实本机配置文件。

## 8. 本阶段修改和复用

主要新增/修改：

- `platform/host/windows-control/src/Jz2440.Control.Core/SerialDeviceConnection.cs`
- `platform/host/windows-control/src/Jz2440.Control.Core/BufferedSerialTransport.cs`
- `platform/host/windows-control/src/Jz2440.Control.Core/ApplicationProviderSessions.cs`
- `platform/host/windows-control/src/Jz2440.Control.Core/AppRegistry.cs`
- `platform/host/windows-control/src/Jz2440.Control.Core/ApplicationAdapters.cs`
- `platform/host/windows-control/src/Jz2440.Control.Core/Configuration.cs`
- `platform/host/windows-control/src/Jz2440.Control.Core/ControlViewModel.cs`
- `platform/host/windows-control/src/Jz2440.Control/MainForm.cs`
- `platform/host/windows-control/src/Jz2440.Control/ApplicationCardControl.cs`
- `platform/host/windows-control/tests/Jz2440.Control.Core.Tests/Program.cs`
- `apps/runboard/bridge/RunBoardAdapter.cs`
- `apps/runboard/bridge/RunBoardBridgeProgram.cs`
- `scripts/build/build-runboard-bridge.ps1`
- `platform/host/board-controller/BoardController.cs`

复用的现有组件：

- `ComPortDiscovery` / `SerialTransport`；
- `BoardController` 的 console/application 状态、echo probe、APPREADY/APPSTOP 处理；
- `CodexMonitorAdapter`、`BoardApplicationSession`、CQM parser/encoder；
- `RealCodexProvider` 和 Codex provider transport；
- RunBoard 的 `RunBoardAdapter`、live worker、RB1 host/target protocol；
- `platform/board/appctl/appctl` 的应用注册、前台生命周期、Qtopia 恢复和 PID 清理。

没有删除旧 bridge，没有修改 appctl、target、Flash、rootfs 或启动链。

## 9. 验证记录

### 9.1 软件验证

- .NET 8 SDK-style solution restore/build：PASS。
- `Jz2440.Control.Core.Tests`：PASS。
- 覆盖 registry 状态转移、连接状态转移、mock 切换、失败恢复、重复 Launch 防护、协议 parser 和 RunBoard adapter/lifecycle：PASS。
- RunBoard legacy bridge rebuild：PASS。
- RunBoard bridge self-test：PASS。
- Codex provider-only 5 次真实 provider 检查：PASS；无串口访问。

### 9.2 真实板验证

显式硬件 smoke 命令为：

```text
dotnet run --project platform/host/windows-control/tests/Jz2440.Control.Core.Tests -- --hardware
```

本阶段实际通过：

1. 自动发现并连接真实板，console 查询得到 Qtopia 基线；
2. Qtopia -> Codex Monitor -> Qtopia；
3. Qtopia -> RunBoard -> Qtopia；
4. Codex Monitor -> RunBoard -> Qtopia；
5. Codex provider 实际发送 CQM1；
6. RunBoard live provider 实际发送首个 RB1；
7. APPREADY/APPSTOP、Qtopia 恢复和 stop/PID 生命周期安全；
8. Control 释放串口后的逻辑断开/重新连接，回到 Qtopia 查询状态；
9. 收尾只读检查确认 `appctl list` 仍注册 Codex Monitor 和 RunBoard，宿主进程均已退出。

本节保留 Phase 2 集成阶段的离线/逻辑验证记录；物理 USB 拔插和 LCD 人工核对已在下方“9.3 最终现场验收”中完成。

### 9.3 最终现场验收（2026-09-14）

- Control 作为唯一 UART owner 启动，自动发现并连接开发板，重连后重新查询 Qtopia。
- 物理 USB 拔出后进入 `Disconnected`，Current App 清空，Launch/Retry 禁用；重新插入后自动恢复连接，本次未观察到 COM 号变化。
- 重连后通过 GUI 完成 Qtopia → Codex Monitor → RunBoard → Qtopia；Windows Current App、Running 状态和 LCD 画面人工核对一致。
- Codex provider 继续发送 CQM1，RunBoard provider 首帧 RB1 正常发送；RunBoard 的 `server offline` 仅记录为数据源状态，不影响 lifecycle 通过。
- 最终恢复 Qtopia 并关闭 Control；未发现 Control、旧 Bridge 或 RunBoard worker 残留。
- 现场首次在 Unknown 状态点击应用时暴露了可操作性问题；已改为 Unknown 时禁用 Launch/Retry，并重新构建、测试和完成现场复验。

WinForms GUI 进程已按默认 serial 模式启动，并通过 Control 本地日志确认真实 console 连接。当前桌面自动化 trusted RPC 不可用，因此本阶段没有自动点击卡片；切换业务逻辑通过同一 Core connection/VM 路径完成，UI click-level 验证保留为人工现场检查。

## 10. appd / UART mux 重新评估

Phase 2 结果仍不要求 `appd`：

- `appctl` 已能完成注册、start、stop、Qtopia recovery 和 PID 清理；
- Control 作为唯一 Windows UART owner，应用 provider 与 lifecycle 控制共享同一 transport；
- 当前应用协议已有明确前缀，CQM 和 RB1 不需要第三个常驻串口 reader。

仍需重新评估 `appd` 的条件：

- 需要在任意应用运行期间同时接收独立控制帧；
- 应用数据和控制数据必须长期复用一根 UART 且无法通过时分复用协调；
- 应用数量、并发控制或故障隔离超过 console-compatible appctl 的边界。

如果将来引入，必须先定义 UART mux、应用 IPC 和迁移期协议，并禁止 `appd` 与目标应用同时直接打开同一 UART。

## 11. 后续工作

1. 决定是否将 RunBoard live worker 从脚本进程抽成可部署的 provider 组件，减少 Windows 发布对 Python 的运行时依赖。
2. 为真实配置提供脱敏 example，而不是把本机配置复制进仓库。
3. 根据应用数量和协议共存需求再次评审 appd/UART mux；在此之前不改板端启动链。

## 12. 最终 ownership 与离线收尾审计

### 12.1 UART ownership

- 正常模式只有 `JZ2440 Control.exe` 打开物理 COM；`SerialDeviceConnection` 创建一个 `SerialTransport`，`BufferedSerialTransport` 启动一个物理 reader。
- `BoardController`、Codex provider 和 RunBoard provider 都只通过这条 transport/session 工作，不自行发现、打开或关闭 COM。
- `CodexQuotaBridge.exe`、`RunBoardBridge.exe` 和手动串口脚本仍可用于诊断或兼容部署，但属于互斥工具；它们不能与 Control 并行运行。
- provider 停止会先取消 provider task、停止本地 worker 并等待其退出，然后才允许下一次 lifecycle 写入；断线会停止 provider、清空当前应用判定并禁止盲目恢复。

### 12.2 生命周期和 fail-closed

- 正常切换顺序固定为：停止 provider → 发送应用 stop frame → 等待 `APPSTOP`/console probe → 通过 `appctl start` 启动目标 → 等待 `APPREADY` → 启动目标 provider → 标记 `Running`。
- provider 启动失败时会尝试确认停止目标；若已恢复 Qtopia，目标卡片保留 `Failed`/`Retry`，不会被错误地清成 `Running`。
- provider 运行时异常会把连接置为 `Error`，清除 stale `Running` 判断并要求重新探测；不会启动另一个 Bridge 作为隐式补偿。
- 取消中的切换、串口 fault 和无法确认的 stop 都采取 fail-closed：不猜测当前应用，不自动启动上一次应用。

### 12.3 协议分流和测试边界

- 控制/lifecycle marker 使用 `APPREADY`、`APPSTOP` 和 console echo；Codex `CQMREQ`/`CQM1`、RunBoard `RB1` 各自使用明确前缀。
- partial/multiple/malformed framing、CQM lifecycle、RunBoard adapter、registry availability、重复 Launch、失败恢复和 buffer reset 均由离线测试覆盖；现场物理拔插和 LCD 画面不由自动化测试代替。
- 通用 `<APP|REQ>` parser/encoder 仍是后续协议演进的纯逻辑组件，不代表当前板端已启用该 daemon 协议。

### 12.4 RunBoard Python worker 的当前结论

- Control 启动仓库现有 `runboard_state_cli.py --live-worker`，worker 只生成 RB1，不打开串口；COM 仍由 Control 持有。
- worker 的输入/输出通过本地进程管道连接。启动失败、无效帧、异常退出和超时都会让 provider 失败并触发 fail-closed；停止时先取消读取、终止 worker，再进入下一次 lifecycle 操作。
- Python 解释器和 live provider 依赖仍属于发布封装优化，不阻塞当前 Phase 2，也不在本阶段迁移或重写。

### 12.5 最终现场边界

最终现场验收已完成，详细结果记录在 `docs/windows-control-final-acceptance.md`。本次物理拔插从 Qtopia 状态开始并通过；RunBoard 在静默运行期间缺少主动状态回报，因此该特殊场景仍保持 fail-closed，不通过猜测恢复 provider。
