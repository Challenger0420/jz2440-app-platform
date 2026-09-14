# JZ2440 Control 设计与规划

状态：设计基线与最终实现记录。Phase 2 真实开发板集成及 2026-09-14 现场验收已完成；MVP 通过条件满足。

当前实施状态（以 `docs/windows-control-phase2-integration.md` 为实现事实来源）：

- Windows 端已落地为 .NET 8 WinForms 的 `JZ2440 Control.exe`。
- RunBoard 已有 target、`app.conf`、统一 adapter/provider 和真实协议接入，并已通过真实硬件 smoke。
- 正常运行时 `JZ2440 Control.exe` 是唯一 Windows UART owner；旧 Bridge 仅保留为诊断/兼容工具。
- 当前仍不新增板端 `appd`；`appctl` 继续承担板端 lifecycle supervisor 职责。RunBoard 静默运行时的拔插重连状态握手留作后续协议增强。

## 1. 设计结论

推荐把 Windows 端做成一个轻量的 .NET 8 WinForms 应用，暂定名称为 **JZ2440 Control**。它与一个进程内的 `DeviceConnection` 共同拥有串口，所有串口读写、板端控制和应用协议复用同一条连接。

```text
JZ2440 Control (WinForms UI)
        |
        |  一个进程、一个 SerialTransport、一个读写循环
        v
DeviceConnection / BoardController
        |
        v
PL2303 COM 端口 -> JZ2440 UART0
        |
        +-- Console mode: appctl / 控制协议
        |
        +-- Application mode: 当前应用协议
                         |
                         +-- RunBoard adapter
                         +-- Codex Monitor adapter (CQMREQ/CQM1)
                         +-- Qtopia system-mode adapter
```

MVP 不再让 Codex Monitor Bridge、RunBoard 控制程序和 Windows UI 分别打开 UART。Codex Monitor 的 quota provider 和 `BoardApplicationSession` 可以被新的单一 host 进程复用；如果保留旧 CLI，旧 CLI 只能作为同一 host 核心的另一个入口，不能与 UI 并行占用 COM。

当前建议不增加独立的板端 `appd`。已有的 `platform/board/appctl/appctl` 已经承担应用注册、前台启动、停止、恢复 Qtopia 和生命周期 marker 的 supervisor 角色。先扩展它的通用性并补齐结构化 ACK/ERROR；只有当未来要求在任意前台应用运行期间仍由一个独立进程统一处理控制帧、或者应用需要通过代理共享 UART 时，才引入真正的 `appd`/UART mux。届时必须同时改造应用的数据通道，不能让 `appd` 与应用同时直接读同一 UART。

## 2. 当前仓库审计

### 2.1 已确认的现有结构

- `platform/board/appctl/appctl`：BusyBox 兼容的前台应用 launcher，已有 `list`、`status`、`start <app>`、`stop`、`qtopia`。
- `platform/host/board-controller/SerialTransport.cs`：115200 8N1、无流控的 `System.IO.Ports` 封装。
- `platform/host/board-controller/ComPortDiscovery.cs`：按 PL2303 `VID_067B/PID_2303` 发现候选 COM，拒绝多候选猜测。
- `platform/host/board-controller/BoardController.cs`：已有 `UNKNOWN`、`CONSOLE`、Codex application mode，支持 console sync、list、start、stop 和生命周期等待。
- `platform/host/board-controller/ApplicationAdapter.cs`：已有应用适配器扩展点，但当前接口只有单个应用的 start/stop marker。
- `apps/codex-monitor/host/CodexMonitorAdapter.cs`、`BoardApplicationSession.cs`、`BoardProtocol.cs`：可复用 Codex Monitor 的启动/停止 marker 和 request-driven 会话。
- `apps/codex-monitor/protocol/`：已有严格的 CQM1 parser/encoder、`CQMREQ`、`CQMQUIT` 和分片帧测试。
- `apps/codex-monitor/app.conf`：已验证的应用注册样例，包含应用名、绝对执行路径、参数和协议名。
- `docs/architecture/APP_PLATFORM.md`、`docs/architecture/OVERVIEW.md`、`docs/deployment/SERIAL_OWNERSHIP.md`：已经确定单 UART 时分复用和拒绝 board autostart 的原因。

