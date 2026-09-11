"""Process-to-experiment grouping with a GPU-first, parent-aware policy."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .parsers import GPUProcess, ProcessInfo


@dataclass(frozen=True)
class ExperimentMetadata:
    name: Optional[str] = None
    dataset: Optional[str] = None
    seed: Optional[int] = None
    total_round: Optional[int] = None
    current_round: Optional[int] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    error: bool = False


def _argument(command: str, key: str) -> Optional[str]:
    match = re.search(r"(?:^|\s){}\s+([^\s]+)".format(re.escape(key)), command)
    return match.group(1) if match else None


def _work_dir(command: str) -> Optional[str]:
    return _argument(command, "--work-dir")


def _cell_name(work_dir: Optional[str]) -> Optional[str]:
    if not work_dir:
        return None
    path = PurePosixPath(work_dir)
    try:
        return path.parents[2].name
    except IndexError:
        return None


def _root_pid(pid: int, processes: Dict[int, ProcessInfo]) -> int:
    seen: Set[int] = set()
    current = pid
    while current in processes and current not in seen:
        seen.add(current)
        parent = processes[current].ppid
        if parent <= 1 or parent not in processes:
            return current
        current = parent
    return current


def _is_python_experiment(process: ProcessInfo) -> bool:
    command = process.command.lower()
    if "python" not in command or "python -c" in command:
        return False
    markers = ("--work-dir", "train.py", "train_", "experiment", "run_")
    return any(marker in command for marker in markers)


def _status(processes: Sequence[ProcessInfo], metadata: ExperimentMetadata) -> str:
    if metadata.error and not any("R" in process.state for process in processes):
        return "ERROR"
    if any(process.state and process.state[0] not in {"Z", "X"} for process in processes):
        return "RUNNING"
    return "UNKNOWN"


class ExperimentDetector:
    """Group GPU workers and their ancestors into Research Jobs.

    Priority is deterministic and documented: GPU-active, running, larger GPU
    memory, then older start time (larger elapsed time), then root PID.
    """

    def detect(self, processes: Dict[int, ProcessInfo],
               gpu_processes: Iterable[GPUProcess],
               metadata: Optional[Dict[int, ExperimentMetadata]] = None) -> List[Dict[str, object]]:
        metadata = metadata or {}
        gpu_by_pid = {item.pid: item for item in gpu_processes}
        candidate_pids = set(gpu_by_pid)
        candidate_pids.update(pid for pid, process in processes.items() if _is_python_experiment(process))

        roots: Dict[int, Set[int]] = {}
        for pid in candidate_pids:
            root = _root_pid(pid, processes)
            roots.setdefault(root, set()).add(pid)

        detected: List[Dict[str, object]] = []
        for root, seed_pids in roots.items():
            member_pids = {pid for pid in processes if _root_pid(pid, processes) == root}
            member_pids.update(seed_pids)
            members = [processes[pid] for pid in member_pids if pid in processes]
            if not members:
                continue
            root_process = processes.get(root) or members[0]
            commands = [process.command for process in members]
            work_dir = next((_work_dir(command) for command in commands if _work_dir(command)), None)
            cell = _cell_name(work_dir)
            meta = metadata.get(root, ExperimentMetadata())
            name = meta.name or cell or _argument(root_process.command, "--module") or "python-job-{}".format(root)
            current_round = None
            if work_dir:
                match = re.search(r"round[_-](\d+)", work_dir, re.IGNORECASE)
                current_round = int(match.group(1)) if match else None
            if current_round is None:
                current_round = meta.current_round
            total_round = meta.total_round
            if total_round is None:
                match = re.search(r"(?:^|[_-])r(\d+)(?:[_-]|$)", cell or "", re.IGNORECASE)
                total_round = int(match.group(1)) if match else None
            progress = None
            if current_round is not None and total_round and total_round > 0:
                progress = min(100.0, 100.0 * current_round / total_round)
            gpu_memory = sum(gpu_by_pid[pid].memory_used_mib for pid in member_pids if pid in gpu_by_pid)
            members_with_gpu = [processes[pid] for pid in member_pids if pid in processes and pid in gpu_by_pid]
            item: Dict[str, object] = {
                "name": name,
                "user": root_process.user,
                "status": _status(members, meta),
                "progress": progress,
                "currentRound": current_round,
                "totalRound": total_round,
                "elapsedSeconds": root_process.elapsed_seconds,
                "dataset": meta.dataset,
                "seed": meta.seed,
                "metricName": meta.metric_name,
                "metricValue": meta.metric_value,
                "_rootPid": root,
                "_gpuActive": bool(members_with_gpu),
                "_gpuMemoryMiB": gpu_memory,
                "_commandIdentity": next((command for command in commands if "python" in command.lower()), root_process.command),
                "_cwd": root_process.cwd,
            }
            detected.append(item)

        detected.sort(key=lambda item: (
            bool(item["_gpuActive"]),
            item["status"] == "RUNNING",
            float(item["_gpuMemoryMiB"]),
            int(next(process.elapsed_seconds for process in processes.values() if process.pid == item["_rootPid"])),
            -int(item["_rootPid"]),
        ), reverse=True)
        return detected
