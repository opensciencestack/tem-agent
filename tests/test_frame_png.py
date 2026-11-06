"""Run: .venv\\Scripts\\python.exe tests\\test_frame_png.py"""
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo_server import to_png  # noqa: E402


def test_to_png():
    png = to_png(np.arange(512 * 512, dtype=float).reshape(512, 512))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (256, 256)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    assert len(raw) == h * (w + 1)
    assert raw[1] == 0 and raw[-1] == 255   # stretched to the full 8-bit range
    assert to_png(np.full((100, 100), 7.0))[:8] == png[:8]   # flat frame: no divide by zero


if __name__ == "__main__":
    test_to_png()
    print("ok")
