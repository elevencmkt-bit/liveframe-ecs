"""Built-in FTP server for the camera (Sony A7 IV "FTP transfer", auto mode).

Set the camera up once (server = this Mac's IP on the travel router, port 2121, user `liveframe`,
password from the app's Settings, passive mode on). Every event after that needs nothing on the camera:
files land in ~/LiveFrame/inbox and the app routes them to the active event.

The screen shows "Camera connected" when the camera logs in (and for a while after its last file,
since some bodies reconnect per transfer). Credentials: the password lives in the Keychain, never here.
"""
from __future__ import annotations

import logging
import secrets as pysecrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import secrets

log = logging.getLogger("liveframe.ftp")

FTP_USER = "liveframe"
FTP_SECRET = "ftp:password"
RECENT = 120.0  # seconds after the last file during which the camera still counts as connected


@dataclass
class CameraStatus:
    sessions: int = 0
    address: str = ""
    since: float = 0.0
    last_seen: float = 0.0
    last_file: str = ""
    files: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def connected(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.sessions > 0 or (self.last_seen > 0 and now - self.last_seen < RECENT)

    def as_dict(self) -> dict:
        return {"connected": self.connected(), "address": self.address, "since": self.since,
                "last_seen": self.last_seen, "last_file": self.last_file, "files": self.files}


def ftp_password() -> str:
    pw = secrets.get(FTP_SECRET)
    if not pw:
        pw = pysecrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
        secrets.set(FTP_SECRET, pw)
    return pw


class CameraFTP:
    """pyftpdlib server in a background thread; writes to `inbox`."""

    def __init__(self, inbox: Path, port: int = 2121, host: str = "0.0.0.0",
                 passive_ports: range = range(60000, 60100), on_file=None):
        self.inbox = inbox
        self.port = port
        self.host = host
        self.passive_ports = passive_ports
        self.on_file = on_file
        self.status = CameraStatus()
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import ThreadedFTPServer

        self.inbox.mkdir(parents=True, exist_ok=True)
        auth = DummyAuthorizer()
        # e = change dir, l = list, w = store, m = mkdir (cameras create dated folders); no delete/rename.
        auth.add_user(FTP_USER, ftp_password(), str(self.inbox), perm="elwm")
        status, on_file = self.status, self.on_file

        class Handler(FTPHandler):
            def on_login(self, username):
                with status.lock:
                    status.sessions += 1
                    status.address = self.remote_ip
                    status.since = status.since if status.sessions > 1 else time.time()
                    status.last_seen = time.time()
                log.info("camera logged in from %s", self.remote_ip)

            def on_logout(self, username):
                self._lf_left()

            def on_disconnect(self):
                # on_disconnect also follows on_logout; count each session once
                self._lf_left()

            def _lf_left(self):
                if getattr(self, "_lf_gone", False) or not self.authenticated:
                    return
                self._lf_gone = True
                with status.lock:
                    status.sessions = max(0, status.sessions - 1)
                    status.last_seen = time.time()

            def on_file_received(self, file):
                with status.lock:
                    status.files += 1
                    status.last_file = Path(file).name
                    status.last_seen = time.time()
                if on_file:
                    on_file(Path(file))

            def on_incomplete_file_received(self, file):
                # connection dropped mid-file: drop the partial; the camera resends it
                Path(file).unlink(missing_ok=True)

        Handler.authorizer = auth
        Handler.passive_ports = self.passive_ports
        Handler.banner = "LiveFrame ECS ready."
        self._server = ThreadedFTPServer((self.host, self.port), Handler)
        self.port = self._server.address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"handle_exit": False}, daemon=True)
        self._thread.start()
        log.info("FTP server on %s:%d → %s", self.host, self.port, self.inbox)

    def stop(self) -> None:
        if self._server:
            self._server.close_all()
            self._server = None
