#!/usr/bin/env python3
"""
The TEM agent's two-stage focus loop (rule policy "focus2", with the survey
setup in front of it) written as a plain AutoScript script: no agent, no policy,
no tool layer. Each step is the same AutoScript call autoscript_interface.py makes.

    .venv\\Scripts\\python.exe direct_focus_sweep.py --host 65.1.231.167

It leaves the beam blanked and the column valve closed when it ends, even on an error.
"""

import argparse
import math

import numpy as np
from autoscript_tem_microscope_client import TemMicroscopeClient
from autoscript_tem_microscope_client.enumerations import OpticalMode

STAGES = [(300e-9, 7), (100e-9, 5)]   # (half range, points): coarse, then fine
DETECTOR = "HAADF"


def read_state(tem):
    pos = tem.specimen.stage.position
    print(f"  optical mode      {tem.optics.optical_mode}")
    print(f"  defocus           {tem.optics.focusing.stem.objective.defocus * 1e9:+.1f} nm")
    print(f"  stage x, y, z     {pos.x * 1e6:.2f}, {pos.y * 1e6:.2f}, {pos.z * 1e6:.2f} um")
    print(f"  stage tilt a      {math.degrees(pos.a):.1f} deg")
    print(f"  column valves     {tem.vacuum.column_valves.state}")
    print(f"  beam blanked      {tem.optics.blanker.is_beam_blanked}")


def score(tem, size, dwell):
    image = tem.acquisition.acquire_stem_image(scanning_detector=DETECTOR, size=size, dwell_time=dwell)
    arr = np.asarray(image.data, dtype=float)
    mean = arr.mean()
    return float(arr.var() / mean ** 2) if abs(mean) > 1e-9 else 0.0


def sweep(tem, center, half_range, points, size, dwell):
    step = 2 * half_range / (points - 1)
    results = []
    for i in range(points):
        target = center - half_range + i * step
        tem.optics.focusing.stem.objective.defocus = target
        actual = tem.optics.focusing.stem.objective.defocus
        s = score(tem, size, dwell)
        results.append((actual, s))
        print(f"  {actual * 1e9:+8.1f} nm -> normalized variance {s:.6f}")
    return max(results, key=lambda r: r[1])


def main():
    parser = argparse.ArgumentParser(description="Two-stage STEM focus sweep, straight on AutoScript.")
    parser.add_argument("--host", default="65.1.231.167")
    parser.add_argument("--port", type=int, default=7521)
    parser.add_argument("--size", type=int, default=256, help="image size in pixels: 128, 256, 512, ...")
    parser.add_argument("--dwell", type=float, default=2e-6, help="dwell time per pixel, seconds")
    args = parser.parse_args()

    tem = TemMicroscopeClient()
    print(f"1. Connecting to {args.host}:{args.port}")
    tem.connect(args.host, args.port)
    try:
        print(f"   offline (simulator): {tem.service.autoscript.server.is_offline}")

        print("2. Microscope state before")
        read_state(tem)

        print("3. Setup: open column valves, unblank, STEM mode")
        tem.vacuum.column_valves.open()
        tem.optics.blanker.unblank()
        tem.optics.optical_mode = OpticalMode.STEM

        start = tem.optics.focusing.stem.objective.defocus
        best = (start, -1.0)
        center = start
        for n, (half_range, points) in enumerate(STAGES, 1):
            print(f"4.{n} Sweep {n}: {points} points, +/-{half_range * 1e9:.0f} nm around {center * 1e9:+.1f} nm")
            found = sweep(tem, center, half_range, points, args.size, args.dwell)
            if found[1] > best[1]:
                best = found
            center = best[0]

        print(f"5. Restoring best defocus {best[0] * 1e9:+.1f} nm (score {best[1]:.6f})")
        tem.optics.focusing.stem.objective.defocus = best[0]
        landed = tem.optics.focusing.stem.objective.defocus
        print(f"   landed at {landed * 1e9:+.1f} nm, {abs(landed - best[0]) * 1e9:.1f} nm from the target")

        print("6. Microscope state after")
        read_state(tem)
    finally:
        print("7. Blanking the beam, closing the column valves, disconnecting")
        for step in (tem.optics.blanker.blank, tem.vacuum.column_valves.close):
            try:
                step()
            except Exception as e:
                print(f"   {step.__name__} failed: {e}")
        tem.disconnect()


if __name__ == "__main__":
    main()