### 2.2 当前不存在或尚未确认的内容

- RunBoard 已有 `app.conf`、target、`RunBoardAdapter`、RB1 协议和 Windows live provider，并已通过真实硬件 smoke。
- Qtopia 当前是 `appctl` 管理的系统模式，不是现有应用 registry 中的普通应用。
- Windows `.sln`、SDK-style `.csproj` 和 WinForms UI 已位于 `platform/host/windows-control/`；旧 bridge 仍保持原有编译边界。
- 单一 UART owner、逻辑断开/重连和应用生命周期已经验证；物理 USB 拔插及 LCD 一致性已由现场清单确认，不能由 host 测试代替。
- `scripts/deploy/serial-command.ps1` 和 `scripts/deploy/deploy-app-platform.ps1` 仍有固定端口默认值。它们是现场辅助脚本的遗留配置，不能作为新 UI 的端口策略。

当前工作区有大量未提交的目录重组和代码新增。本文档记录设计基线和当前实现边界，不要求通过目录搬迁解决历史 dirty worktree，也不改变提交、部署和板端启动策略。

## 3. 产品目标与边界

### 3.1 目标

JZ2440 Control 只解决三件事：确认板子是否可连接、显示板上当前运行的模式/应用、在受控状态下切换应用。它是一个面向开发者的轻量控制端，不是设备管理后台。

首版支持的显示项：

| 用户名称 | 内部建议 ID | 当前仓库状态 |
| --- | --- | --- |
| RunBoard | `runboard` | 已注册、可由 appctl 管理；Control 使用统一 adapter/provider 发送 RB1 |
| Codex Monitor | `codex-monitor` | 已有应用、host 协议和 target 实现 |
| Qtopia | `qtopia` | 系统模式，由 `appctl qtopia` 恢复 |

应用列表必须来自板端注册信息和/或本地脱敏 UI metadata；UI 不应通过三个 `if` 分支把应用写死。

### 3.2 非目标

- 不在本阶段修改启动链、Flash、U-Boot、kernel、rootfs、Qtopia 自启动或 Windows Task Scheduler。
- 不在 UI 中展示真实服务器 IP、SSH 信息、用户名、内部路径或 Codex 凭据。
- 不把当前现场端口作为默认端口；端口名只作为运行时连接句柄，不能进入代码常量或截图说明。
- 不支持多个控制端同时连接同一块板。
- 不在 UI 中实现复杂设置页、远程文件管理、日志后台或花哨动画。

## 4. UI 页面结构

主窗口使用单页 WinForms，建议固定为适合开发者桌面的紧凑布局。

```text
┌──────────────────────────────────────────────┐
│ JZ2440 Control                               │
├──────────────────────────────────────────────┤
│ Device                                       │
│ Status: Connected     Port: [COMx ▼] [Refresh]│
├──────────────────────────────────────────────┤
│ Current App                                  │
│ Codex Monitor                         Running │
├──────────────────────────────────────────────┤
│ Applications                                │
│ ┌──────────────┐ ┌──────────────┐ ┌─────────┐│
│ │ RunBoard     │ │ Codex Monitor│ │ Qtopia  ││
│ │              │ │ Running      │ │ Launch  ││
│ │ Launch       │ │              │ │         ││
│ └──────────────┘ └──────────────┘ └─────────┘│
├──────────────────────────────────────────────┤
│ Ready / Last error / Reconnecting...          │
└──────────────────────────────────────────────┘
```

### 4.1 顶部与 Device 区域

- 顶部标题固定显示 `JZ2440 Control`。
- Device 区域显示 `Connected`、`Disconnected`、`Connecting` 或 `Error`。
- COM 下拉列表由自动发现填充；允许用户手动选择候选端口，但不预填固定 COM 号。
- `Refresh` 只重新枚举并显示候选；连接动作由同一 `DeviceConnection` 执行。
- 多个符合硬件 ID 的候选时显示“需要选择”，不自动猜测。

### 4.2 Current App 区域

