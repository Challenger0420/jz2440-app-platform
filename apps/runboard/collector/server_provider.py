"""SSH-backed, read-only server provider for the RunBoard Preview."""

from datetime import datetime
import json
import logging
from pathlib import Path, PurePosixPath
import shlex
from typing import Dict, Iterable, List, Optional, Tuple

from apps.runboard.shared.state_model import Snapshot, validate_snapshot

from .config import RunBoardConfig, load_live_config
from .experiment_detector import ExperimentDetector, ExperimentMetadata, _root_pid, _work_dir
from .matrix_metadata import matrix_root_from_cell, parse_matrix_observations
from .parsers import (
    ProcessInfo,
    parse_cpu_percent,
    parse_loadavg,
    parse_meminfo,
    parse_nvidia_compute,
    parse_nvidia_gpus,
    parse_ps,
    parse_tmux_panes,
    parse_uptime,
)
from .ssh_transport import SSHError, SSHTransport


LOGGER = logging.getLogger("runboard.live")


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()

PROBE_COMMAND = r"""
printf '%s\n' '__RUNBOARD_IDENTITY__'
hostname
printf '%s\n' '__RUNBOARD_CPU__'
cpu_line() { awk '/^cpu / {total=$2+$3+$4+$5+$6+$7+$8+$9; print total, $5}' /proc/stat; }
a=$(cpu_line); sleep 0.25; b=$(cpu_line); printf 'CPU %s\nCPU %s\n' "$a" "$b"
printf '%s\n' '__RUNBOARD_LOADAVG__'
cat /proc/loadavg
printf '%s\n' '__RUNBOARD_UPTIME__'
cat /proc/uptime
printf '%s\n' '__RUNBOARD_MEMINFO__'
awk '/^(MemTotal|MemAvailable|MemFree|Buffers|Cached):/ {print}' /proc/meminfo
printf '%s\n' '__RUNBOARD_GPU__'
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>&1 || true
printf '%s\n' '__RUNBOARD_GPU_COMPUTE__'
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>&1 || true
printf '%s\n' '__RUNBOARD_PROCESSES__'
ps -eo pid=,ppid=,user=,etimes=,stat=,args= --sort=pid
printf '%s\n' '__RUNBOARD_TMUX__'
tmux list-panes -a -F '#{session_name}|#{window_index}|#{pane_index}|#{pane_pid}|#{pane_current_path}|#{pane_current_command}' 2>&1 || true
"""


def _sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("__RUNBOARD_") and line.endswith("__"):
            current = line.strip("_")
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {key: "\n".join(lines) for key, lines in sections.items()}


def _metadata_command(cells: Dict[int, str]) -> str:
    chunks: List[str] = []
    for root_pid, cell in sorted(cells.items()):
        quoted = shlex.quote(cell)
        chunks.append("printf '__RUNBOARD_META__ %s plan\\n' '{}'; cat {}/experiment_plan.json 2>/dev/null || true".format(root_pid, quoted))
        chunks.append("printf '__RUNBOARD_META__ %s latest\\n' '{}'; cat {}/latest_round.json 2>/dev/null || true".format(root_pid, quoted))
        chunks.append("printf '__RUNBOARD_LOG__ %s\\n' '{}'; log=$(find {} -maxdepth 4 -type f -name '*.log' -print 2>/dev/null | head -1); if [ -z \"$log\" ]; then log=$(find {} -maxdepth 4 -type f -name '*.out' -print 2>/dev/null | head -1); fi; if [ -n \"$log\" ]; then tail -80 \"$log\"; fi".format(root_pid, quoted, quoted, quoted))
    return "\n".join(chunks) or "printf '%s\\n' '__RUNBOARD_META_EMPTY__'"


