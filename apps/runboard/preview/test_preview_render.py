import copy
import unittest
from pathlib import Path

from apps.runboard.preview.runboard_preview import RunBoardPreview
from apps.runboard.shared.state_model import load_snapshot, validate_snapshot


ROOT = Path(__file__).resolve().parents[3]


class FakeRoot:
    def title(self, value):
        self.last_title = value


class FakeCanvas:
    def __init__(self):
        self.texts = []

    def delete(self, *_args):
        self.texts = []

    def create_rectangle(self, *_args, **_kwargs):
        return None

    def create_line(self, *_args, **_kwargs):
        return None

    def create_text(self, *_args, **kwargs):
        self.texts.append(kwargs.get("text"))
        return None


def render(snapshot):
    preview = RunBoardPreview.__new__(RunBoardPreview)
    preview.root = FakeRoot()
    preview.canvas = FakeCanvas()
    preview.is_live = False
    preview.set_snapshot(snapshot, "test", False)
    return preview.canvas.texts


class PreviewRenderTests(unittest.TestCase):
    def setUp(self):
        mocks = ROOT / "apps" / "runboard" / "shared" / "mocks"
        self.snapshots = {name: load_snapshot(mocks / (name + ".json")) for name in ("idle", "single", "double", "completed", "error")}

    def test_idle_single_double_layout_tokens(self):
        idle = render(self.snapshots["idle"])
        single = render(self.snapshots["single"])
        double = render(self.snapshots["double"])
        self.assertIn("NO ACTIVE EXPERIMENT", idle)
        self.assertIn("CODEX USAGE", single)
        self.assertNotIn("SECOND EXPERIMENT", single)
        self.assertIn("CURRENT EXPERIMENT", double)
        self.assertIn("SECOND EXPERIMENT", double)
        self.assertNotIn("CODEX USAGE", double)

    def test_completed_and_error_statuses_render(self):
        self.assertIn("COMPLETED", render(self.snapshots["completed"]))
        self.assertIn("ERROR", render(self.snapshots["error"]))

    def test_stale_and_offline_badges_render_without_erasing_experiment(self):
        data = copy.deepcopy(self.snapshots["single"].data)
        data["freshness"] = {"server": {}, "codexUsage": {"state": "fresh"}}
        data["freshness"]["server"] = {"state": "stale", "consecutiveFailures": 1}
        stale = render(validate_snapshot(data))
        self.assertIn("STALE", stale)
        self.assertIn("CURRENT EXPERIMENT", stale)

        data["server"]["status"] = "offline"
        data["freshness"]["server"] = {"state": "offline", "consecutiveFailures": 3}
        offline = render(validate_snapshot(data))
        self.assertIn("OFFLINE", offline)
        self.assertIn("CURRENT EXPERIMENT", offline)


if __name__ == "__main__":
    unittest.main()