- 显示最后一次经过 ACK 或生命周期事件确认的应用。
- 重新连接后先显示 `Unknown`，完成 status/observe 后再显示真实状态。
- Qtopia 显示为当前系统模式，不把它误称为普通前台应用。

### 4.3 Applications 区域

每个应用卡片来自 `AppDescriptor`，至少包含 display name、内部 ID、当前状态和动作按钮：

- 当前运行应用：显示 `Running`，按钮禁用。
- 其他可启动应用：显示 `Launch`。
- `Starting`、`Stopping`：按钮显示进行中状态并禁用所有切换按钮。
- `Failed`：显示简短错误，可提供 `Retry`，但必须先完成一次状态重新确认。
- `Unknown`：不允许直接重复启动，先显示 `Refresh status` 或等待连接恢复。

不使用复杂设置页。超时、日志路径和发现规则走本地配置文件；第一版可以只提供默认值，不需要设置窗口。

## 5. 用户操作流程

### 5.1 自动发现和连接

1. 应用启动时加载脱敏配置，枚举 Windows PnP 串口候选。
2. 没有候选：显示 `Disconnected`，后台低频重试，不发送任何板端命令。
3. 一个候选：尝试打开并进入 `Connecting`。
4. 多个候选：显示候选列表，等待用户选择，不自动打开多个端口。
5. 打开成功后进行最小 handshake/observe；确认 console 或 application mode 后显示 `Connected`。

### 5.2 启动应用

1. 用户点击某个非当前应用的 `Launch`。
2. UI 取得切换锁，所有应用按钮进入禁用状态。
3. Controller 确认当前状态，不在 `Unknown` 时直接发 start。
4. 如果当前是 Codex Monitor 等前台应用，先发送该应用的 stop frame，等待 `APPSTOP` 和 console sync。
5. 发送目标应用的 start 操作，等待 ACK，再等待 `APPREADY`/`RUNNING` 事件。
6. 成功后更新 Current App 和卡片；失败进入 `Error`/`Failed`，保留错误原因，不自动重复启动。

### 5.3 切回 Qtopia

Qtopia 是系统恢复操作。当前 Codex Monitor 仍使用 `<CQMQUIT>`，收到 `APPSTOP` 后再完成 console sync；未来可在控制协议中统一为 `STOP` + `START qtopia` 的语义。恢复失败不能假设 Qtopia 已经运行，必须显示 `Error` 并等待人工重试/现场检查。

### 5.4 拔出、重插和重新连接

- 端口消失、读写异常或 `SerialPort` 抛出异常时异步释放 provider 和旧 transport，显示 `Disconnected`/`Reconnecting`。
- 重连只重新发现和查询，不自动重新启动上一次应用，避免重复启动或改变板端状态。
- 重连后的应用显示顺序为 `Unknown` → `Connecting` → `Connected` + status 结果。
- 没有兼容设备时保持 `Disconnected`；探测失败后停止自动循环，避免 `Connecting/Error` 闪烁。
- Current App 为 `Unknown` 时禁用 Launch/Retry，避免在未确认当前应用时发送生命周期命令。
- 端口号变化不影响逻辑；候选以 PnP identity 和用户选择为主，COM 名称只是当前连接句柄。

## 6. 总体通信架构

### 6.1 UART 所有权原则

Windows 侧只允许一个 `SerialTransport` 实例拥有 COM。UI、Codex quota provider、应用 adapter 都通过 `DeviceConnection` 发送请求，不能直接调用 `SerialPort`。

板端仍然遵循现有的 Console/Application 时分复用：

- Console mode：shell/appctl 接收受控命令，应用未占用 UART RX。
- Application mode：前台应用占用 UART RX，host 只按该应用的 adapter 处理应用协议。
- `UNKNOWN`：不启动、不停止、不猜测；先 observe 或要求人工恢复。

### 6.2 当前兼容路径

在真正的 board control daemon 尚未存在时，host controller 继续复用当前方式：

