"""Animated WebP copies of the Myanmar banner's GIF stickers.

The GIFs in static/img/stickers/animated_*.gif are ~2 MB each and have a solid white
background (no transparency), which would show as white boxes on the green banner.
This makes small animated WebPs that keep every frame and its timing, with the white
background cut out and the anti-aliased edge softened (no white halo):

    python scripts/make_animated_stickers.py

Do NOT run these GIFs through make_display_images.py – that saves one still frame.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parent.parent / "static" / "img"
SRC = ROOT / "stickers"
OUT = ROOT / "display" / "stickers"
NAMES = ["confetti", "stars", "peace", "ok"]
SIZE = 240            # 2× the ~120 px the banner shows
NEAR_WHITE = 22       # how far from pure white still counts as background
FRINGE = 3            # px of edge (at full size) that get a soft alpha
QUALITY = 64          # keeps each sticker well under 400 KB


def dilate(mask):
    grown = mask.copy()
    grown[1:, :] |= mask[:-1, :]; grown[:-1, :] |= mask[1:, :]
    grown[:, 1:] |= mask[:, :-1]; grown[:, :-1] |= mask[:, 1:]
    return grown


def cut_out(frame):
    """RGBA frame: white connected to the border becomes transparent; the edge is un-matted from white."""
    rgb = frame.convert("RGB")
    whiteish = np.asarray(rgb, dtype=np.int16).min(axis=-1) >= 255 - NEAR_WHITE
    # flood fill from the border through near-white pixels (white inside the picture – teeth, eyes – stays)
    bg = np.zeros_like(whiteish)
    bg[0, :], bg[-1, :], bg[:, 0], bg[:, -1] = whiteish[0, :], whiteish[-1, :], whiteish[:, 0], whiteish[:, -1]
    while True:
        grown = dilate(bg) & whiteish
        if (grown == bg).all():
            break
        bg = grown
    src = np.asarray(rgb, dtype=np.float32)
    alpha = np.where(bg, 0.0, 1.0)
    # edge band: pixels next to the background, blended with white by the original anti-aliasing
    near = bg.copy()
    for _ in range(FRINGE):
        near = dilate(near)
    edge = near & ~bg
    # how much colour a pixel has compared with white -> its coverage
    cover = np.clip((255.0 - src.min(axis=-1)) / 110.0, 0.0, 1.0)
    alpha = np.where(edge, cover, alpha)
    # un-matte: remove the white the edge pixel was blended with
    a = np.maximum(alpha, 1e-3)[..., None]
    rgb_out = np.where(edge[..., None], np.clip((src - (1 - a) * 255.0) / a, 0, 255), src)
    out = np.dstack([rgb_out, alpha * 255.0]).round().astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def shrink(img, size):
    """Resize with premultiplied alpha, so transparent white can't bleed into the edge."""
    img = img.convert("RGBa")
    img.thumbnail((size, size), Image.LANCZOS)
    return img.convert("RGBA")


def main(out_dir=OUT):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        src = SRC / f"animated_{name}.gif"
        if not src.exists():
            print(f"missing {src.name}")
            continue
        gif = Image.open(src)
        frames, durations = [], []
        for frame in ImageSequence.Iterator(gif):
            frames.append(shrink(cut_out(frame), SIZE))
            durations.append(frame.info.get("duration", 40))
        dest = out_dir / f"animated_{name}.webp"
        frames[0].save(dest, "WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=0,
                       quality=QUALITY, method=6, lossless=False, allow_mixed=True)
        check = Image.open(dest)
        print(f"{dest.stat().st_size // 1024:>4} KB  {dest.name}  {check.size}  "
              f"{getattr(check, 'n_frames', 1)} frames (GIF had {len(frames)})")


if __name__ == "__main__":
    main(*sys.argv[1:])
