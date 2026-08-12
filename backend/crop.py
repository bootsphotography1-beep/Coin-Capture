"""Crop a coin out of a photo so the dashboard card shows the coin, not the table.

Detection is deliberately simple (no OpenCV): sample the corners as background,
keep pixels that differ, take the largest blob's bounding box, pad, square-crop,
and composite onto the dark brand background.
"""
from __future__ import annotations
import io
from typing import Any
from PIL import Image, ImageDraw, ImageFilter, ImageOps


BG = (13, 15, 18)  # --bg
DETECT = 320


def _open(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img) or img
    return img.convert("RGB")


def _background_color(small: Image.Image) -> tuple[int, int, int]:
    w, h = small.size
    pts = [
        small.getpixel((2, 2)),
        small.getpixel((w - 3, 2)),
        small.getpixel((2, h - 3)),
        small.getpixel((w - 3, h - 3)),
        small.getpixel((w // 2, 2)),
        small.getpixel((2, h // 2)),
    ]
    r = sum(p[0] for p in pts) // len(pts)
    g = sum(p[1] for p in pts) // len(pts)
    b = sum(p[2] for p in pts) // len(pts)
    return (r, g, b)


def _mask_from_background(small: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    """Binary mask: 255 = coin candidate (not background)."""
    w, h = small.size
    px = small.load()
    mask = Image.new("L", (w, h), 0)
    mp = mask.load()
    # Distance threshold — coins are metal, usually far from table/hand color.
    thr = 45
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            dist = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])
            if dist > thr:
                mp[x, y] = 255
    return mask.filter(ImageFilter.MedianFilter(size=3))


def _largest_blob_bbox(mask: Image.Image) -> tuple[int, int, int, int] | None:
    """Flood-fill connected components; return bbox of the largest blob."""
    w, h = mask.size
    pix = mask.load()
    seen = bytearray(w * h)
    best = None
    best_count = 0

    def idx(x: int, y: int) -> int:
        return y * w + x

    for y0 in range(h):
        for x0 in range(w):
            if pix[x0, y0] < 128 or seen[idx(x0, y0)]:
                continue
            stack = [(x0, y0)]
            seen[idx(x0, y0)] = 1
            minx = maxx = x0
            miny = maxy = y0
            count = 0
            while stack:
                x, y = stack.pop()
                count += 1
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[idx(nx, ny)] and pix[nx, ny] >= 128:
                        seen[idx(nx, ny)] = 1
                        stack.append((nx, ny))
            if count > best_count:
                best_count = count
                best = (minx, miny, maxx, maxy)

    # Ignore tiny speckles (~2% of frame).
    if best is None or best_count < (w * h * 0.02):
        return None
    return best


def crop_coin(image_bytes: bytes, *, size: int = 640) -> tuple[bytes, dict[str, Any]]:
    """Return (jpeg_bytes, meta). Always returns an image — falls back to a
    centered square crop if blob detection fails."""
    img = _open(image_bytes)
    ow, oh = img.size
    small = img.copy()
    small.thumbnail((DETECT, DETECT), Image.LANCZOS)
    sw, sh = small.size
    scale_x = ow / sw
    scale_y = oh / sh

    bg = _background_color(small)
    mask = _mask_from_background(small, bg)
    bbox = _largest_blob_bbox(mask)

    if bbox:
        x0, y0, x1, y1 = bbox
        cx = (x0 + x1) / 2 * scale_x
        cy = (y0 + y1) / 2 * scale_y
        bw = (x1 - x0) * scale_x
        bh = (y1 - y0) * scale_y
        side = max(bw, bh) * 1.18  # padding around the coin
        method = "blob"
    else:
        # Center square — still better than a full table shot.
        cx, cy = ow / 2, oh / 2
        side = min(ow, oh) * 0.72
        method = "center-fallback"

    half = side / 2
    left = int(max(0, cx - half))
    top = int(max(0, cy - half))
    right = int(min(ow, cx + half))
    bottom = int(min(oh, cy + half))
    # Force square
    side_px = min(right - left, bottom - top)
    right = left + side_px
    bottom = top + side_px
    cropped = img.crop((left, top, right, bottom))

    # Circular composite on brand background so cards look like coins, not squares.
    cropped = cropped.resize((size, size), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), BG)
    mask_c = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask_c)
    inset = 4
    draw.ellipse((inset, inset, size - inset - 1, size - inset - 1), fill=255)
    canvas.paste(cropped, (0, 0), mask_c)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88, optimize=True)
    meta = {
        "method": method,
        "box": [left, top, right, bottom],
        "original_size": [ow, oh],
        "output_size": [size, size],
    }
    return buf.getvalue(), meta


def downscale_for_model(image_bytes: bytes, max_dim: int = 768) -> bytes:
    """Shrink (and JPEG-compress) before sending to Ollama — biggest latency win."""
    img = _open(image_bytes)
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()
