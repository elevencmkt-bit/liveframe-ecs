"""Recap reel (30–60 s, 9:16) built by code with ffmpeg.

Clips from the event's video/ folder are cut on the music beat, graded with
the same .cube as the photos, framed to 1080×1920, signed with the event PNG
and laid over the licensed track Levi picks. Output: an mp4 plus a JSON log
of which clip/second went where, for Levi's review.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v"}


class RecapError(RuntimeError):
    pass


@dataclass
class Segment:
    clip: str
    start: float
    length: float


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError as exc:
        raise RecapError(f"cannot read duration of {path.name}") from exc


def plan(clips: list[tuple[Path, float]], total: float, cut: float) -> list[Segment]:
    """Spread `total` seconds across the clips in shot order, `cut` seconds each.

    Every clip is used before any is reused; each new visit takes a later,
    not-yet-used part of the clip (skipping the first/last 10% where the
    camera is usually moving).
    """
    usable = [(p, d) for p, d in clips if d >= cut]
    if not usable:
        raise RecapError(f"no clip is at least {cut:.1f}s long")
    n = math.ceil(total / cut - 1e-9)
    visits = {p: 0 for p, _ in usable}
    segs: list[Segment] = []
    for i in range(n):
        p, d = usable[i % len(usable)]
        k = visits[p]
        visits[p] += 1
        per_clip = math.ceil(n / len(usable))
        lo, hi = d * 0.1, max(d * 0.9 - cut, d * 0.1)
        start = lo + (hi - lo) * ((k + 0.5) / per_clip) if hi > lo else 0.0
        start = min(max(0.0, start), max(0.0, d - cut))
        length = min(cut, total - cut * i)
        segs.append(Segment(p.name, round(start, 3), round(length, 3)))
    return segs


def _esc(path: Path) -> str:
    # Escape for use inside an ffmpeg filter argument.
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build(clips_dir: Path, out: Path, music: Path | None = None, lut: Path | None = None,
          signature: Path | None = None, duration: float = 45.0, bpm: float = 120.0,
          beats_per_cut: int = 4, music_start: float = 0.0, width: int = 1080,
          height: int = 1920, fps: int = 30, dry_run: bool = False) -> dict:
    if not 30 <= duration <= 60:
        raise RecapError("duration must be between 30 and 60 seconds")
    clips = sorted(p for p in clips_dir.iterdir()
                   if p.suffix.lower() in VIDEO_EXT and not p.name.startswith("."))
    if not clips:
        raise RecapError(f"no clips in {clips_dir}")
    cut = 60.0 / bpm * beats_per_cut
    segs = plan([(p, probe_duration(p)) for p in clips], duration, cut)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s in segs:
        cmd += ["-ss", f"{s.start}", "-t", f"{s.length}", "-i", str(clips_dir / s.clip)]
    idx = len(segs)
    sig_idx = mus_idx = None
    if signature:
        cmd += ["-loop", "1", "-i", str(signature)]
        sig_idx, idx = idx, idx + 1
    if music:
        cmd += ["-ss", f"{music_start}", "-i", str(music)]
        mus_idx, idx = idx, idx + 1

    grade = f"lut3d=file='{_esc(lut)}'," if lut else ""
    parts = []
    for i in range(len(segs)):
        parts.append(
            f"[{i}:v]{grade}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1,format=yuv420p[v{i}]")
    parts.append("".join(f"[v{i}]" for i in range(len(segs)))
                 + f"concat=n={len(segs)}:v=1:a=0[cat]")
    last = "cat"
    if sig_idx is not None:
        parts.append(f"[{sig_idx}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                     f"crop={width}:{height},format=rgba[sig]")
        parts.append(f"[cat][sig]overlay=0:0:shortest=1[sv]")
        last = "sv"
    parts.append(f"[{last}]fade=t=in:st=0:d=0.4,fade=t=out:st={duration - 0.8}:d=0.8,"
                 f"format=yuv420p[vout]")
    if mus_idx is not None:
        parts.append(f"[{mus_idx}:a]atrim=0:{duration},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d=0.3,afade=t=out:st={duration - 2}:d=2[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    if mus_idx is not None:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", f"{duration}", str(out)]

    report = {
        "output": str(out), "duration": duration, "bpm": bpm, "cut_seconds": cut,
        "lut": str(lut) if lut else None, "signature": str(signature) if signature else None,
        "music": str(music) if music else None, "music_start": music_start,
        "segments": [asdict(s) for s in segs],
    }
    if dry_run:
        report["ffmpeg"] = cmd
        return report
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RecapError(r.stderr.strip()[-1500:])
    out.with_suffix(".json").write_text(json.dumps(report, indent=2))
    return report
