"""RunBoard Codex Usage interface and pure app-server response parser.

The real Codex Monitor process remains the owner of its current quota runtime.
RunBoard intentionally exposes the adapter seam and a mock/fallback provider
until that implementation is declared stable for reuse.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, Optional, Protocol, Sequence


class CodexUsageProvider(Protocol):
    def collect(self) -> "CodexUsageResult":
        ...


@dataclass(frozen=True)
class CodexUsageResult:
    usage: Optional[Dict[str, Any]]
    provider_status: str
    updated_at: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.usage is not None and self.error is None


def _reset_label(epoch: Any, now: datetime) -> str:
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool) or epoch <= 0:
        return "unknown"
    target = datetime.fromtimestamp(epoch, tz=now.tzinfo)
    if target.date() == now.date():
        return target.strftime("%H:%M")
    return "{}/{} {:02d}:{:02d}".format(target.month, target.day, target.hour, target.minute)


def _window(rate_limits: Dict[str, Any], duration: int) -> Optional[Dict[str, Any]]:
    for key in ("primary", "secondary"):
        value = rate_limits.get(key)
        if isinstance(value, dict) and value.get("windowDurationMins") == duration:
            return value
    return None


def parse_rate_limits_response(payload: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Normalize Codex app-server rate limits to the RunBoard usage model."""
    now = now or datetime.now(timezone.utc)
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ValueError("rate-limit response has no result")
    by_id = result.get("rateLimitsByLimitId")
    rate_limits = by_id.get("codex") if isinstance(by_id, dict) else None
    if not isinstance(rate_limits, dict):
        rate_limits = result.get("rateLimits")
    if not isinstance(rate_limits, dict):
        raise ValueError("rate-limit response has no rateLimits")
    five = _window(rate_limits, 300)
    week = _window(rate_limits, 10080)
    if five is None or week is None:
        raise ValueError("required 300-minute or 10080-minute window is unavailable")
    used_values = (five.get("usedPercent"), week.get("usedPercent"))
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100 for value in used_values):
        raise ValueError("rate-limit usedPercent is invalid")
    credits = result.get("rateLimitResetCredits")
    reset_cards = credits.get("availableCount") if isinstance(credits, dict) else 0
    if not isinstance(reset_cards, int) or isinstance(reset_cards, bool) or reset_cards < 0:
        reset_cards = 0
    updated_at = now.astimezone().isoformat(timespec="seconds")
    return {
        "fiveHourPercent": 100 - int(used_values[0]),
        "fiveHourReset": _reset_label(five.get("resetsAt"), now),
        "weekPercent": 100 - int(used_values[1]),
        "weekReset": _reset_label(week.get("resetsAt"), now),
        "resetCards": reset_cards,
        "updatedAt": updated_at,
        "providerStatus": "live",
    }


