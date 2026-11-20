"""LiveFrame ECS app: a local screen in the browser over the live engine.

    python3 -m liveframe app            → http://127.0.0.1:8711 (opens by itself)

Works offline: the screen, the camera FTP and treatment run on the Mac; only the gallery
upload needs internet, and it waits in the queue while there is none.
The screen listens on 127.0.0.1 only. The FTP server listens on the network (the camera's side).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import ConfigError
from .ftpserver import FTP_USER, CameraFTP, ftp_password
from .studio import Studio, StudioError

log = logging.getLogger("liveframe.app")
WEB = Path(__file__).parent / "web"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def make_handler(studio: Studio, ftp: CameraFTP | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LiveFrame"

        def log_message(self, fmt, *args):  # quiet; errors still go to the log
            log.debug(fmt, *args)

        # helpers ------------------------------------------------------
        def _host_ok(self) -> bool:
            # DNS-rebinding guard: only answer to the Mac itself
            host = (self.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
            return host in LOCAL_HOSTS

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _file(self, path: Path, ctype: str | None = None, cache: bool = False):
            data = path.read_bytes()
            self._send(200, data, ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                       {"cache-control": "private, max-age=300"} if cache else None)

        def _body(self) -> dict:
            n = int(self.headers.get("content-length") or 0)
            if n > 1_000_000:
                raise StudioError("request too large")
            raw = self.rfile.read(n) if n else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except ValueError as exc:
                raise StudioError("bad JSON") from exc
            if not isinstance(data, dict):
                raise StudioError("bad JSON")
            return data

        # routes -------------------------------------------------------
        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "forbidden"}, 403)
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            parts = [p for p in u.path.split("/") if p]
            try:
                if u.path in ("/", "/index.html"):
                    return self._file(WEB / "index.html", "text/html; charset=utf-8")
                if u.path == "/api/state":
                    st = studio.state()
                    if ftp:
                        st["ftp"] = {"port": ftp.port, "user": FTP_USER}
                    return self._json(st)
                if u.path == "/api/events":
                    return self._json({"events": studio.events(), "presets": studio.presets(),
                                       "assets": studio.assets()})
                if u.path == "/api/camera-setup":
                    # Shown on demand for the one-time camera setup; local screen only.
                    return self._json({"user": FTP_USER, "password": ftp_password(),
                                       "port": ftp.port if ftp else None, "addresses": _lan_addresses()})
                if len(parts) == 3 and parts[:2] == ["api", "events"]:
                    return self._json(studio.summary(studio.event(parts[2])))
                if len(parts) == 4 and parts[:2] == ["api", "events"] and parts[3] == "photos":
                    return self._json({"photos": studio.photos(parts[2], q.get("folder", "review"))})
                if len(parts) == 3 and parts[0] == "thumb":
                    t = studio.thumbnail(parts[1], parts[2], int(q.get("s", 480)))
                    return self._file(t, "image/jpeg", cache=True) if t else self._json({"error": "not found"}, 404)
                if len(parts) == 3 and parts[0] == "photo":
                    p = studio.find_photo(parts[1], parts[2])
                    return self._file(p, "image/jpeg") if p else self._json({"error": "not found"}, 404)
                if len(parts) == 2 and parts[0] == "recap":
                    f = studio.event(parts[1]).root / "recap" / f"{parts[1]}_recap.mp4"
                    return self._file(f, "video/mp4") if f.exists() else self._json({"error": "not found"}, 404)
                return self._json({"error": "not found"}, 404)
            except (StudioError, ConfigError) as exc:
                return self._json({"error": str(exc)}, 400)

        do_HEAD = do_GET

        def do_POST(self):
            # Custom header = a browser on another site can't post here without a CORS preflight we never answer.
            if not self._host_ok() or self.headers.get("x-liveframe") != "1":
                return self._json({"error": "forbidden"}, 403)
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                body = self._body()
                if parts == ["api", "events"]:
                    return self._json(studio.save_event(body), 201)
                if parts == ["api", "settings"]:
                    s = studio.save_settings(**{k: body[k] for k in ("hub_url", "tether_folder") if k in body})
                    return self._json(s)
                if len(parts) == 4 and parts[:2] == ["api", "events"]:
                    slug, action = parts[2], parts[3]
                    if action == "edit":
                        return self._json(studio.save_event(body, slug))
                    if action in ("play", "pause", "close"):
                        status = {"play": "live", "pause": "paused", "close": "closed"}[action]
                        return self._json(studio.set_status(slug, status))
                    if action == "decide":
                        names = body.get("names") or []
                        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                            raise StudioError("names must be a list")
                        return self._json(studio.decide(slug, names[:500], str(body.get("decision"))))
                    if action == "notify":
                        return self._json(studio.notify(slug, str(body.get("kind", "update"))))
                    if action == "recap":
                        return self._json(studio.start_recap(slug, body.get("music"), float(body.get("bpm") or 120),
                                                             float(body.get("duration") or 45)))
                    if action == "gallery":
                        return self._json(studio.refresh_gallery(slug))
                return self._json({"error": "not found"}, 404)
            except (StudioError, ConfigError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)

    return Handler


def _lan_addresses() -> list[str]:
    """This Mac's IPv4 addresses (the one on the travel router goes in the camera)."""
    import socket
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
        out.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in out if not a.startswith("127."))


class Server:
    """The running app: screen (HTTP on 127.0.0.1), camera FTP and the live engine."""

    def __init__(self, port: int = 8711, ftp_port: int | None = None, studio: Studio | None = None):
        self.studio = studio or Studio()
        self.ftp = None
        try:
            self.ftp = CameraFTP(self.studio.inbox, port=ftp_port if ftp_port is not None
                                 else int(self.studio.settings()["ftp_port"]))
            self.ftp.start()
            self.studio.camera = self.ftp.status
        except Exception as exc:  # e.g. port taken: the app still works with tethering
            log.error("FTP server not started (%s); camera over cable only", exc)
            self.ftp = None
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.studio, self.ftp))
        except OSError:
            if self.ftp:
                self.ftp.stop()
            raise
        self.studio.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        log.info("LiveFrame ECS %s → %s (data: %s)", __version__, self.url, self.studio.home)

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.httpd.serve_forever, name="liveframe-http", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self.studio.stop()
        if self.ftp:
            self.ftp.stop()
        self.httpd.shutdown()
        self.httpd.server_close()


def serve(port: int = 8711, ftp_port: int | None = None, open_browser: bool = True,
          studio: Studio | None = None) -> None:
    srv = Server(port, ftp_port, studio)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(srv.url)).start()
    t = srv.start_background()
    try:
        while t.is_alive():
            t.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
