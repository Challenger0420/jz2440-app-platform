#!/usr/bin/env python3
"""Windows Preview for the 480x272 RunBoard screen."""

import argparse
from datetime import datetime
import logging
import sys
import tkinter as tk
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


ROOT = Path(__file__).resolve().parents[3]
SHARED = ROOT / "apps" / "runboard" / "shared"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.runboard.shared.state_model import Snapshot, load_snapshot  # noqa: E402
from apps.runboard.collector.server_provider import (  # noqa: E402
    SSHServerProvider,
    make_offline_snapshot,
)
from apps.runboard.collector.config import load_live_config  # noqa: E402
from apps.runboard.host.aggregator import (  # noqa: E402
    RunBoardAggregator,
    StaticServerProvider,
)
from apps.runboard.host.codex_usage_provider import (  # noqa: E402
    CodexMonitorQuotaProvider,
    MockCodexUsageProvider,
)


SCENARIOS = {
    "idle": "idle.json",
    "single": "single.json",
    "double": "double.json",
    "matrix_single": "matrix_single.json",
    "completed": "completed.json",
    "error": "error.json",
    "degraded": "degraded.json",
    "stale": "stale.json",
    "stale_after_last_good": "stale_after_last_good.json",
    "offline": "offline.json",
    "offline_after_last_good": "offline_after_last_good.json",
    "offline_cold_start": "offline_cold_start.json",
    "longtext": "longtext.json",
}

SCALE = 2
W, H = 480, 272
COLORS = {
    "bg": "#071018",
    "panel": "#0d1b28",
    "panel_alt": "#102536",
    "line": "#25445b",
    "bar_border": "#5b86a8",
    "track": "#1b3042",
    "white": "#effaff",
    "label": "#a5b4c3",
    "muted": "#86aabd",
    "cyan": "#4de3ff",
    "green": "#45f09a",
    "blue": "#5cb0ff",
    "yellow": "#ffc857",
    "red": "#ff5b6e",
}


def fmt_elapsed(seconds: Optional[int]) -> str:
    if seconds is None:
        return "--:--:--"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)


def fmt_cell_elapsed(seconds: Optional[int]) -> str:
    if seconds is None:
        return "--"
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return "{}H{:02d}M".format(hours, minutes)


def fmt_percent(value: object) -> str:
    return "--%" if value is None else "{:.0f}%".format(float(value))


def quota_color(value: object, normal: str = "green") -> str:
    if value is None:
        return "muted"
    remaining = float(value)
    if remaining < 20:
        return "red"
    if remaining < 50:
        return "yellow"
    return normal


def fmt_number(value: object, digits: int = 1) -> str:
    return "--" if value is None else ("{:0.%df}" % digits).format(float(value))


def fmt_round(current: object, total: object) -> str:
    if current is None or total is None:
        return "ROUND --/--"
    return "ROUND {}/{}".format(current, total)


def fmt_uptime(seconds: object) -> str:
    if seconds is None:
        return "UP --"
    days = int(seconds) // 86400
    hours = (int(seconds) % 86400) // 3600
    return "UP {}d {:02d}h".format(days, hours)


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


def fmt_clock(value: object) -> str:
    parsed = _parse_timestamp(value)
    return parsed.strftime("%H:%M") if parsed is not None else "--:--"


def updated_age_seconds(snapshot: Dict[str, object]) -> Optional[int]:
    freshness = snapshot.get("freshness") or {}
    server_freshness = freshness.get("server") or {}
    server_age = server_freshness.get("ageSeconds")
    server_offline = (
        server_freshness.get("state") == "offline"
        or (snapshot.get("server") or {}).get("status") == "offline"
    )
    if server_offline:
        if isinstance(server_age, (int, float)) and not isinstance(server_age, bool) and server_age >= 0:
            return int(server_age)
        return None
    ages = []
    for provider in ("server", "codexUsage"):
        item = freshness.get(provider) or {}
        age = item.get("ageSeconds")
        if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 0:
            ages.append(int(age))
    if ages:
        return max(ages)
    parsed = _parse_timestamp(snapshot.get("updatedAt"))
    if parsed is None:
        return None
    return max(0, int((datetime.now().astimezone() - parsed).total_seconds()))