def _matrix_metadata_command(cells: Dict[int, str]) -> str:
    """Emit matrix plans and manifests using read-only shell commands.

    Some accepted experiments keep the active cell in a shard plan such as
    ``formal_kgcoop_shard_plan.json`` while the authoritative 45-cell plan is
    a sibling ``formal_matrix_plan.json``.  Emit both candidates with a
    source token; the parser chooses the candidate containing the active
    run_id and prefers the larger authoritative plan.
    """
    roots: Dict[str, int] = {}
    for root_pid, cell in sorted(cells.items()):
        matrix_root = matrix_root_from_cell(cell)
        if matrix_root and matrix_root not in roots:
            roots[matrix_root] = root_pid
    chunks: List[str] = []
    for matrix_root, root_pid in sorted(roots.items(), key=lambda item: item[1]):
        quoted_root = shlex.quote(matrix_root)
        parent_root = shlex.quote(str(PurePosixPath(matrix_root).parent))
        chunks.append("plan_source=0")
        chunks.append(
            "for plan in $(find {root} -maxdepth 1 -type f "
            "\\( -name 'formal_matrix_plan.json' -o -name '*shard*plan*.json' \\) "
            "-print 2>/dev/null); do "
            "printf '__RUNBOARD_MATRIX_PLAN__ {pid} p%s\\n' \"$plan_source\"; "
            "cat \"$plan\" 2>/dev/null || true; "
            "plan_dir=${{plan%/*}}; "
            "for manifest in \"$plan_dir\"/cells/*/completion_manifest.json; do "
            "if [ -f \"$manifest\" ]; then "
            "printf '__RUNBOARD_MATRIX_COMPLETION__ {pid} p%s\\n' \"$plan_source\"; "
            "cat \"$manifest\"; fi; done; "
            "plan_source=$((plan_source+1)); done".format(root=quoted_root, pid=root_pid)
        )
        chunks.append(
            "for plan in $(find {parent} -maxdepth 2 -type f "
            "-name 'formal_matrix_plan.json' -print 2>/dev/null); do "
            "printf '__RUNBOARD_MATRIX_PLAN__ {pid} p%s\\n' \"$plan_source\"; "
            "cat \"$plan\" 2>/dev/null || true; "
            "plan_dir=${{plan%/*}}; "
            "for manifest in \"$plan_dir\"/cells/*/completion_manifest.json; do "
            "if [ -f \"$manifest\" ]; then "
            "printf '__RUNBOARD_MATRIX_COMPLETION__ {pid} p%s\\n' \"$plan_source\"; "
            "cat \"$manifest\"; fi; done; "
            "plan_source=$((plan_source+1)); done".format(parent=parent_root, pid=root_pid)
        )
    return "\n".join(chunks) or "printf '%s\\n' '__RUNBOARD_MATRIX_EMPTY__'"


def _cwd_command(pids: Iterable[int]) -> str:
    values = [str(pid) for pid in sorted(set(pids)) if int(pid) > 0]
    if not values:
        return "printf '%s\\n' '__RUNBOARD_CWD_EMPTY__'"
    return "\n".join(
        "printf '__RUNBOARD_CWD__ %s ' '{}'; readlink -f /proc/{}/cwd 2>/dev/null || true".format(pid, pid)
        for pid in values
    )


