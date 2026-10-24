#!/usr/bin/env python3
"""Render one vector design to lossless app icons; Pillow is a tooling-only extra.

    python -m pip install -e '.[artwork]'
    python tools/generate_icons.py

The artwork is drawn at 4x each target resolution, then downsampled once.
PNG metadata is intentionally omitted. App icons use an opaque, full-bleed
background; the operating system supplies the final icon shape.
"""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "s300_control" / "static" / "icons" / "v2"
BACKGROUND = "#bcf28a"
FOREGROUND = "#20321b"
RAILS = [(280, 264, 324, 760), (490, 210, 534, 814), (700, 264, 744, 760)]
HANDLES = [(214, 378, 390, 470), (424, 574, 600, 666), (634, 330, 810, 422)]


def vector():
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><rect width="1024" height="1024" rx="240" fill="{BACKGROUND}"/>']
    for x0, y0, x1, y1 in RAILS:
        parts.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="22" fill="{FOREGROUND}"/>')
    for x0, y0, x1, y1 in HANDLES:
        parts.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" rx="28" fill="{FOREGROUND}" stroke="{BACKGROUND}" stroke-width="18"/>')
    return "".join(parts) + "</svg>\n"


def raster(size, filename):
    scale = size * 4 / 1024
    image = Image.new("RGB", (size * 4, size * 4), BACKGROUND)
    draw = ImageDraw.Draw(image)
    def rectangle(box, radius, fill):
        draw.rounded_rectangle(tuple(round(v * scale) for v in box), radius=round(radius * scale), fill=fill)
    for box in RAILS:
        rectangle(box, 22, FOREGROUND)
    for x0, y0, x1, y1 in HANDLES:
        rectangle((x0-9, y0-9, x1+9, y1+9), 37, BACKGROUND)
        rectangle((x0+9, y0+9, x1-9, y1-9), 19, FOREGROUND)
    image.resize((size, size), Image.Resampling.LANCZOS).save(OUTPUT / filename, optimize=True)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    svg = vector()
    (OUTPUT / "icon.svg").write_text(svg, encoding="utf-8", newline="\n")
    (ROOT / "docs" / "assets" / "logo.svg").write_text(svg, encoding="utf-8", newline="\n")
    for size in (16, 32, 48, 96, 128, 192, 256, 384, 512, 1024):
        raster(size, f"app-{size}.png")
    for size in (120, 152, 167, 180):
        raster(size, f"apple-touch-icon-{size}.png")
    raster(1024, "apple-touch-icon.png")
    raster(1024, "apple-touch-icon-1024-v3.png")
    for size in (512, 1024):
        raster(size, f"maskable-{size}.png")
    print("Generated SVG master and 18 lossless PNGs (16–1024 px).")


if __name__ == "__main__":
    main()
