"""Draw the application icon.

    python app/src-tauri/icons/make_icons.py

A generator rather than a checked-in binary, for the same reason a measured
number lives in one place: an icon nobody can regenerate is an icon nobody can
change. No Pillow -- this is polygons and lines, and a PNG encoder for that is
thirty lines, fewer than the argument for adding a dependency.

**The mark is not the orb, and that is deliberate.** It used to be: 34 points
and the lines between them, the same lattice the window draws. It read as a
smudge at 16 pixels, which is the size the taskbar and the alt-tab strip
actually use, and no amount of tuning fixes a mesh whose links are thinner than
a pixel. So the icon is the same idea reduced to what survives: a solid seen
down its corner, three faces, six edges and a centre. Vertices and the lines
between them, still -- just few enough to read at any size.

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

#: The one colour. `--text` from the stylesheet, byte for byte.
WHITE = (255, 252, 247)

#: How much of the tile the solid fills. Enough air around it that the mark is
#: never touching the edges at a size Windows composites it into.
RADIUS = 0.34

#: Alpha per face. The top catches the light and the two sides fall away from
#: it, which is the whole reason a cube reads as a cube rather than a hexagon.
TOP, RIGHT, LEFT = 0.34, 0.17, 0.085

SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}
#: What Windows actually reads. Every size it might ask for, so it never has to
#: scale one down and make a smudge of it.
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def corners(size: int):
    """The centre and the six silhouette vertices, in screen coordinates.

    A cube seen down its long diagonal is a regular hexagon with a Y in it. The
    vertices sit every 60 degrees; the three spokes go to every other one.
    """
    centre = ((size - 1) / 2, (size - 1) / 2)
    radius = size * RADIUS
    points = []
    for i in range(6):
        angle = math.radians(30 + 60 * i)
        points.append(
            (centre[0] + radius * math.cos(angle), centre[1] - radius * math.sin(angle))
        )
    return centre, points


def inside(poly, x: float, y: float) -> bool:
    """Crossing test. Convex quads only, so the cheap one is right."""
    hit = False
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            hit = not hit
    return hit


def light(size: int) -> list[float]:
    """One greyscale buffer: how much light reaches each pixel.

    Everything is drawn into this and coloured afterwards, so an edge crossing
    a face adds to it rather than painting over it.
    """
    centre, v = corners(size)
    buffer = [0.0] * (size * size)

    # The three faces, brightest first. Two samples each way, which is enough
    # antialiasing for an edge that also has a line drawn along it.
    faces = (
        ([centre, v[0], v[1], v[2]], TOP),
        ([centre, v[4], v[5], v[0]], RIGHT),
        ([centre, v[2], v[3], v[4]], LEFT),
    )
    for poly, alpha in faces:
        lo_x = max(0, int(min(p[0] for p in poly)) - 1)
        hi_x = min(size, int(max(p[0] for p in poly)) + 2)
        lo_y = max(0, int(min(p[1] for p in poly)) - 1)
        hi_y = min(size, int(max(p[1] for p in poly)) + 2)
        for y in range(lo_y, hi_y):
            row = y * size
            for x in range(lo_x, hi_x):
                hits = 0
                for dy in (0.25, 0.75):
                    for dx in (0.25, 0.75):
                        if inside(poly, x + dx, y + dy):
                            hits += 1
                if hits:
                    buffer[row + x] += alpha * hits / 4

    def splat(px: float, py: float, r: float, weight: float) -> None:
        lo_x, hi_x = int(px - r - 1), int(px + r + 2)
        lo_y, hi_y = int(py - r - 1), int(py + r + 2)
        for y in range(max(0, lo_y), min(size, hi_y)):
            row = y * size
            dy = y - py
            for x in range(max(0, lo_x), min(size, hi_x)):
                dx = x - px
                d = math.sqrt(dx * dx + dy * dy)
                if d > r:
                    continue
                fall = 1.0 - d / r
                buffer[row + x] += weight * fall * fall

    def line(a, b, width: float, weight: float) -> None:
        steps = max(2, int(math.dist(a, b) * 2))
        for s in range(steps + 1):
            t = s / steps
            splat(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, width, weight)

    # Six edges around, three spokes in. The spokes are softer on purpose: they
    # are the inside of the solid, and at 16 pixels three bright lines meeting
    # at a point turn into a blob.
    width = max(0.75, size * 0.026)
    for i in range(6):
        line(v[i], v[(i + 1) % 6], width, 0.5)
    for i in (0, 2, 4):
        line(centre, v[i], width * 0.85, 0.32)

    # The bloom. A flat mark on a transparent square reads as a diagram; the
    # glow is what makes it read as light, which is what the window is made of.
    bloom = size * 0.52
    for y in range(size):
        row = y * size
        dy = y - centre[1]
        for x in range(size):
            dx = x - centre[0]
            d = math.sqrt(dx * dx + dy * dy)
            if d < bloom:
                buffer[row + x] += (1.0 - d / bloom) ** 2.6 * 0.1

    return buffer


def pixels(size: int) -> bytes:
    """The buffer, coloured white and turned into PNG scanlines."""
    buffer = light(size)
    rows = []
    for y in range(size):
        row = bytearray()
        base = y * size
        for x in range(size):
            alpha = min(1.0, buffer[base + x])
            if alpha <= 0.004:
                row += bytes(4)
                continue
            row += bytes(WHITE) + bytes((round(alpha * 255),))
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
