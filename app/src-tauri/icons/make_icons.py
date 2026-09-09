"""Draw the application icon.

The icon is the orb, because the orb is what the app is, and because deriving
it from the same three numbers means it cannot drift away from the window it
sits above. A generator rather than a checked-in binary for the same reason a
measured number lives in one place: an icon nobody can regenerate is an icon
nobody can change.

    python app/src-tauri/icons/make_icons.py

No Pillow. This is a circle with a gradient in it, and a PNG encoder for that
is thirty lines -- fewer than the argument for adding a dependency.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The orb at rest, in the window's own coral. The icon is the orb because the
#: orb is what the app is, and deriving it from the same handful of numbers
#: means it cannot drift away from the thing it sits above.
CORE = (247, 186, 150)
MID = (217, 119, 87)
EDGE = (108, 52, 30)
#: The bloom around it. A flat disc reads as a bullet point at 16 pixels; the
#: glow is what makes it read as light, which is the whole idea of the orb.
GLOW = (217, 119, 87)

SIZES = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
}
#: What Windows actually reads. Every size it might ask for, so it never has to
#: scale one down and make a smudge of it.
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def pixels(size: int) -> bytes:
    """One RGBA image: a lit sphere inside a soft bloom.

    Three layers, the same three the canvas draws -- bloom, body, highlight --
    because an icon that is a flat filled circle looks like a status dot and
    the orb is meant to look like a light. There is deliberately no outline:
    an edge is what made the first version of the orb itself look like a
    widget.
    """
    centre = (size - 1) / 2
    body = size * 0.34
    bloom = size * 0.5
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            dx = x - centre
            dy = y - centre
            d = (dx * dx + dy * dy) ** 0.5

            # The bloom: falls off to nothing well before the edge, so the
            # icon has air around it at every size.
            halo = max(0.0, 1.0 - d / bloom) ** 2.2 * 0.55

            if d <= body:
                # Anti-aliased by distance; a hard edge at 16 px reads square.
                solid = min(1.0, (body - d) * 1.6)
                # The highlight sits up and to the left, as it does on canvas.
                hx = (x - size * 0.36) / body
                hy = (y - size * 0.34) / body
                lit = max(0.0, 1.0 - ((hx * hx + hy * hy) ** 0.5)) ** 1.5
                # Core to mid across the sphere, mid to edge at the rim.
                rim = (d / body) ** 2
                channels = []
                for i in range(3):
                    base = MID[i] + (EDGE[i] - MID[i]) * rim
                    channels.append(round(base + (CORE[i] - base) * lit))
                alpha = max(solid, halo)
                # Where the bloom is stronger than the body edge, blend toward
                # the glow colour so the two layers meet without a seam.
                if halo > solid:
                    channels = [
                        round(c + (GLOW[i] - c) * (halo - solid))
                        for i, c in enumerate(channels)
                    ]
                row += bytes(channels) + bytes((round(min(1.0, alpha) * 255),))
            elif halo > 0.004:
                row += bytes(GLOW) + bytes((round(halo * 255),))
            else:
                row += bytes(4)
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
