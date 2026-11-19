"""Event configuration (one TOML file per event)."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CURATION_MODES = ("all", "flagged")
SIGNATURE_MODES = ("frame", "logo", "none")
PUBLISH_BACKENDS = ("folder", "rclone")


class ConfigError(ValueError):
    pass


@dataclass
class Event:
    slug: str
    name: str
    root: Path                      # working folder of this event (incoming/ready/sent/...)
    lut: Path | None = None
    signature: Path | None = None
    signature_portrait: Path | None = None
    signature_mode: str = "frame"   # frame = full-size PNG on top; logo = corner mark
    logo_width: float = 0.18        # logo width as a fraction of the image width
    logo_margin: float = 0.03
    logo_corner: str = "bottom-right"
    long_edge: int = 2048
    jpeg_quality: int = 90
    curation: str = "all"           # all | flagged (in-camera rating >= min_rating)
    min_rating: int = 1
    publish_backend: str = "folder"
    publish_target: str = ""        # folder path (Drive for desktop) or rclone remote:path
    gallery_link: str = ""
    client_whatsapp: str = ""       # digits only, with country code, e.g. 14075550123
    notice_template: str = "📸 +{n} new photos from {event} are live in your gallery: {link}"
    extra: dict = field(default_factory=dict)

    # folders -----------------------------------------------------------
    @property
    def incoming(self) -> Path: return self.root / "incoming"
    @property
    def originals(self) -> Path: return self.root / "originals"
    @property
    def ready(self) -> Path: return self.root / "ready"
    @property
    def sent(self) -> Path: return self.root / "sent"
    @property
    def skipped(self) -> Path: return self.root / "skipped"
    @property
    def failed(self) -> Path: return self.root / "failed"
    @property
    def video(self) -> Path: return self.root / "video"
    @property
    def state_db(self) -> Path: return self.root / "state.sqlite"

    def ensure_dirs(self) -> None:
        for d in (self.incoming, self.originals, self.ready, self.sent,
                  self.skipped, self.failed, self.video):
            d.mkdir(parents=True, exist_ok=True)


def _path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def load_event(config_file: str | Path) -> Event:
    config_file = Path(config_file).expanduser().resolve()
    with open(config_file, "rb") as fh:
        raw = tomllib.load(fh)
    base = config_file.parent
    ev = raw.get("event", {})
    tr = raw.get("treatment", {})
    pub = raw.get("publish", {})
    ntf = raw.get("notice", {})

    name = ev.get("name") or ""
    slug = ev.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ConfigError("event.name or event.slug is required")
    root = _path(base, ev.get("root")) or (base / "work" / slug)

    event = Event(
        slug=slug, name=name or slug, root=root,
        lut=_path(base, tr.get("lut")),
        signature=_path(base, tr.get("signature")),
        signature_portrait=_path(base, tr.get("signature_portrait")),
        signature_mode=tr.get("signature_mode", "frame"),
        logo_width=float(tr.get("logo_width", 0.18)),
        logo_margin=float(tr.get("logo_margin", 0.03)),
        logo_corner=tr.get("logo_corner", "bottom-right"),
        long_edge=int(tr.get("long_edge", 2048)),
        jpeg_quality=int(tr.get("jpeg_quality", 90)),
        curation=ev.get("curation", "all"),
        min_rating=int(ev.get("min_rating", 1)),
        publish_backend=pub.get("backend", "folder"),
        publish_target=str(pub.get("target", "")),
        gallery_link=pub.get("gallery_link", ""),
        client_whatsapp=re.sub(r"\D", "", str(ntf.get("client_whatsapp", ""))),
        notice_template=ntf.get("template", Event.notice_template),
        extra=raw,
    )
    validate(event)
    return event


def validate(e: Event) -> None:
    if e.curation not in CURATION_MODES:
        raise ConfigError(f"curation must be one of {CURATION_MODES}")
    if e.signature_mode not in SIGNATURE_MODES:
        raise ConfigError(f"signature_mode must be one of {SIGNATURE_MODES}")
    if e.publish_backend not in PUBLISH_BACKENDS:
        raise ConfigError(f"publish.backend must be one of {PUBLISH_BACKENDS}")
    if not e.publish_target:
        raise ConfigError("publish.target is required")
    if e.logo_corner not in ("top-left", "top-right", "bottom-left", "bottom-right"):
        raise ConfigError("logo_corner must be top-left/top-right/bottom-left/bottom-right")
    for p in (e.lut, e.signature, e.signature_portrait):
        if p is not None and not p.exists():
            raise ConfigError(f"file not found: {p}")
    if e.long_edge < 256:
        raise ConfigError("long_edge too small")