- status/list：console sync 后发送 `/opt/jz2440/bin/appctl status` 或 `list`，用唯一 marker 截止输出。
- start：console sync 后发送 `/opt/jz2440/bin/appctl start <app>`，等待 `<APPREADY|app>`。
- stop Codex Monitor：发送 `<CQMQUIT>`，等待 `<APPSTOP|codex-monitor|RC=N>`，再同步 shell。
- Qtopia：复用 `appctl qtopia` 的恢复语义；不要从 Windows 直接拼接 Qtopia 进程命令。
- ACK/ERROR：第一版由命令输出、退出结果、生命周期 marker 和 timeout 归一化为 host-side `OperationResult`。

这条兼容路径能支持 MVP，但它不是最终的独立帧协议；`appctl status` 当前输出 `APP ...`、`QTOPIA` 或 `STOPPED`，仍需增加严格解析和错误分类。

## 7. 文本协议设计

### 7.1 通用约束

- 编码：ASCII 子集，UTF-8 仅允许在未来明确扩展；首版字段值使用 `[A-Za-z0-9_.:/=-]`。
- 帧：一帧一行，以 `\n` 结束；最大长度 255 字节。
- 字段：`<APP|TYPE|KEY=VALUE|...>`；字段顺序固定，未知字段可以忽略但不应覆盖已知字段。
- `ID` 是 host 生成的单调递增请求号，用于关联 ACK/ERROR；重连后重新开始即可。
- 一个请求只对应一个终态 ACK 或 ERROR；生命周期 EVENT 是异步通知，不代替操作结果。
- 解析错误、重复字段、越界字段、超长帧都拒绝，不执行副作用。

### 7.2 目标控制帧

```text
Host -> Board: <APP|REQ|ID=42|OP=STATUS>\n
Board -> Host: <APP|ACK|ID=42|OP=STATUS|NAME=codex-monitor|STATE=RUNNING>\n

Host -> Board: <APP|REQ|ID=43|OP=LIST>\n
Board -> Host: <APP|ACK|ID=43|OP=LIST|COUNT=3|ITEMS=runboard,codex-monitor,qtopia>\n
Host -> Board: <APP|REQ|ID=44|OP=START|NAME=runboard>\n
Board -> Host: <APP|ACK|ID=44|OP=START|NAME=runboard|STATE=STARTING>\n
Board -> Host: <APP|EVENT|NAME=runboard|STATE=RUNNING>\n
Host -> Board: <APP|REQ|ID=45|OP=STOP>\n
Board -> Host: <APP|ACK|ID=45|OP=STOP|NAME=runboard|STATE=STOPPING>\n
Board -> Host: <APP|EVENT|NAME=runboard|STATE=STOPPED|RC=0>\n
Board -> Host: <APP|ERROR|ID=44|OP=START|CODE=NOT_FOUND|MSG=app-not-registered>\n
Board -> Host: <APP|ERROR|ID=45|OP=STOP|CODE=TIMEOUT|MSG=app-did-not-exit>\n```

实际实现时，`MSG` 只允许稳定的短错误码或受限文本，不传 shell 原始输出，不把路径、IP、用户名或凭据放入帧。

### 7.3 现有 Codex Monitor 协议共存

Codex Monitor 的应用 payload 继续使用：

```text
Board -> Host: <CQMREQ|V=1>\n
Host -> Board: <CQM1|...>\n
Host -> Board: <CQMQUIT>\n
```

控制帧和 CQM 帧按首字段分流：`APP` 只进控制 parser，`CQMREQ`/`CQMQUIT` 只进 Codex adapter，`CQM1` 只作为 quota payload。没有 `CQMREQ` 时 host 不发送伪造或周期性 CQM1，保留当前 request-driven 和 host TX silence 约束。

RunBoard 的未来协议也必须通过 `ApplicationAdapter` 注册，由同一读循环分发。它不能自行创建第二个 `SerialPort`。如果未来应用的 payload 与控制帧可能冲突，应为该应用定义独立前缀并在 adapter 中严格解析。

### 7.4 appctl 与目标帧的关系

目标 `APP|REQ` 帧需要板端控制入口才能直接解析。MVP 不应为了形式统一而盲目创建常驻 daemon。首版可由 host 的 `BoardController` 将控制操作映射到现有 console/appctl 命令，并把其输出规范化为相同的 host-side result。随后再选择以下演进之一：

