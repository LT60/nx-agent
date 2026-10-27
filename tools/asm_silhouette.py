# -*- coding: utf-8 -*-
"""把 NX 导出的装配几何渲染成 **ASCII 三视图**，让我在终端里"看见"形状。

为什么需要它：
  用户连续两轮说「形状不对/不像企业号」，而**我看不到 PNG**（模型不支持读图）。
  光靠包围盒数字判断"像不像"是瞎猜。所以把 NX 给的 origin+matrix+本地几何
  投影成正交三视图（俯视/前视/侧视），用字符画出来 —— 剪影一眼就能判断。

★ 这不是"用网页画图"，是**拿 NX 的数据做一次终端里的自检**。

用法：
    python tools/asm_silhouette.py output/enterprise/assembly_v2_from_nx.json
"""

from __future__ import print_function

import argparse
import json
import math
import os
import sys

CHARS = "123456789abc"


def tf(org, m, p):
    return (org[0] + m[0] * p[0] + m[1] * p[1] + m[2] * p[2],
            org[1] + m[3] * p[0] + m[4] * p[1] + m[5] * p[2],
            org[2] + m[6] * p[0] + m[7] * p[1] + m[8] * p[2])


def edges_of(comp, n_rim=72):
    """返回 [(p0, p1), ...] 世界坐标下的棱/轮廓线。

    支持两种数据来源：
      1) local = {kind: box|cyl, ...}   —— 参数化零件（v4/v5 那套）
      2) local_min / local_max          —— 导入的网格件（土星五号那套），
         用它的局部包围盒当方块画（轴对称零件画出来是矩形，够判断堆叠）
    """
    org = comp["origin"]
    m = comp["matrix"] or [1, 0, 0, 0, 1, 0, 0, 0, 1]
    loc = comp.get("local")
    if loc is None and comp.get("local_min") and comp.get("local_max"):
        lo_, hi_ = comp["local_min"], comp["local_max"]
        loc = {"kind": "box", "origin": lo_,
               "lengths": [hi_[i] - lo_[i] for i in range(3)]}
    if loc is None:
        return []
    out = []
    if loc["kind"] == "box":
        o, s = loc["origin"], loc["lengths"]
        c = {}
        for i in range(8):
            c[i] = tf(org, m, (o[0] + (s[0] if i & 1 else 0),
                               o[1] + (s[1] if i & 2 else 0),
                               o[2] + (s[2] if i & 4 else 0)))
        for a, b in ((0, 1), (1, 3), (3, 2), (2, 0),
                     (4, 5), (5, 7), (7, 6), (6, 4),
                     (0, 4), (1, 5), (2, 6), (3, 7)):
            out.append((c[a], c[b]))
    else:
        r = loc["diameter"] / 2.0
        a0 = tf(org, m, tuple(loc["origin"]))
        b0 = tf(org, m, (loc["origin"][0], loc["origin"][1],
                         loc["origin"][2] + loc["height"]))
        ax = (b0[0] - a0[0], b0[1] - a0[1], b0[2] - a0[2])
        L = math.sqrt(sum(v * v for v in ax)) or 1.0
        ax = tuple(v / L for v in ax)
        seed = (0.0, 0.0, 1.0) if abs(ax[2]) < 0.9 else (1.0, 0.0, 0.0)
        d = sum(seed[i] * ax[i] for i in range(3))
        u = tuple(seed[i] - ax[i] * d for i in range(3))
        ul = math.sqrt(sum(v * v for v in u)) or 1.0
        u = tuple(v / ul for v in u)
        v = (ax[1] * u[2] - ax[2] * u[1], ax[2] * u[0] - ax[0] * u[2],
             ax[0] * u[1] - ax[1] * u[0])
        rim_a, rim_b = [], []
        for i in range(n_rim):
            t = 2 * math.pi * i / n_rim
            cs, sn = math.cos(t), math.sin(t)
            rim_a.append((a0[0] + r * (cs * u[0] + sn * v[0]),
                          a0[1] + r * (cs * u[1] + sn * v[1]),
                          a0[2] + r * (cs * u[2] + sn * v[2])))
            rim_b.append((b0[0] + r * (cs * u[0] + sn * v[0]),
                          b0[1] + r * (cs * u[1] + sn * v[1]),
                          b0[2] + r * (cs * u[2] + sn * v[2])))
        for i in range(n_rim):
            j = (i + 1) % n_rim
            out.append((rim_a[i], rim_a[j]))
            out.append((rim_b[i], rim_b[j]))
        out.append((a0, b0))  # 轴线
    return out


