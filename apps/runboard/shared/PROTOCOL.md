# RunBoard shared snapshot

RunBoard 的 Preview、Host Aggregator 和未来的 Windows Bridge / JZ2440 target
共享同一份状态快照结构：

```text
state-model.schema.json
        ↓
JSON snapshot { server, experiments, codexUsage, updatedAt }
```

Provider 先在 Windows Host 上归一化为该快照。Host Aggregator 独立合并
Server Provider 和 CodexUsage Provider，并为两个数据源分别维护 freshness。
UI 不直接依赖 Provider；本阶段仍不连接串口或 target。

字段约定：

- `server`：服务器标识、在线状态、CPU、RAM、GPU utilization 和 GPU memory。
- `experiments`：采集层可保留完整列表；数组顺序按优先级决定主实验和第二实验。
- `codexUsage`：`5H`、`5H reset`、`week`、`week reset`、`RC`。
- `updatedAt`：这份快照的展示时间。
- `source`：`mock` 或 `live`。
- `providers`：每个 Provider 的 `live`、`mock`、`unavailable` 或 `error` 状态。
- `freshness`：Server 和 Codex Usage 独立使用 `fresh`、`stale`、`offline`、`error`。
- 单次失败时保留上一份有效数据并标记 `stale`；连续失败达到配置阈值后才标记 `offline`。
- `collectionError`：当前采集错误，不代表另一 Provider 失效。
- 资源、round、dataset、seed、metric 等无法可靠读取的字段使用 `null`，不由采集层猜测。

## Windows Host → board protocol draft

`apps/runboard/shared/board_protocol.py` 定义低开销 ASCII 帧，而不是把完整
JSON 发送到老 ARM 平台：

```text
RB1|L=<payload bytes>|V=1|SEQ=7|SF=fresh|CF=stale|...|CRC=ABCD\n
```

Payload 是缩写 TLV（例如 `CPU`、`GMU`、`E0N`、`C5`），字符串使用 percent
encoding，未知值使用 `?`。`L` 检查完整长度，`CRC` 为 CRC-16-CCITT，换行
提供帧边界。板端编码最多包含优先级最高的两个实验；Host state 仍保留完整列表。
`FrameDecoder` 支持分片和连续帧，并拒绝截断、非法长度及 checksum 错误。

`apps/runboard/host/transport.py` 当前只提供 `MemoryTransport`；真实
`SerialTransport`、COM 口和 target 集成留待后续明确任务。已有的
`apps/codex-monitor/protocol` 与 `platform/host/board-controller` 保持不变。
