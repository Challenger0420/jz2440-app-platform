# RunBoard

RunBoard 是运行在 JZ2440 上的科研服务器实验状态监控板。本阶段交付工程骨架、统一状态模型、mock 数据、Windows UI Preview 和真实服务器只读采集。

## 分层

```text
apps/runboard/shared/       shared JSON schema, model loader, mock snapshots
apps/runboard/collector/    SSH transport, Linux parsers, ExperimentDetector, ServerProvider
apps/runboard/host/         Provider interfaces, Aggregator, freshness, MemoryTransport
apps/runboard/preview/      Windows Tkinter Preview
platform/target/            JZ2440 runtime and diagnostics (existing reusable layer)
platform/host/board-controller/  Windows Bridge and serial controller (existing reusable layer)
config/                     example and ignored local configuration
docs/                       architecture, deployment, and handoff documents
```

已有 `apps/codex-monitor` 组件保留为已验证的 framebuffer、CQM 协议和 Codex Bridge 参考实现；RunBoard 复用其 ARM/OABI 构建基线和 Windows board-controller，但不修改 Codex Monitor 的 UI、target 或部署逻辑。

## 状态模型

模型定义在 `apps/runboard/shared/state-model.schema.json`：

- Server：online/offline/degraded、CPU、RAM、GPU utilization、GPU memory。
- Experiment：name、user、status、progress、current/total round、elapsed、dataset、seed、metric name/value。
- CodexUsage：5H、5H reset、week、week reset、reset cards（界面显示为 RC3 等）。

矩阵任务在可选的 `job` 对象中表达：`name`、`matrixCompleted`、
`matrixTotal`、`currentCell`、`method` 和 `matrixProgressPercent`。当前
`experiments[0]` 仍然是当前 cell；它的 `progress` 明确标记为
`current-cell-round`。因此 `MATRIX 7/45`、`CELL 8/45` 与 `ROUND 37/50`
不会混用。没有显式矩阵元数据时，旧的单实验状态不携带这些字段。

`experiments` 按优先级表达主实验和第二实验；采集层可以保留超过两个。一个实验时底部显示 Codex Usage；两个实验时底部自动切换为 Second Experiment。

采集层可以保留超过两个实验；Preview 只显示按“GPU active → running → GPU memory → start time”排序后的前两个。实验识别以 GPU compute PID 为入口，沿 PPID 归并到实验根进程，并结合 user、command line、work directory、tmux pane 和实验元数据，避免 DataLoader/worker 子进程被重复计数。无法可靠取得的字段使用 `null`。

## 本阶段边界

`--live` 只通过本地忽略配置发起 SSH 只读查询；失败时生成 `OFFLINE` 快照并记录本地日志。服务器采集不启停实验、不修改服务器状态。RunBoard
已安装在平台标准应用路径并由现有 `appctl` 管理；安装不写 Flash、NAND、
bootloader、kernel、U-Boot 环境或全局 startup。Reset 后默认仍为 Qtopia，
RunBoard 不开机自启动。

## Host Aggregator

`apps/runboard/host/aggregator.py` 是唯一的 Host 聚合入口。它分别调用
Server Provider 和 CodexUsage Provider，单个 provider 失败不会清空另一个
provider 的有效数据。Server 与 Codex Usage 各自拥有 `FreshnessTracker`：

```text
success → fresh
已有有效值 + 短暂失败 → stale（保留上一份值）
连续失败达到 offlineAfterFailures → offline
明确解析/Provider error → error
```

默认采样为 server resource 10 秒、experiment 20 秒、Codex Usage 300 秒、
UI render 1000 毫秒。当前 Server Provider 将资源和实验作为同一 SSH 原子
快照读取，因此实际 server probe 使用资源周期；experiment 周期作为后续拆分
Provider 的配置接口保留。

`apps/runboard/host/live_sender.py` 提供长驻 live sender。它在会话开始时
创建一次 Provider/Aggregator，后续按配置 cadence 请求快照并编码 RB1；因此
一次 SSH/Codex 短暂失败可以保留上一份有效状态并让 UA 持续增长。Bridge 的
`--live` 模式使用 `runboard_state_cli.py --live-worker`，而普通 one-shot CLI
仍保留给 mock、诊断、测试和兼容性 frame 输出。sender 不打开串口，串口仍完全由 Windows
board-controller 所有。

Codex Monitor 当前仍处于验收边界，RunBoard 只复用其 app-server response
字段约定和纯解析逻辑，不修改其 UI、target、串口或部署代码。

## 板端协议草案

