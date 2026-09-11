import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.runboard.host.aggregator import RunBoardAggregator, StaticServerProvider
from apps.runboard.host.codex_usage_provider import (
    CodexMonitorQuotaProvider,
    CodexUsageResult,
    MockCodexUsageProvider,
    parse_cqm1_frame,
    parse_rate_limits_response,
)
from apps.runboard.host.freshness import FreshnessTracker
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


class FailingCodexProvider:
    def __init__(self, status="unavailable"):
        self.status = status
        self.calls = 0

    def collect(self):
        self.calls += 1
        return CodexUsageResult(None, self.status, error="quota unavailable")


class HostTests(unittest.TestCase):
    def setUp(self):
        self.single = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "single.json")
        self.double = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "double.json")
        self.now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

    def test_codex_parser_matches_codex_monitor_contract(self):
        with open(ROOT / "apps" / "codex-monitor" / "host" / "testdata" / "rate_limits_sample.json", encoding="utf-8") as handle:
            usage = parse_rate_limits_response(json.load(handle), self.now)
        self.assertEqual(usage["fiveHourPercent"], 78)
        self.assertEqual(usage["weekPercent"], 87)
        self.assertEqual(usage["resetCards"], 3)
        self.assertEqual(usage["providerStatus"], "live")

    def test_verified_cqm1_provider_adapter(self):
        frame = "<CQM1|5H=96|5HR=1789148226|W=67|WR=1789448757|RC=3|NOW=1789133007|TZ=480>\n"
        parsed = parse_cqm1_frame(frame)
        self.assertEqual(parsed["fiveHourPercent"], 96)
        self.assertEqual(parsed["weekPercent"], 67)
        self.assertEqual(parsed["resetCards"], 3)

        def fake_runner(*args, **kwargs):
            return type("Completed", (), {"returncode": 0, "stdout": frame, "stderr": ""})()

        result = CodexMonitorQuotaProvider(ROOT, runner=fake_runner).collect()
        self.assertTrue(result.ok)
        self.assertEqual(result.provider_status, "live")
        self.assertEqual(result.usage["weekPercent"], 67)

    def test_server_failure_does_not_remove_codex_usage(self):
        server = SequenceServerProvider([self.single, RuntimeError("SSH temporary failure")])
        aggregator = RunBoardAggregator(server, MockCodexUsageProvider(self.single.data["codexUsage"]), offline_after_failures=3)
        first = aggregator.collect(self.now, force=True)
        second = aggregator.collect(self.now + timedelta(seconds=10), force=True)
        self.assertEqual(len(first.experiments), 1)
        self.assertEqual(len(second.experiments), 1)
        self.assertEqual(second.data["freshness"]["server"]["state"], "stale")
        self.assertEqual(second.data["freshness"]["codexUsage"]["state"], "fresh")
        self.assertEqual(second.data["codexUsage"]["resetCards"], 3)

    def test_fresh_stale_offline_transition_is_configured(self):
        tracker = FreshnessTracker(offline_after_failures=2)
        tracker.success(self.now, "first")
        tracker.failure("one", self.now + timedelta(seconds=1))
        self.assertEqual(tracker.state, "stale")
        tracker.failure("two", self.now + timedelta(seconds=2))
        self.assertEqual(tracker.state, "offline")

    def test_server_fresh_stale_offline_and_recovery_keep_independent_data(self):
        server = SequenceServerProvider([self.single, RuntimeError("temporary"), RuntimeError("temporary"), self.double])
        aggregator = RunBoardAggregator(
            server, MockCodexUsageProvider(self.single.data["codexUsage"]),
            offline_after_failures=2, server_resource_seconds=1,
        )
        fresh = aggregator.collect(self.now, force=True)
        stale = aggregator.collect(self.now + timedelta(seconds=1), force=True)
        offline = aggregator.collect(self.now + timedelta(seconds=2), force=True)
        recovered = aggregator.collect(self.now + timedelta(seconds=3), force=True)
        self.assertEqual(fresh.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(stale.data["freshness"]["server"]["state"], "stale")
        self.assertEqual(len(stale.experiments), 1)
        self.assertEqual(offline.data["freshness"]["server"]["state"], "offline")
        self.assertEqual(offline.data["server"]["status"], "offline")
        self.assertEqual(recovered.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(len(recovered.experiments), 2)
        self.assertEqual(recovered.data["freshness"]["codexUsage"]["state"], "fresh")

    def test_codex_stale_and_recovery_do_not_remove_server_experiments(self):
        class SequenceCodexProvider:
            def __init__(self):
                self.calls = 0

            def collect(self):
                self.calls += 1
                if self.calls in (2, 3):
                    return CodexUsageResult(None, "unavailable", error="quota temporary")
                return CodexUsageResult(self.single_usage, "live")

        codex = SequenceCodexProvider()
        codex.single_usage = dict(self.single.data["codexUsage"])
        aggregator = RunBoardAggregator(
            StaticServerProvider(self.single), codex,
            offline_after_failures=2, codex_usage_seconds=1,
        )
        first = aggregator.collect(self.now, force=True)
        stale = aggregator.collect(self.now + timedelta(seconds=1), force=True)
        offline = aggregator.collect(self.now + timedelta(seconds=2), force=True)
        recovered = aggregator.collect(self.now + timedelta(seconds=3), force=True)
        self.assertEqual(first.data["freshness"]["codexUsage"]["state"], "fresh")
        self.assertEqual(stale.data["freshness"]["codexUsage"]["state"], "stale")
        self.assertEqual(offline.data["freshness"]["codexUsage"]["state"], "offline")
        self.assertEqual(len(offline.experiments), 1)
        self.assertEqual(recovered.data["freshness"]["codexUsage"]["state"], "fresh")

    def test_codex_failure_is_independent_and_terminal_error_is_visible(self):
        aggregator = RunBoardAggregator(
            StaticServerProvider(self.single), FailingCodexProvider("error"), offline_after_failures=3
        )
        state = aggregator.collect(self.now, force=True)
        self.assertEqual(state.data["server"]["status"], "online")
        self.assertEqual(state.data["freshness"]["codexUsage"]["state"], "error")
        self.assertEqual(len(state.experiments), 1)

    def test_zero_one_two_and_multiple_experiments_keep_contract(self):
        idle = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "idle.json")
        for source, mode, count in ((idle, "idle", 0), (self.single, "codex-usage", 1), (self.double, "second-experiment", 2)):
            state = RunBoardAggregator(StaticServerProvider(source), MockCodexUsageProvider(source.data["codexUsage"])).collect(self.now, force=True)
            self.assertEqual(state.layout_mode, mode)
            self.assertEqual(len(state.experiments), count)
        more = dict(self.double.data)
        more["experiments"] = self.double.data["experiments"] + [self.single.data["experiments"][0]]
        state = RunBoardAggregator(StaticServerProvider(validate_snapshot(more)), MockCodexUsageProvider(self.single.data["codexUsage"])).collect(self.now, force=True)
        self.assertEqual(len(state.experiments), 3)
        self.assertEqual(state.layout_mode, "second-experiment")

    def test_sampling_prevents_high_frequency_codex_calls(self):
        codex = FailingCodexProvider("unavailable")
        aggregator = RunBoardAggregator(StaticServerProvider(self.single), codex, codex_usage_seconds=300)
        aggregator.collect(self.now, force=True)
        aggregator.collect(self.now + timedelta(seconds=1))
        self.assertEqual(codex.calls, 1)


if __name__ == "__main__":
    unittest.main()
