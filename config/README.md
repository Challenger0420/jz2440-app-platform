# RunBoard configuration

`config.example.json` 只提供占位符和 Preview 默认值。Live Preview 只读取被忽略的 `config/config.local.json`；真实 SSH 地址、用户名、端口、identity file 和参数不得进入源码、mock、README、docs、测试或截图。

安全约定：

- 示例主机使用 `100.x.x.x`，服务器标识使用 `SERVER-01`。
- 不把真实用户名、主机名、端口、内部路径或凭据写入仓库。
- `config.local.json`、`.env` 和其他本地配置已加入 `.gitignore`。
- Live Provider 只执行 `hostname`、`/proc`、`ps`、`nvidia-smi`、`tmux list-*` 和实验元数据/日志读取，不执行启停、修改、上传或权限操作。
- `sampling` 配置分别控制 server resource、experiment、Codex Usage 和 UI render；当前 Server Provider 为原子快照，实际 SSH probe 使用 `serverResourceSeconds`，Codex Usage 默认低频刷新。