`apps/runboard/shared/board_protocol.py` 使用 `RB1|L=...|...|CRC=...` 的
ASCII TLV 帧：版本号、长度、CRC-16-CCITT、状态 freshness、server、0/1/2
实验和 Codex Usage 均有固定缩写字段。未知字段可忽略，未知值为 `?`。本阶段
只使用 `MemoryTransport` 做 round-trip、分片、连续帧、截断和 checksum 测试，
不实现独立的第二套 SerialTransport；真实 COM transport 只在明确的实机验收脚本中由现有 Windows board-controller 打开。

Host 会把 `UT` 作为当前聚合时间戳，并附带 `CT`（按 Host 本地/配置时区计算的
`HH:MM`）和 `UA`（最后一次有效 provider snapshot 的年龄，单位秒）显示字段。
短暂失败保留 last-known 有效值并使 `UA` 继续增长，freshness 独立转为 `stale`；
连续失败达到配置阈值后才进入 `offline/error`。
target 只绘制 Host 提供的值，不自行维护复杂 RTC；`UPDATED 10S AGO`、
`UPDATED 3M AGO`、`UPDATED 1H AGO` 仅表达最后一次有效 RunBoard 状态刷新，
不表达任何实验 ETA。

RB1 v1 对矩阵上下文采用可选字段：`JN`（job name）、`MC/MT`（已完成/总
cell）、`CI`（当前 cell）、`JM`（method）。旧状态不发送这些字段，旧解析器
可以继续忽略未知字段；不发送派生的百分比，避免重复状态。target 对 `CPU`、
`RP`、`GU` 使用无 libc 的定点十进制定点解析后四舍五入为屏幕整数，未知值仍为
`?`/`--`。

## Matrix metadata provenance

Server Provider 只把显式 `formal_matrix_plan.json` 的 cell 列表作为矩阵总数，
只把成功 completion manifest 的数量作为已完成数，并用活动进程 work directory
的 cell identity 与 plan 做 join。job 显示名优先取 plan 的显式字段，其次取本地
忽略配置 `jobDisplayNames`，最后才使用清洗后的 matrix version 作为推断值。
缺少 join 或字段时保持 unknown，不从进程名推测矩阵进度。

## Real live dry-run 与当前收尾状态

Host 离线硬门槛命令：

```powershell
python apps/runboard/host/runboard_state_cli.py --live --dry-run
python apps/runboard/host/runboard_state_cli.py --live --persistent-dry-run --cycles 3 --interval 10
```

该命令只做 SSH Server Provider 与真实 Codex Provider 的只读采样，随后执行
状态 schema 校验、RB1 encode/decode、CRC、newline 和帧大小检查，输出脱敏摘要并
明确 `SERIAL=NO`。它不会打开 COM，也不会发送任何帧。

`--persistent-dry-run` 使用同一个 sender 实例运行多个 live cycle，同样明确
`SERIAL=NO`，用于验证 cadence、last-good 缓存、独立 freshness 和 UA 增长；它
不会启动 Bridge，也不会连接开发板。

当前 live 状态固定区分三层进度：

- `MATRIX`：completion manifest 中已完成 cell / plan 中显式总 cell。
- `CELL`：活动 work-directory 与 plan join 得到的当前 cell / 总 cell。
- `ROUND`：当前 cell 的 active worker round / protocol global rounds。

`TIME` 不再使用可能跨 cell 存活的 matrix runner 根进程 `etimes`；优先使用 cell
metadata 的显式 `startedAt`，其次使用可标记为 `INFERRED` 的 cell `createdAt`，否则
保持 unknown。`UPDATED`/`UA` 表示最后一次有效 snapshot 的 age；短暂失败保留
last-known 值并转为 `stale`，达到配置阈值后才进入 `offline/error`。Codex quota
provider 失败时百分比保持 `null/--`，不把 unknown 伪装成 `0%`。

### 最终现场回归状态

- final matrix mock UI renderer = FROZEN；所有最终状态页面均已完成现场肉眼确认。
- 最终 target 已按既有 ARM/OABI 基线安装到平台标准应用路径，板端大小、校验和及可执行权限一致；Reset 后仍然存在，完成了“已安装但不自启动”的持久性验收。
- Persistent real-live sender = PASS：同一 Provider/Aggregator 会话连续生成实时 RB1，UA 从接近 0 持续增长，未退化为逐帧 one-shot。
- Real live board path = PASS：`APPREADY`、seq 0–5、长度/CRC/解析/绘制均有板端证据；Server/Codex freshness 独立。
- 稳定 live 窗口中的矩阵字段已通过板端核对：`MATRIX 12/45`、`CELL 13/45`、
  当前 cell `ROUND 2/50`、方法和数据集均随 RB1 到达；CPU/RAM/GPU/VRAM 也随帧更新。
