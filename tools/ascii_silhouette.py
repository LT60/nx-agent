#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立侧影 —— 用真实 STL 顶点 + 布局坐标直接画，**完全不经过 NX**。

★★★ 为什么必须有这个（2026-10-10，我踩的最大的坑）★★★
  当时的现象：NX 报告的 Z 区间连续、轴心偏心 0~1.26mm、空隙 0 处（全是好的），
  但 NX 渲染出来的图里零件是**散的**。两个数据互相矛盾。

  这个工具就是用来**判"到底谁在说谎"**的：
    · 本工具画出来连续 → 布局对，问题在 NX 渲染/小平面体显示侧
    · 本工具画出来也散 → 布局还错，继续修布局

  它不受 NX 的包围盒、相机、显示设置影响，是第三方视角。

用法：
    python tools/ascii_silhouette.py [--width 46] [--height 34]
"""

import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUT = os.path.join(ROOT, "output", "enterprise", "saturn_layout.json")
SRC = os.path.join(ROOT, "output", "enterprise", "downloaded")


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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    W, H = 46, 34
    lay_path = LAYOUT
    for i, a in enumerate(argv):
        if a == "--width" and i + 1 < len(argv):
            W = int(argv[i + 1])
        elif a == "--height" and i + 1 < len(argv):
            H = int(argv[i + 1])
        elif not a.startswith("-"):
            lay_path = a

    with io.open(lay_path, "r", encoding="utf-8") as fh:
        lay = json.load(fh)
    src_dir = os.path.join(os.path.dirname(lay_path), "downloaded")
    parts = lay.get("parts") or []
    zmax = float(lay.get("total_height") or 1.0)

    # 横向范围按真实数据算，不拍脑袋
    xs = []
    for p in parts:
        src = os.path.join(src_dir, p["file"])
        if not os.path.isfile(src):
            continue
        vs = stl_vertices(src)
        M, o = p["matrix"], p["origin"]
        st = max(1, len(vs) // 1200)
        for (x, y, z) in vs[::st]:
            xs.append(o[0] + M[0][0] * x + M[0][1] * y + M[0][2] * z)
    half = max(1.0, max(abs(v) for v in xs) * 1.05)

    grid = [[" "] * W for _ in range(H)]
    for i, p in enumerate(parts):
        src = os.path.join(src_dir, p["file"])
        if not os.path.isfile(src):
            continue
        vs = stl_vertices(src)
        M, o = p["matrix"], p["origin"]
        ch = "#" if i % 2 == 0 else "="
        st = max(1, len(vs) // 1200)
        for (x, y, z) in vs[::st]:
            wx = o[0] + M[0][0] * x + M[0][1] * y + M[0][2] * z
            wz = o[2] + M[2][0] * x + M[2][1] * y + M[2][2] * z
            cx = int(round((W / 2.0 - 1) + wx / half * (W / 2.0 - 1)))
            cy = int(round((H - 1) * (1.0 - wz / zmax)))
            if 0 <= cx < W and 0 <= cy < H:
                grid[cy][cx] = ch

    print("独立侧影（真实 STL 顶点 + 布局坐标，不经过 NX）")
    for r in grid:
        print("  |" + "".join(r))
    print("  +" + "-" * W)
    print("  底 z=0 ── 顶 z=%.0f mm ｜ 横向 ±%.0f mm" % (zmax, half))

    # 顺便报每段的实际 Z 区间，看有没有空隙
    segs = []
    for p in parts:
        src = os.path.join(src_dir, p["file"])
        if not os.path.isfile(src):
            continue
        vs = stl_vertices(src)
        M, o = p["matrix"], p["origin"]
        zs = [o[2] + M[2][0] * x + M[2][1] * y + M[2][2] * z for (x, y, z) in vs]
        segs.append((min(zs), max(zs), p["label"]))
    segs.sort()
    gaps = [(segs[i][0] - segs[i - 1][1], segs[i - 1][2], segs[i][2])
            for i in range(1, len(segs)) if segs[i][0] - segs[i - 1][1] > 0.5]
    if gaps:
        print("\n  ★ 有 %d 处真实空隙（间隙>0.5mm）：" % len(gaps))
        for g, a, b in gaps:
            print("     %-22s → %-22s  %.1f mm" % (a[:22], b[:22], g))
    else:
        print("\n  ✔ 相邻段无空隙（间隙 <= 0.5mm）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
