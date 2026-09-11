"""State-to-encoder-to-transport publishing without opening COM ports."""

from typing import Protocol

from apps.runboard.shared.board_protocol import encode_state
from apps.runboard.shared.state_model import Snapshot

from .transport import Transport


class StatePublisher:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def publish(self, snapshot: Snapshot, sequence: int = 0) -> bytes:
        frame = encode_state(snapshot.data, sequence=sequence)
        self.transport.send(frame)
        return frame
