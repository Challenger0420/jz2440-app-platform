"""Emit one RunBoard state as an RB1 frame for the host bridge.

This process owns data collection only.  It never opens a serial port; the
small C# bridge owns COM discovery and transport, which keeps the two failure
domains independent and makes scenario replay safe.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from urllib.parse import unquote

# Direct script execution is the supported bridge entry point; make the
# repository package importable without requiring PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.runboard.collector.config import load_live_config
from apps.runboard.collector.server_provider import SSHServerProvider
from apps.runboard.host.aggregator import RunBoardAggregator, StaticServerProvider
from apps.runboard.host.codex_usage_provider import (
    CodexMonitorQuotaProvider,
    MockCodexUsageProvider,
)
from apps.runboard.host.live_sender import PersistentLiveSender, run_worker
from apps.runboard.shared.board_protocol import decode_frame, encode_state
from apps.runboard.shared.state_model import Snapshot, load_snapshot, validate_snapshot


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mock_aggregator(root: Path, scenario: str) -> RunBoardAggregator:
    snapshot = _load_mock_snapshot(root, scenario)
    return RunBoardAggregator(
        StaticServerProvider(snapshot),
        MockCodexUsageProvider(snapshot.data["codexUsage"]),
        server_resource_seconds=1,
        codex_usage_seconds=1,
    )


def _load_mock_snapshot(root: Path, scenario: str) -> Snapshot:
    return load_snapshot(root / "apps" / "runboard" / "shared" / "mocks" / (scenario + ".json"))


def _restore_mock_display_state(collected: Snapshot, mock_source: Snapshot) -> Snapshot:
    """Keep explicit mock freshness/status instead of treating replay as live success."""
    data = dict(collected.data)
    data["freshness"] = {
        key: dict(value)
        for key, value in (mock_source.data.get("freshness") or {}).items()
    }
    data["server"] = dict(data["server"])
    data["server"]["status"] = mock_source.data["server"]["status"]
    return validate_snapshot(data)


def build_aggregator(root: Path, live: bool, scenario: str | None) -> RunBoardAggregator:
    if not live:
        if scenario not in {"idle", "single", "double", "matrix_single", "completed", "error", "degraded", "stale", "stale_after_last_good", "offline", "offline_after_last_good", "offline_cold_start", "longtext"}:
            raise ValueError("scenario must be idle, single, double, matrix_single, completed, error, degraded, stale, stale_after_last_good, offline, offline_after_last_good, offline_cold_start or longtext")
        return _mock_aggregator(root, scenario)
    config = load_live_config(root)
    server = SSHServerProvider(root, config)
    codex = CodexMonitorQuotaProvider(
        root,
        command=config.codex_usage.command,
        timeout_seconds=config.codex_usage.timeout_seconds,
    )
    return RunBoardAggregator(
        server,
        codex,
        offline_after_failures=config.offline_after_failures,
        server_resource_seconds=config.server_resource_seconds,
        experiment_seconds=config.experiment_seconds,
        codex_usage_seconds=config.codex_usage_seconds,
    )


def dry_run_summary(snapshot: Snapshot, frame: bytes, decoded: dict[str, object]) -> str:
    """Return a sanitized, human-readable live verification summary.

    This deliberately never prints the raw RB1 frame or the Linux username.
    The command is therefore safe to use as the pre-hardware gate before a
    later, explicit serial-send operation.
    """
    data = snapshot.data
    server = data["server"]
    gpu = server["gpu"]
    ram = server["ram"]
    job = data.get("job") or {}
    experiments = data.get("experiments") or []
    experiment = experiments[0] if experiments else {}
    codex = data["codexUsage"]
    freshness = data.get("freshness") or {}
    decoded_freshness = decoded.get("freshness") or {}
    decoded_codex = decoded.get("codexUsage") or {}
    frame_fields = {}
    for item in frame[:-1].split(b"|"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            frame_fields[key.decode("ascii")] = value.decode("ascii")
    return "\n".join((
        "RUNBOARD_DRY_RUN=PASS",
        "SERIAL=NO",
        "SERVER_STATUS={}".format(server.get("status")),
        "SERVER_CPU={}".format(server.get("cpuPercent")),
        "SERVER_RAM={}".format(ram.get("usedPercent")),
        "SERVER_GPU={}".format(gpu.get("utilizationPercent")),
        "SERVER_VRAM={}/{}".format(gpu.get("memoryUsedGiB"), gpu.get("memoryTotalGiB")),
        "SERVER_FRESHNESS={}".format((freshness.get("server") or {}).get("state")),
        "JOB_NAME={}".format(job.get("name") or "UNKNOWN"),
        "MATRIX={}/{}".format(job.get("matrixCompleted", "--"), job.get("matrixTotal", "--")),
        "CURRENT_CELL={}".format(job.get("currentCell", "--")),
        "METHOD={}".format(job.get("method") or "UNKNOWN"),
        "EXPERIMENT_COUNT={}".format(len(experiments)),
        "EXPERIMENT_USER=USER-0" if experiment else "EXPERIMENT_USER=UNKNOWN",
        "EXPERIMENT_STATUS={}".format(experiment.get("status") or "UNKNOWN"),
        "ROUND={}/{}".format(experiment.get("currentRound", "--"), experiment.get("totalRound", "--")),
        "ROUND_PROGRESS_SCOPE={}".format(experiment.get("progressScope") or "UNKNOWN"),
        "TIME={} SOURCE={}".format(experiment.get("elapsedSeconds", "--"), experiment.get("elapsedSource", "UNKNOWN")),
        "DATASET={}".format(experiment.get("dataset") or "UNKNOWN"),
        "SEED={}".format(experiment.get("seed") if experiment.get("seed") is not None else "UNKNOWN"),
        "CODEX_5H={}".format(decoded_codex.get("fiveHourPercent")),
        "CODEX_5H_RESET={}".format(decoded_codex.get("fiveHourReset") or "UNKNOWN"),
        "CODEX_WEEK={}".format(decoded_codex.get("weekPercent")),
        "CODEX_WEEK_RESET={}".format(decoded_codex.get("weekReset") or "UNKNOWN"),
        "CODEX_RC={}".format(decoded_codex.get("resetCards") if decoded_codex.get("resetCards") is not None else "UNKNOWN"),
        "CODEX_FRESHNESS={}".format((decoded_freshness.get("codexUsage") or {}).get("state")),
        "CT={}".format(unquote(frame_fields.get("CT", "?"))),
        "UA={}".format(frame_fields.get("UA", "?")),
        "FRAME_BYTES={}".format(len(frame)),
        "FRAME_TRAILING_NEWLINE=PASS" if frame.endswith(b"\n") and not frame[:-1].endswith(b"\n") else "FRAME_TRAILING_NEWLINE=FAIL",
        "FRAME_CRC=PASS",
        "FRAME_DECODE=PASS",
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RunBoard host state to RB1")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="read server and accepted Codex provider")
    mode.add_argument("--scenario", choices=("idle", "single", "double", "matrix_single", "completed", "error", "degraded", "stale", "stale_after_last_good", "offline", "offline_after_last_good", "offline_cold_start", "longtext"))
    mode.add_argument("--live-worker", action="store_true", help="serve persistent live RB1 frames on stdin/stdout")
    parser.add_argument("--sequence", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="verify live aggregation/RB1 without opening serial")
    parser.add_argument("--persistent-dry-run", action="store_true", help="run several persistent live cycles without serial I/O")
    parser.add_argument("--cycles", type=int, default=3, help="persistent dry-run cycle count")
    parser.add_argument("--interval", type=float, default=0.0, help="persistent dry-run delay in seconds")
    args = parser.parse_args(argv)
    if args.dry_run and not args.live:
        parser.error("--dry-run requires --live")
    if args.persistent_dry_run and not args.live:
        parser.error("--persistent-dry-run requires --live")
    if args.dry_run and args.persistent_dry_run:
        parser.error("choose --dry-run or --persistent-dry-run")
    if args.live_worker and (args.dry_run or args.persistent_dry_run):
        parser.error("--live-worker cannot be combined with dry-run modes")
    root = _root()
    try:
        if args.live_worker:
            return run_worker(root)
        if args.persistent_dry_run:
            sender = PersistentLiveSender.from_root(root)
            sender.run_dry_run(args.cycles, args.interval, sys.stdout, dry_run_summary)
            return 0
        mock_source = None if args.live else _load_mock_snapshot(root, args.scenario)
        aggregator = build_aggregator(root, args.live, args.scenario)
        snapshot = aggregator.collect(force=True)
        if mock_source is not None:
            snapshot = _restore_mock_display_state(snapshot, mock_source)
        frame = encode_state(snapshot.data, sequence=args.sequence)
        if args.dry_run:
            decoded = decode_frame(frame)
            print(dry_run_summary(snapshot, frame, decoded))
        else:
            sys.stdout.buffer.write(frame)
        return 0
    except Exception as error:
        print("runboard state collection failed: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
