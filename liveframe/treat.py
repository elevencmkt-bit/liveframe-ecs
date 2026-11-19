"""Photo treatment: orientation → LUT → resize → event signature → JPEG."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageOps

from .config import Event
from .lut import apply_lut


def _signature_for(event: Event, portrait: bool) -> Path | None:
    if portrait and event.signature_portrait:
        return event.signature_portrait
    return event.signature


def apply_signature(img: Image.Image, event: Event) -> Image.Image:
    if event.signature_mode == "none":
        return img
    sig_path = _signature_for(event, img.height > img.width)
    if sig_path is None:
        return img
    with Image.open(sig_path) as s:
        sig = s.convert("RGBA")
    base = img.convert("RGBA")
    W, H = base.size
    if event.signature_mode == "frame":
        # Frame PNG covers the whole picture; "cover" fit keeps its proportions.
        sig = ImageOps.fit(sig, (W, H), method=Image.LANCZOS)
        base.alpha_composite(sig)
    else:  # logo
        w = max(1, round(W * event.logo_width))
        h = max(1, round(sig.height * w / sig.width))
        sig = sig.resize((w, h), Image.LANCZOS)
        m = round(min(W, H) * event.logo_margin)
        x = m if "left" in event.logo_corner else W - w - m
        y = m if event.logo_corner.startswith("top") else H - h - m
        base.alpha_composite(sig, (x, y))
    return base.convert("RGB")


def treat_photo(src: Path, dst: Path, event: Event) -> Path:
    with Image.open(src) as im:
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im = apply_lut(im, event.lut)
        if max(im.size) > event.long_edge:
            im.thumbnail((event.long_edge, event.long_edge), Image.LANCZOS)
        im = apply_signature(im, event)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name("." + dst.name + ".part")
    # EXIF/GPS are dropped on purpose: the gallery is public to the client.
    kwargs = {"quality": event.jpeg_quality, "optimize": True, "progressive": True}
    if icc:
        kwargs["icc_profile"] = icc
    im.save(tmp, "JPEG", **kwargs)
    os.replace(tmp, dst)
    return dst
