"""Compact RunBoard board frame encoder/decoder.

Frame format (ASCII, one frame per line)::

    RB1|L=<payload byte length>|<TLV fields>|CRC=<CRC16-CCITT>\n
The payload is a pipe-separated abbreviated TLV list, not JSON. Unknown
values are encoded as ``?`` and strings are percent-encoded, so an old ARM
parser only needs line framing, integer parsing and a small field table.
"""

from binascii import crc_hqx
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, unquote

from .state_model import validate_snapshot


class ProtocolError(ValueError):
    pass


def _value(value: Any) -> str:
    if value is None:
        return "?"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return "{:.3f}".format(value).rstrip("0").rstrip(".")
    return quote(str(value), safe="-_.~")


def _decode(value: str) -> Optional[str]:
    return None if value == "?" else unquote(value)


def _field(fields: Dict[str, str], key: str, default: Any = None) -> Any:
    if key not in fields:
        return default
    return _decode(fields[key])


def _number(fields: Dict[str, str], key: str, integer: bool = False) -> Any:
    value = _field(fields, key)
    if value is None:
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ProtocolError("invalid numeric field {}".format(key))


def _int_text(value: Any) -> Any:
    return None if value is None else int(value)


def _put(fields: List[str], key: str, value: Any) -> None:
    fields.append("{}={}".format(key, _value(value)))


def _experiment_fields(index: int, experiment: Dict[str, Any]) -> Iterable[str]:
    prefix = "E{}".format(index)
    mapping = {
        "N": experiment.get("name"), "U": experiment.get("user"),
        "S": experiment.get("status"), "P": experiment.get("progress"),
        "R": experiment.get("currentRound"), "T": experiment.get("totalRound"),
        "E": experiment.get("elapsedSeconds"), "D": experiment.get("dataset"),
        "SD": experiment.get("seed"), "MN": experiment.get("metricName"),
        "MV": experiment.get("metricValue"),
    }
    for key, value in mapping.items():
        yield "{}{}={}".format(prefix, key, _value(value))


def encode_state(data: Dict[str, Any], sequence: int = 0) -> bytes:
    """Encode a validated state, retaining at most the two board-visible jobs."""
    snapshot = validate_snapshot(data).data
    server = snapshot["server"]
    ram = server["ram"]
    gpu = server["gpu"]
    load = server.get("loadAverage") or {}
    usage = snapshot["codexUsage"]
    freshness = snapshot.get("freshness") or {}
    fields: List[str] = []
    _put(fields, "V", 1)
    _put(fields, "SEQ", sequence)
    _put(fields, "UT", snapshot.get("updatedAt"))
    _put(fields, "SF", (freshness.get("server") or {}).get("state", "fresh"))
    _put(fields, "CF", (freshness.get("codexUsage") or {}).get("state", "fresh"))
    _put(fields, "SI", server.get("id"))
    _put(fields, "SD", server.get("displayName"))
    _put(fields, "SS", server.get("status"))
    _put(fields, "CPU", server.get("cpuPercent"))
    _put(fields, "RU", ram.get("usedGiB"))
    _put(fields, "RT", ram.get("totalGiB"))
    _put(fields, "RP", ram.get("usedPercent"))
    _put(fields, "GN", gpu.get("name"))
    _put(fields, "GU", gpu.get("utilizationPercent"))
    _put(fields, "GMU", gpu.get("memoryUsedGiB"))
    _put(fields, "GMT", gpu.get("memoryTotalGiB"))
    _put(fields, "GT", gpu.get("temperatureC"))
    _put(fields, "LA1", load.get("one"))
    _put(fields, "LA5", load.get("five"))
    _put(fields, "LA15", load.get("fifteen"))
    _put(fields, "UP", server.get("uptimeSeconds"))
    experiments = snapshot.get("experiments", [])[:2]
    _put(fields, "EC", len(experiments))
    for index, experiment in enumerate(experiments):
        fields.extend(_experiment_fields(index, experiment))
    _put(fields, "C5", usage.get("fiveHourPercent"))
    _put(fields, "C5R", usage.get("fiveHourReset"))
    _put(fields, "CW", usage.get("weekPercent"))
    _put(fields, "CWR", usage.get("weekReset"))
    _put(fields, "CRC", usage.get("resetCards"))
    _put(fields, "CPS", usage.get("providerStatus"))
    _put(fields, "ERR", snapshot.get("collectionError"))
    payload = "|".join(fields).encode("ascii")
    checksum = crc_hqx(payload, 0xFFFF)
    return b"RB1|L=" + str(len(payload)).encode("ascii") + b"|" + payload + b"|CRC=" + "{:04X}".format(checksum).encode("ascii") + b"\n"


