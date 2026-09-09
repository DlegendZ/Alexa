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

#: The orb at rest, warmed slightly so the icon reads at 16 pixels. Amber is
#: work happening on this machine, which is what the program mostly is.
CORE = (232, 178, 96)
EDGE = (120, 78, 24)

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
    """One RGBA image: a soft-edged disc with a highlight up and to the left."""
    centre = (size - 1) / 2
    radius = size * 0.46
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            dx = x - centre
            dy = y - centre
            distance = (dx * dx + dy * dy) ** 0.5
            # Anti-aliased by distance: a hard edge at 16 pixels reads square.
            alpha = max(0.0, min(1.0, (radius - distance) * 1.4))
            if alpha <= 0:
                row += b"\0\0\0\0"
                continue
            # The highlight is the same one the canvas draws, at 35% across.
            hx = (x - size * 0.35) / radius
            hy = (y - size * 0.35) / radius
            lit = max(0.0, 1.0 - ((hx * hx + hy * hy) ** 0.5))
            mix = lit**1.6
            channels = tuple(
                round(EDGE[i] + (CORE[i] - EDGE[i]) * (0.25 + 0.75 * mix)) for i in range(3)
            )
            row += bytes(channels) + bytes((round(alpha * 255),))
        rows.append(bytes(row))
    return b"".join(b"\0" + row for row in rows)


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
