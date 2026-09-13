import unittest
from pathlib import Path

from apps.runboard.shared.state_model import load_snapshot, validate_snapshot


MOCKS = Path(__file__).resolve().parent / "mocks"


class StateModelTests(unittest.TestCase):
    def test_required_layout_modes(self):
        self.assertEqual(load_snapshot(MOCKS / "idle.json").layout_mode, "idle")
        self.assertEqual(load_snapshot(MOCKS / "single.json").layout_mode, "codex-usage")
        self.assertEqual(load_snapshot(MOCKS / "double.json").layout_mode, "second-experiment")
        self.assertEqual(load_snapshot(MOCKS / "matrix_single.json").layout_mode, "matrix")

    def test_matrix_job_keeps_cell_round_progress_separate(self):
        snapshot = load_snapshot(MOCKS / "matrix_single.json")
        self.assertEqual(snapshot.data["job"]["matrixCompleted"], 7)
        self.assertEqual(snapshot.data["job"]["matrixTotal"], 45)
        self.assertEqual(snapshot.data["job"]["currentCell"], 8)
        self.assertEqual(snapshot.data["experiments"][0]["currentRound"], 37)
        self.assertEqual(snapshot.data["experiments"][0]["progressScope"], "current-cell-round")

    def test_extra_statuses_are_valid(self):
        self.assertEqual(load_snapshot(MOCKS / "completed.json").experiments[0]["status"], "COMPLETED")
        self.assertEqual(load_snapshot(MOCKS / "error.json").experiments[0]["status"], "ERROR")

    def test_double_scenario_keeps_usage_data_but_changes_layout(self):
        snapshot = load_snapshot(MOCKS / "double.json")
        self.assertEqual(len(snapshot.experiments), 2)
        self.assertEqual(snapshot.data["codexUsage"]["resetCards"], 3)
        self.assertEqual(snapshot.layout_mode, "second-experiment")

    def test_partial_fields_and_more_than_two_experiments_are_allowed(self):
        base = load_snapshot(MOCKS / "single.json").data
        partial = dict(base["experiments"][0])
        partial.update({"progress": None, "currentRound": None, "totalRound": None,
                        "dataset": None, "seed": None, "metricName": None, "metricValue": None})
        data = dict(base)
        data["experiments"] = [base["experiments"][0], partial, dict(partial)]
        snapshot = validate_snapshot(data)
        self.assertEqual(len(snapshot.experiments), 3)
        self.assertEqual(snapshot.layout_mode, "second-experiment")


if __name__ == "__main__":
    unittest.main()
