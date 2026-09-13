import copy
import unittest
from pathlib import Path

from apps.runboard.preview.runboard_preview import COLORS, RunBoardPreview, fmt_clock, fmt_updated, quota_color
from apps.runboard.shared.state_model import load_snapshot, validate_snapshot


ROOT = Path(__file__).resolve().parents[3]


class FakeRoot:
    def title(self, value):
        self.last_title = value


class FakeCanvas:
    def __init__(self):
        self.texts = []
        self.text_calls = []
        self.rectangle_calls = []

    def delete(self, *_args):
        self.texts = []

    def create_rectangle(self, *_args, **_kwargs):
        self.rectangle_calls.append((_args, _kwargs))
        return None

    def create_line(self, *_args, **_kwargs):
        return None

    def create_text(self, *_args, **kwargs):
        self.texts.append(kwargs.get("text"))
        self.text_calls.append((_args, kwargs))
        return None


def render(snapshot):
    return render_canvas(snapshot).texts


def render_canvas(snapshot):
    preview = RunBoardPreview.__new__(RunBoardPreview)
    preview.root = FakeRoot()
    preview.canvas = FakeCanvas()
    preview.is_live = False
    preview.set_snapshot(snapshot, "test", False)
    return preview.canvas


class PreviewRenderTests(unittest.TestCase):
    def setUp(self):
        mocks = ROOT / "apps" / "runboard" / "shared" / "mocks"
        self.snapshots = {name: load_snapshot(mocks / (name + ".json")) for name in ("idle", "single", "double", "matrix_single", "completed", "error", "degraded", "stale", "stale_after_last_good", "offline", "offline_after_last_good", "offline_cold_start", "longtext")}

    def test_idle_single_double_layout_tokens(self):
        idle = render(self.snapshots["idle"])
        single = render(self.snapshots["single"])
        double = render(self.snapshots["double"])
        self.assertIn("NO ACTIVE EXPERIMENT", idle)
        self.assertNotIn("CODEX USAGE", single)
        self.assertNotIn("SECOND EXPERIMENT", single)
        self.assertIn("CURRENT EXPERIMENT", double)
        self.assertIn("SECOND EXPERIMENT", double)
        self.assertNotIn("CODEX USAGE", double)

    def test_idle_reuses_final_header_and_quota_footer(self):
        canvas = render_canvas(self.snapshots["idle"])
        texts = [str(text) for text in canvas.texts]
        self.assertIn("NO ACTIVE EXPERIMENT", texts)
        self.assertTrue(any(text.startswith("CPU 8% RAM 20% GPU 0% 0.4/16G") for text in texts))
        self.assertIn("92%", texts)
        self.assertIn("76%", texts)
        self.assertIn("15:27", texts)
        self.assertIn("9/19 16:09", texts)
        self.assertTrue(any(text.startswith("UPDATED ") for text in texts))
        for legacy in ("CODEX", "5H", "WEEK", "RESET", "RC", "LOAD", "MEM"):
            self.assertNotIn(legacy, texts)
        resource_call = next(
            (args, kwargs) for args, kwargs in canvas.text_calls
            if str(kwargs.get("text")).startswith("CPU ")
        )
        self.assertEqual(resource_call[0][:2], (468 * 2, 28 * 2))
        self.assertEqual(resource_call[1]["anchor"], "ne")

    def test_double_uses_final_header_and_hides_single_experiment_footer_fields(self):
        canvas = render_canvas(self.snapshots["double"])
        texts = [str(text) for text in canvas.texts]
        self.assertTrue(any(text.startswith("CPU 91% RAM 89% GPU 98% 15.2/16G") for text in texts))
        self.assertNotIn("METRIC", texts)
        self.assertNotIn("VRAM", texts)
        self.assertFalse(any(text.startswith("UPDATED ") for text in texts))

    def test_double_second_experiment_has_three_non_overlapping_information_rows(self):
        canvas = render_canvas(self.snapshots["double"])
        texts = [str(text) for text in canvas.texts]
        self.assertIn("SECOND EXPERIMENT", texts)
        self.assertIn("fedprox-cifar100", texts)
        self.assertIn("PAUSED", texts)
        self.assertIn("USER-B", texts)
        self.assertIn("PROGRESS 31%", texts)
        self.assertIn("ROUND 31/100", texts)
        self.assertNotIn("ELAPSED", " ".join(texts))
        self.assertNotIn("CIFAR-100", texts)
        self.assertNotIn("VRAM", texts)

    def test_matrix_layout_separates_job_and_cell_round(self):
        matrix = render(self.snapshots["matrix_single"])
        self.assertIn("Prompt Horizontal Formal V6", matrix)
        self.assertIn("MATRIX 7/45", matrix)
        self.assertIn("CELL 8/45", matrix)
        self.assertIn("ROUND 37/50", matrix)
        self.assertTrue(any("GPU 17% 9.8/16G" in str(text) for text in matrix))
        self.assertIn("11%", matrix)
        self.assertIn("35%", matrix)
        self.assertIn("21:09", matrix)
        self.assertIn("9/12 11:20", matrix)
        self.assertNotIn("METRIC", matrix)
        self.assertNotIn("RC", matrix)
        self.assertNotIn("CODEX USAGE", matrix)

    def test_matrix_layout_has_two_round_and_matrix_bars(self):
        canvas = render_canvas(self.snapshots["matrix_single"])
        fills = [kwargs.get("fill") for args, kwargs in canvas.rectangle_calls]
        self.assertGreaterEqual(sum(fill in (COLORS["cyan"], COLORS["green"], COLORS["yellow"], COLORS["red"])
                                    for fill in fills), 4)
        self.assertEqual(sum(fill == COLORS["bar_border"] for fill in fills), 4)
        self.assertEqual(sum(fill == COLORS["track"] for fill in fills), 4)
        self.assertTrue(any(args[3] - args[1] == 24 for args, kwargs in canvas.rectangle_calls
                            if kwargs.get("fill") == COLORS["bar_border"]))
        self.assertTrue(any(args[3] - args[1] == 20 for args, kwargs in canvas.rectangle_calls
                            if kwargs.get("fill") == COLORS["track"]))
        self.assertTrue(any(args[3] - args[1] == 16 for args, kwargs in canvas.rectangle_calls
                            if kwargs.get("fill") in (COLORS["cyan"], COLORS["green"])))

    def test_matrix_header_places_server_and_resources_on_one_row(self):
        canvas = render_canvas(self.snapshots["matrix_single"])
        calls = {kwargs.get("text"): args for args, kwargs in canvas.text_calls}
        self.assertEqual(calls["SERVER-01"][1], 28 * 2)
        resources = next(text for text in canvas.texts if str(text).startswith("CPU "))
        resource_args = next(args for args, kwargs in canvas.text_calls if kwargs.get("text") == resources)
        resource_kwargs = next(kwargs for args, kwargs in canvas.text_calls if kwargs.get("text") == resources)
        self.assertEqual(resource_args[0], 468 * 2)
        self.assertEqual(resource_args[1], 28 * 2)
        self.assertEqual(resource_kwargs["anchor"], "ne")
        self.assertIn("GPU ", resources)
        self.assertNotIn("VRAM ", resources)
        self.assertNotIn("VRAM", [text for text in canvas.texts if text == "VRAM"])
        self.assertFalse(any(
            kwargs.get("text", "").startswith("VRAM ") and args[1] != 28 * 2
            for args, kwargs in canvas.text_calls
        ))

    def test_matrix_header_keeps_compact_gpu_memory_inside_resource_line(self):
        canvas = render_canvas(self.snapshots["matrix_single"])
        resources = next(text for text in canvas.texts if str(text).startswith("CPU "))
        self.assertEqual(resources, "CPU 6% RAM 12% GPU 17% 9.8/16G")
        self.assertLessEqual(len(resources), 44)

    def test_matrix_quota_colors_match_percentage_and_bar(self):
        self.assertEqual(quota_color(75), "green")
        self.assertEqual(quota_color(50), "green")
        self.assertEqual(quota_color(49), "yellow")
        self.assertEqual(quota_color(20), "yellow")
        self.assertEqual(quota_color(19), "red")
        self.assertEqual(quota_color(0), "red")
        canvas = render_canvas(self.snapshots["matrix_single"])
        calls = {kwargs.get("text"): kwargs for args, kwargs in canvas.text_calls}
        self.assertEqual(calls["11%"]["fill"], COLORS["red"])
        self.assertEqual(calls["35%"]["fill"], COLORS["yellow"])
        self.assertEqual(calls["21:09"]["fill"], COLORS["muted"])
        self.assertEqual(calls["9/12 11:20"]["fill"], COLORS["muted"])
        colored_bars = [kwargs.get("fill") for args, kwargs in canvas.rectangle_calls]
        self.assertIn(COLORS["red"], colored_bars)
        self.assertIn(COLORS["yellow"], colored_bars)

    def test_host_clock_and_relative_updated_label_are_rendered(self):
        canvas = render_canvas(self.snapshots["matrix_single"])
        matrix = canvas.texts
        self.assertEqual(fmt_clock(self.snapshots["matrix_single"].data["updatedAt"]), "12:00")
        self.assertTrue(any(text == "12:00" for text in matrix))
        self.assertTrue(any(str(text).startswith("UPDATED ") for text in matrix))
        self.assertLessEqual(len(fmt_updated(self.snapshots["matrix_single"].data)), 16)
        calls = {kwargs.get("text"): (args, kwargs) for args, kwargs in canvas.text_calls}
        self.assertEqual(calls["12:00"][0][:2], (394 * 2, 14 * 2))
        status_x, status_y = calls["ONLINE"][0][:2]
        self.assertGreaterEqual(status_x, 405 * 2)
        self.assertLessEqual(status_x, 466 * 2)
        self.assertEqual(status_y, 15 * 2)
        updated_args, updated_kwargs = next(
            (args, kwargs) for args, kwargs in canvas.text_calls
            if str(kwargs.get("text")).startswith("UPDATED ")
        )
        self.assertEqual(updated_args[:2], (466 * 2, 250 * 2))
        self.assertEqual(updated_kwargs["anchor"], "ne")

    def test_updated_uses_now_for_a_recent_success(self):
        data = copy.deepcopy(self.snapshots["matrix_single"].data)
        data["freshness"] = {
            "server": {"state": "fresh", "ageSeconds": 0},
            "codexUsage": {"state": "fresh", "ageSeconds": 0},
        }
        self.assertEqual(fmt_updated(data), "UPDATED NOW")

        data["freshness"]["server"]["ageSeconds"] = 3
        self.assertEqual(fmt_updated(data), "UPDATED 3S AGO")

    def test_completed_and_error_statuses_render(self):
        for name, status in (("single", "RUNNING"), ("completed", "COMPLETED"), ("error", "ERROR")):
            texts = [str(text) for text in render(self.snapshots[name])]
            self.assertIn(status, texts)
            self.assertTrue(any(text.startswith("CPU ") and "GPU " in text for text in texts))
            self.assertNotIn("METRIC", texts)
            self.assertNotIn("VRAM", texts)
            for legacy in ("CODEX", "5H", "WEEK", "RESET", "RC"):
                self.assertNotIn(legacy, texts)
            self.assertTrue(any(text.startswith("UPDATED ") for text in texts))

    def test_completed_and_error_share_final_single_footer_and_absolute_resets(self):
        for name in ("completed", "error"):
            texts = [str(text) for text in render(self.snapshots[name])]
            self.assertIn("15:27", texts)
            self.assertIn("9/19 16:09", texts)
            self.assertTrue(any(("100%" if name == "completed" else "43%") in text for text in texts))

    def test_error_status_does_not_degrade_server_health(self):
        snapshot = self.snapshots["error"]
        data = snapshot.data
        self.assertEqual(data["server"]["status"], "online")
        self.assertEqual(data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(data["experiments"][0]["status"], "ERROR")

        texts = [str(text) for text in render(snapshot)]
        self.assertIn("ONLINE", texts)
        self.assertIn("ERROR", texts)
        self.assertTrue(any(text.startswith("CPU 18% RAM 32% GPU 4% 1.1/16G") for text in texts))
        self.assertNotIn("DEGRADED", texts)

    def test_degraded_server_keeps_known_resources_and_uses_distinct_status(self):
        snapshot = self.snapshots["degraded"]
        data = snapshot.data
        self.assertEqual(data["server"]["status"], "degraded")
        self.assertEqual(data["server"]["cpuPercent"], 18)
        self.assertEqual(data["server"]["ram"]["usedPercent"], 32)
        self.assertIsNone(data["server"]["gpu"]["utilizationPercent"])
        self.assertEqual(data["freshness"]["server"]["state"], "fresh")
        self.assertEqual(data["experiments"], [])

        texts = [str(text) for text in render(snapshot)]
        self.assertIn("DEGRADED", texts)
        self.assertTrue(any(text.startswith("CPU 18% RAM 32% GPU --% --/--G") for text in texts))
        self.assertIn("NO ACTIVE EXPERIMENT", texts)
        self.assertNotIn("SERVER OFFLINE", texts)
        self.assertNotIn("UPDATED --", texts)

    def test_stale_retains_experiment_but_offline_clears_server_content(self):
        data = copy.deepcopy(self.snapshots["single"].data)
        data["freshness"] = {"server": {}, "codexUsage": {"state": "fresh"}}
        data["freshness"]["server"] = {"state": "stale", "consecutiveFailures": 1}
        stale = render(validate_snapshot(data))
        self.assertTrue(any("SERVER STALE" in str(text) for text in stale))
        self.assertIn("CURRENT EXPERIMENT", stale)

        data["server"]["status"] = "offline"
        data["freshness"]["server"] = {"state": "offline", "consecutiveFailures": 3}
        offline = render(validate_snapshot(data))
        self.assertIn("SERVER OFFLINE", offline)
        self.assertIn("NO LIVE SERVER DATA", offline)
        self.assertNotIn("CURRENT EXPERIMENT", offline)
        self.assertNotIn("NO ACTIVE EXPERIMENT", offline)
        self.assertIn("CPU --% RAM --% GPU --% --/--G", offline)
        self.assertEqual(offline.count("SERVER OFFLINE"), 1)

    def test_stale_fixture_uses_absolute_codex_reset_display(self):
        stale = render(self.snapshots["stale"])
        self.assertIn("15:27", stale)
        self.assertIn("9/19 16:09", stale)
        self.assertNotIn("01h 48m", stale)
        self.assertNotIn("2d 19h", stale)

    def test_stale_after_last_good_retains_server_and_matrix_content(self):
        stale = self.snapshots["stale_after_last_good"]
        rendered = render(stale)
        self.assertIn("STALE", rendered)
        self.assertTrue(any(text.startswith("CPU 42% RAM 58% GPU 73% 6.4/16G") for text in rendered))
        self.assertIn("Prompt Horizontal Formal V6", rendered)
        self.assertIn("MATRIX 12/45", rendered)
        self.assertIn("CELL 13/45", rendered)
        self.assertIn("ROUND 3/50", rendered)
        self.assertIn("UPDATED 18S AGO", rendered)
        self.assertIn("92%", rendered)
        self.assertIn("76%", rendered)
        self.assertNotIn("SERVER OFFLINE", rendered)

    def test_offline_fixture_keeps_codex_fresh_and_uses_final_footer(self):
        offline = render(self.snapshots["offline"])
        self.assertIn("SERVER OFFLINE", offline)
        self.assertIn("NO LIVE SERVER DATA", offline)
        self.assertIn("82%", offline)
        self.assertIn("64%", offline)
        self.assertIn("15:27", offline)
        self.assertIn("9/19 16:09", offline)
        self.assertTrue(any(str(text).startswith("UPDATED ") for text in offline))

    def test_offline_updated_age_uses_server_age_not_fresh_codex_age(self):
        data = copy.deepcopy(self.snapshots["offline_after_last_good"].data)
        self.assertEqual(fmt_updated(data), "UPDATED 3M AGO")
        data["freshness"]["codexUsage"]["ageSeconds"] = 0
        self.assertEqual(fmt_updated(data), "UPDATED 3M AGO")

        data["freshness"]["server"]["ageSeconds"] = None
        self.assertEqual(fmt_updated(data), "UPDATED --")

    def test_offline_cold_start_does_not_fabricate_updated_age(self):
        rendered = render(self.snapshots["offline_cold_start"])
        self.assertIn("UPDATED --", rendered)
        self.assertNotIn("UPDATED NOW", rendered)

    def test_offline_after_last_good_uses_absolute_codex_reset_format(self):
        rendered = render(self.snapshots["offline_after_last_good"])
        self.assertIn("15:27", rendered)
        self.assertIn("9/19 16:09", rendered)
        self.assertNotIn("01h 48m", rendered)
        self.assertNotIn("2d 19h", rendered)

    def test_offline_codex_unknown_stays_unknown(self):
        data = copy.deepcopy(self.snapshots["offline"].data)
        data["codexUsage"] = {
            "fiveHourPercent": None,
            "fiveHourReset": "unknown",
            "weekPercent": None,
            "weekReset": "unknown",
            "resetCards": None,
        }
        offline = render(validate_snapshot(data))
        self.assertEqual(offline.count("--%"), 2)
        self.assertIn("unknown", offline)
        self.assertNotIn("0%", offline)


if __name__ == "__main__":
    unittest.main()