def parse_cqm1_frame(text: str) -> Dict[str, Any]:
    """Parse the verified Codex Monitor CQM1 output without touching serial."""
    frame = next((line.strip() for line in text.splitlines() if line.strip().startswith("<CQM1|") and line.strip().endswith(">")), None)
    if frame is None or len(frame) > 255:
        raise ValueError("CQM1 frame is missing or too long")
    tokens = frame[1:-1].split("|")
    if not tokens or tokens[0] != "CQM1":
        raise ValueError("invalid CQM1 frame marker")
    fields: Dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise ValueError("invalid CQM1 field")
        key, value = token.split("=", 1)
        if not key or not value or key in fields:
            raise ValueError("invalid or duplicate CQM1 field")
        fields[key] = value
    required = ("5H", "5HR", "W", "WR", "NOW", "TZ")
    if any(key not in fields for key in required):
        raise ValueError("CQM1 required field is missing")
    try:
        remaining_5h = int(fields["5H"])
        reset_5h = int(fields["5HR"])
        remaining_week = int(fields["W"])
        reset_week = int(fields["WR"])
        sent_at = int(fields["NOW"])
        offset_minutes = int(fields["TZ"])
        reset_cards = int(fields["RC"]) if "RC" in fields else -1
    except ValueError:
        raise ValueError("CQM1 numeric field is invalid")
    if not 0 <= remaining_5h <= 100 or not 0 <= remaining_week <= 100:
        raise ValueError("CQM1 quota percentage is outside 0..100")
    if not 0 <= reset_5h <= 0xFFFFFFFF or not 0 <= reset_week <= 0xFFFFFFFF or not 0 <= sent_at <= 0xFFFFFFFF:
        raise ValueError("CQM1 timestamp is outside uint32")
    if not -1440 <= offset_minutes <= 1440 or reset_cards < -1:
        raise ValueError("CQM1 timezone or reset cards is invalid")
    local_zone = timezone(timedelta(minutes=offset_minutes))
    sent = datetime.fromtimestamp(sent_at, tz=local_zone)
    return {
        "fiveHourPercent": remaining_5h,
        "fiveHourReset": _reset_label(reset_5h, sent) if reset_5h else "unknown",
        "weekPercent": remaining_week,
        "weekReset": _reset_label(reset_week, sent) if reset_week else "unknown",
        "resetCards": None if reset_cards < 0 else reset_cards,
        "updatedAt": sent.isoformat(timespec="seconds"),
        "providerStatus": "live",
    }


class MockCodexUsageProvider:
    def __init__(self, usage: Dict[str, Any]) -> None:
        self.usage = dict(usage)

    def collect(self) -> CodexUsageResult:
        value = dict(self.usage)
        value["providerStatus"] = "mock"
        value.setdefault("updatedAt", datetime.now().astimezone().isoformat(timespec="seconds"))
        return CodexUsageResult(value, "mock", value["updatedAt"])


class UnavailableCodexUsageProvider:
    """Explicit fallback used while the real Codex runtime is in acceptance."""

    def __init__(self, reason: str = "Codex Usage provider is not enabled") -> None:
        self.reason = reason

    def collect(self) -> CodexUsageResult:
        return CodexUsageResult(None, "unavailable", error=self.reason)


class CodexMonitorQuotaProvider:
    """Invoke the already-validated Codex Monitor quota bridge in dry-run mode.

    The bridge owns the real ``codex app-server --stdio`` provider. Dry-run
    emits one CQM1 frame and deliberately does not open a COM port, so RunBoard
    reuses the accepted quota implementation instead of duplicating it.
    """

    def __init__(self, root: Path, command: str = "build/bridge/CodexQuotaBridge.exe",
                 timeout_seconds: int = 20, runner: Optional[Callable[..., Any]] = None) -> None:
        self.root = root
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.runner = runner or subprocess.run

    def collect(self) -> CodexUsageResult:
        executable = Path(self.command)
        if not executable.is_absolute():
            executable = self.root / executable
        try:
            completed = self.runner(
                [str(executable), "--dry-run"], cwd=str(self.root), timeout=self.timeout_seconds,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "bridge exited with {}".format(completed.returncode)).strip())
            usage = parse_cqm1_frame(completed.stdout)
            return CodexUsageResult(usage, "live", usage["updatedAt"])
        except Exception as error:
            return CodexUsageResult(None, "error", error="Codex Monitor quota bridge failed: {}".format(error))


class ParsedResponseCodexUsageProvider:
    """Small adapter for a caller that already owns app-server I/O.

    It deliberately receives a response reader instead of starting Codex or
    touching credentials, keeping the RunBoard boundary testable and local.
    """

    def __init__(self, response_reader: Callable[[], Dict[str, Any]]) -> None:
        self.response_reader = response_reader

    def collect(self) -> CodexUsageResult:
        try:
            usage = parse_rate_limits_response(self.response_reader())
            return CodexUsageResult(usage, "live", usage["updatedAt"])
        except Exception as error:
            return CodexUsageResult(None, "error", error="Codex Usage parse failed: {}".format(error))
