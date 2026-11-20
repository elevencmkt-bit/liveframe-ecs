"""Draws the LiveFrame icon (.icns + .png) and the DMG background with Pillow.

ECS identity: near-black icon with yellow (#f9bf24) viewfinder corners and a "live" dot;
the DMG window uses the off-white page with yellow corners (ECS document style).
No fonts needed for the icon (shapes only), so it renders the same on any runner.

    python packaging/make_art.py build/art
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (5, 10, 14, 255)
BG2 = (17, 22, 28, 255)
YELLOW = (249, 191, 36, 255)
INK = (244, 244, 242, 255)
SOFT = (154, 160, 168, 255)
PAPER = (244, 244, 242, 255)   # off-white page: Finder draws the icon labels in black
GREY = (90, 96, 104, 255)


def icon(size: int = 1024) -> Image.Image:
    s = size / 1024
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # macOS icon grid: 824px rounded square centred in 1024
    m, r = 100 * s, 185 * s
    d.rounded_rectangle([m, m, size - m, size - m], radius=r, fill=BG)
    d.rounded_rectangle([m + 6 * s, m + 6 * s, size - m - 6 * s, size - m - 6 * s], radius=r - 6 * s,
                        outline=BG2, width=round(10 * s))
    # viewfinder corners
    a, b, L, w = 250 * s, size - 250 * s, 130 * s, round(44 * s)
    for (x, y, dx, dy) in ((a, a, 1, 1), (b, a, -1, 1), (a, b, 1, -1), (b, b, -1, -1)):
        d.line([(x, y + dy * L), (x, y), (x + dx * L, y)], fill=YELLOW, width=w, joint="curve")
    # live dot
    c, rr = size / 2, 96 * s
    d.ellipse([c - rr, c - rr, c + rr, c + rr], fill=YELLOW)
    return im


def _font(px: int, bold: bool = False):
    for f in ["/System/Library/Fonts/HelveticaNeue.ttc",
            "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(f, px, index=1 if bold and f.endswith(".ttc") else 0)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def dmg_background(w: int = 660, h: int = 400, scale: int = 1) -> Image.Image:
    W, H = w * scale, h * scale
    im = Image.new("RGBA", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    # yellow corner marks of the ECS layout
    L, t = 28 * scale, 4 * scale
    for (x, y, dx, dy) in ((14, 14, 1, 1), (w - 14, 14, -1, 1), (14, h - 14, 1, -1), (w - 14, h - 14, -1, -1)):
        x, y = x * scale, y * scale
        d.line([(x, y + dy * L), (x, y), (x + dx * L, y)], fill=YELLOW, width=t)
    d.text((W / 2, 48 * scale), "LIVEFRAME ECS", font=_font(22 * scale, True), fill=BG, anchor="mm")
    # arrow between the app (x=170) and Applications (x=490), icons at y=200
    y, x0, x1 = 200 * scale, 262 * scale, 398 * scale
    d.line([(x0, y), (x1 - 18 * scale, y)], fill=BG, width=6 * scale)
    d.polygon([(x1, y), (x1 - 26 * scale, y - 16 * scale), (x1 - 26 * scale, y + 16 * scale)], fill=BG)
    d.text((W / 2, 330 * scale), "Drag LiveFrame to Applications", font=_font(17 * scale), fill=BG, anchor="mm")
    d.text((W / 2, 356 * scale), "Arraste o LiveFrame para Aplicativos", font=_font(13 * scale), fill=GREY,
           anchor="mm")
    return im


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ic = icon()
    ic.save(out / "LiveFrame.png")
    ic.save(out / "LiveFrame.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512),
                                            (1024, 1024)])
    dmg_background().save(out / "dmg_background.png")
    dmg_background(scale=2).save(out / "dmg_background@2x.png")
    print(f"art in {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/art"))
