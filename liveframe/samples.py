"""Test material made by code (until Levi sends the real LUT, PNG and track):
a warm .cube, a frame + logo signature PNG, a click track and short clips.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import tools


def write_cube(path: Path, size: int = 17) -> Path:
    """Warm, slightly contrasty look — obviously visible in tests."""
    lines = ['TITLE "LiveFrame sample warm"', f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                R, G, B = (x / (size - 1) for x in (r, g, b))
                s = lambda v: min(1.0, max(0.0, 0.5 + (v - 0.5) * 1.12))  # noqa: E731
                lines.append(f"{s(R * 1.06):.6f} {s(G * 1.0):.6f} {s(B * 0.88):.6f}")
    path.write_text("\n".join(lines) + "\n")
    return path


def write_frame_png(path: Path, w: int = 3000, h: int = 2000, text: str = "SAMPLE EVENT") -> Path:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bar = h // 12
    d.rectangle([0, h - bar, w, h], fill=(10, 10, 10, 200))
    d.rectangle([0, h - bar, w // 60, h], fill=(245, 200, 0, 255))
    d.text((w // 40, h - bar + bar // 3), text, fill=(255, 255, 255, 255))
    im.save(path)
    return path


def write_logo_png(path: Path, w: int = 600, h: int = 200) -> Path:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 5, fill=(245, 200, 0, 230))
    d.text((w // 10, h // 3), "LIVEFRAME", fill=(10, 10, 10, 255))
    im.save(path)
    return path


def write_photo(path: Path, w: int = 4000, h: int = 2667, seed: int = 0,
                rating: int | None = None) -> Path:
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for i in range(0, w, max(1, w // 40)):
        d.rectangle([i, 0, i + w // 40, h], fill=((i * 3 + seed * 40) % 256, (i + 90) % 256, 140))
    exif = Image.Exif()
    if rating is not None:
        exif[0x4746] = rating
    im.save(path, "JPEG", quality=85, exif=exif.tobytes())
    return path


def write_clip(path: Path, seconds: float = 6, pattern: str = "testsrc2", portrait=False) -> Path:
    size = "1080x1920" if portrait else "1920x1080"
    subprocess.run([tools.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"{pattern}=size={size}:rate=30:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def write_track(path: Path, seconds: float = 70, bpm: float = 120) -> Path:
    """Sine pad + click on every beat; stands in for the licensed track."""
    beat = 60.0 / bpm
    expr = f"0.2*sin(2*PI*220*t)+0.5*sin(2*PI*1000*t)*lt(mod(t\\,{beat})\\,0.03)"
    subprocess.run([tools.ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"aevalsrc={expr}:s=44100:d={seconds}", "-c:a", "aac", str(path)],
                   check=True)
    return path


def make_all(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return {
        "lut": write_cube(folder / "sample_warm.cube"),
        "frame": write_frame_png(folder / "sample_frame.png"),
        "frame_portrait": write_frame_png(folder / "sample_frame_portrait.png", 2000, 3000),
        "logo": write_logo_png(folder / "sample_logo.png"),
        "track": write_track(folder / "sample_track_120bpm.m4a"),
    }
