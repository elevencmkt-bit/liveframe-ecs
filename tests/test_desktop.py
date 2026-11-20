import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liveframe import __version__, desktop, samples, tools


class TestDataFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        support = self.tmp / "Application Support" / "LiveFrame"
        self.p = [mock.patch.object(desktop, "SUPPORT", support),
                  mock.patch.object(desktop, "POINTER", support / "location.json")]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_open_has_no_folder(self):
        self.assertIsNone(desktop.saved_home())

    def test_chosen_folder_is_remembered_outside_the_app(self):
        chosen = self.tmp / "External" / "LiveFrame data"
        desktop.save_home(chosen)
        self.assertEqual(desktop.saved_home(), chosen.resolve())
        self.assertTrue(chosen.is_dir())
        self.assertTrue(desktop.POINTER.exists())

    def test_broken_pointer_asks_again(self):
        desktop.SUPPORT.mkdir(parents=True)
        desktop.POINTER.write_text("not json")
        self.assertIsNone(desktop.saved_home())

    def test_version_flag(self):
        with mock.patch("builtins.print") as pr:
            self.assertEqual(desktop.main(["--version"]), 0)
        pr.assert_called_with(__version__)


class TestTools(unittest.TestCase):
    def test_duration_without_ffprobe(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            clip = samples.write_clip(tmp / "c.mp4", 3)
            with mock.patch.object(tools, "ffprobe", return_value=None):
                d = tools.media_duration(clip)
            self.assertAlmostEqual(d, 3.0, delta=0.1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_frozen_app_prefers_bundled_ffmpeg(self):
        tools.ffmpeg.cache_clear()
        try:
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(tools, "_find", side_effect=AssertionError("must not look at PATH")):
                self.assertIn("imageio_ffmpeg", tools.ffmpeg())
        finally:
            tools.ffmpeg.cache_clear()
