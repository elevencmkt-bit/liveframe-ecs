import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from liveframe import notify, pipeline, samples
from liveframe.config import ConfigError, load_event
from liveframe.lut import apply_lut, load_cube


def _age(p: Path, seconds=10):
    t = time.time() - seconds
    os.utime(p, (t, t))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.s = self.tmp / "samples"
        self.s.mkdir()
        samples.write_cube(self.s / "warm.cube")
        samples.write_frame_png(self.s / "frame.png", 600, 400)
        samples.write_logo_png(self.s / "logo.png")
        self.gallery = self.tmp / "gallery"
        self.gallery.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def event(self, **over):
        cfg = {
            "curation": '"all"', "signature_mode": '"frame"', "signature": '"samples/frame.png"',
            "target": f'"{self.gallery}"', "long_edge": "1200",
        }
        cfg.update(over)
        f = self.tmp / "ev.toml"
        f.write_text(f"""
[event]
name = "Test Expo"
curation = {cfg['curation']}
[treatment]
lut = "samples/warm.cube"
signature = {cfg['signature']}
signature_mode = {cfg['signature_mode']}
long_edge = {cfg['long_edge']}
[publish]
backend = "folder"
target = {cfg['target']}
gallery_link = "https://example.com/g"
[notice]
client_whatsapp = "+1 (407) 555-0123"
""")
        ev = load_event(f)
        ev.ensure_dirs()
        return ev

    def drop(self, ev, name, **kw):
        p = samples.write_photo(ev.incoming / name, 900, 600, **kw)
        _age(p)
        return p


class TestLut(Base):
    def test_identity_cube_keeps_pixels(self):
        n = 5
        rows = ["LUT_3D_SIZE 5"] + [f"{r/(n-1)} {g/(n-1)} {b/(n-1)}"
                                    for b in range(n) for g in range(n) for r in range(n)]
        (self.s / "id.cube").write_text("\n".join(rows))
        im = Image.new("RGB", (4, 4), (200, 100, 50))
        self.assertEqual(apply_lut(im, self.s / "id.cube").getpixel((0, 0)), (200, 100, 50))

    def test_warm_cube_changes_color(self):
        im = Image.new("RGB", (4, 4), (128, 128, 128))
        r, g, b = apply_lut(im, self.s / "warm.cube").getpixel((0, 0))
        self.assertGreater(r, b)

    def test_bad_cube(self):
        (self.s / "bad.cube").write_text("LUT_3D_SIZE 3\n0 0 0\n")
        with self.assertRaises(ValueError):
            load_cube(str(self.s / "bad.cube"))


