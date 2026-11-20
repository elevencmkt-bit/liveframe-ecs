"""External programs LiveFrame calls (ffmpeg for the recap and the samples).

Inside LiveFrame.app the bundled ffmpeg (a static build from the imageio-ffmpeg package) is
used, so the app never depends on Homebrew. From the terminal: the ffmpeg on PATH or from
Homebrew first, then the imageio-ffmpeg one. An app opened from the Finder gets a short PATH
without /opt/homebrew/bin, so the Homebrew paths are checked by hand.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

BREW_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


class ToolMissing(RuntimeError):
    pass


def _find(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in BREW_DIRS:
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


@lru_cache(maxsize=None)
def ffmpeg() -> str:
    if os.environ.get("LIVEFRAME_FFMPEG"):
        return os.environ["LIVEFRAME_FFMPEG"]
    found = None if _frozen() else _find("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # not installed / no binary for this platform
        found = _find("ffmpeg")
        if found:
            return found
        raise ToolMissing("ffmpeg not found (install it with: brew install ffmpeg)") from exc
    if not os.access(exe, os.X_OK):
        try:
            os.chmod(exe, 0o755)
        except OSError:
            pass
    return exe


@lru_cache(maxsize=None)
def ffprobe() -> str | None:
    """ffprobe if there is one; the bundled build has none (see media_duration)."""
    return None if _frozen() else _find("ffprobe")


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def media_duration(path: Path) -> float | None:
    """Seconds of a video/audio file, or None if it can't be read."""
    probe = ffprobe()
    if probe:
        r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    # Without ffprobe: `ffmpeg -i file` prints "Duration: hh:mm:ss.xx" and exits non-zero.
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = _DURATION.search(r.stderr)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)
