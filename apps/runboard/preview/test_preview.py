import unittest

from apps.runboard.preview.runboard_preview import shorten


class PreviewFormattingTests(unittest.TestCase):
    def test_long_identity_is_ellipsized(self):
        self.assertEqual(shorten("experiment-name-that-is-too-long", 12), "experimen...")

    def test_short_identity_is_unchanged(self):
        self.assertEqual(shorten("fedavg", 12), "fedavg")


if __name__ == "__main__":
    unittest.main()
