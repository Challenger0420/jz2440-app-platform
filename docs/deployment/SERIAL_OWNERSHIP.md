# 串口归属与未来正式方案

当前硬件链路：Windows Bridge → USB serial adapter → JZ2440 UART0。COM 号不稳定，因此 Bridge 默认按兼容 USB serial adapter 的 VID/PID 自动发现，也保留 `--port COMx` override。

板端已观测：kernel command line 为 `console=ttySAC0`；`/proc/tty/driver/serial` 的有效实例为 UART 0；用户空间 shell 和 direct backend 实测使用 `/dev/s3c2410_serial0`。`/dev/ttySAC*` 不存在，`/dev/ttyS0` 虽存在但 direct backend 的 `TCGETS` 配置失败，因此不能作为正式节点。未来正式 backend 应以实际可用节点和启动脚本为准，不仅凭名称猜测。

风险：

- kernel console 会向同一 UART 输出启动日志。
- shell/getty 可能同时读取或写入该 UART。
- Bridge 长期独占的是 Windows 侧 COM，板端 Monitor 还需要独占 UART0 的用户空间输入。
- 不能让 Qtopia、shell、getty 和 Monitor 同时争用同一个 stdin/fb 状态。
- `<CQMQUIT>`、Ctrl-C 和异常退出都必须能释放 framebuffer 映射并恢复 shell/Qtopia。

本轮没有 kill getty、改 inittab、改 bootargs、停 Qtopia 或启动 Monitor。