def decode_frame(frame: bytes) -> Dict[str, Any]:
    if not frame.endswith(b"\n"):
        raise ProtocolError("frame is not terminated")
    raw = frame[:-1]
    parts = raw.split(b"|")
    if len(parts) < 5 or parts[0] != b"RB1" or not parts[1].startswith(b"L=") or not parts[-1].startswith(b"CRC="):
        raise ProtocolError("malformed RunBoard frame")
    try:
        payload_length = int(parts[1][2:])
        provided_crc = int(parts[-1][4:], 16)
    except ValueError:
        raise ProtocolError("invalid frame length or checksum")
    payload = b"|".join(parts[2:-1])
    if len(payload) != payload_length:
        raise ProtocolError("truncated or invalid frame length")
    if crc_hqx(payload, 0xFFFF) != provided_crc:
        raise ProtocolError("checksum mismatch")
    try:
        fields: Dict[str, str] = {}
        for item in payload.decode("ascii").split("|"):
            key, value = item.split("=", 1)
            fields[key] = value
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError("invalid field encoding")
    if _field(fields, "V") != "1":
        raise ProtocolError("unsupported protocol version")
    count = _number(fields, "EC", integer=True)
    if count is None or count < 0 or count > 2:
        raise ProtocolError("board experiment count must be 0, 1 or 2")
    server = {
        "id": _field(fields, "SI", "SERVER-01") or "SERVER-01",
        "displayName": _field(fields, "SD"), "hostname": None,
        "status": _field(fields, "SS", "offline") or "offline",
        "cpuPercent": _number(fields, "CPU"),
        "ram": {"usedGiB": _number(fields, "RU"), "totalGiB": _number(fields, "RT"), "usedPercent": _number(fields, "RP")},
        "loadAverage": {"one": _number(fields, "LA1"), "five": _number(fields, "LA5"), "fifteen": _number(fields, "LA15")},
        "uptimeSeconds": _number(fields, "UP", integer=True),
        "gpu": {"name": _field(fields, "GN"), "utilizationPercent": _number(fields, "GU"),
                "memoryUsedGiB": _number(fields, "GMU"), "memoryTotalGiB": _number(fields, "GMT"),
                "temperatureC": _number(fields, "GT")},
    }
    experiments = []
    for index in range(count):
        prefix = "E{}".format(index)
        experiment = {
            "name": _field(fields, prefix + "N", "unknown") or "unknown",
            "user": _field(fields, prefix + "U", "unknown") or "unknown",
            "status": _field(fields, prefix + "S", "UNKNOWN") or "UNKNOWN",
            "progress": _number(fields, prefix + "P"),
            "currentRound": _number(fields, prefix + "R", integer=True),
            "totalRound": _number(fields, prefix + "T", integer=True),
            "elapsedSeconds": _number(fields, prefix + "E", integer=True),
            "dataset": _field(fields, prefix + "D"), "seed": _number(fields, prefix + "SD", integer=True),
            "metricName": _field(fields, prefix + "MN"), "metricValue": _number(fields, prefix + "MV"),
        }
        experiments.append(experiment)
    freshness = {
        "server": {"state": _field(fields, "SF", "fresh") or "fresh"},
        "codexUsage": {"state": _field(fields, "CF", "fresh") or "fresh"},
    }
    data = {
        "schemaVersion": 1, "source": "live", "collectionError": _field(fields, "ERR"),
        "providers": {"server": "live", "codexUsage": _field(fields, "CPS", "unavailable") or "unavailable"},
        "sequence": _number(fields, "SEQ", integer=True),
        "freshness": freshness, "server": server, "experiments": experiments,
        "codexUsage": {
            "fiveHourPercent": _number(fields, "C5") or 0, "fiveHourReset": _field(fields, "C5R", "unknown") or "unknown",
            "weekPercent": _number(fields, "CW") or 0, "weekReset": _field(fields, "CWR", "unknown") or "unknown",
            "resetCards": _number(fields, "CRC", integer=True),
            "providerStatus": _field(fields, "CPS", "unavailable") or "unavailable",
        },
        "updatedAt": _field(fields, "UT", "unknown") or "unknown",
    }
    return validate_snapshot(data).data


class FrameDecoder:
    """Incremental decoder for partial and concatenated stdout/memory chunks."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> List[Dict[str, Any]]:
        self._buffer.extend(chunk)
        decoded = []
        while b"\n" in self._buffer:
            index = self._buffer.index(b"\n")
            frame = bytes(self._buffer[:index + 1])
            del self._buffer[:index + 1]
            if frame.strip():
                decoded.append(decode_frame(frame))
        return decoded
