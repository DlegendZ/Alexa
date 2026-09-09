"""Draw the application icon.

The icon is the orb, because the orb is what the app is, and because deriving
it from the same handful of numbers means it cannot drift away from the window
it sits above. A generator rather than a checked-in binary for the same reason
a measured number lives in one place: an icon nobody can regenerate is an icon
nobody can change.

    python app/src-tauri/icons/make_icons.py

No Pillow. This is a lattice of points, the lines between the near ones, and a
bloom behind them; a PNG encoder for that is thirty lines -- fewer than the
argument for adding a dependency.

The geometry is `app/src/lib/orb.js`, held still. Same golden-angle lattice,
same link threshold, same white. What it cannot share is the code, because one
of them is a canvas at sixty frames a second and the other is a bytes object,
so the numbers are repeated here on purpose and are the thing to keep in step.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The web, in the white the orb is at rest. Colour in this program means a
#: tool is running -- amber on this machine, teal off it -- and an icon cannot
#: be in one of those states, so it is the resting one.
WHITE = (255, 252, 247)

#: The orb's own numbers, so the icon and the thing it sits above are one
#: object. Thirty-four points and a 0.92 threshold is exactly `listening`,
#: which is the state the app is in whenever it is waiting for you -- the
#: right thing for a picture of it to be doing.
NODES = 34
LINK = 0.92
#: Held at an angle where the lattice reads as a sphere rather than as a ring.
YAW = 0.9
ROLL = 0.45

SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}
#: What Windows actually reads. Every size it might ask for, so it never has to
#: scale one down and make a smudge of it.
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def lattice(count: int) -> list[tuple[float, float, float]]:
    """Points spread evenly over a sphere, by the golden angle, then turned.

    Evenly is the whole requirement: random points clump, and a clump in a
    particle web is a bright blob that reads as a fault in the drawing.
    """
    golden = math.pi * (3 - math.sqrt(5))
    points = []
    cos_y, sin_y = math.cos(YAW), math.sin(YAW)
    cos_r, sin_r = math.cos(ROLL), math.sin(ROLL)
    for i in range(count):
        y = 1 - (i / (count - 1)) * 2
        ring = math.sqrt(max(0.0, 1 - y * y))
        theta = golden * i
        x, z = math.cos(theta) * ring, math.sin(theta) * ring
        x1 = x * cos_y + z * sin_y
        z1 = z * cos_y - x * sin_y
        y2 = y * cos_r - z1 * sin_r
        z2 = z1 * cos_r + y * sin_r
        points.append((x1, y2, z2))
    return points


def light(size: int) -> list[float]:
    """One greyscale buffer: how much light reaches each pixel.

    Everything is drawn into this and coloured afterwards, which is what keeps
    the layers from seaming -- a link crossing a node adds to it rather than
    painting over it.
    """
    # Below about 96 pixels a web is not a thing a screen can draw: the links
    # are thinner than a pixel and the nodes land on top of each other. So the
    # small sizes are the same object with fewer points, drawn heavier and
    # brighter -- a lit constellation rather than a diagram of one. This is
    # what an icon set is for; a 16-pixel copy of the 512 is a smudge.
    #
    # The large sizes are the orb exactly: same lattice, same threshold, same
    # white. An icon that merely resembles the thing it launches is an icon
    # that drifts away from it the first time either is touched.
    small = size < 96
    count = 14 if small else NODES
    points = lattice(count)
    centre = (size - 1) / 2
    radius = size * (0.30 if small else 0.33)

    buffer = [0.0] * (size * size)

    def splat(px: float, py: float, r: float, weight: float) -> None:
        """One soft dot. Everything here is made of these."""
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

    # The bloom, first and underneath. A flat web on a transparent square reads
    # as a diagram; the glow is what makes it read as light, which is the whole
    # idea of the orb.
    bloom = size * 0.5
    halo = 0.3 if small else 0.13
    for y in range(size):
        row = y * size
        dy = y - centre
        for x in range(size):
            dx = x - centre
            d = math.sqrt(dx * dx + dy * dy)
            if d < bloom:
                buffer[row + x] += (1.0 - d / bloom) ** 2.6 * halo

    # The core: a small light inside the web, or the thing is a hollow shell
    # and the eye has nowhere to rest.
    splat(centre, centre, radius * (0.9 if small else 0.5), 0.62 if small else 0.34)

    screen = [(centre + x * radius, centre + y * radius, z) for x, y, z in points]

    # The links. Drawn by walking each one and splatting, rather than by
    # measuring every pixel against every segment -- that is 147 million
    # distance checks at 512 and this is about twenty thousand.
    width = max(0.75, size * (0.02 if small else 0.009))
    for i in range(count):
        ax, ay, az = screen[i]
        for j in range(i + 1, count):
            bx, by, bz = screen[j]
            d = math.dist(points[i], points[j])
            if d > LINK:
                continue
            close = 1 - d / LINK
            depth = (az + bz + 2) / 4
            weight = close * close * (0.3 + depth * 0.6)
            steps = max(2, int(math.dist((ax, ay), (bx, by)) * 2))
            for s in range(steps + 1):
                t = s / steps
                splat(ax + (bx - ax) * t, ay + (by - ay) * t, width, weight / 2.6)

    # The nodes, last, so they sit on top of their own lines.
    for x, y, z in screen:
        near = (z + 1) / 2
        spread = size * (0.05 if small else 0.015)
        splat(x, y, max(1.0, spread) * (0.65 + near * 0.6), (0.8 if small else 0.5) + near * 0.45)

    return buffer


def pixels(size: int) -> bytes:
    """The buffer, coloured white and turned into PNG scanlines."""
    buffer = light(size)
    rows = []
    for y in range(size):
        row = bytearray()
        base = y * size
        for x in range(size):
            value = buffer[base + x]
            alpha = min(1.0, value)
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
