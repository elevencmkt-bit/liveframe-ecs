"""V2 app: events, camera FTP, review by click, Hub gallery with offline queue, notices."""
import ftplib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from liveframe import samples
from liveframe.app import make_handler
from liveframe.ftpserver import FTP_USER, CameraFTP, ftp_password
from liveframe.studio import Studio, StudioError

KEY = "lf_" + "k" * 32


class FakeHub:
    """Stands in for hub.elevencreativestudio.com/api/liveframe (same contract as the Hub route)."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.posts = 0
        self.lose_next_response = False
        hub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("authorization") != f"Bearer {KEY}":
                    return self._out(401, {"error": "invalid key"})
                self._out(200, {"title": "Expo", "published": True, "status": "publicada",
                                "link": "https://hub.example/g/expo-abc123"})

            def do_POST(self):
                if self.headers.get("authorization") != f"Bearer {KEY}":
                    return self._out(401, {"error": "invalid key"})
                name = urllib.request.unquote(self.headers.get("x-file-name", ""))
                data = self.rfile.read(int(self.headers.get("content-length", 0)))
                hub.posts += 1
                existed = name in hub.files
                hub.files.setdefault(name, data)
                if hub.lose_next_response:  # stored, but the answer never reaches the Mac
                    hub.lose_next_response = False
                    return self._out(502, {"error": "gateway"})
                self._out(200 if existed else 201, {"id": name, "existed": existed})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = {k: os.environ.get(k) for k in ("LIVEFRAME_HOME", "LIVEFRAME_SECRETS", "LIVEFRAME_HUB_KEY")}
        os.environ["LIVEFRAME_HOME"] = str(self.tmp / "LiveFrame")
        os.environ["LIVEFRAME_SECRETS"] = "file"
        os.environ.pop("LIVEFRAME_HUB_KEY", None)
        self.hub = FakeHub()
        self.st = Studio()
        self.st.save_settings(hub_url=self.hub.url)
        assets = self.st.home / "assets"
        samples.write_cube(assets / "warm.cube")
        samples.write_frame_png(assets / "frame.png", 600, 400)

    def tearDown(self):
        self.hub.stop()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_event(self, **over):
        data = {"name": "Orlando Expo", "client": "Acme", "lut": "warm.cube", "signature": "frame.png",
                "signature_mode": "frame", "long_edge": "1200", "curation": "review",
                "client_whatsapp": "+1 (407) 555-0123", "backend": "hub", "hub_key": KEY}
        data.update(over)
        return self.st.save_event(data)

    shots = 0

    def shoot(self, name, **kw):
        Base.shots += 1  # every frame is a different picture (same content = same photo, by design)
        kw.setdefault("seed", Base.shots)
        p = samples.write_photo(self.st.inbox / name, 900, 600, **kw)
        t = time.time() - 30
        os.utime(p, (t, t))
        return p

    def tick(self, online=True):
        return self.st.tick(settle=0, route_settle=0, online=online)


class TestEvents(Base):
    def test_create_event_keeps_key_out_of_files(self):
        ev = self.new_event()
        self.assertEqual(ev["slug"], "orlando-expo")
        self.assertEqual(ev["status"], "draft")
        self.assertTrue(ev["hub_key"])
        self.assertEqual(ev["gallery_link"], "https://hub.example/g/expo-abc123")  # asked the Hub
        self.assertEqual(ev["client_whatsapp"], "14075550123")
        toml = (self.st.home / "events" / "orlando-expo.toml").read_text()
        self.assertNotIn(KEY, toml)
        self.assertNotIn(KEY, json.dumps(self.st.events()))
        secrets_file = self.st.home / ".secrets.json"
        self.assertEqual(secrets_file.stat().st_mode & 0o777, 0o600)

    def test_same_name_gets_new_slug_and_presets_reuse_look(self):
        self.new_event()
        self.assertEqual(self.new_event()["slug"], "orlando-expo-2")
        pre = self.st.presets()[0]
        self.assertEqual((pre["lut"], pre["signature"], pre["long_edge"]), ("warm.cube", "frame.png", 1200))

    def test_validation(self):
        with self.assertRaises(StudioError):
            self.new_event(hub_key="")          # Hub gallery needs its key
        with self.assertRaises(StudioError):
            self.new_event(hub_key="password")  # not a LiveFrame key
        with self.assertRaises(StudioError):
            self.new_event(lut="missing.cube")
        with self.assertRaises(StudioError):
            self.new_event(name=" ")

    def test_drive_fallback_backend(self):
        gallery = self.tmp / "drive"
        gallery.mkdir()
        self.new_event(backend="folder", hub_key="", target=str(gallery), gallery_link="https://drive/x")
        self.st.set_status("orlando-expo", "live")
        self.shoot("DSC1.JPG")
        self.tick()
        self.st.decide("orlando-expo", ["orlando-expo_DSC1.jpg"], "approve")
        self.tick()
        self.assertTrue((gallery / "orlando-expo_DSC1.jpg").exists())

    def test_one_live_event_at_a_time(self):
        self.new_event(name="A")
        self.new_event(name="B")
        self.st.set_status("a", "live")
        self.st.set_status("b", "live")
        status = {e["slug"]: e["status"] for e in self.st.events()}
        self.assertEqual(status, {"a": "paused", "b": "live"})
        self.assertEqual(self.st.active_event().slug, "b")


class TestLiveFlow(Base):
    """The acceptance run: create → photos arrive treated → approve 3 / reject 1 → Hub gallery → notice."""

    def test_full_event(self):
        self.new_event()
        slug = "orlando-expo"
        self.shoot("DSC0001.JPG")  # paused: stays in the inbox
        self.tick()
        self.assertEqual(self.st.state()["inbox_waiting"], 1)
        self.st.set_status(slug, "live")
        for i in range(2, 5):
            self.shoot(f"DSC000{i}.JPG")
        self.tick()
        names = [p["name"] for p in self.st.photos(slug, "review")]
        self.assertEqual(len(names), 4)
        self.assertEqual(self.hub.posts, 0)  # nothing leaves before Levi approves
        self.st.decide(slug, names[:3], "approve")
        self.st.decide(slug, names[3:], "reject")
        self.tick()
        self.assertEqual(sorted(self.hub.files), sorted(names[:3]))
        c = self.st.summary(self.st.event(slug))["counters"]
        self.assertEqual((c["review"], c["queue"], c["sent"], c["rejected"]), (0, 0, 3, 1))
        n = self.st.notify(slug)
        self.assertEqual(n["count"], 3)
        self.assertIn("+3 new photos from Orlando Expo", n["message"])
        self.assertIn("hub.example/g/expo-abc123", n["message"])
        self.assertTrue(n["wa_link"].startswith("https://wa.me/14075550123?text="))
        self.assertEqual(self.st.notify(slug)["count"], 0)  # nothing new since
        start = self.st.notify(slug, "start")
        self.assertIn("live photo gallery", start["message"])

    def test_offline_queue_drains_without_duplicates(self):
        self.new_event()
        slug = "orlando-expo"
        self.st.set_status(slug, "live")
        for i in range(1, 4):
            self.shoot(f"DSC00{i}.JPG")
        self.tick(online=False)                                  # camera ↔ Mac works offline
        names = [p["name"] for p in self.st.photos(slug, "review")]
        self.assertEqual(len(names), 3)
        self.st.decide(slug, names, "approve")
        self.tick(online=False)
        self.assertEqual(self.st.counters(self.st.event(slug))["queue"], 3)
        self.assertEqual(self.hub.posts, 0)
        # internet "back" but the Hub answer for the first photo is lost after it was stored
        self.hub.lose_next_response = True
        self.tick()
        self.assertEqual(len(self.hub.files), 1)
        self.assertEqual(self.st.counters(self.st.event(slug))["queue"], 3)
        # retry later (backoff) → the Hub already has it: no duplicate, the rest goes out
        import sqlite3
        db = sqlite3.connect(str(self.st.event(slug).state_db))
        db.execute("UPDATE files SET next_try=0")
        db.commit()
        db.close()
        self.tick()
        self.assertEqual(sorted(self.hub.files), sorted(names))
        self.assertEqual(self.hub.posts, 4)  # 3 photos + 1 retry of the lost answer
        self.assertEqual(self.st.counters(self.st.event(slug))["sent"], 3)
        self.tick()
        self.assertEqual(self.hub.posts, 4)

    def test_close_event_still_drains_queue(self):
        self.new_event()
        slug = "orlando-expo"
        self.st.set_status(slug, "live")
        self.shoot("DSC9.JPG")
        self.tick(online=False)
        self.st.decide(slug, ["orlando-expo_DSC9.jpg"], "approve")
        self.st.set_status(slug, "closed")
        self.assertIsNone(self.st.active_event())
        with self.assertRaises(StudioError):
            self.st.set_status(slug, "live")
        self.tick()
        self.assertIn("orlando-expo_DSC9.jpg", self.hub.files)

    def test_rejected_can_be_approved_after_all(self):
        self.new_event()
        slug = "orlando-expo"
        self.st.set_status(slug, "live")
        self.shoot("DSC5.JPG")
        self.tick()
        self.st.decide(slug, ["orlando-expo_DSC5.jpg"], "reject")
        self.tick()
        self.assertEqual(self.hub.posts, 0)
        r = self.st.decide(slug, ["orlando-expo_DSC5.jpg"], "approve")
        self.assertEqual(r["moved"], ["orlando-expo_DSC5.jpg"])
        self.tick()
        self.assertIn("orlando-expo_DSC5.jpg", self.hub.files)

    def test_decide_ignores_paths(self):
        self.new_event()
        r = self.st.decide("orlando-expo", ["../../etc/passwd", "nope.jpg"], "approve")
        self.assertEqual(r["moved"], [])

    def test_tether_folder(self):
        tether = self.tmp / "tether"
        tether.mkdir()
        self.st.save_settings(tether_folder=str(tether))
        self.new_event()
        self.st.set_status("orlando-expo", "live")
        p = samples.write_photo(tether / "CABLE1.JPG", 900, 600)
        os.utime(p, (time.time() - 30, time.time() - 30))
        self.tick()
        self.assertEqual([x["name"] for x in self.st.photos("orlando-expo")], ["orlando-expo_CABLE1.jpg"])


class TestCameraFTP(Base):
    def test_camera_detected_and_file_routed(self):
        ftp = CameraFTP(self.st.inbox, port=0, host="127.0.0.1", passive_ports=range(61000, 61050))
        ftp.start()
        self.st.camera = ftp.status
        try:
            self.assertFalse(self.st.state()["camera"]["connected"])
            cam = ftplib.FTP()
            cam.connect("127.0.0.1", ftp.port, timeout=10)
            with self.assertRaises(ftplib.error_perm):
                cam.login(FTP_USER, "wrong")
            cam.login(FTP_USER, ftp_password())
            time.sleep(0.2)
            self.assertTrue(self.st.state()["camera"]["connected"])
            buf = io.BytesIO()
            from PIL import Image
            Image.new("RGB", (800, 600), (120, 90, 60)).save(buf, "JPEG")
            buf.seek(0)
            cam.storbinary("STOR DSC07001.JPG", buf)
            with self.assertRaises(ftplib.error_perm):
                cam.delete("DSC07001.JPG")  # the camera can't delete anything
            cam.quit()
            time.sleep(0.2)
            st = self.st.state()["camera"]
            self.assertEqual((st["files"], st["last_file"]), (1, "DSC07001.JPG"))
            self.assertTrue(st["connected"])  # recently seen
            self.assertTrue((self.st.inbox / "DSC07001.JPG").exists())
            self.new_event()
            self.st.set_status("orlando-expo", "live")
            self.tick()
            self.assertEqual(len(self.st.photos("orlando-expo")), 1)
        finally:
            ftp.stop()


class TestScreen(Base):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.st))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None, headers=None):
        h = {"content-type": "application/json", "x-liveframe": "1"} if body is not None else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=h, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_screen_flow(self):
        code, html = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"LiveFrame", html)
        code, _ = self.call("/api/events", {"name": "X"}, headers={"x-liveframe": "0"})
        self.assertEqual(code, 403)  # no custom header → a website can't drive the app
        code, _ = self.call("/api/state", headers={"host": "evil.example"})
        self.assertEqual(code, 403)
        code, body = self.call("/api/events", {"name": "Orlando Expo", "lut": "warm.cube", "signature": "frame.png",
                                               "backend": "hub", "hub_key": KEY, "long_edge": "1200"})
        self.assertEqual(code, 201, body)
        self.assertEqual(self.call("/api/events/orlando-expo/play", {})[0], 200)
        self.shoot("DSC1.JPG")
        self.tick()
        code, body = self.call("/api/events/orlando-expo/photos?folder=review")
        name = json.loads(body)["photos"][0]["name"]
        code, thumb = self.call(f"/thumb/orlando-expo/{name}?s=200")
        self.assertEqual((code, thumb[:2]), (200, b"\xff\xd8"))
        code, body = self.call("/api/events/orlando-expo/decide", {"names": [name], "decision": "approve"})
        self.assertEqual(json.loads(body)["moved"], [name])
        self.tick()
        state = json.loads(self.call("/api/state")[1])
        self.assertEqual(state["active"], "orlando-expo")
        ev = json.loads(self.call("/api/events/orlando-expo")[1])
        self.assertEqual(ev["counters"]["sent"], 1)
        code, body = self.call("/api/events/orlando-expo/notify", {"kind": "update"})
        self.assertEqual(json.loads(body)["count"], 1)
        self.assertEqual(self.call("/thumb/orlando-expo/..%2F..%2Fsettings.json")[0], 404)


if __name__ == "__main__":
    unittest.main()