def _parse_metadata(text: str, logger: logging.Logger) -> Tuple[Dict[int, ExperimentMetadata], Dict[int, bool]]:
    metadata: Dict[int, ExperimentMetadata] = {}
    errors: Dict[int, bool] = {}
    current: Optional[Tuple[int, str]] = None
    buffer: List[str] = []

    def flush() -> None:
        if current is None:
            return
        pid, kind = current
        content = "\n".join(buffer).strip()
        if kind == "log":
            errors[pid] = bool(content and any(token in content.lower() for token in ("traceback", "runtimeerror", "cuda out of memory", "error")))
            return
        if not content:
            return
        try:
            value = json.loads(content)
        except ValueError as error:
            logger.debug("metadata JSON ignored for root %s: %s", pid, error)
            return
        previous = metadata.get(pid, ExperimentMetadata())
        if kind == "plan":
            protocol = value.get("protocol") if isinstance(value, dict) else {}
            explicit_started = None
            explicit_started_source = None
            if isinstance(value, dict):
                for key in ("startedAt", "started_at", "startTime", "start_time"):
                    explicit_started = _parse_timestamp(value.get(key))
                    if explicit_started is not None:
                        explicit_started_source = "RELIABLE"
                        break
                if explicit_started is None:
                    for key in ("createdAt", "created_at"):
                        explicit_started = _parse_timestamp(value.get(key))
                        if explicit_started is not None:
                            explicit_started_source = "INFERRED"
                            break
            metadata[pid] = ExperimentMetadata(
                name=value.get("run_id") or previous.name,
                dataset=value.get("dataset") or previous.dataset,
                seed=value.get("seed") if value.get("seed") is not None else previous.seed,
                total_round=protocol.get("global_rounds") if isinstance(protocol, dict) else previous.total_round,
                current_round=previous.current_round,
                metric_name=previous.metric_name,
                metric_value=previous.metric_value,
                error=previous.error,
                started_at=explicit_started or previous.started_at,
                started_at_source=explicit_started_source or previous.started_at_source,
            )
        elif kind == "latest":
            round_value = value.get("round") if isinstance(value, dict) else None
            metadata[pid] = ExperimentMetadata(
                name=previous.name,
                dataset=previous.dataset,
                seed=previous.seed,
                total_round=previous.total_round,
                current_round=round_value if isinstance(round_value, int) else previous.current_round,
                metric_name=previous.metric_name,
                metric_value=previous.metric_value,
                error=previous.error,
                started_at=previous.started_at,
                started_at_source=previous.started_at_source,
            )

    for line in text.splitlines():
        if line.startswith("__RUNBOARD_META__ ") or line.startswith("__RUNBOARD_LOG__ "):
            flush()
            buffer = []
            fields = line.split()
            if line.startswith("__RUNBOARD_META__ ") and len(fields) == 3:
                current = (int(fields[1]), fields[2])
            elif line.startswith("__RUNBOARD_LOG__ ") and len(fields) == 2:
                current = (int(fields[1]), "log")
            else:
                current = None
        elif current is not None:
            buffer.append(line)
    flush()
    for pid, has_error in errors.items():
        previous = metadata.get(pid, ExperimentMetadata())
        metadata[pid] = ExperimentMetadata(
            name=previous.name,
            dataset=previous.dataset,
            seed=previous.seed,
            total_round=previous.total_round,
            current_round=previous.current_round,
            metric_name=previous.metric_name,
            metric_value=previous.metric_value,
            error=has_error,
            started_at=previous.started_at,
            started_at_source=previous.started_at_source,
        )
    return metadata, errors


def _metadata_cells(processes: Dict[int, ProcessInfo], roots: Iterable[int]) -> Dict[int, str]:
    cells: Dict[int, str] = {}
    for root in roots:
        commands = [process.command for process in processes.values() if _root_pid(process.pid, processes) == root]
        work_dir = next((_work_dir(command) for command in commands if _work_dir(command)), None)
        if work_dir:
            path = PurePosixPath(work_dir)
            if len(path.parents) >= 3:
                cells[root] = str(path.parents[2])
    return cells


def _aggregate_gpu(gpus: List[object]) -> Dict[str, Optional[float]]:
    if not gpus:
        return {"name": None, "utilizationPercent": None, "memoryUsedGiB": None, "memoryTotalGiB": None, "temperatureC": None}  # type: ignore
    values = gpus
    names = [item.name for item in values if item.name]
    util = [item.utilization_percent for item in values if item.utilization_percent is not None]
    used = [item.memory_used_mib for item in values if item.memory_used_mib is not None]
    total = [item.memory_total_mib for item in values if item.memory_total_mib is not None]
    temperature = [item.temperature_c for item in values if item.temperature_c is not None]
    return {
        "name": names[0] if len(names) == 1 else ("{} GPUs".format(len(names)) if names else None),
        "utilizationPercent": sum(util) / len(util) if util else None,
        "memoryUsedGiB": sum(used) / 1024.0 if used else None,
        "memoryTotalGiB": sum(total) / 1024.0 if total else None,
        "temperatureC": max(temperature) if temperature else None,
    }  # type: ignore


