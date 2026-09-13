import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.runboard.host.aggregator import RunBoardAggregator, StaticServerProvider
from apps.runboard.host.runboard_state_cli import _restore_mock_display_state, dry_run_summary
from apps.runboard.collector.config import RunBoardConfig, SSHConfig
from apps.runboard.host.codex_usage_provider import (
    CodexMonitorQuotaProvider,
    CodexUsageResult,
    MockCodexUsageProvider,
    parse_cqm1_frame,
    parse_rate_limits_response,
)
from apps.runboard.host.freshness import FreshnessTracker
from apps.runboard.shared.state_model import load_snapshot, validate_snapshot
from apps.runboard.shared.board_protocol import decode_frame, encode_state


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
        self.matrix = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "matrix_single.json")
        self.now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

    def test_default_live_sampling_matches_host_schedule(self):
        config = RunBoardConfig(
            "SERVER-01", "SERVER-01", SSHConfig("100.x.x.x", "USER", 22, "identity")
        )
        self.assertEqual(config.server_resource_seconds, 600)
        self.assertEqual(config.experiment_seconds, 600)
        self.assertEqual(config.codex_usage_seconds, 60)

    def test_codex_parser_matches_codex_monitor_contract(self):
        with open(ROOT / "apps" / "codex-monitor" / "host" / "testdata" / "rate_limits_sample.json", encoding="utf-8") as handle:
            usage = parse_rate_limits_response(json.load(handle), self.now)
        self.assertEqual(usage["fiveHourPercent"], 78)
        self.assertEqual(usage["weekPercent"], 87)
        self.assertEqual(usage["resetCards"], 3)
        self.assertEqual(usage["providerStatus"], "live")
        self.assertRegex(usage["fiveHourReset"], r"^(\d{2}:\d{2}|\d+/\d+ \d{2}:\d{2})$")
        self.assertRegex(usage["weekReset"], r"^(\d{2}:\d{2}|\d+/\d+ \d{2}:\d{2})$")

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
        self.assertRegex(result.usage["fiveHourReset"], r"^(\d{2}:\d{2}|\d+/\d+ \d{2}:\d{2})$")

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

    def test_minute_publish_cadence_polls_server_only_at_start_and_ten_minutes(self):
        class CountingCodexProvider:
            def __init__(self, usage):
                self.usage = dict(usage)
                self.calls = 0

            def collect(self):
                self.calls += 1
                return CodexUsageResult(self.usage, "live", "quota")

        server = SequenceServerProvider([self.matrix, self.matrix])
        codex = CountingCodexProvider(self.matrix.data["codexUsage"])
        aggregator = RunBoardAggregator(
            server, codex, offline_after_failures=3,
            server_resource_seconds=600, experiment_seconds=600,
            codex_usage_seconds=60,
        )
        states = [aggregator.collect(self.now + timedelta(seconds=seconds))
                  for seconds in (0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600, 660)]

        self.assertEqual(server.calls, 2)
        self.assertEqual(codex.calls, 12)
        self.assertEqual(states[9].data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(states[9].data["freshness"]["server"]["ageSeconds"], 540)
        self.assertEqual(states[10].data["freshness"]["server"]["ageSeconds"], 0)
        self.assertTrue(all(len(state.data["experiments"]) == 1 for state in states))

    def test_server_cache_remains_fresh_until_scheduled_poll_failure(self):
        server = SequenceServerProvider([self.matrix, RuntimeError("scheduled failure"), self.matrix])
        aggregator = RunBoardAggregator(
            server, MockCodexUsageProvider(self.matrix.data["codexUsage"]),
            offline_after_failures=3, server_resource_seconds=600,
            experiment_seconds=600, codex_usage_seconds=60,
        )
        fresh = aggregator.collect(self.now)
        cached = aggregator.collect(self.now + timedelta(seconds=599))
        stale = aggregator.collect(self.now + timedelta(seconds=600))
        still_stale = aggregator.collect(self.now + timedelta(seconds=660))
        recovered = aggregator.collect(self.now + timedelta(seconds=1200))

        self.assertEqual(server.calls, 3)
        self.assertEqual(fresh.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(cached.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(cached.data["freshness"]["server"]["ageSeconds"], 599)
        self.assertEqual(stale.data["freshness"]["server"]["state"], "stale")
        self.assertEqual(stale.data["freshness"]["server"]["ageSeconds"], 600)
        self.assertEqual(still_stale.data["freshness"]["server"]["state"], "stale")
        self.assertEqual(len(still_stale.data["experiments"]), 1)
        self.assertEqual(recovered.data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(recovered.data["freshness"]["server"]["ageSeconds"], 0)

    def test_server_due_time_does_not_drift_after_a_late_publish_tick(self):
        server = SequenceServerProvider([self.matrix, self.matrix, self.matrix])
        aggregator = RunBoardAggregator(
            server, MockCodexUsageProvider(self.matrix.data["codexUsage"]),
            server_resource_seconds=600, experiment_seconds=600,
            codex_usage_seconds=60,
        )
        aggregator.collect(self.now)
        aggregator.collect(self.now + timedelta(seconds=650))
        aggregator.collect(self.now + timedelta(seconds=1200))
        self.assertEqual(server.calls, 3)

    def test_fresh_stale_offline_transition_is_configured(self):
        tracker = FreshnessTracker(offline_after_failures=2)
        tracker.success(self.now, "first")
        tracker.failure("one", self.now + timedelta(seconds=1))
        self.assertEqual(tracker.state, "stale")
        tracker.failure("two", self.now + timedelta(seconds=2))
        self.assertEqual(tracker.state, "offline")

    def test_last_success_age_grows_during_failures_and_resets_on_recovery(self):
        tracker = FreshnessTracker(offline_after_failures=2)
        tracker.success(self.now, "snapshot-at-t0")
        self.assertEqual(tracker.as_dict(self.now)["ageSeconds"], 0)

        tracker.failure("temporary", self.now + timedelta(seconds=5))
        stale = tracker.as_dict(self.now + timedelta(seconds=5))
        self.assertEqual(stale["state"], "stale")
        self.assertEqual(stale["ageSeconds"], 5)

        tracker.failure("still temporary", self.now + timedelta(seconds=30))
        offline = tracker.as_dict(self.now + timedelta(seconds=30))
        self.assertEqual(offline["state"], "offline")
        self.assertEqual(offline["ageSeconds"], 30)

        tracker.success(self.now + timedelta(seconds=31), "snapshot-at-recovery")
        recovered = tracker.as_dict(self.now + timedelta(seconds=31))
        self.assertEqual(recovered["state"], "fresh")
        self.assertEqual(recovered["ageSeconds"], 0)

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

    def test_unknown_codex_usage_stays_null_through_state_and_protocol(self):
        aggregator = RunBoardAggregator(
            StaticServerProvider(self.single), FailingCodexProvider("unavailable"), offline_after_failures=3
        )
        state = aggregator.collect(self.now, force=True)
        self.assertIsNone(state.data["codexUsage"]["fiveHourPercent"])
        self.assertIsNone(state.data["codexUsage"]["weekPercent"])
        decoded = decode_frame(encode_state(state.data))
        self.assertIsNone(decoded["codexUsage"]["fiveHourPercent"])
        self.assertIsNone(decoded["codexUsage"]["weekPercent"])

    def test_dry_run_summary_is_sanitized_and_reports_protocol_gate(self):
        frame = encode_state(self.matrix.data, sequence=4)
        decoded = decode_frame(frame)
        summary = dry_run_summary(self.matrix, frame, decoded)
        self.assertIn("RUNBOARD_DRY_RUN=PASS", summary)
        self.assertIn("SERIAL=NO", summary)
        self.assertIn("EXPERIMENT_USER=USER-0", summary)
        self.assertNotIn("USER-A", summary)
        self.assertIn("FRAME_TRAILING_NEWLINE=PASS", summary)
        self.assertIn("FRAME_CRC=PASS", summary)

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

    def test_matrix_context_survives_aggregator_and_server_failure(self):
        server = SequenceServerProvider([self.matrix, RuntimeError("temporary")])
        aggregator = RunBoardAggregator(
            server, MockCodexUsageProvider(self.matrix.data["codexUsage"]),
            offline_after_failures=3, server_resource_seconds=1,
        )
        fresh = aggregator.collect(self.now, force=True)
        stale = aggregator.collect(self.now + timedelta(seconds=1), force=True)
        self.assertEqual(fresh.layout_mode, "matrix")
        self.assertEqual(fresh.data["job"]["matrixCompleted"], 7)
        self.assertEqual(stale.data["job"]["matrixTotal"], 45)
        self.assertEqual(stale.data["freshness"]["server"]["state"], "stale")

    def test_sampling_prevents_high_frequency_codex_calls(self):
        codex = FailingCodexProvider("unavailable")
        aggregator = RunBoardAggregator(StaticServerProvider(self.single), codex, codex_usage_seconds=60)
        aggregator.collect(self.now, force=True)
        aggregator.collect(self.now + timedelta(seconds=1))
        self.assertEqual(codex.calls, 1)

    def test_acceptance_mock_preserves_explicit_freshness(self):
        stale = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "stale.json")
        offline = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "offline.json")
        stale_after_last_good = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "stale_after_last_good.json")
        for source in (stale, stale_after_last_good, offline):
            collected = RunBoardAggregator(
                StaticServerProvider(source), MockCodexUsageProvider(source.data["codexUsage"])
            ).collect(self.now, force=True)
            restored = _restore_mock_display_state(collected, source)
            self.assertEqual(
                restored.data["freshness"]["server"]["state"],
                source.data["freshness"]["server"]["state"],
            )
            self.assertEqual(restored.data["server"]["status"], source.data["server"]["status"])

    def test_stale_after_last_good_fixture_keeps_cached_matrix_state(self):
        source = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "stale_after_last_good.json")
        self.assertEqual(source.data["freshness"]["server"]["state"], "stale")
        self.assertEqual(source.data["server"]["cpuPercent"], 42)
        self.assertEqual(source.data["job"]["matrixCompleted"], 12)
        self.assertEqual(source.data["job"]["currentCell"], 13)
        self.assertEqual(source.data["experiments"][0]["currentRound"], 3)


if __name__ == "__main__":
    unittest.main()
