"""Draw the application icon.

    python app/src-tauri/icons/make_icons.py

A generator rather than a checked-in binary, for the same reason a measured
number lives in one place: an icon nobody can regenerate is an icon nobody can
change. No Pillow -- this is five convex polygons and a PNG encoder, and the
encoder is thirty lines, fewer than the argument for adding a dependency.

**The mark is not the orb, and that is deliberate.** It used to be: 34 points
and the lines between them, the same lattice the window draws. It read as a
smudge at 16 pixels, which is the size the taskbar and the alt-tab strip
actually use, and no amount of tuning fixes a mesh whose links are thinner than
a pixel. So the icon is the same idea reduced to what survives: a solid seen
down its long diagonal, which is a hexagon with a Y in it -- three faces, six
edges, one centre. Vertices and the lines between them, still.

**Everything is one supersampled rasteriser**, which is what the first version
of this got wrong. It drew the faces by testing four points per pixel and the
lines by stamping soft dots along them, so the edges were four-level and the
strokes were fuzzy -- pixelated in the literal sense, quantised rather than
antialiased. Now every shape is a polygon, they are rasterised together at four
samples each way by scanline, and the result is averaged down. One code path,
sixteen levels of coverage per pixel, and the strokes have actual edges.

White on nothing, like the orb. Colour in this program means something is
happening -- teal off the machine, red refused -- and an icon cannot be in one
of those states.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The one colour: the orb's own white (`orb.js`), byte for byte. Not the
#: stylesheet's `--text`, which is #f5f4ef -- the icon matches the thing it
#: is a picture of rather than the text beside it.
WHITE = (255, 252, 247)

#: How much of the tile the solid fills, as a fraction of its width. Big enough
#: to read at 16 pixels, with enough air that the mark is never touching the
#: edges of whatever Windows composites it into.
RADIUS = 0.385

#: Stroke width, proportional with a floor: below about 1.1 pixels a line stops
#: being a line and becomes a grey smear, whatever the antialiasing.
STROKE = 0.034
STROKE_MIN = 1.15

#: Alpha per surface. The top catches the light and the two sides fall away
#: from it, which is the whole reason a cube reads as a cube and not a hexagon.
#: The edges are near-solid; the spokes are the inside of the solid and sit a
#: little back, or three bright lines meeting at a point become a blob.
TOP, RIGHT, LEFT = 0.30, 0.15, 0.075
EDGE, SPOKE = 0.98, 0.62

#: Samples each way. Sixteen levels of coverage is enough that a diagonal at 16
#: pixels reads as a straight line rather than a staircase.
SAMPLES = 4

SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}
#: What Windows actually reads. Every size it might ask for, so it never has to
#: scale one down and make a smudge of it.
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def hexagon(cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
    """Six points every sixty degrees, flat-topped-corner up."""
    out = []
    for i in range(6):
        angle = math.radians(30 + 60 * i)
        out.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    return out


def bar(a, b, width: float) -> list[tuple[float, float]]:
    """A line as a rectangle, which is how a line gets antialiased properly."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    return [
        (a[0] + nx, a[1] + ny),
        (b[0] + nx, b[1] + ny),
        (b[0] - nx, b[1] - ny),
        (a[0] - nx, a[1] - ny),
    ]


def span(poly, y: float):
    """Where a horizontal line at `y` enters and leaves a convex polygon."""
    lo = hi = None
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if (ay > y) != (by > y):
            x = ax + (bx - ax) * (y - ay) / (by - ay)
            if lo is None or x < lo:
                lo = x
            if hi is None or x > hi:
                hi = x
    return None if lo is None or hi is None or hi <= lo else (lo, hi)


def shapes(size: int):
    """The whole drawing: convex polygons with an alpha each, back to front.

    Returned as `(polygon, hole, alpha)`. The outline is the only one with a
    hole -- a ring is an outer hexagon minus an inner one, which mitres its own
    corners for free and is why the six edges are not six rectangles.
    """
    cx = cy = (size - 1) / 2
    radius = size * RADIUS
    width = max(STROKE_MIN, size * STROKE)
    v = hexagon(cx, cy, radius)
    centre = (cx, cy)

    out = [
        ([centre, v[0], v[1], v[2]], None, TOP),
        ([centre, v[4], v[5], v[0]], None, RIGHT),
        ([centre, v[2], v[3], v[4]], None, LEFT),
    ]
    for i in (0, 2, 4):
        out.append((bar(centre, v[i], width * 0.88), None, SPOKE))
    out.append((hexagon(cx, cy, radius + width / 2), hexagon(cx, cy, radius - width / 2), EDGE))
    return out


