"""LiveFrame app core: events, the live engine, camera input and Levi's decisions.

The web screen (app.py) is a thin layer over this. Everything lives in the LiveFrame home
folder (~/LiveFrame, or LIVEFRAME_HOME):

    settings.json         Hub address, FTP port, tether folder, active event (no secrets)
    inbox/                where the camera's FTP uploads land (routed to the live event)
    assets/               LUTs (.cube), signature PNGs, licensed tracks
    events/<slug>.toml    one file per event (the V1 format + [app] status)
    work/<slug>/          the event's folders (incoming, review, sent, ...) and ledger

One event is live at a time (one camera). Pausing keeps camera files in inbox/ until play.
Closing stops intake; anything still in the queue keeps uploading until it is empty.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from . import hub, notify, pipeline, recap, secrets
from .config import DEFAULT_HUB_URL, ConfigError, Event, load_event
from .state import Ledger

log = logging.getLogger("liveframe.studio")

STATUSES = ("draft", "live", "paused", "closed")
PHOTO_FOLDERS = ("review", "approved", "ready", "sent", "rejected")
ASSET_KINDS = {"lut": {".cube"}, "signature": {".png"}, "music": {".m4a", ".mp3", ".wav", ".aac"}}
START_TEMPLATE = ("Hi! Your live photo gallery for {event} is ready: {link}\n"
                  "New photos will keep arriving during the session.")
SETTLE = 2.0


class StudioError(ValueError):
    pass


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)  # JSON strings are valid TOML basic strings


def write_toml(path: Path, sections: dict[str, dict]) -> None:
    lines = ["# LiveFrame ECS event (written by the app). No passwords or keys here."]
    for name, values in sections.items():
        lines.append(f"\n[{name}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in values.items() if v is not None and v != ""]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Net:
    online: bool = False
    checked: float = 0.0


class Studio:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else secrets.home()
        for d in ("inbox", "assets", "events", "work"):
            (self.home / d).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.net = Net()
        self.camera = None              # ftpserver.CameraStatus when the FTP server runs
        self.recaps: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # settings ----------------------------------------------------------
    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"

    def settings(self) -> dict:
        base = {"hub_url": DEFAULT_HUB_URL, "ftp_port": 2121, "tether_folder": "", "active": ""}
        try:
            base.update(json.loads(self.settings_file.read_text()))
        except (FileNotFoundError, ValueError):
            pass
        return base

    def save_settings(self, **changes) -> dict:
        with self.lock:
            s = self.settings()
            allowed = {"hub_url", "ftp_port", "tether_folder", "active"}
            s.update({k: v for k, v in changes.items() if k in allowed})
            tmp = self.settings_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(s, indent=2))
            os.replace(tmp, self.settings_file)
            return s

    @property
    def inbox(self) -> Path:
        return self.home / "inbox"

    # assets ------------------------------------------------------------
    def assets(self) -> dict[str, list[str]]:
        files = sorted(p.name for p in (self.home / "assets").iterdir() if p.is_file())
        return {k: [f for f in files if Path(f).suffix.lower() in ext] for k, ext in ASSET_KINDS.items()}

    def asset_path(self, value: str | None, kind: str) -> Path | None:
        if not value:
            return None
        p = Path(value).expanduser()
        if not p.is_absolute():
            p = self.home / "assets" / value
        if p.suffix.lower() not in ASSET_KINDS[kind] or not p.is_file():
            raise StudioError(f"{kind} file not found: {value}")
        return p

    # events ------------------------------------------------------------
    def event_file(self, slug: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,60}", slug or ""):
            raise StudioError("unknown event")
        return self.home / "events" / f"{slug}.toml"

    def event(self, slug: str) -> Event:
        f = self.event_file(slug)
        if not f.exists():
            raise StudioError("unknown event")
        return load_event(f)

    @staticmethod
    def status_of(ev: Event) -> str:
        return ev.extra.get("app", {}).get("status", "draft")

    def counters(self, ev: Event) -> dict:
        out = {"review": 0, "approved": 0, "queue": 0, "sent": 0, "rejected": 0, "failed": 0,
               "videos": 0, "retry_error": ""}
        if not ev.state_db.exists():
            return out
        led = Ledger(ev.state_db)
        try:
            for k in ("review", "sent", "rejected", "failed"):
                out[k] = led.count(k)
            out["videos"] = led.count("video")
            out["queue"] = led.count("ready")
            row = led.conn.execute("SELECT error FROM files WHERE status='ready' AND error IS NOT NULL"
                                   " ORDER BY next_try DESC LIMIT 1").fetchone()
            out["retry_error"] = row[0] if row else ""
            out["last_notice"] = led.last_notice_at()
            out["since_notice"] = led.sent_since(out["last_notice"])
        finally:
            led.close()
        # approved by Levi but not picked up by the engine yet → part of the queue
        if ev.approved.is_dir():
            n = sum(1 for p in ev.approved.iterdir() if p.is_file() and not p.name.startswith("."))
            out["approved"] = n
            out["queue"] += n
        return out

    def summary(self, ev: Event) -> dict:
        app = ev.extra.get("app", {})
        return {"slug": ev.slug, "name": ev.name, "client": ev.client, "status": self.status_of(ev),
                "created": app.get("created", 0), "backend": ev.publish_backend,
                "gallery_link": ev.gallery_link, "client_whatsapp": ev.client_whatsapp,
                "curation": ev.curation, "long_edge": ev.long_edge,
                "lut": ev.lut.name if ev.lut else "", "signature": ev.signature.name if ev.signature else "",
                "signature_mode": ev.signature_mode,
                "hub_key": ev.publish_backend == "hub" and secrets.has(hub.key_name(ev)),
                "counters": self.counters(ev)}

    def events(self) -> list[dict]:
        out = []
        for f in (self.home / "events").glob("*.toml"):
            try:
                out.append(self.summary(load_event(f)))
            except (ConfigError, OSError, ValueError) as exc:
                out.append({"slug": f.stem, "name": f.stem, "status": "error", "error": str(exc), "created": 0})
        return sorted(out, key=lambda e: e.get("created", 0), reverse=True)

    def presets(self) -> list[dict]:
        """Past events' look and delivery settings, newest first (no secrets)."""
        keys = ("lut", "signature", "signature_mode", "long_edge", "curation", "backend", "client")
        return [{"from": e["name"], **{k: e.get(k) for k in keys}} for e in self.events() if e["status"] != "error"]

    def save_event(self, data: dict, slug: str | None = None) -> dict:
        """Creates (slug=None) or edits an event from the New event form."""
        name = str(data.get("name", "")).strip()
        if not name:
            raise StudioError("Give the event a name.")
        with self.lock:
            if slug is None:
                base = slugify(name) or "event"
                slug, n = base, 2
                while self.event_file(slug).exists():
                    slug, n = f"{base}-{n}", n + 1
                app = {"status": "draft", "created": int(time.time())}
            else:
                app = dict(self.event(slug).extra.get("app", {}))
            backend = data.get("backend") or "hub"
            if backend not in ("hub", "folder", "rclone"):
                raise StudioError("Unknown gallery type.")
            mode = data.get("signature_mode") or "frame"
            lut = self.asset_path(data.get("lut"), "lut")
            sig = self.asset_path(data.get("signature"), "signature")
            sig_p = self.asset_path(data.get("signature_portrait"), "signature")
            if mode != "none" and not sig:
                raise StudioError("Choose the event signature PNG (or signature = none).")
            try:
                long_edge = int(data.get("long_edge") or 2048)
            except ValueError as exc:
                raise StudioError("Output size must be a number of pixels.") from exc
            whatsapp = re.sub(r"\D", "", str(data.get("client_whatsapp", "")))
            if whatsapp and not 10 <= len(whatsapp) <= 15:
                raise StudioError("Client WhatsApp: digits with country code, e.g. 1 407 555 0123.")
            key = str(data.get("hub_key", "")).strip()
            if key and not re.fullmatch(r"lf_[A-Za-z0-9_-]{20,80}", key):
                raise StudioError("That doesn't look like a LiveFrame key from the Hub (starts with lf_).")
            if backend == "hub" and not key and not secrets.has(f"hub:{slug}"):
                raise StudioError("Paste the gallery's LiveFrame key (Hub → Galleries → the gallery → LiveFrame).")
            if backend != "hub" and not data.get("target"):
                raise StudioError("Drive fallback: give the Drive folder (or rclone remote).")
            gallery_link = str(data.get("gallery_link", "")).strip()
            if key:
                secrets.set(f"hub:{slug}", key)
            write_toml(self.event_file(slug), {
                "event": {"name": name, "slug": slug, "client": str(data.get("client", "")).strip(),
                          "root": str(self.home / "work" / slug),
                          "curation": data.get("curation") or "review", "min_rating": 1},
                "treatment": {"lut": str(lut) if lut else "", "signature": str(sig) if sig else "",
                              "signature_portrait": str(sig_p) if sig_p else "",
                              "signature_mode": mode, "long_edge": long_edge, "jpeg_quality": 90},
                "publish": {"backend": backend, "hub_url": self.settings()["hub_url"], "hub_key_ref": slug,
                            "target": str(data.get("target", "")) if backend != "hub" else "",
                            "gallery_link": gallery_link},
                "notice": {"client_whatsapp": whatsapp},
                "app": app,
            })
            try:
                ev = self.event(slug)
            except ConfigError as exc:
                raise StudioError(str(exc)) from exc
            ev.ensure_dirs()
        if backend == "hub":
            self.refresh_gallery(slug)
        return self.summary(self.event(slug))

    def _set_app(self, slug: str, **values) -> Event:
        with self.lock:
            f = self.event_file(slug)
            import tomllib
            raw = tomllib.loads(f.read_text(encoding="utf-8"))
            raw.setdefault("app", {}).update(values)
            write_toml(f, raw)
            return self.event(slug)

    def refresh_gallery(self, slug: str) -> dict:
        """Asks the Hub which gallery the key opens and keeps its client link in the event."""
        ev = self.event(slug)
        if ev.publish_backend != "hub":
            return {"link": ev.gallery_link}
        try:
            info = hub.gallery_info(ev)
        except hub.HubError as exc:
            return {"error": str(exc)}
        with self.lock:
            import tomllib
            f = self.event_file(slug)
            raw = tomllib.loads(f.read_text(encoding="utf-8"))
            raw.setdefault("publish", {})["gallery_link"] = info.get("link", "")
            raw.setdefault("app", {}).update({"gallery_title": info.get("title", ""),
                                              "gallery_published": bool(info.get("published"))})
            write_toml(f, raw)
        return info

    def set_status(self, slug: str, status: str) -> dict:
        if status not in STATUSES:
            raise StudioError("unknown status")
        with self.lock:
            ev = self.event(slug)
            if self.status_of(ev) == "closed" and status != "closed":
                raise StudioError("This event is closed.")
            active = self.settings()["active"]
            if status == "live":
                if active and active != slug:
                    try:
                        if self.status_of(self.event(active)) == "live":
                            self._set_app(active, status="paused")
                    except StudioError:
                        pass
                self.save_settings(active=slug)
                ev.ensure_dirs()
            elif active == slug and status == "closed":
                self.save_settings(active="")
            extra = {"closed_at": int(time.time())} if status == "closed" else {}
            ev = self._set_app(slug, status=status, **extra)
        return self.summary(ev)

    def active_event(self) -> Event | None:
        slug = self.settings()["active"]
        if not slug:
            return None
        try:
            ev = self.event(slug)
        except (StudioError, ConfigError):
            return None
        return ev if self.status_of(ev) == "live" else None

    # photos and decisions -------------------------------------------------
    def photos(self, slug: str, folder: str = "review", limit: int = 400) -> list[dict]:
        if folder not in PHOTO_FOLDERS:
            raise StudioError("unknown folder")
        d = getattr(self.event(slug), folder)
        if not d.is_dir():
            return []
        files = [p for p in d.iterdir() if p.suffix.lower() in (".jpg", ".jpeg") and not p.name.startswith(".")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=folder != "review")  # review: oldest first
        return [{"name": p.name, "at": p.stat().st_mtime} for p in files[:limit]]

    def find_photo(self, slug: str, name: str) -> Path | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", name or ""):
            return None
        ev = self.event(slug)
        for folder in PHOTO_FOLDERS:
            p = getattr(ev, folder) / name
            if p.is_file():
                return p
        return None

    def thumbnail(self, slug: str, name: str, size: int = 480) -> Path | None:
        src = self.find_photo(slug, name)
        if src is None:
            return None
        size = min(max(size, 120), 1600)
        thumb = self.event(slug).root / ".thumbs" / f"{size}_{name}"
        if not thumb.exists() or thumb.stat().st_mtime < src.stat().st_mtime:
            thumb.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((size, size))
                tmp = thumb.with_name("." + thumb.name)
                im.save(tmp, "JPEG", quality=82)
                os.replace(tmp, thumb)
        return thumb

    def decide(self, slug: str, names: list[str], decision: str) -> dict:
        """approve: review/ or rejected/ → approved/ · reject: review/ → rejected/."""
        if decision not in ("approve", "reject"):
            raise StudioError("unknown decision")
        ev = self.event(slug)
        if self.status_of(ev) == "closed" and decision == "approve":
            raise StudioError("This event is closed.")
        ev.ensure_dirs()
        moved, missing = [], []
        with self.lock:
            sources = (ev.review, ev.rejected) if decision == "approve" else (ev.review,)
            dest = ev.approved if decision == "approve" else ev.rejected
            for n in names:
                src = next((d / n for d in sources if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", n)
                            and (d / n).is_file()), None)
                if src is None:
                    missing.append(n)
                    continue
                os.replace(src, dest / n)
                moved.append(n)
        return {"moved": moved, "missing": missing, "counters": self.counters(ev)}

    # client messages --------------------------------------------------------
    @staticmethod
    def wa_link(phone: str, text: str) -> str:
        q = urllib.parse.quote(text)
        return f"https://wa.me/{phone}?text={q}" if phone else f"https://wa.me/?text={q}"

    def notify(self, slug: str, kind: str = "update") -> dict:
        """Pre-filled WhatsApp message; Levi taps send. kind = start (gallery link) | update (+N photos)."""
        ev = self.event(slug)
        if not ev.gallery_link:
            raise StudioError("No gallery link yet. Check the Hub key and the internet, then try again.")
        if kind == "start":
            msg = START_TEMPLATE.format(event=ev.name, link=ev.gallery_link)
            return {"count": 0, "message": msg, "wa_link": self.wa_link(ev.client_whatsapp, msg)}
        led = Ledger(ev.state_db)
        try:
            n = notify.build(ev, led)
            if n is None:
                return {"count": 0, "message": "", "wa_link": ""}
            led.record_notice(n.count, n.message)
        finally:
            led.close()
        with open(ev.root / "notices.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t+{n.count}\t{n.wa_link}\n")
        return {"count": n.count, "message": n.message, "wa_link": n.wa_link}

    # recap --------------------------------------------------------------
    def start_recap(self, slug: str, music: str | None, bpm: float = 120.0, duration: float = 45.0) -> dict:
        ev = self.event(slug)
        track = self.asset_path(music, "music") if music else None
        if self.recaps.get(slug, {}).get("state") == "running":
            return self.recaps[slug]
        out = ev.root / "recap" / f"{ev.slug}_recap.mp4"
        job = self.recaps[slug] = {"state": "running", "started": time.time(), "file": str(out)}

        def work():
            try:
                rep = recap.build(ev.video, out, track, ev.lut, ev.signature_portrait or ev.signature,
                                  float(duration), float(bpm))
                job.update(state="done", report=rep)
            except Exception as exc:  # shown on screen; Levi decides what to do
                job.update(state="error", error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return job

    # live engine ----------------------------------------------------------
    def route_inbox(self, ev: Event, settle: float = 10.0) -> int:
        """Camera files (FTP inbox, tether folder) → the live event's incoming/."""
        n = 0
        sources = [self.inbox]
        tether = self.settings().get("tether_folder")
        if tether and Path(tether).expanduser().is_dir():
            sources.append(Path(tether).expanduser())
        now = time.time()
        for src_dir in sources:
            for p in sorted(src_dir.rglob("*")):
                if not p.is_file() or p.name.startswith("."):
                    continue
                try:
                    if now - p.stat().st_mtime < settle:
                        continue  # still arriving
                except FileNotFoundError:
                    continue
                dst = ev.incoming / p.name
                if dst.exists():
                    dst = ev.incoming / f"{p.stem}_{int(now * 1000) % 10**8}{p.suffix}"
                shutil.move(str(p), str(dst))
                n += 1
        return n

    def check_internet(self, every: float = 10.0) -> bool:
        if time.time() - self.net.checked < every:
            return self.net.online
        host = urllib.parse.urlparse(self.settings()["hub_url"]).hostname or "hub.elevencreativestudio.com"
        try:
            socket.create_connection((host, 443), timeout=3).close()
            self.net.online = True
        except OSError:
            self.net.online = False
        self.net.checked = time.time()
        return self.net.online

    def tick(self, settle: float = SETTLE, route_settle: float = 10.0, online: bool | None = None) -> dict:
        """One engine cycle: route camera files, treat, pick up decisions, then upload the queue."""
        online = self.check_internet() if online is None else online
        done = {"routed": 0, "treated": 0, "sent": 0}
        work: list[Event] = []
        with self.lock:
            ev = self.active_event()
            if ev is not None:
                ev.ensure_dirs()
                done["routed"] = self.route_inbox(ev, route_settle)
                led = Ledger(ev.state_db)
                st = pipeline.Stats()
                try:
                    pipeline.ingest(ev, led, st, settle)
                    pipeline.release(ev, led, st)
                finally:
                    led.close()
                done["treated"] = st.treated
            # every event with something queued keeps uploading (also paused / closed ones)
            for e in self.events():
                c = e.get("counters", {})
                if e["status"] in ("live", "paused", "closed") and c.get("queue"):
                    try:
                        qe = self.event(e["slug"])
                    except (StudioError, ConfigError):
                        continue
                    if qe.slug != (ev.slug if ev else None) and qe.approved.is_dir():
                        led = Ledger(qe.state_db)
                        try:
                            pipeline.release(qe, led, pipeline.Stats())
                        finally:
                            led.close()
                    work.append(qe)
        if online:
            for qe in work:
                led = Ledger(qe.state_db)
                st = pipeline.Stats()
                try:
                    pipeline.upload(qe, led, st)
                finally:
                    led.close()
                done["sent"] += st.sent
                if qe.publish_backend == "hub" and not qe.gallery_link:
                    self.refresh_gallery(qe.slug)
        return done

    def start(self, interval: float = 1.5) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("engine cycle failed; trying again")
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, daemon=True, name="liveframe-engine")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> dict:
        ev = self.active_event()
        cam = self.camera.as_dict() if self.camera else {"connected": False, "disabled": True}
        return {"camera": cam, "online": self.net.online, "active": ev.slug if ev else "",
                "inbox_waiting": sum(1 for p in self.inbox.rglob("*") if p.is_file() and not p.name.startswith(".")),
                "settings": self.settings(), "recaps": self.recaps}
