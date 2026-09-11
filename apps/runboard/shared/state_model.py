"""Small dependency-free loader and validator for RunBoard snapshots."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


EXPERIMENT_STATUSES = {"RUNNING", "PAUSED", "ERROR", "COMPLETED", "UNKNOWN"}
SERVER_STATUSES = {"online", "offline", "degraded"}
FRESHNESS_STATES = {"fresh", "stale", "offline", "error"}
PROVIDER_STATUSES = {"live", "mock", "unavailable", "error"}


@dataclass(frozen=True)
class Snapshot:
    data: Dict[str, Any]

    @property
    def experiments(self) -> List[Dict[str, Any]]:
        return self.data["experiments"]

    @property
    def layout_mode(self) -> str:
        count = len(self.experiments)
        if count == 0:
            return "idle"
        if count == 1:
            return "codex-usage"
        return "second-experiment"


def _require(mapping: Dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ValueError("missing {}".format(path + "." + key))
    return mapping[key]


def _number(value: Any, path: str, minimum: Optional[float] = None,
            maximum: Optional[float] = None) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("{} must be a number".format(path))
    if minimum is not None and value < minimum:
        raise ValueError("{} must be >= {}".format(path, minimum))
    if maximum is not None and value > maximum:
        raise ValueError("{} must be <= {}".format(path, maximum))


def _text(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(path))


def _optional_text(mapping: Dict[str, Any], key: str, path: str) -> None:
    if key in mapping and mapping[key] is not None:
        _text(mapping[key], path + "." + key)


def _optional_number(mapping: Dict[str, Any], key: str, path: str,
                     minimum: Optional[float] = None,
                     maximum: Optional[float] = None) -> None:
    if key in mapping and mapping[key] is not None:
        _number(mapping[key], path + "." + key, minimum, maximum)


def validate_snapshot(data: Dict[str, Any]) -> Snapshot:
    """Validate the shared shape without inventing unavailable values."""
    if not isinstance(data, dict):
        raise ValueError("snapshot must be an object")
    if data.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    if "source" in data and data["source"] not in {"mock", "live"}:
        raise ValueError("source is invalid")
    if "collectionError" in data and data["collectionError"] is not None:
        _text(data["collectionError"], "collectionError")

    providers = data.get("providers")
    if providers is not None:
        if not isinstance(providers, dict):
            raise ValueError("providers must be an object")
        for key in ("server", "codexUsage"):
            if key in providers and providers[key] not in PROVIDER_STATUSES:
                raise ValueError("providers.{} is invalid".format(key))

    freshness = data.get("freshness")
    if freshness is not None:
        if not isinstance(freshness, dict):
            raise ValueError("freshness must be an object")
        for key in ("server", "codexUsage"):
            item = freshness.get(key)
            if item is None:
                continue
            if not isinstance(item, dict):
                raise ValueError("freshness.{} must be an object".format(key))
            if item.get("state") not in FRESHNESS_STATES:
                raise ValueError("freshness.{}.state is invalid".format(key))
            _optional_text(item, "updatedAt", "freshness." + key)
            _optional_number(item, "ageSeconds", "freshness." + key, 0)
            failures = item.get("consecutiveFailures")
            if failures is not None and (not isinstance(failures, int) or isinstance(failures, bool) or failures < 0):
                raise ValueError("freshness.{}.consecutiveFailures must be a non-negative integer".format(key))

    server = _require(data, "server", "snapshot")
    if not isinstance(server, dict):
        raise ValueError("server must be an object")
    _text(_require(server, "id", "server"), "server.id")
    if _require(server, "status", "server") not in SERVER_STATUSES:
        raise ValueError("server.status is invalid")
    _optional_text(server, "displayName", "server")
    _optional_text(server, "hostname", "server")
    _optional_number(server, "cpuPercent", "server", 0, 100)

    ram = _require(server, "ram", "server")
    gpu = _require(server, "gpu", "server")
    if not isinstance(ram, dict) or not isinstance(gpu, dict):
        raise ValueError("server.ram and server.gpu must be objects")
    for key, minimum, maximum in (("usedGiB", 0, None), ("totalGiB", 0.0001, None), ("usedPercent", 0, 100)):
        value = _require(ram, key, "server.ram")
        if value is not None:
            _number(value, "server.ram." + key, minimum, maximum)
    _optional_text(gpu, "name", "server.gpu")
    for key, minimum, maximum in (("utilizationPercent", 0, 100), ("memoryUsedGiB", 0, None), ("memoryTotalGiB", 0.0001, None), ("temperatureC", -100, 200)):
        _optional_number(gpu, key, "server.gpu", minimum, maximum)
    if "loadAverage" in server and server["loadAverage"] is not None:
        load = server["loadAverage"]
        if not isinstance(load, dict):
            raise ValueError("server.loadAverage must be an object")
        for key in ("one", "five", "fifteen"):
            _optional_number(load, key, "server.loadAverage")
    _optional_number(server, "uptimeSeconds", "server", 0)

    experiments = _require(data, "experiments", "snapshot")
    if not isinstance(experiments, list):
        raise ValueError("experiments must be a list")
    for index, experiment in enumerate(experiments):
        path = "experiments[{}]".format(index)
        if not isinstance(experiment, dict):
            raise ValueError("{} must be an object".format(path))
        for key in ("name", "user"):
            _text(_require(experiment, key, path), path + "." + key)
        if _require(experiment, "status", path) not in EXPERIMENT_STATUSES:
            raise ValueError("{}.status is invalid".format(path))
        for key, minimum, maximum in (("progress", 0, 100), ("metricValue", None, None)):
            _optional_number(experiment, key, path, minimum, maximum)
        for key in ("currentRound", "totalRound", "elapsedSeconds", "seed"):
            value = _require(experiment, key, path)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("{}.{} must be an integer or null".format(path, key))
            if key != "seed" and value < 0:
                raise ValueError("{}.{} must be >= 0".format(path, key))
        if (experiment["totalRound"] is not None and experiment["currentRound"] is not None and
                (experiment["totalRound"] < 1 or experiment["currentRound"] > experiment["totalRound"])):
            raise ValueError("{} round values are invalid".format(path))
        for key in ("dataset", "metricName"):
            _optional_text(experiment, key, path)

    usage = _require(data, "codexUsage", "snapshot")
    if not isinstance(usage, dict):
        raise ValueError("codexUsage must be an object")
    for key in ("fiveHourPercent", "weekPercent"):
        _number(_require(usage, key, "codexUsage"), "codexUsage." + key, 0, 100)
    for key in ("fiveHourReset", "weekReset"):
        _text(_require(usage, key, "codexUsage"), "codexUsage." + key)
    cards = _require(usage, "resetCards", "codexUsage")
    if cards is not None and (not isinstance(cards, int) or isinstance(cards, bool) or cards < 0):
        raise ValueError("codexUsage.resetCards must be a non-negative integer or null")
    _optional_text(usage, "updatedAt", "codexUsage")
    if "providerStatus" in usage and usage["providerStatus"] not in PROVIDER_STATUSES:
        raise ValueError("codexUsage.providerStatus is invalid")
    _text(_require(data, "updatedAt", "snapshot"), "updatedAt")
    return Snapshot(data)


def load_snapshot(path: Path) -> Snapshot:
    with path.open("r", encoding="utf-8") as handle:
        return validate_snapshot(json.load(handle))