def _codex_usage(root: Path) -> Dict[str, object]:
    # Quota is intentionally not fabricated in the Server Provider.  The
    # separate Codex provider owns this data; until it succeeds, preserve
    # unknown semantics instead of showing a misleading 0%.
    return {"fiveHourPercent": None, "fiveHourReset": "unknown",
            "weekPercent": None, "weekReset": "unknown", "resetCards": None}


def make_offline_snapshot(root: Path, error: str,
                          server_id: str = "SERVER-01",
                          display_name: str = "SERVER-01") -> Snapshot:
    return validate_snapshot({
        "schemaVersion": 1,
        "source": "live",
        "collectionError": error,
        "server": {
            "id": server_id,
            "displayName": display_name,
            "hostname": None,
            "status": "offline",
            "cpuPercent": None,
            "ram": {"usedGiB": None, "totalGiB": None, "usedPercent": None},
            "loadAverage": {"one": None, "five": None, "fifteen": None},
            "uptimeSeconds": None,
            "gpu": {"name": None, "utilizationPercent": None, "memoryUsedGiB": None, "memoryTotalGiB": None, "temperatureC": None},
        },
        "experiments": [],
        "codexUsage": _codex_usage(root),
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    })


class SSHServerProvider:
    """Collect one normalized snapshot. Every remote command is read-only."""

    def __init__(self, root: Path, config: RunBoardConfig, logger: Optional[logging.Logger] = None) -> None:
        self.root = root
        self.config = config
        self.logger = logger or LOGGER
        self.transport = SSHTransport(config.ssh)

    @classmethod
    def from_local_config(cls, root: Path, logger: Optional[logging.Logger] = None) -> "SSHServerProvider":
        return cls(root, load_live_config(root), logger)

    def collect(self) -> Snapshot:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            probe = self.transport.run(PROBE_COMMAND).stdout
            snapshot = self._from_probe(probe, now)
            self.logger.info("live collection succeeded: experiments=%d", len(snapshot.experiments))
            return snapshot
        except SSHError as error:
            self.logger.error("live collection failed: %s", error)
            return validate_snapshot(self._offline_snapshot(now, str(error)))
        except Exception as error:
            self.logger.exception("live collection parsing failed")
            return validate_snapshot(self._offline_snapshot(now, "parse error: {}".format(error)))

    def _from_probe(self, text: str, now: str) -> Snapshot:
        sections = _sections(text)
        processes = parse_ps(sections.get("RUNBOARD_PROCESSES", ""))
        gpu_processes = parse_nvidia_compute(sections.get("RUNBOARD_GPU_COMPUTE", ""))
        gpus = parse_nvidia_gpus(sections.get("RUNBOARD_GPU", ""))
        panes = parse_tmux_panes(sections.get("RUNBOARD_TMUX", ""))
        del panes  # Parsed to keep the observation boundary explicit; detector uses PPID first.
        detector = ExperimentDetector()
        observation_time = _parse_timestamp(now) or datetime.now().astimezone()
        preliminary = detector.detect(processes, gpu_processes, now=observation_time)
        roots = [int(item["_rootPid"]) for item in preliminary]

        cwd_result = self.transport.run(_cwd_command(roots)).stdout if roots else ""
        for line in cwd_result.splitlines():
            fields = line.split(None, 2)
            if len(fields) == 3 and fields[0] == "__RUNBOARD_CWD__":
                try:
                    if int(fields[1]) in processes:
                        processes[int(fields[1])].cwd = fields[2].strip() or None
                except ValueError:
                    pass

        cells = _metadata_cells(processes, roots)
        metadata_text = self.transport.run(_metadata_command(cells)).stdout if cells else ""
        matrix_text = self.transport.run(_matrix_metadata_command(cells)).stdout if cells else ""
        matrix_by_root = parse_matrix_observations(
            matrix_text,
            {root_pid: PurePosixPath(cell).name for root_pid, cell in cells.items()},
            self.config.job_display_names,
        )
        metadata, log_errors = _parse_metadata(metadata_text, self.logger)
        for root_pid, has_error in log_errors.items():
            previous = metadata.get(root_pid, ExperimentMetadata())
            metadata[root_pid] = ExperimentMetadata(
                name=previous.name, dataset=previous.dataset, seed=previous.seed,
                total_round=previous.total_round, current_round=previous.current_round,
                metric_name=previous.metric_name, metric_value=previous.metric_value, error=has_error,
                started_at=previous.started_at, started_at_source=previous.started_at_source,
            )
        experiments = detector.detect(processes, gpu_processes, metadata, now=observation_time)
        clean_experiments = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in experiments
        ]
        job = None
        if experiments:
            matrix = matrix_by_root.get(int(experiments[0]["_rootPid"]))
            if matrix:
                job = {
                    "name": matrix.job_name,
                    "matrixCompleted": matrix.matrix_completed,
                    "matrixTotal": matrix.matrix_total,
                    "currentCell": matrix.current_cell,
                    "method": matrix.method,
                    "matrixProgressPercent": matrix.matrix_progress_percent,
                    "progressScope": "matrix-cells",
                }
                job = {key: value for key, value in job.items() if value is not None}

        meminfo = parse_meminfo(sections.get("RUNBOARD_MEMINFO", ""))
        total_kib = meminfo.get("MemTotal")
        available_kib = meminfo.get("MemAvailable")
        if total_kib is not None and available_kib is not None:
            used_kib = max(0, total_kib - available_kib)
            used_percent = 100.0 * used_kib / total_kib if total_kib else None
        else:
            used_kib = None
            used_percent = None
        load = parse_loadavg(sections.get("RUNBOARD_LOADAVG", ""))
        uptime = parse_uptime(sections.get("RUNBOARD_UPTIME", ""))
        cpu = parse_cpu_percent(sections.get("RUNBOARD_CPU", ""))
        gpu = _aggregate_gpu(gpus)
        gpu_text = sections.get("RUNBOARD_GPU", "")
        degraded = cpu is None or total_kib is None or (not gpus and "NVIDIA-SMI" in gpu_text)
        errors = []
        if cpu is None:
            errors.append("CPU unavailable")
        if total_kib is None:
            errors.append("RAM unavailable")
        if not gpus and "NVIDIA-SMI" in gpu_text:
            errors.append("nvidia-smi unavailable")
        state = {
            "schemaVersion": 1,
            "source": "live",
            "collectionError": "; ".join(errors) or None,
            "server": {
                "id": self.config.server_id,
                "displayName": self.config.display_name,
                # The public state contract uses the configured display name;
                # never propagate the remote machine hostname into RB1/Preview.
                "hostname": None,
                "status": "degraded" if degraded else "online",
                "cpuPercent": cpu,
                "ram": {
                    "usedGiB": used_kib / 1024.0 / 1024.0 if used_kib is not None else None,
                    "totalGiB": total_kib / 1024.0 / 1024.0 if total_kib is not None else None,
                    "usedPercent": used_percent,
                },
                "loadAverage": {"one": load[0] if load else None, "five": load[1] if load else None, "fifteen": load[2] if load else None},
                "uptimeSeconds": uptime,
                "gpu": gpu,
            },
            "experiments": clean_experiments,
            "codexUsage": _codex_usage(self.root),
            "updatedAt": now,
        }
        if job is not None:
            state["job"] = job
        return validate_snapshot(state)

    def _offline_snapshot(self, now: str, error: str) -> Dict[str, object]:
        return {
            "schemaVersion": 1,
            "source": "live",
            "collectionError": error,
            "server": {
                "id": self.config.server_id,
                "displayName": self.config.display_name,
                "hostname": None,
                "status": "offline",
                "cpuPercent": None,
                "ram": {"usedGiB": None, "totalGiB": None, "usedPercent": None},
                "loadAverage": {"one": None, "five": None, "fifteen": None},
                "uptimeSeconds": None,
                "gpu": {"name": None, "utilizationPercent": None, "memoryUsedGiB": None, "memoryTotalGiB": None, "temperatureC": None},
            },
            "experiments": [],
            "codexUsage": _codex_usage(self.root),
            "updatedAt": now,
        }
