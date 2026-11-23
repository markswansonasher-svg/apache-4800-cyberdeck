"""Checks every STL fits the school printer (<= 175 x 175 footprint, <= 150 tall).
Usage: python check_sizes.py <folder with .stl files>"""
import os
import struct
import sys

BED, HEIGHT = 175.0, 150.0


def bounds(path):
    with open(path, "rb") as f:
        data = f.read()
    pts = []
    if data[:5] == b"solid" and b"facet" in data[:400]:
        for line in data.decode("ascii", "ignore").splitlines():
            line = line.strip()
            if line.startswith("vertex"):
                pts.append(tuple(map(float, line.split()[1:4])))
    else:
        n = struct.unpack_from("<I", data, 80)[0]
        for i in range(n):
            off = 84 + i * 50 + 12
            for k in range(3):
                pts.append(struct.unpack_from("<3f", data, off + 12 * k))
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return [hi[i] - lo[i] for i in range(3)], len(pts) // 3


def main(folder):
    bad = 0
    print(f"\n{'part':<24}{'X':>8}{'Y':>8}{'Z':>8}  fits?")
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".stl"):
            continue
        (x, y, z), tris = bounds(os.path.join(folder, name))
        ok = max(x, y) <= BED and min(x, y) <= BED and z <= HEIGHT
        bad += not ok
        print(f"{name[:-4]:<24}{x:8.1f}{y:8.1f}{z:8.1f}  {'yes' if ok else 'NO  <--'}")
    print("\nAll parts fit." if not bad else f"\n{bad} part(s) too big for the printer!")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "stl"))