1. 给 `appctl` 增加机器可读 `--wire` 输出和严格错误码；仍由 console shell 受控执行。
2. 在确认 UART ownership 需求后，引入真正的 `appd`，由它成为唯一板端 UART mux，并通过 pipe/子进程接口连接应用。

第二种方案是架构升级，不属于 MVP，也不能与当前应用直接打开 `/dev/s3c2410_serial0` 的方式并存。

## 8. Windows 端代码架构

WinForms 只负责显示状态和转发意图；不要把串口读写、sleep、协议解析或启动命令放进 Form 事件处理器。

建议模块：

| 模块 | 职责 |
| --- | --- |
| `MainForm` / `ApplicationCard` | 单页 UI、绑定 ViewModel、按钮状态和用户操作 |
| `ControlViewModel` | 把连接状态、当前应用、卡片状态转换为 UI 可绑定数据 |
| `DeviceConnection` | 打开/关闭/reconnect、独占 transport、读循环、断线通知 |
| `SerialTransport` | `System.IO.Ports` 具体实现；保留可测试的 `ISerialTransport` |
| `ComPortDiscovery` | PnP 枚举、候选排序、用户选择；不假设固定 COM 名称 |
| `BoardController` | console/application mode、操作锁、超时、status/list/start/stop 流程 |
| `ProtocolParser` / `ProtocolEncoder` | APP 控制帧的严格解析和编码；按行缓冲、长度限制、ID 校验 |
| `AppRegistry` | `AppDescriptor` 集合和动态应用 metadata，不写死三个卡片 |
| `ApplicationAdapter` | 每个应用的 ready/stop/payload/退出语义；Codex adapter 复用现有实现 |
| `OperationStateMachine` | ConnectionState、AppState 和合法迁移，防止重复点击 |
| `Configuration` | 本地 JSON 配置、默认值、脱敏示例、配置校验 |
| `Logging` | 本地轮转日志；记录状态迁移、请求 ID、错误码，不记录凭据和完整敏感 payload |

### 8.1 线程模型

- 所有串口 I/O 在后台 async loop 或专用 worker 中完成。
- UI 只通过事件/`SynchronizationContext` 回到 UI 线程更新控件。
- `SwitchingApp` 期间使用一次性操作锁；同一时刻最多一个控制请求和一个应用会话。
- transport 关闭、重连和 reader loop 取消必须是幂等的。
- 取消/超时只终止 host 等待，不自动向板端发送第二个 stop/start。

### 8.2 推荐目录树

不在本阶段创建目录；开始编码时建议沿用当前 `platform/host` 归属，避免另起一个顶层 bridge：

```text
platform/host/
  board-controller/             现有可复用 Core/Transport/CLI 源码
  windows-control/
    Jz2440.Control.sln
    src/Jz2440.Control/
      Jz2440.Control.csproj     net8.0-windows; UseWindowsForms=true
      Program.cs
      MainForm.cs
      ViewModels/
      Controls/
    src/Jz2440.Control.Core/
      DeviceConnection.cs
      BoardController.cs
      AppRegistry.cs
      StateMachine.cs
    src/Jz2440.Control.Protocol/
      AppProtocolParser.cs
      AppProtocolEncoder.cs
      ProtocolFrames.cs
    tests/
      Jz2440.Control.Core.Tests/
      Jz2440.Control.Protocol.Tests/
```

第一阶段可以直接把现有 `platform/host/board-controller` 的源文件纳入新 solution，暂不做大范围移动；等 UI 和协议稳定后再决定是否抽成独立 class library。

## 9. 状态机

### 9.1 Windows 连接状态

```text
Disconnected
    -> Connecting        枚举到候选并尝试打开
Connecting
    -> Connected         transport 打开且 observe/status 成功
    -> Error             打开、handshake 或解析失败
Connected
    -> SwitchingApp      用户发起 start/stop/qtopia
    -> Disconnected      端口消失或读取失败
    -> Error             操作失败且连接仍存在
SwitchingApp
    -> Connected          目标状态收到确认
    -> Disconnected      端口断开
    -> Error             ACK/生命周期超时或返回错误
Error
    -> Connecting        用户 Retry 或下一次受控重连
```