def light(size: int) -> list[float]:
    """One greyscale buffer: how much light reaches each pixel.

    Shapes are combined by taking the brighter of the two rather than by
    adding. Adding is what makes an edge crossing a face read as a third,
    brighter thing, and there is no third thing here.
    """
    polys = shapes(size)
    ss = SAMPLES
    wide = size * ss
    row = [0.0] * wide
    out = [0.0] * (size * size)

    for y in range(size):
        touched_lo, touched_hi = wide, 0
        acc = [0.0] * size
        for sub in range(ss):
            sy = y + (sub + 0.5) / ss
            lo_here, hi_here = wide, 0
            for poly, hole, alpha in polys:
                found = span(poly, sy)
                if found is None:
                    continue
                inner = span(hole, sy) if hole else None
                pieces = []
                if inner is None:
                    pieces.append(found)
                else:
                    if found[0] < inner[0]:
                        pieces.append((found[0], inner[0]))
                    if inner[1] < found[1]:
                        pieces.append((inner[1], found[1]))
                for x0, x1 in pieces:
                    i0 = max(0, math.ceil(x0 * ss - 0.5))
                    i1 = min(wide - 1, math.floor(x1 * ss - 0.5))
                    if i1 < i0:
                        continue
                    for i in range(i0, i1 + 1):
                        if row[i] < alpha:
                            row[i] = alpha
                    if i0 < lo_here:
                        lo_here = i0
                    if i1 > hi_here:
                        hi_here = i1
            if lo_here <= hi_here:
                for i in range(lo_here, hi_here + 1):
                    value = row[i]
                    if value:
                        acc[i // ss] += value
                        row[i] = 0.0
                if lo_here < touched_lo:
                    touched_lo = lo_here
                if hi_here > touched_hi:
                    touched_hi = hi_here
        if touched_lo <= touched_hi:
            base = y * size
            for x in range(touched_lo // ss, touched_hi // ss + 1):
                out[base + x] = acc[x] / (ss * ss)

    # The bloom, added at full-pixel resolution because it is smooth by nature
    # and gains nothing from sampling. A flat mark on a transparent square reads
    # as a diagram; the glow is what makes it read as light, which is what the
    # window is made of.
    cx = cy = (size - 1) / 2
    bloom = size * 0.54
    for y in range(size):
        base = y * size
        dy = y - cy
        for x in range(size):
            dx = x - cx
            d = math.sqrt(dx * dx + dy * dy)
            if d < bloom:
                out[base + x] = min(1.0, out[base + x] + (1.0 - d / bloom) ** 2.8 * 0.09)
    return out


def pixels(size: int) -> bytes:
    """The buffer, coloured white and turned into PNG scanlines."""
    buffer = light(size)
    rows = []
    for y in range(size):
        row = bytearray()
        base = y * size
        for x in range(size):
            alpha = buffer[base + x]
            if alpha <= 0.004:
                row += bytes(4)
                continue
            row += bytes(WHITE) + bytes((round(min(1.0, alpha) * 255),))
        rows.append(bytes(row))
    # Each PNG scanline carries a filter byte; zero means "none".
    return b"".join(bytes(1) + row for row in rows)


def png(size: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels(size), 9))
        + chunk(b"IEND", b"")
    )


def ico(sizes: tuple[int, ...]) -> bytes:
    """PNG-in-ICO, which Windows has read since Vista."""
    images = [png(size) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = b""
    for size, image in zip(sizes, images):
        directory += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(image),
            offset,
        )
        offset += len(image)
    return struct.pack("<HHH", 0, 1, len(images)) + directory + b"".join(images)


def main() -> None:
    for name, size in SIZES.items():
        (HERE / name).write_bytes(png(size))
        print(f"{name} ({size}px)")
    (HERE / "icon.ico").write_bytes(ico(ICO_SIZES))
    print(f"icon.ico ({', '.join(str(s) for s in ICO_SIZES)})")


if __name__ == "__main__":
    main()
