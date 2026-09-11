"""RunBoard Host aggregation: providers in, one validated state out."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from apps.runboard.shared.state_model import Snapshot, validate_snapshot

from .codex_usage_provider import CodexUsageProvider, CodexUsageResult
from .freshness import FreshnessTracker


class ServerProvider(Protocol):
    def collect(self) -> Snapshot:
        ...


class StaticServerProvider:
    """Adapter for mock snapshots; it never performs external I/O."""

    def __init__(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot

    def collect(self) -> Snapshot:
        return self.snapshot


def _now() -> datetime:
    return datetime.now().astimezone()


def _error_text(error: Any, default: str) -> str:
    return str(error or default)


class RunBoardAggregator:
    def __init__(self, server_provider: ServerProvider, codex_provider: CodexUsageProvider,
                 offline_after_failures: int = 3, server_resource_seconds: int = 10,
                 experiment_seconds: int = 20, codex_usage_seconds: int = 300) -> None:
        self.server_provider = server_provider
        self.codex_provider = codex_provider
        self.server_freshness = FreshnessTracker(offline_after_failures)
        self.codex_freshness = FreshnessTracker(offline_after_failures)
        self.server_resource_seconds = max(1, server_resource_seconds)
        self.experiment_seconds = max(1, experiment_seconds)
        self.codex_usage_seconds = max(1, codex_usage_seconds)
        self._last_server: Optional[Dict[str, Any]] = None
        self._last_experiments = []
        self._last_usage: Optional[Dict[str, Any]] = None
        self._server_provider_status = "unavailable"
        self._codex_provider_status = "unavailable"
        self._source = "live"
        self._last_collection_at: Dict[str, Optional[datetime]] = {"server": None, "codexUsage": None}

    def _due(self, key: str, now: datetime, interval: int, force: bool) -> bool:
        if force or self._last_collection_at[key] is None:
            return True
        return (now - self._last_collection_at[key]).total_seconds() >= interval  # type: ignore

    def collect(self, now: Optional[datetime] = None, force: bool = False) -> Snapshot:
        now = now or _now()
        errors = []
        server_snapshot: Optional[Snapshot] = None
        # ServerProvider currently returns one atomic resource+experiment
        # snapshot. The resource cadence therefore controls that single SSH
        # probe; experiment_seconds is the configured seam for splitting those
        # queries later without duplicating the collector.
        if self._due("server", now, self.server_resource_seconds, force):
            self._last_collection_at["server"] = now
            try:
                server_snapshot = self.server_provider.collect()
                server_data = server_snapshot.data
                failed = server_data.get("source") == "live" and bool(server_data.get("collectionError"))
                if failed:
                    if self._last_server is None:
                        # Keep the configured display identity even when the
                        # first live probe is offline; metrics remain unknown.
                        self._last_server = dict(server_data["server"])
                        self._last_experiments = []
                    raise RuntimeError(server_data.get("collectionError"))
                self._last_server = dict(server_data["server"])
                self._last_experiments = list(server_data["experiments"])
                self._source = server_data.get("source") if server_data.get("source") in {"mock", "live"} else "live"
                self._server_provider_status = "live" if server_data.get("source") == "live" else "mock"
                self.server_freshness.success(now, server_data.get("updatedAt", now.isoformat()))
            except Exception as error:
                message = _error_text(error, "Server provider failed")
                self.server_freshness.failure(message, now)
                self._server_provider_status = "error"
                errors.append("server: {}".format(message))
        if self._due("codexUsage", now, self.codex_usage_seconds, force):
            self._last_collection_at["codexUsage"] = now
            terminal = False
            try:
                result: CodexUsageResult = self.codex_provider.collect()
                if not result.ok:
                    terminal = result.provider_status == "error"
                    raise RuntimeError(result.error or "Codex Usage provider failed")
                self._last_usage = dict(result.usage or {})
                self._codex_provider_status = result.provider_status
                self.codex_freshness.success(now, result.updated_at or now.isoformat())
            except Exception as error:
                message = _error_text(error, "Codex Usage provider failed")
                self.codex_freshness.failure(message, now, terminal=terminal)
                self._codex_provider_status = "error"
                errors.append("codexUsage: {}".format(message))

        if self._last_server is None:
            server = {
                "id": "SERVER-01", "displayName": "SERVER-01", "hostname": None,
                "status": "offline", "cpuPercent": None,
                "ram": {"usedGiB": None, "totalGiB": None, "usedPercent": None},
                "loadAverage": {"one": None, "five": None, "fifteen": None},
                "uptimeSeconds": None,
                "gpu": {"name": None, "utilizationPercent": None, "memoryUsedGiB": None, "memoryTotalGiB": None, "temperatureC": None},
            }
        else:
            server = dict(self._last_server)
            if self.server_freshness.state in {"offline", "error"}:
                server["status"] = "offline"

        if self._last_usage is None:
            usage = {
                "fiveHourPercent": 0, "fiveHourReset": "unknown",
                "weekPercent": 0, "weekReset": "unknown", "resetCards": 0,
            }
        else:
            usage = dict(self._last_usage)
        usage["providerStatus"] = self._codex_provider_status
        usage["updatedAt"] = self.codex_freshness.last_success_label

        source = self._source
        combined_error = "; ".join(errors) or None
        state = {
            "schemaVersion": 1,
            "source": source,
            "collectionError": combined_error,
            "providers": {"server": self._server_provider_status, "codexUsage": self._codex_provider_status},
            "freshness": {
                "server": self.server_freshness.as_dict(now),
                "codexUsage": self.codex_freshness.as_dict(now),
            },
            "server": server,
            "experiments": self._last_experiments,
            "codexUsage": usage,
            "updatedAt": now.astimezone().isoformat(timespec="seconds"),
        }
        return validate_snapshot(state)