`Reconnecting` 可以作为 UI 文案或内部子状态，但不必把它做成独立公共状态。

### 9.2 应用状态

每个 `AppDescriptor` 使用：

```text
Unknown -> Stopped       status 明确确认未运行
Stopped -> Starting      已发起 start
Starting -> Running      ACK + APPREADY/EVENT 确认
Starting -> Failed       ERROR、退出码或 timeout
Running -> Stopping      已发起 stop
Stopping -> Stopped      APPSTOP/EVENT + console sync 确认
Stopping -> Failed       stop timeout 或恢复失败
Failed -> Starting       用户显式 Retry 且重新 status 成功
```

未知状态不允许直接 start/stop；先 status。状态机不根据 UI 点击本身推断 `Running`，必须等待板端证据。

### 9.3 按钮策略

| 连接/应用状态 | Launch 按钮 | 当前应用按钮 | Refresh/Retry |
| --- | --- | --- | --- |
| Disconnected | 禁用 | 禁用 | 可用 |
| Connecting | 禁用 | 禁用 | 禁用或取消当前连接尝试 |
| Connected + Stopped/明确当前应用 | 其他应用可用 | 显示 `Running` 并禁用 | 可用 |
| SwitchingApp / Starting / Stopping | 全部禁用 | 显示进行中 | 禁用 |
| Error / Unknown | 禁用 | 不显示假定状态 | `Retry`/`Refresh status` 可用 |
| Failed | 仅目标项可 `Retry`，且需重新 status | 不把 Failed 当 Running | 可用 |

## 10. 错误处理与安全停止

### 10.1 错误分类

- `NO_DEVICE`：没有符合条件的候选。
- `AMBIGUOUS_DEVICE`：多个候选，等待用户选择。
- `OPEN_FAILED`：端口被占用、权限或驱动失败。
- `PROTOCOL_ERROR`：帧超长、字段非法、重复字段、未知必需版本。
- `TIMEOUT`：没有收到 ACK、APPREADY、APPSTOP 或 console marker。
- `APP_NOT_FOUND`：板端未注册目标应用。
- `APP_START_FAILED` / `APP_STOP_FAILED`：应用退出码或生命周期失败。
- `QTOPIA_RECOVERY_FAILED`：停止应用后无法确认系统恢复。
- `UART_LOST`：读写期间端口消失。

UI 只显示简短、可行动的错误；详细原因写入本地轮转日志。原始 shell 输出应截断并脱敏，不能直接展示或持久化服务器信息。

### 10.2 断线原则

断线后立刻释放 transport，停止旧读循环，清空未完成 request 的等待，当前应用置为 `Unknown`。自动重试只负责重新连接和查询，不自动 start/stop，不发送 CQMQUIT，不恢复上一次操作。

### 10.3 失败切换原则

如果 stop 已经成功但 start 失败，UI 应显示“当前应用未知/目标启动失败”，而不是错误地显示目标为 Running；如果 Qtopia 恢复失败，不重复执行 kill 或 qpe 命令。所有恢复动作都应由一次新的、明确的用户操作触发。

## 11. 配置、日志与隐私

建议运行时配置放在当前用户的 LocalAppData 下，例如由程序根据 Windows 标准 API 定位 `JZ2440Control/config.json`；仓库只提交脱敏的 `config.example.json`。

配置字段建议：

```json
{
  "autoDiscover": true,
  "preferredPort": null,
  "allowedHardwareIds": ["USB\\VID_067B&PID_2303"],
  "baudRate": 115200,
  "connectTimeoutMs": 1500,
  "operationTimeoutMs": 5000,
  "reconnectMinMs": 1000,
  "reconnectMaxMs": 10000,
  "logLevel": "Information"
}
```

约束：

- `preferredPort` 默认 `null`，不能写死当前现场 COM 号。
- 不在配置中放服务器 IP、SSH 用户名、SSH key、密码、token、Codex session 或内部绝对路径。
- 将来如果 RunBoard 有服务器状态，UI 只展示脱敏后的状态/别名；真实连接信息由受控本地 provider 管理，不能进入截图、README、示例配置或公开日志。
- 本地配置、用户日志、构建输出和开发机覆盖文件应加入 `.gitignore`，例如 `*.local.json`、`config.local.json` 和 Windows control 的 `logs/`。本阶段不创建真实配置文件，也不改现有用户工作区中的 `.gitignore`。

