"""Pure parsers for Linux and nvidia-smi fixture text."""

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Tuple


@dataclass
class GPUInfo:
    name: Optional[str]
    utilization_percent: Optional[float]
    memory_used_mib: Optional[float]
    memory_total_mib: Optional[float]
    temperature_c: Optional[float]


@dataclass
class GPUProcess:
    pid: int
    process_name: Optional[str]
    memory_used_mib: float


@dataclass
class ProcessInfo:
    pid: int
    ppid: int
    user: str
    elapsed_seconds: int
    state: str
    command: str
    cwd: Optional[str] = None
    gpu_memory_mib: float = 0.0

    @property
    def gpu_active(self) -> bool:
        return self.gpu_memory_mib > 0


@dataclass
class TmuxPane:
    session: str
    window: str
    pane: str
    pid: int
    cwd: Optional[str]
    command: Optional[str]


def _float(value: str) -> Optional[float]:
    value = value.strip()
    if not value or value.upper() in {"N/A", "NA", "-"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_meminfo(text: str) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^(MemTotal|MemAvailable|MemFree|Buffers|Cached):\s+(\d+)", line)
        if match:
            result[match.group(1)] = int(match.group(2))
    return result


def parse_loadavg(text: str) -> Optional[Tuple[float, float, float]]:
    fields = text.strip().split()
    if len(fields) < 3:
        return None
    values = [_float(value) for value in fields[:3]]
    return tuple(values) if all(value is not None for value in values) else None  # type: ignore


def parse_uptime(text: str) -> Optional[int]:
    match = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)", text)
    return int(float(match.group(1))) if match else None


def parse_cpu_percent(text: str) -> Optional[float]:
    values: List[Tuple[float, float]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == "CPU":
            try:
                values.append((float(fields[1]), float(fields[2])))
            except ValueError:
                pass
    if len(values) < 2:
        return None
    total_a, idle_a = values[0]
    total_b, idle_b = values[1]
    total_delta = total_b - total_a
    idle_delta = idle_b - idle_a
    if total_delta <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta)))


def parse_nvidia_gpus(text: str) -> List[GPUInfo]:
    result: List[GPUInfo] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 5 or not fields[0]:
            continue
        result.append(GPUInfo(
            name=fields[0],
            utilization_percent=_float(fields[1]),
            memory_used_mib=_float(fields[2]),
            memory_total_mib=_float(fields[3]),
            temperature_c=_float(fields[4]),
        ))
    return result


def parse_nvidia_compute(text: str) -> List[GPUProcess]:
    result: List[GPUProcess] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 3:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        memory = _float(fields[2]) or 0.0
        result.append(GPUProcess(pid, fields[1] or None, memory))
    return result


def parse_ps(text: str) -> Dict[int, ProcessInfo]:
    result: Dict[int, ProcessInfo] = {}
    for line in text.splitlines():
        fields = line.strip().split(None, 5)
        if len(fields) < 6:
            continue
        try:
            pid, ppid, elapsed = int(fields[0]), int(fields[1]), int(fields[3])
        except ValueError:
            continue
        result[pid] = ProcessInfo(pid, ppid, fields[2], elapsed, fields[4], fields[5])
    return result


def parse_tmux_panes(text: str) -> List[TmuxPane]:
    result: List[TmuxPane] = []
    for line in text.splitlines():
        fields = line.split("|", 5)
        if len(fields) != 6:
            continue
        try:
            pid = int(fields[3])
        except ValueError:
            continue
        result.append(TmuxPane(fields[0], fields[1], fields[2], pid,
                               fields[4] or None, fields[5] or None))
    return result
