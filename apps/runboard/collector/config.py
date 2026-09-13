"""Load the ignored local-only live collection configuration."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SSHConfig:
    host: str
    user: str
    port: int
    identity_file: str
    connect_timeout_seconds: int = 8
    command_timeout_seconds: int = 15


@dataclass(frozen=True)
class CodexUsageConfig:
    mode: str = "real"
    command: str = "build/bridge/CodexQuotaBridge.exe"
    timeout_seconds: int = 20


@dataclass(frozen=True)
class RunBoardConfig:
    server_id: str
    display_name: str
    ssh: SSHConfig
    server_resource_seconds: int = 10
    experiment_seconds: int = 20
    codex_usage_seconds: int = 300
    ui_render_milliseconds: int = 1000
    offline_after_failures: int = 3
    codex_usage: CodexUsageConfig = CodexUsageConfig()
    job_display_names: Dict[str, str] = field(default_factory=dict)


def load_live_config(root: Path) -> RunBoardConfig:
    """Read config/config.local.json; never fall back to the public example."""
    path = root / "config" / "config.local.json"
    if not path.is_file():
        raise ConfigError("missing local config: config/config.local.json")
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw: Dict[str, Any] = json.load(handle)
    except (OSError, ValueError) as error:
        raise ConfigError("cannot read local config: {}".format(error))

    server = raw.get("server")
    ssh = server.get("ssh") if isinstance(server, dict) else None
    if not isinstance(server, dict) or not isinstance(ssh, dict):
        raise ConfigError("local config must contain server.ssh")
    required = ("host", "user", "port", "identityFile")
    if any(not ssh.get(key) for key in required):
        raise ConfigError("local config is missing an SSH field")
    if ssh["host"] == "100.x.x.x" or ssh["user"] == "USER":
        raise ConfigError("local config still contains public placeholders")
    try:
        port = int(ssh["port"])
        connect_timeout = int(ssh.get("connectTimeoutSeconds", 8))
        command_timeout = int(ssh.get("commandTimeoutSeconds", 15))
    except (TypeError, ValueError):
        raise ConfigError("SSH numeric fields are invalid")
    if not 1 <= port <= 65535 or connect_timeout < 1 or command_timeout < 1:
        raise ConfigError("SSH numeric fields are out of range")

    sampling = raw.get("sampling") if isinstance(raw.get("sampling"), dict) else {}
    try:
        sample_values = {
            "server_resource_seconds": int(sampling.get("serverResourceSeconds", 10)),
            "experiment_seconds": int(sampling.get("experimentSeconds", 20)),
            "codex_usage_seconds": int(sampling.get("codexUsageSeconds", 300)),
            "ui_render_milliseconds": int(sampling.get("uiRenderMilliseconds", 1000)),
            "offline_after_failures": int(sampling.get("offlineAfterFailures", 3)),
        }
    except (TypeError, ValueError):
        raise ConfigError("sampling numeric fields are invalid")
    if any(value < 1 for value in sample_values.values()):
        raise ConfigError("sampling numeric fields must be positive")

    usage_raw = raw.get("codexUsage") if isinstance(raw.get("codexUsage"), dict) else {}
    usage_mode = str(usage_raw.get("mode", "real"))
    usage_command = str(usage_raw.get("command", "build/bridge/CodexQuotaBridge.exe"))
    try:
        usage_timeout = int(usage_raw.get("timeoutSeconds", 20))
    except (TypeError, ValueError):
        raise ConfigError("codexUsage timeoutSeconds is invalid")
    if usage_mode not in {"real", "mock", "unavailable"} or not usage_command or usage_timeout < 1:
        raise ConfigError("codexUsage configuration is invalid")

    server_id = str(server.get("id") or "SERVER-01")
    display_name = str(server.get("displayName") or server_id)
    display_names_raw = raw.get("jobDisplayNames")
    job_display_names = {
        str(key): str(value).strip()
        for key, value in display_names_raw.items()
        if str(key).strip() and str(value).strip()
    } if isinstance(display_names_raw, dict) else {}
    return RunBoardConfig(
        server_id=server_id,
        display_name=display_name,
        ssh=SSHConfig(
            host=str(ssh["host"]),
            user=str(ssh["user"]),
            port=port,
            identity_file=str(ssh["identityFile"]),
            connect_timeout_seconds=connect_timeout,
            command_timeout_seconds=command_timeout,
        ),
        **sample_values,
        codex_usage=CodexUsageConfig(usage_mode, usage_command, usage_timeout),
        job_display_names=job_display_names,
    )