def render(name, edges, marks, axis_x, axis_y, flip_y=True,
           width=104, height=34):
    """把线段画到字符网格。axis_x/axis_y 是取哪两个分量（0=x,1=y,2=z）。"""
    xs, ys = [], []
    for p0, p1, _ in edges:
        for p in (p0, p1):
            xs.append(p[axis_x]); ys.append(p[axis_y])
    if not xs:
        return
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dx = max(x1 - x0, 1e-6)
    dy = max(y1 - y0, 1e-6)
    # 字符格子高:宽 ≈ 2:1，所以纵向按 2 倍像素算
    px_w, px_h = width, height * 2
    s = min(px_w / dx, px_h / dy) * 0.94
    ox = (px_w - dx * s) / 2.0 - x0 * s
    oy = (px_h - dy * s) / 2.0 - y0 * s

    grid = [[" "] * px_w for _ in range(px_h)]

    def put(px, py, ch):
        if 0 <= px < px_w and 0 <= py < px_h:
            cur = grid[py][px]
            if cur == " " or cur == ".":
                grid[py][px] = ch

    for p0, p1, ch in edges:
        ax_ = p0[axis_x] * s + ox
        ay_ = p0[axis_y] * s + oy
        bx_ = p1[axis_x] * s + ox
        by_ = p1[axis_y] * s + oy
        n = int(max(abs(bx_ - ax_), abs(by_ - ay_))) + 1
        n = max(n, 2)
        for k in range(n + 1):
            t = k / float(n)
            px = int(round(ax_ + (bx_ - ax_) * t))
            py = int(round(ay_ + (by_ - ay_) * t))
            put(px, py, ch)

    # 像素行 → 字符行（2 行合成 1 行）
    lines = []
    for r in range(height):
        top = grid[2 * r]
        bot = grid[2 * r + 1] if 2 * r + 1 < px_h else [" "] * px_w
        row = []
        for cix in range(px_w):
            a, b = top[cix], bot[cix]
            if a != " " and b != " ":
                row.append(a)
            elif a != " ":
                row.append(a)
            elif b != " ":
                row.append(b)
            else:
                row.append(" ")
        lines.append("".join(row).rstrip())

    if flip_y:
        lines = list(reversed(lines))

    print("")
    print("  【%s】  横轴=%s  纵轴=%s（上为 +%s）"
          % (name, "XYZ"[axis_x], "XYZ"[axis_y], "XYZ"[axis_y]))
    print("  " + "-" * width)
    for ln in lines:
        print("  |" + ln)
    print("  " + "-" * width)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NX 装配几何 → ASCII 三视图")
    ap.add_argument("asm")
    ap.add_argument("--width", type=int, default=104)
    ap.add_argument("--height", type=int, default=34)
    ns = ap.parse_args(argv)

    if not os.path.isfile(ns.asm):
        print("找不到: %s" % ns.asm)
        return 2
    with open(ns.asm, "r", encoding="utf-8") as fh:
        asm = json.load(fh)

    comps = asm.get("components") or []
    edges = []
    print("=" * 74)
    print("ASCII 三视图（数据全部来自 NX，不是我猜的）")
    print("  装配: %s ｜ 组件 %d 个" % (asm.get("assembly"), len(comps)))
    print("=" * 74)
    for i, c in enumerate(comps):
        ch = CHARS[i % len(CHARS)]
        for e in edges_of(c):
            edges.append((e[0], e[1], ch))
        print("  [%s] %-10s %s" % (ch, c.get("name") or c.get("label") or "?",
                                   c.get("part_file") or ""))

    # 俯视：X 横、Y 纵（看船的平面轮廓）
    render("俯视 TOP（从上往下看，船头在右 +Y）", edges, None, 0, 1,
           flip_y=False, width=ns.width, height=ns.height)
    # 前视：X 横、Z 纵（看正面剪影）
    render("前视 FRONT（从船头往船尾看，X 横、Z 纵）", edges, None, 0, 2,
           flip_y=True, width=ns.width, height=ns.height)
    # 侧视：Y 横、Z 纵（★ 最能看出像不像企业号）
    render("侧视 SIDE（★ 企业号剪影看这个：Y 横、Z 纵，船头在右）", edges, None, 1, 2,
           flip_y=True, width=ns.width, height=ns.height)
    return 0


if __name__ == "__main__":
    sys.exit(main())
