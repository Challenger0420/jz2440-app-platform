"""Persistent live state sender for the RunBoard Host.

The one-shot state CLI remains useful for mock replay and diagnostics.  A real
live session, however, must keep its providers and aggregator alive so the
last-good snapshot and freshness clocks survive a transient provider failure.
This module deliberately stops at bytes written to an output stream; it has
no serial-port knowledge.
"""

from __future__ import annotations

from datetime import datetime
import sys
import time
from typing import BinaryIO, Callable, Iterable, Optional, TextIO, Tuple

from apps.runboard.shared.board_protocol import decode_frame, encode_state
from apps.runboard.shared.state_model import Snapshot

from .aggregator import RunBoardAggregator


Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


class PersistentLiveSender:
    """Reuse one Aggregator for every frame in a live session."""

    def __init__(self, aggregator: RunBoardAggregator) -> None:
        self.aggregator = aggregator

    @classmethod
    def from_root(cls, root):
        """Build live providers once, without opening a serial port."""
        # Import lazily to keep the state CLI's direct-script bootstrap simple
        # and to avoid making this reusable Host class own configuration.
        from .runboard_state_cli import build_aggregator

        return cls(build_aggregator(root, live=True, scenario=None))

    def collect_frame(
        self,
        sequence: int,
        now: Optional[datetime] = None,
        force: bool = False,
    ) -> Tuple[Snapshot, bytes]:
        """Collect according to configured provider cadence and encode RB1."""
        snapshot = self.aggregator.collect(now=now, force=force)
        return snapshot, encode_state(snapshot.data, sequence=sequence)

    def next_frame(self, sequence: int, now: Optional[datetime] = None) -> bytes:
        return self.collect_frame(sequence, now=now)[1]

    def run_requests(self, requests: Iterable[str], output: BinaryIO) -> int:
        """Serve sequence requests until stdin closes.

        Stdout contains only complete RB1 frames.  Diagnostics belong on
        stderr so a bridge cannot accidentally transmit logging as a frame.
        """
        count = 0
        for request in requests:
            value = request.strip()
            if not value:
                continue
            try:
                sequence = int(value)
            except ValueError:
                raise ValueError("live worker received an invalid sequence")
            _, frame = self.collect_frame(sequence)
            output.write(frame)
            output.flush()
            count += 1
        return count

    def run_dry_run(
        self,
        cycles: int,
        interval_seconds: float,
        output: TextIO,
        summary: Callable[[Snapshot, bytes, dict[str, object]], str],
        clock: Optional[Clock] = None,
        sleeper: Sleeper = time.sleep,
    ) -> int:
        """Run several live cycles without serial I/O or raw-frame output."""
        if cycles < 1:
            raise ValueError("cycles must be positive")
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be non-negative")
        clock = clock or (lambda: datetime.now().astimezone())
        for cycle in range(cycles):
            snapshot, frame = self.collect_frame(cycle, now=clock())
            decoded = decode_frame(frame)
            output.write("PERSISTENT_CYCLE={}\n".format(cycle))
            output.write(summary(snapshot, frame, decoded))
            output.write("\n")
            output.flush()
            if cycle + 1 < cycles and interval_seconds:
                sleeper(interval_seconds)
        return cycles


def run_worker(root, input_stream: TextIO = sys.stdin, output_stream: BinaryIO = sys.stdout.buffer) -> int:
    """Entry point for the long-lived Bridge worker."""
    sender = PersistentLiveSender.from_root(root)
    return sender.run_requests(input_stream, output_stream)
