"""Curation filter at ingest: everything, or only frames rated in camera.

"review" lets every frame through here; the human check happens later
(review/ → approved/, see pipeline.release).

Rating is read from EXIF tag 0x4746 (Rating) or from the XMP packet
(xmp:Rating). Which one the camera writes must be confirmed on the real body
during the home pilot (see README, "Curadoria").
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

EXIF_RATING = 0x4746
_XMP_RATING = re.compile(rb'xmp:Rating(?:="|>)\s*(-?\d+)')


def read_rating(path: Path) -> int:
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if EXIF_RATING in exif:
                return int(exif[EXIF_RATING])
            ifd = exif.get_ifd(0x8769)
            if EXIF_RATING in ifd:
                return int(ifd[EXIF_RATING])
    except Exception:
        pass
    # XMP lives near the start of the file; 256 KB is plenty.
    with open(path, "rb") as fh:
        m = _XMP_RATING.search(fh.read(256 * 1024))
    return int(m.group(1)) if m else 0


def accept(path: Path, mode: str, min_rating: int = 1) -> bool:
    if mode in ("all", "review"):
        return True
    return read_rating(path) >= min_rating