def fmt_updated(snapshot: Dict[str, object]) -> str:
    age = updated_age_seconds(snapshot)
    if age is None:
        return "UPDATED --"
    if age <= 2:
        return "UPDATED NOW"
    if age < 60:
        return "UPDATED {}S AGO".format(age)
    if age < 3600:
        return "UPDATED {}M AGO".format(age // 60)
    return "UPDATED {}H AGO".format(age // 3600)


def shorten(value: object, limit: int) -> str:
    """Keep long live metadata inside the fixed 480x272 layout."""
    text = str(value)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:max(1, limit)]
    return text[:max(1, limit - 3)] + "..."


class RunBoardPreview:
    def __init__(self, root: tk.Tk, initial: str,
                 snapshot: Optional[Snapshot] = None,
                 live_aggregator: Optional[RunBoardAggregator] = None,
                 ui_render_milliseconds: int = 1000) -> None:
        self.root = root
        self.snapshot: Snapshot
        self.scenario = initial
        self.live_aggregator = live_aggregator
        self.ui_render_milliseconds = max(250, ui_render_milliseconds)
        self.is_live = snapshot is not None
        self.root.title("RunBoard Preview — local mock only")
        self.root.configure(bg="#111923")
        self.root.resizable(False, False)

        controls = tk.Frame(root, bg="#111923")
        controls.pack(fill="x", padx=12, pady=(10, 6))
        mode_label = "LIVE READ-ONLY" if snapshot is not None else "LOCAL MOCK ONLY"
        tk.Label(
            controls,
            text="RunBoard Preview  |  480 × 272 target layout  |  " + mode_label,
            bg="#111923",
            fg=COLORS["white"],
            font=("Segoe UI", 10, "bold"),
            ).pack(side="left")
        for name in SCENARIOS:
            button = tk.Button(
                controls,
                text=name.upper(),
                command=lambda value=name: self.show(value),
                bg="#203344",
                fg=COLORS["white"],
                activebackground="#31536b",
                activeforeground=COLORS["white"],
                relief="flat",
                padx=7,
                pady=2,
                font=("Segoe UI", 8, "bold"),
            )
            button.pack(side="left", padx=(5, 0))
        if live_aggregator is not None:
            tk.Button(
                controls,
                text="REFRESH LIVE",
                command=self.refresh_live,
                bg="#31536b",
                fg=COLORS["white"],
                activebackground="#44758f",
                activeforeground=COLORS["white"],
                relief="flat",
                padx=7,
                pady=2,
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left", padx=(5, 0))

        self.canvas = tk.Canvas(
            root,
            width=W * SCALE,
            height=H * SCALE,
            bg=COLORS["bg"],
            highlightthickness=1,
            highlightbackground="#33566c",
        )
        self.canvas.pack(padx=12, pady=(0, 12))
        if snapshot is None:
            self.show(initial)
        else:
            self.set_snapshot(snapshot, "live", True)
            self.root.after(self.ui_render_milliseconds, self.scheduled_live_refresh)

    def show(self, scenario: str) -> None:
        self.scenario = scenario
        self.is_live = False
        source = load_snapshot(SHARED / "mocks" / SCENARIOS[scenario])
        aggregator = RunBoardAggregator(
            StaticServerProvider(source),
            MockCodexUsageProvider(source.data["codexUsage"]),
        )
        self.set_snapshot(aggregator.collect(force=True), scenario, False)

    def refresh_live(self) -> None:
        if self.live_aggregator is None:
            return
        self.is_live = True
        self.set_snapshot(self.live_aggregator.collect(force=True), "live", True)

    def scheduled_live_refresh(self) -> None:
        if self.live_aggregator is None:
            return
        self.is_live = True
        self.set_snapshot(self.live_aggregator.collect(), "live", True)
        self.root.after(self.ui_render_milliseconds, self.scheduled_live_refresh)

    def set_snapshot(self, snapshot: Snapshot, scenario: str, is_live: bool) -> None:
        self.snapshot = snapshot
        self.scenario = scenario
        self.is_live = is_live
        suffix = "live read-only" if is_live else "local mock only"
        self.root.title("RunBoard Preview — {} / {} — {}".format(
            scenario.upper(), self.snapshot.layout_mode.upper(), suffix
        ))
        self.draw()

    def xy(self, value: float) -> int:
        return int(round(value * SCALE))

    def box(self, x1: float, y1: float, x2: float, y2: float,
            fill: str, outline: str = "", width: int = 1) -> None:
        self.canvas.create_rectangle(
            self.xy(x1), self.xy(y1), self.xy(x2), self.xy(y2),
            fill=fill, outline=outline, width=max(1, self.xy(width)),
        )

    def line(self, points: Iterable[Tuple[float, float]], fill: str, width: int = 1) -> None:
        flattened = []
        for x, y in points:
            flattened.extend((self.xy(x), self.xy(y)))
        self.canvas.create_line(*flattened, fill=fill, width=max(1, self.xy(width)))

    def text(self, x: float, y: float, value: str, size: int = 8,
             color: str = "white", anchor: str = "nw", bold: bool = False) -> None:
        self.canvas.create_text(
            self.xy(x), self.xy(y), text=value, anchor=anchor,
            fill=COLORS[color], font=("Segoe UI", max(7, size * SCALE), "bold" if bold else "normal"),
        )

    def pill(self, x: float, y: float, value: str, color: str) -> None:
        width = max(34, 5.7 * len(value) + 12)
        self.box(x, y, x + width, y + 16, color)
        self.text(x + width / 2, y + 8, value, size=7, color="bg", anchor="center", bold=True)

    def progress(self, x: float, y: float, width: float, value: Optional[float], color: str) -> None:
        bar_height = 12
        self.box(x, y, x + width, y + bar_height, COLORS["bar_border"])
        self.box(x + 1, y + 1, x + width - 1, y + bar_height - 1, COLORS["track"])
        if value is None:
            return
        bounded = max(0.0, min(100.0, float(value)))
        filled = max(0.0, (width - 4) * bounded / 100.0)
        if filled > 0:
            self.box(x + 2, y + 2, x + 2 + filled, y + bar_height - 2, COLORS[color])

    def draw(self) -> None:
        self.canvas.delete("all")
        snapshot = self.snapshot.data
        server = snapshot["server"]
        freshness = snapshot.get("freshness") or {}
        server_freshness = (freshness.get("server") or {}).get("state", "fresh")
        server_offline = server.get("status") == "offline" or server_freshness == "offline"
        self.box(0, 0, W, H, COLORS["bg"])

        # Header: stable server identity, host-provided clock and resource signals.
        self.text(14, 8, "RUNBOARD", size=11, color="white", bold=True)
        status_badge = server_freshness.upper() if server_freshness in {"stale", "offline", "error"} else server["status"].upper()
        status_color = "green" if status_badge == "ONLINE" else "yellow" if status_badge in {"STALE", "DEGRADED"} else "red"
        self.text(394, 14, fmt_clock(snapshot.get("updatedAt")), size=7, color="muted", anchor="e")
        self.pill(405, 7, status_badge, COLORS[status_color])
        ram = server["ram"]
        gpu = server["gpu"]
        load = server.get("loadAverage") or {}
        self.text(14, 28, shorten(server.get("displayName") or server["id"], 18), size=7, color="muted")
        if self.snapshot.layout_mode == "matrix" or server_offline or not snapshot["experiments"] or len(snapshot["experiments"]) >= 1:
            resource_line = "CPU {} RAM {} GPU {} {}/{}G".format(
                "--%" if server_offline else fmt_percent(server.get("cpuPercent")),
                "--%" if server_offline else fmt_percent(ram.get("usedPercent")),
                "--%" if server_offline else fmt_percent(gpu.get("utilizationPercent")),
                "--" if server_offline else fmt_number(gpu.get("memoryUsedGiB")),
                "--" if server_offline else fmt_number(gpu.get("memoryTotalGiB"), 0),
            )
            self.text(468, 28, shorten(resource_line, 44), size=7, color="cyan", anchor="ne", bold=True)
        else:
            self.text(188, 28, "CPU " + fmt_percent(server.get("cpuPercent")), size=8, color="cyan", bold=True)
            self.text(264, 28, "RAM " + fmt_percent(ram.get("usedPercent")), size=8, color="cyan", bold=True)
            self.text(345, 28, "GPU " + fmt_percent(gpu.get("utilizationPercent")), size=8, color="cyan", bold=True)
            self.text(188, 41, "LOAD " + fmt_number(load.get("one"), 2), size=7, color="muted")
            self.text(264, 41, fmt_uptime(server.get("uptimeSeconds")), size=7, color="muted")
            self.text(345, 41, "MEM {} / {}G  T{}C".format(
                fmt_number(gpu.get("memoryUsedGiB")), fmt_number(gpu.get("memoryTotalGiB"), 0),
                fmt_number(gpu.get("temperatureC"), 0)), size=7, color="muted")
        self.line([(14, 46), (466, 46)], COLORS["line"])

        experiments = snapshot["experiments"]
        if server_offline:
            self.draw_offline()
        elif not experiments:
            self.draw_idle()
        else:
            if self.snapshot.layout_mode == "matrix":
                self.draw_matrix_experiment(experiments[0], snapshot.get("job") or {}, 12, 48, 456, 184)
            elif len(experiments) >= 2:
                self.draw_experiment(experiments[0], 12, 48, 456, 130, primary=True, double_mode=True)
            else:
                self.draw_experiment(experiments[0], 12, 48, 456, 130, primary=True, double_mode=True)
            if len(experiments) == 1:
                self.draw_matrix_usage(snapshot["codexUsage"], 0, 240, 480, 32)
            else:
                self.draw_second_experiment(experiments[1], 12, 184, 456, 54)
        if server_offline or not experiments:
            self.draw_matrix_usage(snapshot["codexUsage"], 0, 240, 480, 32)

        # Footer is intentionally small and non-operational in Preview.
        self.line([(14, 238), (466, 238)], COLORS["line"])
        if self.snapshot.layout_mode == "matrix" and not server_offline:
            if server_freshness != "fresh":
                self.text(334, 241, "SERVER " + server_freshness.upper(), size=7, color="red")
        elif not server_offline:
            if self.is_live:
                footer = "LIVE SSH  ERROR" if snapshot.get("collectionError") else "LIVE SSH  READ-ONLY"
            else:
                footer = "MOCK  LOCAL DATA"
            if server_freshness != "fresh":
                footer += "  /  SERVER " + server_freshness.upper()
            self.text(14, 253, footer, size=7, color="muted")
        if len(experiments) < 2:
            self.text(466, 250, fmt_updated(snapshot), size=8, color="muted", anchor="ne")

    def draw_idle(self) -> None:
        self.box(74, 91, 406, 171, COLORS["panel"], COLORS["line"])
        self.text(240, 108, "SERVER IDLE", size=17, color="green", anchor="center", bold=True)
        self.text(240, 135, "NO ACTIVE EXPERIMENT", size=8, color="muted", anchor="center", bold=True)
        self.text(240, 151, "Waiting for the next run", size=7, color="white", anchor="center")

    def draw_offline(self) -> None:
        """Show absence of live server data without implying an idle server."""
        self.box(12, 58, 468, 216, COLORS["bg"], COLORS["line"])
        self.text(240, 108, "SERVER OFFLINE", size=13, color="white", anchor="center", bold=True)
        self.text(240, 139, "NO LIVE SERVER DATA", size=8, color="muted", anchor="center", bold=True)

    def draw_experiment(self, experiment: Dict[str, object], x: int, y: int,
                        width: int, height: int, primary: bool,
                        double_mode: bool = False) -> None:
        compact = height < 80
        self.box(x, y, x + width, y + height, COLORS["panel_alt"] if primary else COLORS["panel"], COLORS["line"])
        label = "CURRENT EXPERIMENT" if primary else "SECOND EXPERIMENT"
        self.text(x + 12, y + 8, label, size=7, color="cyan", bold=True)
        status = str(experiment["status"])
        status_color = "green" if status == "RUNNING" else "yellow" if status == "PAUSED" else "red" if status == "ERROR" else "cyan"
        self.pill(x + width - 78, y + 7, status, COLORS[status_color])
        self.text(x + 12, y + 24, shorten(experiment["name"], 36 if primary else 27), size=12 if primary else 8, color="white", bold=True)
        if compact:
            current = experiment.get("currentRound")
            total = experiment.get("totalRound")
            rounds = "--/--" if current is None or total is None else "{}/{}".format(current, total)
            detail = "{}  •  {}  •  {}/{}  •  {}".format(
                shorten(experiment["user"], 18), status, rounds.split("/")[0], rounds.split("/")[1],
                fmt_elapsed(experiment.get("elapsedSeconds")),
            )
            self.text(x + 12, y + 37, detail, size=7, color="muted")
            self.progress(x + 12, y + height - 9, width - 24, experiment.get("progress"), status_color)
            return

        progress_value = experiment.get("progress")
        self.text(x + 12, y + 44, "by {}".format(shorten(experiment["user"], 24)), size=7, color="muted")
        self.progress(x + 12, y + 61, width - 24, progress_value, status_color)
        percent = "--" if progress_value is None else "{:02.0f}%".format(float(progress_value))
        self.text(x + 12, y + 72, "{}   {}".format(percent, fmt_round(experiment.get("currentRound"), experiment.get("totalRound"))), size=8, color=status_color, bold=True)
        dataset = experiment.get("dataset") or "DATASET --"
        seed = "SEED --" if experiment.get("seed") is None else "SEED {}".format(experiment["seed"])
        self.text(x + 12, y + 91, "{}   {}".format(shorten(dataset, 22), seed), size=7, color="white")
        if not double_mode:
            metric_name = experiment.get("metricName") or "METRIC --"
            metric_value = "--" if experiment.get("metricValue") is None else "{:.2f}".format(float(experiment["metricValue"]))
            self.text(x + width - 12, y + 91, "{} {}".format(shorten(metric_name.upper(), 16), metric_value), size=7, color="white", anchor="ne")
        self.text(x + 12, y + 108, "TIME  " + fmt_elapsed(experiment.get("elapsedSeconds")), size=7, color="muted")

    def draw_second_experiment(self, experiment: Dict[str, object], x: int, y: int,
                               width: int, height: int) -> None:
        """Compact double-mode card; keep one field group per baseline."""
        self.box(x, y, x + width, y + height, COLORS["panel"], COLORS["line"])
        status = str(experiment["status"])
        status_color = "green" if status == "RUNNING" else "yellow" if status == "PAUSED" else "red" if status == "ERROR" else "cyan"
        self.text(x + 10, y + 3, "SECOND EXPERIMENT", size=7, color="label", bold=True)
        self.text(x + 10, y + 20, shorten(experiment["name"], 30), size=8, color="white", bold=True)
        self.text(x + width - 12, y + 20, status, size=7, color=status_color, anchor="ne", bold=True)
        current = experiment.get("currentRound")
        total = experiment.get("totalRound")
        rounds = "--/--" if current is None or total is None else "{}/{}".format(current, total)
        progress = "--%" if experiment.get("progress") is None else "{:.0f}%".format(float(experiment["progress"]))
        self.text(x + 10, y + 37, shorten(experiment["user"], 12), size=7, color="muted")
        self.text(x + 126, y + 37, "PROGRESS " + progress, size=7, color="muted")
        self.text(x + 270, y + 37, "ROUND " + rounds, size=7, color="muted")

    def draw_matrix_experiment(self, experiment: Dict[str, object], job: Dict[str, object],
                               x: int, y: int, width: int, height: int) -> None:
        """Show matrix progress separately from the current cell's round."""
        self.box(x, y, x + width, y + height, COLORS["panel_alt"], COLORS["line"])
        self.text(x + 12, y + 8, "CURRENT EXPERIMENT", size=7, color="label", bold=True)
        status = str(experiment["status"])
        status_color = "green" if status == "RUNNING" else "yellow" if status == "PAUSED" else "red" if status == "ERROR" else "cyan"
        self.pill(x + width - 78, y + 7, status, COLORS[status_color])
        self.text(x + 12, y + 26, shorten(job.get("name") or "UNKNOWN JOB", 36), size=11, color="white", bold=True)
        self.text(x + 12, y + 45, "USER " + shorten(experiment["user"], 22), size=7, color="label")
        matrix_completed = job.get("matrixCompleted")
        matrix_total = job.get("matrixTotal")
        current_cell = job.get("currentCell")
        matrix = "MATRIX --/--" if matrix_completed is None or matrix_total is None else "MATRIX {}/{}".format(matrix_completed, matrix_total)
        cell = "CELL --/--" if current_cell is None or matrix_total is None else "CELL {}/{}".format(current_cell, matrix_total)
        self.text(x + 12, y + 64, matrix, size=8, color="cyan", bold=True)
        self.text(x + 242, y + 64, cell, size=8, color="cyan", bold=True)
        matrix_progress = job.get("matrixProgressPercent")
        self.progress(x + 12, y + 82, width - 24, matrix_progress, "cyan")
        method = shorten(str(job.get("method") or "METHOD --"), 22)
        dataset = shorten(str(experiment.get("dataset") or "DATA --"), 16)
        seed = "S--" if experiment.get("seed") is None else "S{}".format(experiment["seed"])
        self.text(x + 12, y + 101, "METHOD " + method, size=7, color="white")
        self.text(x + 242, y + 101, "DATA {}  {}".format(dataset, seed), size=7, color="white")
        current_round = experiment.get("currentRound")
        total_round = experiment.get("totalRound")
        self.text(x + 12, y + 125, fmt_round(current_round, total_round), size=8, color="white")
        round_percent = None
        if current_round is not None and total_round:
            round_percent = 100.0 * float(current_round) / float(total_round)
        self.text(x + 242, y + 125, "TIME " + fmt_cell_elapsed(experiment.get("elapsedSeconds")), size=7, color="label")
        self.progress(x + 12, y + 143, width - 24, round_percent, status_color)

    def draw_usage(self, usage: Dict[str, object], x: int, y: int, width: int, height: int) -> None:
        self.box(x, y, x + width, y + height, COLORS["panel"], COLORS["line"])
        provider_status = usage.get("providerStatus")
        title = "CODEX USAGE" if provider_status in (None, "live") else "CODEX USAGE  " + str(provider_status).upper()
        self.text(x + 12, y + 7, title, size=7, color="cyan", bold=True)
        self.text(x + 112, y + 7, "5H  {}".format(fmt_percent(usage.get("fiveHourPercent"))), size=8, color="white", bold=True)
        self.text(x + 196, y + 7, "RESET  {}".format(shorten(usage.get("fiveHourReset") or "unknown", 14)), size=7, color="muted")
        self.text(x + 294, y + 7, "WEEK  {}".format(fmt_percent(usage.get("weekPercent"))), size=8, color="white", bold=True)
        reset_cards = "--" if usage.get("resetCards") is None else str(usage["resetCards"])
        self.text(x + 388, y + 7, "RC{}".format(reset_cards), size=8, color="yellow", bold=True)
        self.text(x + 112, y + 24, "WEEK RESET  {}".format(shorten(usage.get("weekReset") or "unknown", 14)), size=7, color="muted")

    def draw_matrix_usage(self, usage: Dict[str, object], x: int, y: int, width: int, height: int) -> None:
        """Render matrix-mode quota in the dedicated bottom footer."""
        self.box(x, y, x + width, y + height, COLORS["panel"])
        five_hour = usage.get("fiveHourPercent")
        week = usage.get("weekPercent")
        five_text = "--%" if five_hour is None else "{}%".format(int(round(float(five_hour))))
        week_text = "--%" if week is None else "{}%".format(int(round(float(week))))
        five_color = quota_color(five_hour, "green")
        week_color = quota_color(week, "blue")
        self.text(x + 14, y + 1, five_text, size=9, color=five_color, bold=True)
        self.progress(x + 50, y + 2, 180, five_hour, five_color)
        self.text(x + 244, y + 1, shorten(usage.get("fiveHourReset") or "unknown", 14), size=7, color="muted")
        self.text(x + 14, y + 16, week_text, size=9, color=week_color, bold=True)
        self.progress(x + 50, y + 17, 180, week, week_color)
        self.text(x + 244, y + 16, shorten(usage.get("weekReset") or "unknown", 14), size=7, color="muted")


def main() -> None:
    parser = argparse.ArgumentParser(description="RunBoard local Windows UI Preview")
    parser.add_argument("--scenario", choices=SCENARIOS, default="single")
    parser.add_argument("--live", action="store_true", help="collect one read-only snapshot over SSH")
    args = parser.parse_args()
    logger = logging.getLogger("runboard.live")
    if args.live:
        log_path = ROOT / "build" / "runboard" / "live.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        )
        try:
            provider = SSHServerProvider.from_local_config(ROOT, logger)
            config = load_live_config(ROOT)
            usage = load_snapshot(SHARED / "mocks" / "idle.json").data["codexUsage"]
            codex_provider = (
                CodexMonitorQuotaProvider(ROOT, config.codex_usage.command, config.codex_usage.timeout_seconds)
                if config.codex_usage.mode == "real"
                else MockCodexUsageProvider(usage)
            )
            aggregator = RunBoardAggregator(
                provider,
                codex_provider,
                offline_after_failures=config.offline_after_failures,
                server_resource_seconds=config.server_resource_seconds,
                experiment_seconds=config.experiment_seconds,
                codex_usage_seconds=config.codex_usage_seconds,
            )
            snapshot = aggregator.collect(force=True)
        except Exception as error:
            logger.error("live preview setup failed: %s", error)
            aggregator = None
            snapshot = make_offline_snapshot(ROOT, str(error))
    else:
        aggregator = None
        snapshot = None
    root = tk.Tk()
    ui_render_milliseconds = config.ui_render_milliseconds if args.live and aggregator is not None else 1000
    RunBoardPreview(root, args.scenario, snapshot=snapshot, live_aggregator=aggregator,
                    ui_render_milliseconds=ui_render_milliseconds)
    root.mainloop()


if __name__ == "__main__":
    main()
