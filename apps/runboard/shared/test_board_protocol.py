import unittest
from binascii import crc_hqx
from pathlib import Path

from apps.runboard.shared.board_protocol import FrameDecoder, ProtocolError, decode_frame, encode_state
from apps.runboard.shared.state_model import load_snapshot
from apps.runboard.host.publisher import StatePublisher
from apps.runboard.host.transport import MemoryTransport


ROOT = Path(__file__).resolve().parents[3]


class BoardProtocolTests(unittest.TestCase):
    def setUp(self):
        self.single = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "single.json")
        self.double = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "double.json")
        self.idle = load_snapshot(ROOT / "apps" / "runboard" / "shared" / "mocks" / "idle.json")

    def test_round_trip_supports_zero_one_and_two_experiments(self):
        for snapshot in (self.idle, self.single, self.double):
            decoded = decode_frame(encode_state(snapshot.data, sequence=7))
            self.assertEqual(len(decoded["experiments"]), len(snapshot.experiments))
            self.assertEqual(decoded["server"]["status"], snapshot.data["server"]["status"])
            self.assertEqual(decoded["codexUsage"]["resetCards"], snapshot.data["codexUsage"]["resetCards"])
            self.assertEqual(decoded["server"]["loadAverage"]["one"], snapshot.data["server"].get("loadAverage", {}).get("one"))

    def test_memory_transport_publisher_keeps_io_local(self):
        transport = MemoryTransport()
        frame = StatePublisher(transport).publish(self.single, sequence=3)
        self.assertEqual(transport.frames, [frame])

    def test_sequence_continuous_jump_and_duplicate_are_observable(self):
        sequences = [10, 11, 15, 15]
        decoded = [decode_frame(encode_state(self.idle.data, sequence=value)) for value in sequences]
        self.assertEqual([item["sequence"] for item in decoded], sequences)

    def test_checksum_error_and_truncated_frame(self):
        frame = encode_state(self.single.data)
        broken = bytearray(frame)
        broken[-6] = ord("0") if broken[-6] != ord("0") else ord("1")
        with self.assertRaises(ProtocolError):
            decode_frame(bytes(broken))
        with self.assertRaises(ProtocolError):
            decode_frame(frame[:-2] + b"\n")

    def test_partial_and_concatenated_frames(self):
        first = encode_state(self.idle.data, sequence=1)
        second = encode_state(self.double.data, sequence=2)
        decoder = FrameDecoder()
        output = []
        combined = first + second
        for offset in range(0, len(combined), 5):
            output.extend(decoder.feed(combined[offset:offset + 5]))
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0]["experiments"], [])
        self.assertEqual(len(output[1]["experiments"]), 2)

    def test_unknown_optional_values_use_null_semantics(self):
        data = dict(self.single.data)
        experiment = dict(data["experiments"][0])
        experiment.update({"progress": None, "currentRound": None, "totalRound": None, "dataset": None, "seed": None, "metricName": None, "metricValue": None})
        data["experiments"] = [experiment]
        decoded = decode_frame(encode_state(data))
        self.assertIsNone(decoded["experiments"][0]["progress"])
        self.assertIsNone(decoded["experiments"][0]["metricName"])

    def test_unknown_field_is_ignored(self):
        original = encode_state(self.idle.data)
        parts = original[:-1].split(b"|")
        payload = b"|".join(parts[2:-1]) + b"|ZZ=forward-compatible"
        crc = crc_hqx(payload, 0xFFFF)
        extended = b"RB1|L=" + str(len(payload)).encode("ascii") + b"|" + payload + b"|CRC=" + "{:04X}".format(crc).encode("ascii") + b"\n"
        decoded = decode_frame(extended)
        self.assertEqual(decoded["experiments"], [])

    def test_long_text_and_boundary_numbers_round_trip(self):
        data = dict(self.single.data)
        experiment = dict(data["experiments"][0])
        experiment.update({
            "name": "experiment-" + "x" * 180,
            "user": "user-" + "y" * 120,
            "progress": 0,
            "currentRound": 0,
            "totalRound": 1,
            "metricValue": 0,
        })
        data["experiments"] = [experiment]
        data["server"] = dict(data["server"])
        data["server"]["cpuPercent"] = 0
        data["server"]["ram"] = dict(data["server"]["ram"])
        data["server"]["ram"]["usedPercent"] = 100
        decoded = decode_frame(encode_state(data, sequence=4294967295))
        self.assertEqual(decoded["sequence"], 4294967295)
        self.assertEqual(decoded["experiments"][0]["progress"], 0)
        self.assertEqual(decoded["experiments"][0]["totalRound"], 1)


if __name__ == "__main__":
    unittest.main()
