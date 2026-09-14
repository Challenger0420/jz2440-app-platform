# JZ2440 Control 最终现场验收记录

现场验收结论：`JZ2440 Control MVP COMPLETE`。

本记录基于 2026-09-14 的现场操作。正常运行时保持 `JZ2440 Control.exe` 为唯一 UART owner，不与 `CodexQuotaBridge.exe` 或 `RunBoardBridge.exe` 并行运行。

## A. GUI 与板端画面

| 项目 | PASS / FAIL | Notes |
| --- | --- | --- |
| Control 显示 `Connected`，Current App 为 Qtopia | PASS | 现场重连后重新 probe 并恢复 Qtopia。 |
| Qtopia → Codex Monitor：目标卡片经历 `Starting`，最终显示 `Running` | PASS | CQM provider 正常响应，LCD/UI 显示正常。 |
| Codex Monitor → RunBoard：目标卡片经历 `Starting`，最终显示 `Running` | PASS | RunBoard provider 首帧 RB1 正常发送，LCD/UI 显示正常。 |
| RunBoard → Qtopia：最终 Current App 显示 Qtopia | PASS | APPSTOP、Qtopia 恢复和 UI 状态清理正常。 |
| Windows Current App、Running 卡片与 LCD 当前画面一致 | PASS | 三应用切换过程中人工核对一致。 |

## B. 物理 USB 拔插与恢复

| 项目 | PASS / FAIL | Notes |
| --- | --- | --- |
| 拔出 USB 后 UI 进入 `Disconnected`，Current App 清空，Launch 按钮禁用 | PASS | 从 Qtopia 状态拔出；无异常弹窗循环，Control 未崩溃。 |
| 重新插入后自动发现设备；若端口号变化仍能恢复连接 | PASS | 自动重新发现并连接；本次重插未观察到 COM 号变化。 |
| 重连后 Control 重新查询当前 App，不自动重放上一次启动动作 | PASS | 重连后重新读取 Qtopia，未自动启动断线前应用。 |
| 重连后再次完成一次应用切换 | PASS | 重连后完成 Qtopia → Codex Monitor → RunBoard → Qtopia。 |

## C. 最终恢复与退出

| 项目 | PASS / FAIL | Notes |
| --- | --- | --- |
| 最终恢复 Qtopia，并确认 UI 显示 `Running` | PASS | 最终板端状态为 Qtopia。 |
| 关闭 Control 后确认没有残留 Control/Bridge 进程 | PASS | Control、CodexQuotaBridge、RunBoardBridge、RunBoard Python worker 均无残留。 |
| 未修改启动链、Flash、bootloader、kernel、rootfs 或自启动配置 | PASS | 本次仅修改 Windows Control、日志/文档和既有 host 侧代码。 |

## D. 已知非阻塞事项

- RunBoard 的 `server offline` 是服务器数据源状态，不代表 RunBoard lifecycle 或 RB1 provider 启动失败；本次 RunBoard 卡片、Current App 和 LCD 均已正确运行。
- 如果在 RunBoard 静默运行期间直接拔出 USB，现有板端协议没有主动状态回报，Control 会 fail-closed 为 Unknown/Error，不会盲目恢复 provider；这不影响本次从 Qtopia 开始的 MVP 拔插验收。后续如需支持该场景，应增加安全的板端状态/握手协议。
- RunBoard Python worker 的封装、安装包和自启动仍属于后续增强项。
