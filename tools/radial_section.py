#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印单个零件的**真实径向剖面** —— 看它是开口套筒还是实心柱。

背景（2026-10-10）：我按"包围盒重叠"算套接量，结果 9 个接头里7 个没真贴合。
原因之一就是没搞清楚套筒到底是什么形状 ——
有壁厚的是套筒（内壁r_in、外壁 r_out），上下都开口；
没壁厚的是实心段。套接必须发生在**内孔**里，不是包围盒里。

用法：
    python tools/radial_section.py "1-2 级间段" [档高mm]
"""
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUT = os.path.join(ROOT, "output", "enterprise", "saturn_layout.json")


def stl_vertices(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
    if 84 + n * 50 != size:
        out = []
        for line in io.open(path, "r", errors="ignore"):
            t = line.strip()
            if t.startswith("vertex"):
                q = t.split()
                out.append((float(q[1]), float(q[2]), float(q[3])))
        return out
    out = []
    with open(path, "rb") as fh:
        fh.seek(84)
        while True:
            rec = fh.read(50)
            if len(rec) < 50:
                break
            v = struct.unpack("<12fH", rec)
            for k in range(3):
                out.append((v[3 + k * 3], v[4 + k * 3], v[5 + k * 3]))
    return out


def world_zr(part, src_dir):
    """把 STL 顶点变成世界坐标下的 (z, r)。"""
    vs = stl_vertices(os.path.join(src_dir, part["file"]))
    M, o = part["matrix"], part["origin"]
    out = []
    for (a, b, c) in vs:
        wx = o[0] + M[0][0] * a + M[0][1] * b + M[0][2] * c
        wy = o[1] + M[1][0] * a + M[1][1] * b + M[1][2] * c
        wz = o[2] + M[2][0] * a + M[2][1] * b + M[2][2] * c
        out.append((wz, (wx * wx + wy * wy) ** 0.5))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    key = argv[0]
    band = float(argv[1]) if len(argv) > 1 else 4.0

    lay_path = os.path.join(ROOT, "output", "enterprise", "saturn_layout.json")
    with io.open(lay_path, "r", encoding="utf-8") as fh:
        lay = json.load(fh)
    src_dir = os.path.join(os.path.dirname(lay_path), "downloaded")

    parts = lay["parts"]
    if key in ("all", "ALL"):
        chosen = parts
    else:
        chosen = [p for p in parts
                  if p["label"] == key or key in p["label"] or key in p["file"]]
        if not chosen:
            print("没找到零件 %r。可选：" % key)
            for p in parts:
                print("   %-24s  (%s)" % (p["label"], p["file"]))
            return 2

    for p in chosen:
        ZR = world_zr(p, src_dir)
        z0, z1 = min(z for z, _ in ZR), max(z for z, _ in ZR)
        print("\n" + "=" * 78)
        print("%s   (%s)   z %.1f → %.1f mm"
              % (p["label"], p["file"], z0, z1))
        print("=" * 78)
        print("%-14s %9s %9s %10s  %s"
              % ("高度档", "内壁 r", "外壁 r", "壁厚", "判读"))
        z = z0
        while z < z1 - 1e-6:
            z2 = min(z + band, z1)
            rs = [r for zz, r in ZR if z <= zz <= z2]
            if rs:
                rmin, rmax = min(rs), max(rs)
                thick = rmax - rmin
                if thick > 3.0:
                    kind = "套筒（内孔 r=%.1f，外壁 %.1f）" % (rmin, rmax)
                elif rmax > rmin + 1.0:
                    kind = "薄壁 %.1f" % thick
                else:
                    kind = "实心"
                print("%5.0f-%-8.0f %9.1f %9.1f %10.1f  %s"
                      % (z, z2, rmin, rmax, thick, kind))
            z = z2
    return 0


if __name__ == "__main__":
    sys.exit(main())