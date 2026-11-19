"""Read Adobe/Resolve .cube 3D LUTs and apply them to photos.

The same .cube file is applied to video by ffmpeg's lut3d filter (see recap.py),
so photos and clips share one look.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFilter


class LutError(ValueError):
    pass


@lru_cache(maxsize=8)
def load_cube(path: str) -> ImageFilter.Color3DLUT:
    size = None
    dmin, dmax = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    table: list[float] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key = line.split()[0].upper()
            if key == "LUT_3D_SIZE":
                size = int(line.split()[1])
            elif key == "LUT_1D_SIZE":
                raise LutError("1D LUTs are not supported; export a 3D .cube")
            elif key == "DOMAIN_MIN":
                dmin = [float(x) for x in line.split()[1:4]]
            elif key == "DOMAIN_MAX":
                dmax = [float(x) for x in line.split()[1:4]]
            elif key in ("TITLE", "LUT_3D_INPUT_RANGE"):
                continue
            else:
                try:
                    r, g, b = (float(x) for x in line.split()[:3])
                except ValueError:
                    continue  # unknown keyword
                table.extend((r, g, b))
    if not size:
        raise LutError(f"{path}: missing LUT_3D_SIZE")
    if len(table) != size ** 3 * 3:
        raise LutError(f"{path}: expected {size ** 3} entries, got {len(table) // 3}")
    if dmin != [0.0, 0.0, 0.0] or dmax != [1.0, 1.0, 1.0]:
        # Output values are normalised to 0..1 for Pillow.
        table = [(v - dmin[i % 3]) / (dmax[i % 3] - dmin[i % 3]) for i, v in enumerate(table)]
    # .cube order is red-fastest, which is also what Pillow expects.
    return ImageFilter.Color3DLUT(size, table, channels=3, target_mode="RGB")


def apply_lut(img: Image.Image, cube: str | Path | None) -> Image.Image:
    if cube is None:
        return img
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img.filter(load_cube(str(cube)))
