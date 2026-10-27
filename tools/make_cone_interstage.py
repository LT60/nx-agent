#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成锥形 2-3 级间段（补造件）—— 套件里没有这个零件。

★★★ 为什么需要（2026-10-10，用户指出）★★★
  真机的 S-II/S-IVB 级间段是**锥台**：Ø10.1m（对 S-II）收到 Ø6.6m（对 S-IVB）。
  而 NASA 打印套件里只有两个**圆柱形** coupler（Ø90.4，带两道鼓环），
  建模者把同一个"1-2 coupler"用了两次 —— 2-3 位置装出来就是个胖圆柱，
  用户一眼看出不对。

  套件缺零件 → 只能补造。本工具生成一个**空心锥台壳体** STL：
    · 下端外径 = 级间段现有 Ø90.4（对 S-II，视觉连续）
    · 上端外径 = 按 S-IVB 实测body Ø63.5~66.6 + 壁厚 + 间隙定（喷管/筒身要从上口穿出来）
    · 高度 = 96.9（与套件 coupler 一致，不另改总高比例）
    · 壁厚 3.5mm
  明确标注为**补造件**（文件名前缀 CUSTOM），不冒充套件原件。

  ★ 教训：第一版量 coupler 直径时用 hypot(局部x, 局部z) —— 但这批 STL 的
  **建模原点是偏的**（coupler.stl 轴心偏了 8.59mm），量出 Ø107 的鬼值。
  半径必须以**每档自己的轴心**算。

用法：
    python tools/make_cone_interstage.py            # 生成 STL
    python tools/make_cone_interstage.py --bottom 90.4 --top 77 --height 96.9
"""

import argparse
import io
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "enterprise", "downloaded",
                   "CUSTOM interstage 2-3 cone.stl")


def write_ascii_stl(path, facets):
    """facets: [(normal, (p1,p2,p3)), ...]"""
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("solid cone_interstage\n")
        for (n, tri) in facets:
            fh.write("facet normal %.6e %.6e %.6e\n" % n)
            fh.write("  outer loop\n")
            for p in tri:
                fh.write("    vertex %.6e %.6e %.6e\n" % p)
            fh.write("  endloop\n")
            fh.write("endfacet\n")
        fh.write("endsolid cone_interstage\n")
    return os.path.getsize(path)


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def unit(v):
    L = math.sqrt(sum(x * x for x in v)) or 1.0
    return (x / L for x in v)


def facet(p1, p2, p3, want_outward_axis=None, radial=False, axis_sign=0):
    """生成一个 facet，法向按需定向：
    radial=True → 法向的径向分量取正（外表面）/取负（内表面）
    axis_sign   → 法向的 Y 分量符号（端面环）
    """
    e1 = sub(p2, p1)
    e2 = sub(p3, p1)
    n = tuple(unit(cross(e1, e2)))
    nx, ny, nz = n
    if radial:
        cx = (p1[0] + p2[0] + p3[0]) / 3.0
        cz = (p1[2] + p2[2] + p3[2]) / 3.0
        rr = math.hypot(cx, cz) or 1.0
        radial_comp = (nx * cx + nz * cz) / rr
        if radial_comp < 0:
            nx, ny, nz = -nx, -ny, -nz
            p1, p3 = p3, p1
    if axis_sign:
        if ny * axis_sign < 0:
            nx, ny, nz = -nx, -ny, -nz
            p1, p3 = p3, p1
    return ((nx, ny, nz), (p1, p2, p3))


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成锥形 2-3 级间段（补造件）")
    ap.add_argument("--bottom", type=float, default=90.4,
                    help="下端外径 mm（对 S-II）")
    ap.add_argument("--top", type=float, default=77.0,
                    help="上端外径 mm（对 S-IVB，须 ≥ S-IVB筒身Ø66.6+壁厚+间隙）")
    ap.add_argument("--height", type=float, default=96.9)
    ap.add_argument("--wall", type=float, default=3.5)
    ap.add_argument("--segments", type=int, default=96)
    args = ap.parse_args(argv)

    H = args.height
    N = args.segments
    r1o = args.bottom / 2.0
    r2o = args.top / 2.0
    r1i = r1o - args.wall
    r2i = r2o - args.wall
    y0, y1 = -H / 2.0, H / 2.0

    def pt(r, y, k):
        th = 2 * math.pi * k / N
        return (r * math.cos(th), y, r * math.sin(th))

    facets = []
    for k in range(N):
        k2 = (k + 1) % N
        # 外表面（法向朝外）
        a, b = pt(r1o, y0, k), pt(r1o, y0, k2)
        c, d = pt(r2o, y1, k2), pt(r2o, y1, k)
        facets.append(facet(a, b, c, radial=True))
        facets.append(facet(a, c, d, radial=True))
        # 内表面（法向朝轴心）
        a2, b2 = pt(r1i, y0, k), pt(r1i, y0, k2)
        c2, d2 = pt(r2i, y1, k2), pt(r2i, y1, k)
        facets.append(facet(a2, c2, b2, radial=True))
        facets.append(facet(a2, d2, c2, radial=True))
        # 底端环（法向 -Y）
        facets.append(facet(a, a2, b2, axis_sign=-1))
        facets.append(facet(a, b2, b, axis_sign=-1))
        # 顶端环（法向 +Y）
        facets.append(facet(d, d2, c2, axis_sign=+1))
        facets.append(facet(d, c2, c, axis_sign=+1))

    n = write_ascii_stl(OUT, facets)
    print("已写 %s（%.0f KB，%d 个三角面）" % (OUT, n / 1024.0, len(facets)))
    print("  下端 Ø%.1f（外）/ Ø%.1f（内）" % (2 * r1o, 2 * r1i))
    print("  上端 Ø%.1f（外）/ Ø%.1f（内）" % (2 * r2o, 2 * r2i))
    print("  高 %.1f ｜ 壁厚 %.1f" % (H, args.wall))
    print("  ★ 这是补造件（套件只有圆柱形 coupler），文件名以 CUSTOM 标注")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())