## 12. 第一版 MVP（Phase 1 历史范围）

MVP 只包含：

1. .NET 8 WinForms 单页窗口，标题、Device、Current App、Applications、底部状态条。
2. 自动发现 PL2303 候选、手动选择候选、打开/释放串口；不依赖固定 COM 名称。
3. Connected/Disconnected/Connecting/Error 显示和端口拔插后的受控重连。
4. 复用现有 `BoardController`/`ComPortDiscovery`/`SerialTransport`，完成 console status/list 和 Codex Monitor 的 start/stop。
5. `AppDescriptor` + adapter registry，让 Qtopia、Codex Monitor 和未来 RunBoard 走统一卡片/状态接口。
6. Starting/Running/Stopping/Failed/Unknown 状态显示和操作锁，禁止连续点击。
7. CQMREQ/CQM1/CQMQUIT 与控制帧分流；没有请求时保持 quota TX silence。
8. 协议 parser/encoder、状态机、fake transport 和 UI view-model 单元测试。
9. 本地脱敏配置和本地日志；无自启动、无部署、无远程服务器配置。

Phase 1 MVP 当时不包含 RunBoard 的真实启动、真正的 board-side `APP|REQ` daemon、多个 host client、远程实验数据展示、Windows 自启动和自动恢复上一次应用。当前 Phase 2 已补齐 RunBoard 的真实 target/协议接入，但其 provider 仍不改变上述“无 appd、无自启动、无自动恢复”的边界。

## 13. 后续实现顺序

### Phase 0：设计冻结前的只读确认

- RunBoard 的 board executable、`app.conf`、生命周期 marker 和 RB1 payload 协议已在 Phase 2 确认并接入；后续只需维护兼容性测试。
- 明确 `appctl stop` 当前 PID/子进程语义，并补充不依赖现场的测试。
- 决定 target protocol 是继续走 console compatibility，还是先添加 `appctl --wire`。
- 对当前工作区未提交重组做一次独立审阅，避免把旧 bridge 误当作新的库边界。

### Phase 1：Windows host core

- 建立 SDK-style `.csproj`/solution，目标 `net8.0-windows`。
- 抽取/复用 `SerialTransport`、`ComPortDiscovery`、protocol parser 和 fake transport。
- 建立单一 reader loop、连接状态机、应用状态机和 `AppRegistry`。
- 完成 Codex Monitor compatibility adapter，不创建第二个 COM 使用者。

### Phase 2：最小 WinForms UI

- 按本文单页布局实现静态卡片和状态绑定。
- 接入 start/stop/status，所有状态切换由 core 事件驱动。
- 加入日志和用户配置，但不加设置页。

### Phase 3：Host simulation gate

- 用 fake serial transcript 覆盖：无设备、多设备、分片帧、ACK/ERROR、重复点击、超时、断线、重连、Qtopia 恢复失败。
- 执行 host build 和测试；不得把 simulation PASS 写成 hardware PASS。

### Phase 4：现场验证前评审

- 只在用户明确授权的现场窗口执行真实板端验证。
- 验证顺序应先确认 console status/list，再确认单次 start/stop，再确认 USB 重插，不启用自启动。
- 任何现场失败都先保留日志和状态，不自动重复部署或启动。

### Phase 5：真实集成（已完成）

- RunBoard 已注册到板端 app registry，并通过统一 adapter、provider 和卡片模型接入。
- Qtopia、Codex Monitor、RunBoard 已在同一 Control transport 上完成切换 smoke；CQM1 和 RB1 由活动 provider 分时承载。
- 旧 `CodexQuotaBridge` / `RunBoardBridge` 保留为 diagnostic/compatibility tools，不能与 Control 并行占用同一 UART。

### Phase 6：现场最终验收（已完成）

