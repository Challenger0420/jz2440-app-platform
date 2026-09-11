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

`experiments` 按优先级表达主实验和第二实验；采集层可以保留超过两个。一个实验时底部显示 Codex Usage；两个实验时底部自动切换为 Second Experiment。

采集层可以保留超过两个实验；Preview 只显示按“GPU active → running → GPU memory → start time”排序后的前两个。实验识别以 GPU compute PID 为入口，沿 PPID 归并到实验根进程，并结合 user、command line、work directory、tmux pane 和实验元数据，避免 DataLoader/worker 子进程被重复计数。无法可靠取得的字段使用 `null`。

## 本阶段边界

`--live` 只通过本地忽略配置发起 SSH 只读查询；失败时生成 `OFFLINE` 快照并记录本地日志。服务器采集不启停实验、不修改服务器状态。板端部署只允许使用现有 appctl 的临时路径；不写 Flash、rootfs、启动脚本、bootloader 或 kernel。

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

Codex Monitor 当前仍处于验收边界，RunBoard 只复用其 app-server response
字段约定和纯解析逻辑，不修改其 UI、target、串口或部署代码。

## 板端协议草案

`apps/runboard/shared/board_protocol.py` 使用 `RB1|L=...|...|CRC=...` 的
ASCII TLV 帧：版本号、长度、CRC-16-CCITT、状态 freshness、server、0/1/2
实验和 Codex Usage 均有固定缩写字段。未知字段可忽略，未知值为 `?`。本阶段
只使用 `MemoryTransport` 做 round-trip、分片、连续帧、截断和 checksum 测试，
不实现独立的第二套 SerialTransport；真实 COM transport 只在明确的实机验收脚本中由现有 Windows board-controller 打开。

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

### 已有实机证据

- 临时 target 曾成功注册到 appctl；Host 曾观察到 APPREADY，并发送连续真实 RB1 帧。
- 上述证据不等于 LCD 视觉验收，也不等于停止后 Qtopia 已恢复。

### 当前 blocker

- 上一次实机停止发送 `<RBQUIT>` 后，Host 未观察到 APPSTOP，随后 shell/Qtopia 未确认。
- 本轮已离线修复 target 的 UART termios 恢复，并增强 Bridge 的异常/超时分类；修复后的 target 尚未重新构建或部署到板端。
- APPSTOP 丢失时不能自动重试，必须等用户回工位后按安全脚本执行一次受控核验。

### 回工位后的验收顺序

1. 先只读确认 COM、appctl status/list 和当前 Qtopia/应用状态；若串口未知，停止。
2. 用 `scripts/deploy/accept-runboard.ps1 -Port COMx` 先看 plan，再显式执行临时部署。
3. 运行 mock idle/single/double 中至少一个场景，确认 APPREADY、RB1、APPSTOP/RC=0 和 Qtopia。
4. 再验证 live、fresh/stale/offline、Codex Monitor 回归；物理拔插和 LCD 只由现场人工验收。

离线验收脚本默认 plan-only；`-Execute` 和 `-Rollback` 必须显式选择，步骤失败后
停止，不进入自动重启或无限恢复循环。