- 曾在 cell 切换瞬间观察到一次元数据 join 暂时不可用并回退为通用进程身份；随后只读复核和
  第二次稳定 live 运行均恢复矩阵字段。该瞬时窗口作为后续 join 稳定性观察项保留，不改变
  当前稳定快照的验收结论。
- RunBoard lifecycle = PASS：正常退出收到 `APPSTOP|runboard|RC=0`，shell、termios、Qtopia 和进程清理均通过。
- Codex Monitor regression = PASS；RunBoard-after-Codex = PASS。
- Serial reconnect = PASS：一次拔出/插回后 COM 恢复，驱动状态正常，console、Qtopia 和 termios 可再次确认。
- 最终板端状态为 Qtopia，RunBoard 与 Codex Monitor 均未运行。
- 程序链路已完成真实数据回归；用户已明确确认真实 Formal v6 live LCD，`RunBoard real live LCD acceptance = PASS`。
- Offline 页面遵循同一最终样式：服务器资源显示 `--%` / `--/--G`，主体显示
  `SERVER OFFLINE` / `NO LIVE SERVER DATA`，Codex quota 保持独立；offline、
  degraded 和 longtext 等最终状态均已完成现场肉眼确认。

脚本默认 plan-only；`-LiveDryRun` 不打开串口。只有显式 `-Execute` 才进入板端验收流程，
不修改 Flash/rootfs/startup，也不自动 Reset；正式安装由平台应用目录和 `appctl` 契约管理。

## Host/target 生命周期

`appctl` 是生命周期 owner：它输出 `<APPREADY|runboard>`，前台 target 返回后负责
清理临时状态、恢复 Qtopia，并输出 `<APPSTOP|runboard|RC=N>`。RunBoard target
只接收 `<RBQUIT>`，退出前释放 framebuffer，并恢复打开串口前保存的 `termios`；它
不自行伪造 APPREADY/APPSTOP，避免与 appctl 重复发送 marker。

Windows Bridge 的 stop 有固定超时：`RC=0` 表示确认停止；非零 RC 报告
`RUNBOARD_STOP=ABNORMAL`；超时或 APPSTOP 丢失报告 `RUNBOARD_STOP=UNCONFIRMED`。
已确认 Console 时重复 stop 是幂等的，不会再次发送 `<RBQUIT>`。APPSTOP 丢失不能
被当作 Qtopia 已恢复，必须等待人工状态核验。

## 当前状态与证据边界

### 已完成

- Host Aggregator、真实 Server/Codex Provider、独立 freshness、RB1 v1、Bridge 和 target 源码。
- 本地 Python 测试、Bridge self-test、idle/single/double frame replay。
- target 已按 Codex Monitor 的 ARMv4T/ARM920T/OABI/soft-float/static/custom-runtime 基线构建过。
- matrix-aware job context：矩阵总数只来自显式 plan，完成数只来自 completion manifest；
  `MATRIX/CELL/ROUND` 具有独立语义；target 支持无 libc 的定点百分比解析。

### 已有实机证据

- 最终 target 已安装并持久保留在平台标准应用路径；`appctl list` 可见
  `codex-monitor` 与 `runboard`，Reset 后仍保持注册和文件校验一致。
- 板端已报告连续 `PARSED` / `DRAWN`，正常停止收到 `APPSTOP`，并恢复 shell/Qtopia。
- Codex Monitor 已完成启动、真实额度请求/响应和正常停止回归；随后 RunBoard 再启动也通过。
- 用户已确认真实 live/matrix、double、IDLE、STALE、OFFLINE、COMPLETED、ERROR、
  DEGRADED 和 longtext LCD 页面。

### 当前状态

- RunBoard 功能、最终 UI、RB1 协议、真实 Server/Codex pipeline、生命周期、状态覆盖和
  平台安装均已验收。
- RunBoard 与 Codex Monitor 都是 platform-managed applications；默认状态为 Qtopia，
  两者均不自启动。
- RunBoard UI、协议和 Provider 语义冻结；后续工作转交 Windows Control，不在 RunBoard
  主线上继续增加 UI 场景。

### 下一步

进入 Windows Control 主线：只通过 `appctl` 和 APPREADY/APPSTOP 生命周期契约执行应用
列表、状态读取、停止、启动和断线重连，不绕过板端应用管理器。

离线验收脚本默认 plan-only；`-Execute` 和 `-Rollback` 必须显式选择，步骤失败后
停止，不进入自动重启或无限恢复循环。
