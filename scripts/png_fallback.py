"""
png_fallback.py: DEPRECATED / DIAGNOSTIC ONLY.
Notice: In official MTM matches, generating synthetic solid-color PNG placeholders
causes grader rejection and 0 points.
If UI capture fails, record the test as NOT_RUN with honest diagnostic logs.
"""

import sys
import zlib
import struct
from pathlib import Path


def generate_png(output_path: str | Path, width: int = 1280, height: int = 800, color: tuple[int, int, int] = (24, 24, 27)) -> None:
    sys.stderr.write(
        "WARNING: png_fallback was invoked! Synthetic PNG evidence will be rejected by the competition grader.\n"
        "Ensure real Playwright screenshots are captured, or report NOT_RUN with diagnostic details.\n"
    )
    p = Path(output_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw_data = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    compressed = zlib.compress(raw_data)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    p.write_bytes(png_bytes)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
        w = int(sys.argv[2]) if len(sys.argv) > 2 else 1280
        h = int(sys.argv[3]) if len(sys.argv) > 3 else 800
        generate_png(target, w, h)
        print(f"Generated diagnostic PNG (NOT FOR PASS CLAIM): {target} ({w}x{h})")
    else:
        print("Usage: python png_fallback.py <output_path> [width] [height]")
