"""Small injectable OpenSSH transport. Commands are always read-only."""

from dataclasses import dataclass
import subprocess
from typing import Callable, List, Optional

from .config import SSHConfig


class SSHError(RuntimeError):
    pass


@dataclass(frozen=True)
class SSHResult:
    stdout: str
    stderr: str
    returncode: int


Runner = Callable[..., subprocess.CompletedProcess]


class SSHTransport:
    def __init__(self, config: SSHConfig, runner: Optional[Runner] = None,
                 executable: str = "ssh") -> None:
        self.config = config
        self.runner = runner or subprocess.run
        self.executable = executable

    def run(self, remote_command: str) -> SSHResult:
        target = "{}@{}".format(self.config.user, self.config.host)
        arguments: List[str] = [
            self.executable,
            "-i", self.config.identity_file,
            "-p", str(self.config.port),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", "ConnectionAttempts=1",
            "-o", "ConnectTimeout={}".format(self.config.connect_timeout_seconds),
            target,
            remote_command,
        ]
        try:
            result = self.runner(
                arguments,
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SSHError("SSH transport failed: {}".format(error))
        response = SSHResult(result.stdout or "", result.stderr or "", result.returncode)
        if response.returncode != 0:
            detail = response.stderr.strip() or "exit code {}".format(response.returncode)
            raise SSHError("SSH command failed: {}".format(detail))
        return response
