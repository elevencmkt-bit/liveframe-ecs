import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from liveframe import recap, samples


class TestPlan(unittest.TestCase):
    def test_fills_duration_and_uses_every_clip(self):
        clips = [(Path(f"c{i}.mp4"), 10.0) for i in range(4)]
        segs = recap.plan(clips, 45, 2.0)
        self.assertAlmostEqual(sum(s.length for s in segs), 45)
        self.assertEqual({s.clip for s in segs}, {f"c{i}.mp4" for i in range(4)})
        for s in segs:
            self.assertLessEqual(s.start + s.length, 10.0)

    def test_short_clips_rejected(self):
        with self.assertRaises(recap.RecapError):
            recap.plan([(Path("a.mp4"), 1.0)], 45, 2.0)


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_renders_vertical_reel_with_audio(self):
        clips = self.tmp / "video"
        clips.mkdir()
        samples.write_clip(clips / "C0001.MP4", 6, "testsrc2")
        samples.write_clip(clips / "C0002.MP4", 6, "smptebars", portrait=True)
        lut = samples.write_cube(self.tmp / "w.cube")
        sig = samples.write_frame_png(self.tmp / "f.png", 1080, 1920)
        music = samples.write_track(self.tmp / "t.m4a", 40)
        out = self.tmp / "reel.mp4"
        recap.build(clips, out, music, lut, sig, duration=30, bpm=120)
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(out)],
            capture_output=True, text=True).stdout)
        v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["width"], v["height"]), (1080, 1920))
        self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
        self.assertAlmostEqual(float(info["format"]["duration"]), 30, delta=0.5)
        self.assertTrue(out.with_suffix(".json").exists())

    def test_duration_limits(self):
        with self.assertRaises(recap.RecapError):
            recap.build(self.tmp, self.tmp / "x.mp4", duration=90)


if __name__ == "__main__":
    unittest.main()
