# -*- coding: utf-8 -*-
"""把 NASA 土星五号 9 个零件 STL 平移到「几何包围盒中心 = 原点」。

为什么：装配文件数据正确（两次 NX 几何实测证明），但 NX 交互显示层对
「几何远离零件原点的网格组件」会画错位置（级间段、S-II 两件复现漂移）。
把几何归到原点附近，显示层没有任何大偏移可以出错。

输出：output/enterprise/downloaded_centered/*.stl（ASCII）
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "output", "enterprise", "downloaded")
DST = os.path.join(ROOT, "output", "enterprise", "downloaded_centered")

FILES = [
    "S-IC bottom.stl", "S-IC top.stl", "stage 1-2 coupler.stl", "S-II.stl",
    "S-IV b.stl", "lem shroud.stl", "service module.stl",
    "command moduel.stl", "escape tower.stl",
]


def read_tris(path):
    """返回三角片列表 [((x,y,z),(x,y,z),(x,y,z)), ...]，兼容 ASCII/Binary。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) > 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + n * 50 == len(data):
            tris = []
            off = 84
            for _ in range(n):
                off += 12  # normal
                v = []
                for k in range(3):
                    v.append(struct.unpack("<3f", data[off:off + 12]))
                    off += 12
                off += 2  # attr
                tris.append(tuple(v))
            return tris
    tris = []
    vs = []
    for line in data.decode("utf-8", "ignore").splitlines():
        s = line.strip()
        if s.startswith("vertex"):
            vs.append(tuple(float(t) for t in s.split()[1:4]))
            if len(vs) == 3:
                tris.append(tuple(vs))
                vs = []
    return tris


def main():
    os.makedirs(DST, exist_ok=True)
    for fn in FILES:
        p = os.path.join(SRC, fn)
        tris = read_tris(p)
        if not tris:
            print("解析失败: %s" % fn)
            return 2
        xs = [v[0] for t in tris for v in t]
        ys = [v[1] for t in tris for v in t]
        zs = [v[2] for t in tris for v in t]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        cz = (min(zs) + max(zs)) / 2.0

        out = os.path.join(DST, fn)
        with open(out, "w") as f:
            f.write("solid centered\n")
            for t in tris:
                f.write("  facet normal 0 0 0\n    outer loop\n")
                for v in t:
                    f.write("      vertex %r %r %r\n" % (v[0] - cx, v[1] - cy, v[2] - cz))
                f.write("    endloop\n  endfacet\n")
            f.write("endsolid centered\n")
        print("%-28s 偏移 (%.1f, %.1f, %.1f)  尺寸 %.1f x %.1f x %.1f" % (
            fn, cx, cy, cz, max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
