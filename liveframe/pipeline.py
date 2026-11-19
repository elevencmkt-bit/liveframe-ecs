"""The live loop: incoming → (curation, treatment) → ready → (publish) → sent.

With curation = "review" (default) treated photos stop in review/ first. Levi
looks at them and drags each one to approved/ (→ ready → gallery) or to
rejected/ (never published). `liveframe approve` does the same from the terminal.

Safe to stop and restart at any moment:
- the treated file is written atomically before the ledger is updated;
- a camera file already in the ledger is never treated or published again;
- publishing failures (no internet) keep the file in ready/ and retry with backoff.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import UnidentifiedImageError

from . import curation
from .config import Event
from .publish import PublishError, publish
from .state import Ledger, sha1_of
from .treat import treat_photo

log = logging.getLogger("liveframe")

PHOTO_EXT = {".jpg", ".jpeg"}
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v"}


@dataclass
class Stats:
    treated: int = 0
    skipped: int = 0
    duplicates: int = 0
    videos: int = 0
    failed: int = 0
    sent: int = 0
    to_review: int = 0
    approved: int = 0
    rejected: int = 0
    retrying: int = 0
    errors: list[str] = field(default_factory=list)


def _move(src: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / src.name
    if dst.exists():
        dst = folder / f"{src.stem}_{int(time.time() * 1000)}{src.suffix}"
    shutil.move(str(src), str(dst))
    return dst


def _settled(p: Path, settle: float) -> bool:
    try:
        st = p.stat()
    except FileNotFoundError:
        return False
    return st.st_size > 0 and (time.time() - st.st_mtime) >= settle


def _output_name(event: Event, ledger: Ledger, src: Path, sha: str) -> str:
    name = f"{event.slug}_{src.stem}.jpg"
    taken = ledger.conn.execute(
        "SELECT 1 FROM files WHERE output_name=? AND sha1<>?", (name, sha)).fetchone()
    # Camera counters repeat across cards/bodies: keep names unique.
    return f"{event.slug}_{src.stem}_{sha[:6]}.jpg" if taken else name


def ingest(event: Event, ledger: Ledger, stats: Stats, settle: float = 2.0) -> None:
    for src in sorted(event.incoming.rglob("*")):
        if not src.is_file() or src.name.startswith(".") or not _settled(src, settle):
            continue
        ext = src.suffix.lower()
        try:
            if ext in VIDEO_EXT:
                sha = sha1_of(src)
                if not ledger.get(sha):
                    ledger.add(sha, src.name, src.name, "video")
                    stats.videos += 1
                _move(src, event.video)
                continue
            if ext not in PHOTO_EXT:
                _move(src, event.skipped)  # RAW etc. stay on the card
                stats.skipped += 1
                continue
            sha = sha1_of(src)
            if ledger.get(sha):
                _move(src, event.originals)
                stats.duplicates += 1
                continue
            if not curation.accept(src, event.curation, event.min_rating):
                ledger.add(sha, src.name, None, "skipped")
                _move(src, event.skipped)
                stats.skipped += 1
                continue
            out = _output_name(event, ledger, src, sha)
            status = "review" if event.curation == "review" else "ready"
            treat_photo(src, getattr(event, status) / out, event)
            ledger.add(sha, src.name, out, status)
            _move(src, event.originals)
            stats.treated += 1
            if status == "review":
                stats.to_review += 1
            log.info("treated %s -> %s (%s)", src.name, out, status)
        except (UnidentifiedImageError, OSError, SyntaxError) as exc:
            # Still being written? Give it time before calling it broken.
            if time.time() - src.stat().st_mtime < max(30.0, settle * 10):
                continue
            log.warning("failed %s: %s", src.name, exc)
            _move(src, event.failed)
            stats.failed += 1
            stats.errors.append(f"{src.name}: {exc}")


def release(event: Event, ledger: Ledger, stats: Stats) -> None:
    """Review mode: pick up Levi's decisions from approved/ and rejected/."""
    if event.curation != "review":
        return
    for folder, status in ((event.approved, "ready"), (event.rejected, "rejected")):
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            row = ledger.by_output(f.name)
            if row is None:
                log.warning("%s/%s is not a LiveFrame photo, left alone", folder.name, f.name)
                continue
            sha, current = row
            allowed = ("review", "rejected") if status == "ready" else ("review",)
            if current not in allowed:
                if current == status:
                    continue  # already recorded; the file just stays in rejected/
                log.warning("%s is already %s, left in %s/", f.name, current, folder.name)
                continue
            if status == "ready":
                os.replace(f, event.ready / f.name)
                stats.approved += 1
            else:
                stats.rejected += 1
            ledger.set_status(sha, status)
            log.info("%s %s", "approved" if status == "ready" else "rejected", f.name)
    # Crashed between the move and the ledger update: file already in ready/.
    for sha, name in ledger.conn.execute(
            "SELECT sha1, output_name FROM files WHERE status='review'").fetchall():
        if (event.ready / name).exists():
            ledger.set_status(sha, "ready")


def approve(event: Event, names: list[str] | None = None) -> int:
    """Move photos from review/ to approved/ (all of them when names is empty)."""
    event.ensure_dirs()
    files = [event.review / n for n in names] if names else sorted(event.review.glob("*.jpg"))
    moved = 0
    for f in files:
        if f.is_file():
            os.replace(f, event.approved / f.name)
            moved += 1
        else:
            log.warning("not in review/: %s", f.name)
    return moved


def upload(event: Event, ledger: Ledger, stats: Stats) -> None:
    for sha, name, attempts in ledger.due_for_upload():
        f = event.ready / name
        if not f.exists():
            if (event.sent / name).exists():  # crashed after the move
                ledger.mark_sent(sha)
            continue
        try:
            publish(f, event)
        except PublishError as exc:
            ledger.mark_retry(sha, attempts, str(exc))
            stats.retrying += 1
            log.warning("publish failed (%s), will retry: %s", name, exc)
            continue
        os.replace(f, event.sent / name)
        ledger.mark_sent(sha)
        stats.sent += 1
        log.info("sent %s", name)


def run_once(event: Event, settle: float = 2.0) -> Stats:
    event.ensure_dirs()
    ledger = Ledger(event.state_db)
    stats = Stats()
    try:
        ingest(event, ledger, stats, settle)
        release(event, ledger, stats)
        upload(event, ledger, stats)
    finally:
        ledger.close()
    return stats


def run_forever(event: Event, interval: float = 2.0, settle: float = 2.0) -> None:
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True
        log.info("stopping after this cycle…")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("LiveFrame running for '%s' — watching %s", event.name, event.incoming)
    while not stop["flag"]:
        s = run_once(event, settle)
        if s.treated or s.sent or s.failed or s.approved or s.rejected:
            log.info("cycle: treated=%d to_review=%d approved=%d rejected=%d sent=%d "
                     "skipped=%d failed=%d retrying=%d", s.treated, s.to_review, s.approved,
                     s.rejected, s.sent, s.skipped, s.failed, s.retrying)
        time.sleep(interval)

