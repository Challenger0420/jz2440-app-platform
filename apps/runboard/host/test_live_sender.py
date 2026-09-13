import copy
import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.runboard.host.aggregator import RunBoardAggregator, StaticServerProvider
from apps.runboard.host.codex_usage_provider import CodexUsageResult, MockCodexUsageProvider
from apps.runboard.host.live_sender import PersistentLiveSender
from apps.runboard.host.runboard_state_cli import dry_run_summary
from apps.runboard.shared.board_protocol import FrameDecoder, decode_frame
from apps.runboard.shared.state_model import load_snapshot, validate_snapshot


ROOT = Path(__file__).resolve().parents[3]


class SequenceServerProvider:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def collect(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value


class SequenceCodexProvider:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def collect(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        if isinstance(value, Exception):
            return CodexUsageResult(None, "unavailable", error=str(value))
        return CodexUsageResult(dict(value), "live", "2026-09-13T12:00:00+00:00")


def with_cpu(source, value):
    data = copy.deepcopy(source.data)
    data["server"]["cpuPercent"] = value
    return validate_snapshot(data)


def frame_field(frame, name):
    payload = frame.rstrip(b"\n").split(b"|")
    for item in payload:
        if item.startswith(name.encode("ascii") + b"="):
            return item.split(b"=", 1)[1].decode("ascii")
    raise AssertionError("missing frame field {}".format(name))


class PersistentLiveSenderTests(unittest.TestCase):
    def setUp(self):
        source = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "matrix_single.json")
        self.cpu10 = with_cpu(source, 10)
        self.cpu20 = with_cpu(source, 20)
        self.usage = dict(source.data["codexUsage"])
        self.t0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

    def test_server_failure_timeline_keeps_last_good_state_and_age(self):
        server = SequenceServerProvider([
            self.cpu10,
            RuntimeError("temporary server failure"),
            RuntimeError("temporary server failure"),
            RuntimeError("temporary server failure"),
            self.cpu20,
        ])
        aggregator = RunBoardAggregator(
            server, MockCodexUsageProvider(self.usage), offline_after_failures=3,
            server_resource_seconds=1, codex_usage_seconds=1,
        )
        sender = PersistentLiveSender(aggregator)
        states = []
        frames = []
        for sequence, seconds in enumerate((0, 5, 15, 30, 35)):
            state, frame = sender.collect_frame(sequence, now=self.t0 + timedelta(seconds=seconds))
            states.append(state.data)
            frames.append(frame)

        self.assertEqual(server.calls, 5)
        self.assertEqual(states[0]["server"]["cpuPercent"], 10)
        self.assertEqual(states[1]["server"]["cpuPercent"], 10)
        self.assertEqual(states[2]["server"]["cpuPercent"], 10)
        self.assertEqual(states[3]["server"]["cpuPercent"], 10)
        self.assertEqual(states[3]["freshness"]["server"]["state"], "offline")
        self.assertEqual(states[4]["server"]["cpuPercent"], 20)
        self.assertEqual(states[4]["freshness"]["server"]["state"], "fresh")
        self.assertEqual([frame_field(frame, "UA") for frame in frames], ["0", "5", "15", "30", "0"])
        self.assertEqual([frame_field(frame, "SEQ") for frame in frames], ["0", "1", "2", "3", "4"])
        self.assertEqual(states[1]["job"]["matrixCompleted"], 7)
        self.assertEqual(states[3]["experiments"][0]["currentRound"], 37)

    def test_provider_failures_are_independent_in_both_directions(self):
        server = SequenceServerProvider([self.cpu10, RuntimeError("server temporary")])
        codex = SequenceCodexProvider([self.usage, RuntimeError("codex temporary")])
        sender = PersistentLiveSender(RunBoardAggregator(
            server, codex, offline_after_failures=3, server_resource_seconds=1,
            codex_usage_seconds=1,
        ))
        first, _ = sender.collect_frame(0, now=self.t0)
        second, frame = sender.collect_frame(1, now=self.t0 + timedelta(seconds=5))
        self.assertEqual(first.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(second.data["freshness"]["server"]["state"], "stale")
        self.assertEqual(second.data["freshness"]["codexUsage"]["state"], "stale")
        self.assertEqual(second.data["server"]["cpuPercent"], 10)
        self.assertEqual(second.data["experiments"][0]["user"], "USER-A")
        self.assertEqual(frame_field(frame, "UA"), "5")

        server_ok = SequenceServerProvider([self.cpu10, self.cpu20])
        codex_fail = SequenceCodexProvider([self.usage, RuntimeError("codex temporary")])
        reverse = PersistentLiveSender(RunBoardAggregator(
            server_ok, codex_fail, offline_after_failures=3, server_resource_seconds=1,
            codex_usage_seconds=1,
        ))
        reverse.collect_frame(0, now=self.t0)
        state, _ = reverse.collect_frame(1, now=self.t0 + timedelta(seconds=5))
        self.assertEqual(state.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(state.data["freshness"]["codexUsage"]["state"], "stale")
        self.assertEqual(state.data["server"]["cpuPercent"], 20)

    def test_request_worker_emits_complete_frames_from_one_sender(self):
        server = SequenceServerProvider([self.cpu10])
        sender = PersistentLiveSender(RunBoardAggregator(
            server, MockCodexUsageProvider(self.usage), server_resource_seconds=1,
            codex_usage_seconds=1,
        ))
        output = io.BytesIO()
        self.assertEqual(sender.run_requests(["0\n", "1\n"], output), 2)
        decoded = FrameDecoder().feed(output.getvalue())
        self.assertEqual([item["sequence"] for item in decoded], [0, 1])
        self.assertEqual(server.calls, 1)

    def test_persistent_dry_run_has_no_serial_and_reuses_aggregator(self):
        server = SequenceServerProvider([self.cpu10])
        sender = PersistentLiveSender(RunBoardAggregator(
            server, MockCodexUsageProvider(self.usage), server_resource_seconds=600,
            experiment_seconds=600, codex_usage_seconds=60,
        ))
        clock_values = iter(self.t0 + timedelta(seconds=value) for value in (0, 60, 600))
        output = io.StringIO()
        cycles = sender.run_dry_run(
            3, 0, output, dry_run_summary, clock=lambda: next(clock_values),
        )
        self.assertEqual(cycles, 3)
        self.assertEqual(server.calls, 2)
        self.assertEqual(output.getvalue().count("SERIAL=NO"), 3)
        self.assertNotIn("RB1|", output.getvalue())

    def test_cadence_timeline_publishes_every_minute_but_collects_server_twice(self):
        class CountingCodexProvider:
            def __init__(self, usage):
                self.usage = dict(usage)
                self.calls = 0

            def collect(self):
                self.calls += 1
                return CodexUsageResult(self.usage, "live", "quota")

        server = SequenceServerProvider([self.cpu10, self.cpu20])
        codex = CountingCodexProvider(self.usage)
        sender = PersistentLiveSender(RunBoardAggregator(
            server, codex, server_resource_seconds=600,
            experiment_seconds=600, codex_usage_seconds=60,
        ))
        values = (0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600, 660)
        clock_values = iter(self.t0 + timedelta(seconds=value) for value in values)
        output = io.StringIO()
        cycles = sender.run_dry_run(
            len(values), 0, output, dry_run_summary, clock=lambda: next(clock_values),
        )

        self.assertEqual(cycles, 12)
        self.assertEqual(server.calls, 2)
        self.assertEqual(codex.calls, 12)
        self.assertEqual(output.getvalue().count("PERSISTENT_CYCLE="), 12)
        self.assertEqual(output.getvalue().count("SERIAL=NO"), 12)


if __name__ == "__main__":
    unittest.main()
