"""Make small display copies of the AGOM logos and icons.

The originals in static/img/ are large (up to ~2 MB each). The app shows these
copies from static/img/display/ instead. Run again after replacing an original:

    pip install Pillow
    python scripts/make_display_images.py
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "static" / "img"
OUT = ROOT / "display"
ICONS = ["plane", "boxes", "box-checked", "warning", "house", "flying-box", "calendar"]
STICKERS = ["ok", "peace", "stars", "happy"]  # Myanmar checker's dashboard banner
APP_BG = (244, 246, 239, 255)  # --sand; iPhone shows transparent home-screen icons as black


def fit(img, size):
    img = img.convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def green_cutout(img):
    """The customer icon is a green figure on a pale background with no transparency:
    keep the clearly-green pixels (soft edge), make the rest transparent and crop to the figure."""
    rgb = img.convert("RGB")
    px = rgb.load()
    alpha = Image.new("L", rgb.size, 0)
    ap = alpha.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            pr, pg, pb = px[x, y]
            ap[x, y] = max(0, min(255, (pg - max(pr, pb) - 12) * 6))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out.crop(alpha.getbbox() or (0, 0, *rgb.size))


def square(img, size, pad=0.08):
    """Centre the image on a square background (home-screen icons must be square)."""
    canvas = Image.new("RGBA", (size, size), APP_BG)
    inner = fit(img, int(size * (1 - 2 * pad)))
    canvas.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))
    return canvas


def main():
    (OUT / "icons").mkdir(parents=True, exist_ok=True)
    for name in ICONS:
        src = ROOT / "icons" / f"{name}.png"
        if src.exists():
            fit(Image.open(src), 256).save(OUT / "icons" / f"{name}.webp", "WEBP", quality=86, method=6)
    customer = ROOT / "icons" / "customer.png"   # shown at ~18px next to customer names
    if customer.exists():
        fit(green_cutout(Image.open(customer)), 64).save(OUT / "icons" / "customer.webp", "WEBP", quality=88, method=6)
    (OUT / "stickers").mkdir(parents=True, exist_ok=True)
    for name in STICKERS:
        src = ROOT / "stickers" / f"{name}.png"
        if src.exists():
            fit(Image.open(src), 240).save(OUT / "stickers" / f"{name}.webp", "WEBP", quality=86, method=6)
    logo = ROOT / "3D logo.png"
    if logo.exists():
        fit(Image.open(logo), 256).save(OUT / "3d-logo.png", optimize=True)
    badge = ROOT / "agom-badge.png"
    if badge.exists():
        fit(Image.open(badge), 320).save(OUT / "agom-badge.webp", "WEBP", quality=88, method=6)
    cover = ROOT / "3D cover logo.png"
    if cover.exists():
        img = Image.open(cover)
        for size in (180, 192, 512):
            square(img, size).convert("RGB").save(OUT / f"app-icon-{size}.png", optimize=True)
    for f in sorted(OUT.rglob("*.*")):
        print(f"{f.stat().st_size // 1024:>4} KB  {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
