#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检测零件哪一端是"发动机端"（或支杆端）—— 用来判断上下朝向。

★★★ 为什么需要（2026-10-10，用户提问）★★★
  用户：「每一个部分的上下方向我不确定是否正确，感觉有一些部件上下翻转了」
  实测确认：S-II / S-IVB / 服务舱 / LEM 整流罩 **四件确实装反了** ——
  它们的发动机喷管都朝上了。根因是朝向搜索的代价函数是**全局**的
  （接口失配 + 全箭下粗上细），会为了整体最优把个别零件推错。

  本工具给出一条**零件自身的物理判据**：
    发动机喷管、逃逸塔支杆这类结构，在**端面横截面**上是
    **若干个分离的小块**（5 台 J-2 / 4 个支杆…）；
    而平整的筒端是一整圈 → 只有 1 块。

  ★ 第一版用"顶点聚类"数块 —— 被网格稀疏度污染（虚报 66 块）。
    必须把**三角面栅格化填充**再数连通块，才是真实截面形状。

用法：
    python tools/detect_engine_end.py
    python tools/detect_engine_end.py --band 0.05 --grid 160
"""

import argparse
import io
import json
import os
import struct
import sys
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")


def stl_tris(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        h = fh.read(84)
    n = struct.unpack("<I", h[80:84])[0] if len(h) >= 84 else 0
    out = []
    if 84 + n * 50 != size:
        cur = []
        for line in io.open(path, "r", errors="ignore"):
            t = line.strip()
            if t.startswith("vertex"):
                q = t.split()
                cur.append((float(q[1]), float(q[2]), float(q[3])))
                if len(cur) == 3:
                    out.append(tuple(cur))
                    cur = []
        return out
    with open(path, "rb") as fh:
        fh.seek(84)
        while True:
            r = fh.read(50)
            if len(r) < 50:
                break
            v = struct.unpack("<12fH", r)
            out.append(((v[3], v[4], v[5]), (v[6], v[7], v[8]),
                        (v[9], v[10], v[11])))
    return out


def world(part, tris):
    M = [v for row in part["matrix"] for v in row]
    o = part["origin"]
    out = []
    for t in tris:
        out.append(tuple(
            (o[0] + M[0] * a + M[1] * b + M[2] * c,
             o[1] + M[3] * a + M[4] * b + M[5] * c,
             o[2] + M[6] * a + M[7] * b + M[8] * c) for (a, b, c) in t))
    return out


def raster_islands(tris, zlo, zhi, grid=140, minsize=None):
    """把 z 落在 [zlo,zhi] 的三角面**栅格化填充**，再数连通块。"""
    sel = [t for t in tris if all(zlo <= p[2] <= zhi for p in t)]
    if not sel:
        # 放宽：只要有一个点在带内
        sel = [t for t in tris if any(zlo <= p[2] <= zhi for p in t)]
    if not sel:
        return 0, 0
    xs = [p[0] for t in sel for p in t]
    ys = [p[1] for t in sel for p in t]
    x0, y0 = min(xs), min(ys)
    S = max(max(xs) - x0, max(ys) - y0, 1e-6)
    g = [[0] * grid for _ in range(grid)]

    def to_px(x, y):
        return ((x - x0) / S * (grid - 1), (y - y0) / S * (grid - 1))

    for t in sel:
        (ax, ay, _), (bx, by, _), (cx, cy, _) = t
        ax, ay = to_px(ax, ay)
        bx, by = to_px(bx, by)
        cx, cy = to_px(cx, cy)
        lo_x = max(0, int(min(ax, bx, cx)))
        hi_x = min(grid - 1, int(max(ax, bx, cx)) + 1)
        lo_y = max(0, int(min(ay, by, cy)))
        hi_y = min(grid - 1, int(max(ay, by, cy)) + 1)
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            # 退化三角形：只打三个点
            for (px, py) in ((ax, ay), (bx, by), (cx, cy)):
                g[int(py)][int(px)] = 1
            continue
        inv = 1.0 / den
        for py in range(lo_y, hi_y + 1):
            for px in range(lo_x, hi_x + 1):
                fx, fy = px + 0.5 - cx, py + 0.5 - cy
                l1 = ((by - cy) * fx + (cx - bx) * fy) * inv
                if l1 < -1e-9:
                    continue
                l2 = ((cy - ay) * fx + (ax - cx) * fy) * inv
                if l2 < -1e-9:
                    continue
                if 1.0 - l1 - l2 < -1e-9:
                    continue
                g[py][px] = 1

    # 连通块（4 邻域）
    seen = [[0] * grid for _ in range(grid)]
    sizes = []
    for r in range(grid):
        for c in range(grid):
            if g[r][c] and not seen[r][c]:
                n = 0
                q = deque([(r, c)])
                seen[r][c] = 1
                while q:
                    cr, cc = q.popleft()
                    n += 1
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        rr, cc2 = cr + dr, cc + dc
                        if (0 <= rr < grid and 0 <= cc2 < grid
                                and g[rr][cc2] and not seen[rr][cc2]):
                            seen[rr][cc2] = 1
                            q.append((rr, cc2))
                sizes.append(n)
    if minsize is None:
        # 小于总填充面积 0.3% 的算噪点
        minsize = max(3, int(sum(sizes) * 0.003)) if sizes else 3
    big = [s for s in sizes if s >= minsize]
    return len(big), (max(sizes) if sizes else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="检测零件哪端是发动机端")
    ap.add_argument("--band", type=float, default=0.05,
                    help="端点取样高度比例（默认 5%%）")
    ap.add_argument("--grid", type=int, default=140)
    ap.add_argument("layout", nargs="?",
                    default=os.path.join(ENT, "saturn_layout.json"))
    args = ap.parse_args(argv)

    with io.open(args.layout, "r", encoding="utf-8") as fh:
        lay = json.load(fh)
    src = os.path.join(os.path.dirname(args.layout), "downloaded")

    print("=" * 96)
    print("端点横截面【填充块数】（三角面栅格化后数连通块）。"
          "发动机/支杆端 = 多块，平整筒端 = 1 块")
    print("=" * 96)
    print("%-22s %12s %12s   %s" % ("零件", "下端块数", "上端块数", "判读"))
    print("-" * 96)
    need_flip = []
    for p in lay["parts"]:
        f = os.path.join(src, p["file"])
        if not os.path.isfile(f):
            continue
        tris = world(p, stl_tris(f))
        zs = [q[2] for t in tris for q in t]
        z0, z1 = min(zs), max(zs)
        h = max(z1 - z0, 1e-6)
        nb, _ = raster_islands(tris, z0, z0 + h * args.band, args.grid)
        nt, _ = raster_islands(tris, z1 - h * args.band, z1, args.grid)
        if nb > nt and nb >= 2:
            v = "下端是多块（发动机/支杆）→ 应朝下  OK"
        elif nt > nb and nt >= 2:
            v = "★ 上端是多块 → 这一件**应该翻过来**"
            need_flip.append(p["label"])
        elif nb == nt:
            v = "两端相同 → 形状判不出（等径件）"
        else:
            v = "下端更复杂"
        print("%-22s %12d %12d   %s" % (p["label"][:22], nb, nt, v))

    print("-" * 96)
    if need_flip:
        print("★ 建议翻转的零件（共 %d 件）：" % len(need_flip))
        for x in need_flip:
            print("   - %s" % x)
    else:
        print("没有零件需要翻转。")
    return 0


if __name__ == "__main__":
    sys.exit(main())