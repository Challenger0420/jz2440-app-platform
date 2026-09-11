import unittest
from pathlib import Path


TARGET = Path(__file__).resolve().parent
SOURCE = (TARGET / "src" / "runboard.c").read_text(encoding="utf-8")
APPCTL = (TARGET.parents[2] / "platform" / "board" / "appctl" / "appctl").resolve()


class TargetLifecycleSourceTests(unittest.TestCase):
    def test_serial_termios_are_saved_and_restored(self):
        self.assertIn("serial_open(serial_path,&saved_serial)", SOURCE)
        self.assertIn("ioctl(fd,TCGETS,&t)", SOURCE)
        self.assertIn("t.c_cflag&=~CRTSCTS", SOURCE)
        self.assertIn("serial_close(fd,&saved_serial)", SOURCE)
        self.assertIn("ioctl(fd,TCSETS,saved)", SOURCE)

    def test_quit_path_releases_serial_before_framebuffer(self):
        quit_position = SOURCE.index("if(is_quit(frame))")
        self.assertLess(SOURCE.index("serial_close(fd,&saved_serial)", quit_position),
                        SOURCE.index("fb_close(&fb)", quit_position))

    def test_appctl_owns_lifecycle_markers_and_qtopia_recovery(self):
        appctl = APPCTL.read_text(encoding="utf-8")
        self.assertIn("printf '<APPREADY|%s>\\n'", appctl)
        self.assertIn("printf '<APPSTOP|%s|RC=%s>\\n'", appctl)
        self.assertIn("cleanup_after_app", appctl)
        self.assertIn("restore_qtopia", appctl)


if __name__ == "__main__":
    unittest.main()