- 已从 Qtopia 状态完成物理 USB 拔出/重插、自动重新发现和 UI 状态恢复；本次未观察到 COM 号变化。
- 已人工核对 Windows Current App、卡片 `Running` 与 LCD 画面一致性。
- 已完成 Qtopia → Codex Monitor → RunBoard → Qtopia，并最终恢复 Qtopia、关闭 Control、确认无 Bridge/worker 残留；未修改启动链。
- RunBoard 的服务器数据源可显示 `server offline`，该状态与 RunBoard lifecycle 独立，不阻塞 MVP。

## 14. 开始编码前的关键风险

1. **UART 归属风险**：当前 shell、Qtopia、前台应用和 host bridge 的时序仍有现场验证门槛；UI 不应掩盖 `UNKNOWN`。
2. **现有 stop 语义风险**：`appctl` 记录的是 launcher shell PID，停止时如何影响前台子进程必须通过静态分析和现场安全测试确认。
3. **协议边界风险**：当前 CQM 应用协议不是通用控制协议；直接把 `APP|REQ` 发给现有 Codex target 不会自动获得 status/ACK。
4. **RunBoard worker 依赖风险**：Windows provider 当前启动本地 Python live worker；异常退出可被发现并 fail-closed，但 Python/runtime 封装仍是后续非阻塞优化。
5. **多进程抢串口风险**：旧 `CodexQuotaBridge.exe` / `RunBoardBridge.exe`、Control 和手动 serial script 不能并行；正常运行时必须保持单一 host owner。
6. **.NET 迁移风险**：现有源码是无 namespace 的旧 csc 编译布局，迁移到 .NET 8 需要处理 `System.Management` 引用、WinForms target 和项目边界，但不应借机大范围重构。
7. **配置泄露风险**：默认端口、真实路径、服务器地址和凭据很容易被写入示例或截图；需要在代码 review 和日志测试中明确禁止。
8. **Qtopia 恢复风险**：应用停止不是等同于 Qtopia 已恢复；必须等待明确证据，失败时不能自动反复 kill/launch。
9. **重连误操作风险**：串口重插后不能把上一次的“启动”重放，否则可能产生重复进程或破坏当前板端状态；RunBoard 静默运行时应继续保持 Unknown/fail-closed，直到后续增加安全握手。
10. **现有工作区 dirty 风险**：当前目录重组尚未提交，后续实现前要再次确认文件归属和主线，不能用 reset/checkout 清理。

## 15. 现有组件复用清单

可直接复用或小幅抽取：

- `ComPortDiscovery.cs`：自动发现/多候选拒绝逻辑；需要把固定硬件规则移入配置默认值。
- `SerialTransport.cs`：串口参数和 `ISerialTransport` fake seam；需要补充异步取消、关闭幂等和异常分类。
- `BoardController.cs`：`UNKNOWN` 安全门、console sync、application lifecycle；需要从单 Codex adapter 扩展成 registry。
- `ApplicationAdapter.cs`：保留为应用生命周期适配器的起点，扩展为 descriptor + protocol session。
- `CodexMonitorAdapter.cs`、`BoardProtocol.cs`：Codex `APPREADY`/`APPSTOP` 解析。
- `BoardApplicationSession.cs`、`RequestDrivenSession.cs`：保留 CQMREQ 驱动响应和分片帧处理。
- `ProtocolParser.cs`、`ProtocolEncoder.cs` 及其测试：保留 CQM1 严格校验，不与控制帧 parser 混写。
- `platform/board/appctl/appctl`：作为 MVP 板端生命周期入口，不新增并行 launcher。
- `docs/architecture/APP_PLATFORM.md` 和 `docs/deployment/SERIAL_OWNERSHIP.md`：作为 UART ownership 和部署边界的现有依据。

不应直接复用为新 UI 核心：

- `Program.cs` 当前把 CLI、Codex provider、控制器和长循环全部揉在一起，应抽取 core 后再接 WinForms。
- `scripts/deploy/serial-command.ps1` 是一次性现场脚本，且有固定端口默认值，不能作为 UI transport。
- 旧的 scheduled-task/bridge launcher 只能在确认与新 UI 的单一串口 owner 关系后处理，不能与 MVP 并行自动启动。
