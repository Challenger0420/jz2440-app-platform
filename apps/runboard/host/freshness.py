"""Provider freshness state, independent for each data source."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


STATES = {"fresh", "stale", "offline", "error"}


def _epoch(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


@dataclass
class FreshnessTracker:
    """Keep the last successful observation while failures are transient."""

    offline_after_failures: int = 3
    state: str = "offline"
    consecutive_failures: int = 0
    last_success_at: Optional[datetime] = None
    last_success_label: Optional[str] = None
    last_error: Optional[str] = None

    def success(self, now: datetime, label: str) -> None:
        self.state = "fresh"
        self.consecutive_failures = 0
        self.last_success_at = now
        self.last_success_label = label
        self.last_error = None

    def failure(self, error: str, now: datetime, terminal: bool = False) -> None:
        self.consecutive_failures += 1
        self.last_error = error
        if terminal:
            self.state = "error"
        elif self.last_success_at is None:
            self.state = "offline"
        elif self.consecutive_failures >= self.offline_after_failures:
            self.state = "offline"
        else:
            self.state = "stale"

    def as_dict(self, now: datetime) -> Dict[str, Any]:
        success_epoch = _epoch(self.last_success_at)
        now_epoch = _epoch(now)
        age = None
        if success_epoch is not None and now_epoch is not None:
            age = max(0, int(now_epoch - success_epoch))
        return {
            "state": self.state,
            "updatedAt": self.last_success_label,
            "ageSeconds": age,
            "consecutiveFailures": self.consecutive_failures,
        }
