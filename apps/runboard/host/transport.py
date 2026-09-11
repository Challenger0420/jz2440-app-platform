"""Local transport seam for future board output; no serial implementation."""

from typing import List, Protocol, Union


class Transport(Protocol):
    def send(self, payload: bytes) -> None:
        ...


class MemoryTransport:
    def __init__(self) -> None:
        self.frames: List[bytes] = []

    def send(self, payload: Union[bytes, bytearray]) -> None:
        self.frames.append(bytes(payload))
