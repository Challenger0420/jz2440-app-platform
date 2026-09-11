"""Emit one RunBoard state as an RB1 frame for the host bridge.

This process owns data collection only.  It never opens a serial port; the
small C# bridge owns COM discovery and transport, which keeps the two failure
domains independent and makes scenario replay safe.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

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
from apps.runboard.shared.board_protocol import encode_state
from apps.runboard.shared.state_model import load_snapshot


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mock_aggregator(root: Path, scenario: str) -> RunBoardAggregator:
    snapshot = load_snapshot(root / "apps" / "runboard" / "shared" / "mocks" / (scenario + ".json"))
    return RunBoardAggregator(
        StaticServerProvider(snapshot),
        MockCodexUsageProvider(snapshot.data["codexUsage"]),
        server_resource_seconds=1,
        codex_usage_seconds=1,
    )


def build_aggregator(root: Path, live: bool, scenario: str | None) -> RunBoardAggregator:
    if not live:
        if scenario not in {"idle", "single", "double", "completed", "error"}:
            raise ValueError("scenario must be idle, single, double, completed or error")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RunBoard host state to RB1")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="read server and accepted Codex provider")
    mode.add_argument("--scenario", choices=("idle", "single", "double", "completed", "error"))
    parser.add_argument("--sequence", type=int, default=0)
    args = parser.parse_args(argv)
    root = _root()
    try:
        aggregator = build_aggregator(root, args.live, args.scenario)
        snapshot = aggregator.collect(force=True)
        sys.stdout.buffer.write(encode_state(snapshot.data, sequence=args.sequence))
        return 0
    except Exception as error:
        print("runboard state collection failed: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
