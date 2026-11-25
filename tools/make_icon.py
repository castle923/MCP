"""Draws hub-desk's icon (a pixel monitor with a chalk-green screen) and writes hub_desk/web/hub-desk.ico.

Standard library only: PNG images inside an ICO container. Run again after changing the drawing.
"""
import struct
import zlib
from pathlib import Path

# 16 x 16. One character per pixel; '.' is transparent.
ART = [
    "................",
    ".GGGGGGGGGGGGGG.",
    ".GddddddddddddG.",
    ".GdyyyyyddddddG.",
    ".GddddddddddddG.",
    ".GdgggggggddddG.",
    ".GddddddddddddG.",
    ".GdcccccddddddG.",
    ".GddddddddddddG.",
    ".GGGGGGGGGGGGGG.",
    ".......bb.......",
    ".......bb.......",
    "....bbbbbbbb....",
    "...kkkkkkkkkk...",
    "...kmkmkmkmkk...",
    "...kkkkkkkkkk...",
]
COL = {
    "G": (245, 192, 78), "d": (24, 43, 34), "y": (245, 192, 78), "g": (126, 217, 160),
    "c": (122, 162, 247), "b": (150, 112, 58), "k": (69, 76, 89), "m": (215, 218, 224),
}


def png(size: int) -> bytes:
    cell = size / 16
    rows = []
    for y in range(size):
        line = bytearray([0])  # filter: none
        for x in range(size):
            ch = ART[int(y / cell)][int(x / cell)]
            line += bytes(COL[ch] + (255,)) if ch in COL else b"\0\0\0\0"
        rows.append(bytes(line))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    imgs = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    off, entries = 6 + 16 * len(imgs), b""
    for s, data in zip(sizes, imgs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), off)
        off += len(data)
    return head + entries + b"".join(imgs)


if __name__ == "__main__":
    assert all(len(r) == 16 for r in ART) and len(ART) == 16
    out = Path(__file__).resolve().parent.parent / "hub_desk" / "web"
    (out / "hub-desk.ico").write_bytes(ico())
    (out / "hub-desk-256.png").write_bytes(png(256))
    print(out / "hub-desk.ico")