class TestPipeline(Base):
    def test_photo_flows_to_sent_and_gallery(self):
        ev = self.event()
        self.drop(ev, "DSC00001.JPG")
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.treated, st.sent), (1, 1))
        out = self.gallery / "test-expo_DSC00001.jpg"
        self.assertTrue(out.exists())
        self.assertTrue((ev.sent / out.name).exists())
        self.assertFalse(any(ev.ready.iterdir()))
        self.assertTrue((ev.originals / "DSC00001.JPG").exists())
        with Image.open(out) as im:
            self.assertEqual(max(im.size), 900)  # never upscaled

    def test_resize_and_signature_logo(self):
        ev = self.event(signature='"samples/logo.png"', signature_mode='"logo"', long_edge="500")
        self.drop(ev, "A.jpg")
        pipeline.run_once(ev, settle=0)
        with Image.open(self.gallery / "test-expo_A.jpg") as im:
            self.assertEqual(im.size, (500, 333))

    def test_idempotent_resend_and_restart(self):
        ev = self.event()
        p = self.drop(ev, "DSC00002.JPG")
        data = p.read_bytes()
        pipeline.run_once(ev, settle=0)
        # Camera sends the same file again (FTP retry) → nothing new.
        (ev.incoming / "DSC00002.JPG").write_bytes(data)
        _age(ev.incoming / "DSC00002.JPG")
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.treated, st.sent, st.duplicates), (0, 0, 1))
        self.assertEqual(len(list(self.gallery.iterdir())), 1)

    def test_same_name_different_photo_gets_unique_name(self):
        ev = self.event()
        self.drop(ev, "DSC00003.JPG", seed=1)
        pipeline.run_once(ev, settle=0)
        self.drop(ev, "DSC00003.JPG", seed=2)
        pipeline.run_once(ev, settle=0)
        self.assertEqual(len(list(self.gallery.iterdir())), 2)

    def test_offline_queue_then_recovers(self):
        ev = self.event(target=f'"{self.tmp / "offline"}"')  # folder missing = no internet
        self.drop(ev, "B.jpg")
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.treated, st.sent, st.retrying), (1, 0, 1))
        self.assertEqual(len(list(ev.ready.iterdir())), 1)
        (self.tmp / "offline").mkdir()
        with mock.patch("time.time", return_value=time.time() + 1000):
            st = pipeline.run_once(ev, settle=0)
        self.assertEqual(st.sent, 1)
        self.assertTrue((self.tmp / "offline" / "test-expo_B.jpg").exists())

    def test_flagged_curation(self):
        ev = self.event(curation='"flagged"')
        self.drop(ev, "rated.jpg", rating=3)
        self.drop(ev, "plain.jpg")
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.treated, st.skipped), (1, 1))
        self.assertTrue((ev.skipped / "plain.jpg").exists())

    def test_review_waits_for_levi_then_publishes(self):
        ev = self.event(curation='"review"')
        self.drop(ev, "R1.jpg", seed=1)
        self.drop(ev, "R2.jpg", seed=2)
        self.drop(ev, "R3.jpg", seed=3)
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.treated, st.to_review, st.sent), (3, 3, 0))
        self.assertEqual(len(list(self.gallery.iterdir())), 0)  # nothing goes out unchecked
        # Levi drags one to approved/, one to rejected/, leaves one in review/.
        os.replace(ev.review / "test-expo_R1.jpg", ev.approved / "test-expo_R1.jpg")
        os.replace(ev.review / "test-expo_R2.jpg", ev.rejected / "test-expo_R2.jpg")
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.approved, st.rejected, st.sent), (1, 1, 1))
        self.assertEqual([p.name for p in self.gallery.iterdir()], ["test-expo_R1.jpg"])
        self.assertTrue((ev.rejected / "test-expo_R2.jpg").exists())
        self.assertTrue((ev.review / "test-expo_R3.jpg").exists())
        # Restart: nothing is re-counted or re-sent.
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.approved, st.rejected, st.sent), (0, 0, 0))
        self.assertEqual(notify.run(ev).count, 1)  # notice counts only published photos
        # Changed mind: a rejected photo dragged to approved/ is published.
        os.replace(ev.rejected / "test-expo_R2.jpg", ev.approved / "test-expo_R2.jpg")
        self.assertEqual(pipeline.approve(ev), 1)  # approve the rest (R3) from the terminal
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.approved, st.sent), (2, 2))
        self.assertEqual(len(list(self.gallery.iterdir())), 3)

    def test_review_ignores_unknown_file(self):
        ev = self.event(curation='"review"')
        (ev.approved / "random.jpg").write_bytes(b"x")
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual((st.approved, st.sent), (0, 0))
        self.assertTrue((ev.approved / "random.jpg").exists())

    def test_video_and_raw_routed(self):
        ev = self.event()
        for n in ("C0001.MP4", "DSC0001.ARW"):
            (ev.incoming / n).write_bytes(b"x" * 10)
            _age(ev.incoming / n)
        pipeline.run_once(ev, settle=0)
        self.assertTrue((ev.video / "C0001.MP4").exists())
        self.assertTrue((ev.skipped / "DSC0001.ARW").exists())

    def test_unsettled_file_waits(self):
        ev = self.event()
        samples.write_photo(ev.incoming / "new.jpg", 300, 200)  # just written
        st = pipeline.run_once(ev, settle=5)
        self.assertEqual(st.treated, 0)
        self.assertTrue((ev.incoming / "new.jpg").exists())

    def test_corrupt_file_goes_to_failed(self):
        ev = self.event()
        (ev.incoming / "broken.jpg").write_bytes(b"not a jpeg")
        _age(ev.incoming / "broken.jpg", 120)
        st = pipeline.run_once(ev, settle=0)
        self.assertEqual(st.failed, 1)
        self.assertTrue((ev.failed / "broken.jpg").exists())


class TestNotice(Base):
    def test_counts_only_new_since_last_notice(self):
        ev = self.event()
        self.drop(ev, "1.jpg", seed=1)
        self.drop(ev, "2.jpg", seed=2)
        pipeline.run_once(ev, settle=0)
        n = notify.run(ev)
        self.assertEqual(n.count, 2)
        self.assertIn("+2 new photos from Test Expo", n.message)
        self.assertTrue(n.wa_link.startswith("https://wa.me/14075550123?text="))
        self.assertIsNone(notify.run(ev))  # nothing new → no notice
        self.drop(ev, "3.jpg", seed=3)
        pipeline.run_once(ev, settle=0)
        self.assertEqual(notify.run(ev).count, 1)


class TestConfig(Base):
    def test_rejects_bad_curation(self):
        with self.assertRaises(ConfigError):
            self.event(curation='"some"')


if __name__ == "__main__":
    unittest.main()
