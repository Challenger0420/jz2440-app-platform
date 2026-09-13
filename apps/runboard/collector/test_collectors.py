import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from apps.runboard.collector.config import RunBoardConfig, SSHConfig
from apps.runboard.collector.experiment_detector import ExperimentDetector, ExperimentMetadata
from apps.runboard.collector.parsers import (
    GPUProcess,
    ProcessInfo,
    parse_cpu_percent,
    parse_nvidia_compute,
    parse_nvidia_gpus,
    parse_ps,
)
from apps.runboard.collector.server_provider import PROBE_COMMAND, SSHServerProvider, _matrix_metadata_command
from apps.runboard.collector.ssh_transport import SSHError, SSHResult, SSHTransport
from apps.runboard.shared.state_model import load_snapshot


ROOT = Path(__file__).resolve().parents[3]


class FakeRunner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.result


def ssh_config():
    return SSHConfig("100.x.x.x", "USER", 22, "C:/placeholder/key", 1, 1)


class CollectorTests(unittest.TestCase):
    def test_transport_success_and_failure_are_separate_from_parsing(self):
        runner = FakeRunner(type("Result", (), {"stdout": "ok", "stderr": "", "returncode": 0})())
        transport = SSHTransport(ssh_config(), runner=runner)
        self.assertEqual(transport.run("cat /proc/loadavg").stdout, "ok")
        self.assertIn("IdentitiesOnly=yes", runner.calls[0][0])
        failing = SSHTransport(ssh_config(), runner=FakeRunner(error=OSError("no ssh")))
        with self.assertRaises(SSHError):
            failing.run("cat /proc/loadavg")

    def test_nvidia_and_cpu_parser_handle_partial_or_bad_output(self):
        self.assertEqual(len(parse_nvidia_gpus("NVIDIA-SMI has failed")), 0)
        self.assertEqual(len(parse_nvidia_compute("bad, output\nN/A, N/A, N/A")), 0)
        cpu = parse_cpu_percent("CPU 1000 600\nCPU 1100 640")
        self.assertAlmostEqual(cpu, 60.0)
        self.assertEqual(parse_nvidia_gpus("GPU-0, 50, 1024, 4096, N/A")[0].temperature_c, None)

    def test_child_processes_are_one_experiment(self):
        processes = {
            100: ProcessInfo(100, 1, "USER-A", 5000, "Ss", "tmux new-session -s exp"),
            101: ProcessInfo(101, 100, "USER-A", 4900, "S", "python train.py --work-dir /data/cells/job_a/clients/round_002/client_0"),
            102: ProcessInfo(102, 101, "USER-A", 40, "R", "python worker"),
        }
        detected = ExperimentDetector().detect(processes, [GPUProcess(101, "python", 2048), GPUProcess(102, "python", 1024)])
        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0]["user"], "USER-A")
        self.assertEqual(detected[0]["currentRound"], 2)
        self.assertTrue(detected[0]["_gpuActive"])
        self.assertIsNone(detected[0]["elapsedSeconds"])
        self.assertEqual(detected[0]["elapsedSource"], "UNKNOWN")

    def test_cell_metadata_start_time_overrides_matrix_runner_elapsed(self):
        processes = {
            100: ProcessInfo(100, 1, "USER-A", 9000, "Ss", "tmux new-session -s exp"),
            101: ProcessInfo(101, 100, "USER-A", 8900, "R", "python train.py --work-dir /data/cells/job_a/clients/round_002/client_0"),
        }
        started = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
        now = started + timedelta(minutes=17, seconds=9)
        metadata = {100: ExperimentMetadata(started_at=started, started_at_source="RELIABLE")}
        detected = ExperimentDetector().detect(processes, [GPUProcess(101, "python", 2048)], metadata, now=now)
        self.assertEqual(detected[0]["elapsedSeconds"], 1029)
        self.assertEqual(detected[0]["elapsedSource"], "RELIABLE")

    def test_cpu_only_experiment_is_detected_without_gpu_pid(self):
        processes = {
            300: ProcessInfo(300, 1, "USER-A", 120, "S", "bash launch.sh"),
            301: ProcessInfo(301, 300, "USER-A", 100, "R", "python train.py --work-dir /data/cells/cpu_job/clients/round_001/client_0"),
        }
        detected = ExperimentDetector().detect(processes, [])
        self.assertEqual(len(detected), 1)
        self.assertFalse(detected[0]["_gpuActive"])
        self.assertEqual(detected[0]["status"], "RUNNING")

    def test_two_users_and_priority_with_more_than_two_jobs(self):
        processes = {}
        gpu = []
        for index, user in enumerate(("USER-A", "USER-B", "USER-C")):
            root = 100 + index * 10
            child = root + 1
            processes[root] = ProcessInfo(root, 1, user, 1000 - index * 10, "S", "tmux job")
            processes[child] = ProcessInfo(child, root, user, 900 - index * 10, "R", "python train.py --work-dir /data/cells/job_{}".format(index))
            gpu.append(GPUProcess(child, "python", 1000 * (3 - index)))
        detected = ExperimentDetector().detect(processes, gpu)
        self.assertEqual(len(detected), 3)
        self.assertEqual([item["user"] for item in detected], ["USER-A", "USER-B", "USER-C"])

    def test_live_provider_failure_returns_offline_snapshot_without_touching_mock(self):
        config = RunBoardConfig("SERVER-01", "SERVER-01", ssh_config())
        provider = SSHServerProvider(ROOT, config)
        provider.transport = SSHTransport(ssh_config(), runner=FakeRunner(error=OSError("offline")))
        snapshot = provider.collect()
        self.assertEqual(snapshot.data["server"]["status"], "offline")
        self.assertEqual(snapshot.data["source"], "live")
        self.assertEqual(load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "single.json").layout_mode, "codex-usage")

    def test_live_probe_has_no_mutating_commands(self):
        self.assertNotRegex(PROBE_COMMAND, r"\b(kill|pkill|rm|mv|chmod|systemctl|tee|touch)\b")
        self.assertNotIn(">", PROBE_COMMAND.replace("2>&1", ""))

    def test_matrix_metadata_probe_is_read_only_and_uses_explicit_files(self):
        command = _matrix_metadata_command({123: "/private/formal/cells/cell_008"})
        self.assertIn("formal_matrix_plan.json", command)
        self.assertIn("completion_manifest.json", command)
        self.assertIn("__RUNBOARD_MATRIX_PLAN__", command)
        self.assertNotRegex(command, r"\b(kill|pkill|mv|chmod|systemctl|tee|touch)\b")

    def test_parse_ps_preserves_command_line(self):
        parsed = parse_ps(" 101 100 USER-A 12 R python train.py --work-dir /data/job")
        self.assertEqual(parsed[101].command, "python train.py --work-dir /data/job")


if __name__ == "__main__":
    unittest.main()
