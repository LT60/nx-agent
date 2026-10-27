#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 CLSF 刀位文件渲染成「零件 + 刀路」叠加图 —— CAM 的可视化眼睛。

★★★ 为什么需要（2026-11 用户指出）★★★
  展示页的 CAM 图用的是 NX 自己的截图（cam_toolpath.png），里面有两个问题：
    1. 看不到刀路 —— 截图截的是 CAM 设置界面，刀路曲线不在视图里；
    2. 出现了「单电极」—— CAM 底座是复制的电极加工模板（ngElectrode.prt），
       模板自带 2 个小平面体（电极本体），journal 清特征时没清掉它们，
       于是场景里一直站着个电极。
  修法：CLSF 里有**全部刀位点**（GOTO x,y,z）—— 这是真实刀路数据。
  本工具把刀路折线叠画在零件网格上（深度测试），生成真正的「零件+刀路」图，
  完全绕开 NX 显示层和模板遗留物。

  ★ 坐标系：CLSF 坐标 = STL 局部坐标 + MCS 平移（实测 MSYS/100,50,111.6），
    所以渲染时把 GOTO 减去 MCS 原点，就能和 STL 对齐。

用法：
    python tools/render_toolpath.py --clsf saturn.clsf --stl "stage 1-2 coupler.stl" \
        --title "1-2 级间段" -o output/enterprise/tp_coupler.png
"""

import argparse
import io
import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_assembly_png as R  # noqa: E402  复用相机/光栅化/写PNG


def read_clsf(path):
    """读 CLSF 的 GOTO 点和工序名。返回 [(op_name, [pts...]), ...]"""
    ops = []
    cur = None
    for line in io.open(path, "r", encoding="utf-8", errors="replace"):
        if line.startswith("TOOL PATH"):
            q = line.split(",")
            cur = (q[0].split("/", 1)[-1].strip() if len(q) > 0 else "OP",
                   [])
            ops.append(cur)
        elif line.startswith("GOTO"):
            q = line.split("/")
            if len(q) >= 2:
                v = q[1].split(",")
                try:
                    p = (float(v[0]), float(v[1]), float(v[2]))
                    if cur is None:
                        cur = ("OP", [])
                        ops.append(cur)
                    cur[1].append(p)
                except Exception:
                    pass
    return [(name, pts) for (name, pts) in ops if len(pts) >= 2]


def mcs_origin(path):
    """从 CLSF 头部读 MSYS 平移原点。"""
    for line in io.open(path, "r", encoding="utf-8", errors="replace"):
        if line.startswith("MSYS"):
            q = line.split("/")
            if len(q) >= 2:
                v = q[1].split(",")
                try:
                    return (float(v[0]), float(v[1]), float(v[2]))
                except Exception:
                    pass
    return (0.0, 0.0, 0.0)


def draw_polyline(rgb, zbuf, W, H, right, upv, fwd, cu, cv, scale, pts,
                  color=(0.95, 0.55, 0.10), width=2):
    """把折线逐段画进 z-buffer（深度测试，被零件挡住的部分自动隐藏）。"""
    cr, cg, cb = (int(color[0] * 255), int(color[1] * 255),
                  int(color[2] * 255))

    def proj(p):
        return ((R.dot(p, right) - cu) * scale + W / 2.0,
                H / 2.0 - (R.dot(p, upv) - cv) * scale,
                R.dot(p, fwd))

    for i in range(len(pts) - 1):
        x0, y0, z0 = proj(pts[i])
        x1, y1, z1 = proj(pts[i + 1])
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 1.2))
        for k in range(n + 1):
            t = k / float(n)
            sx = x0 + (x1 - x0) * t
            sy = y0 + (y1 - y0) * t
            sz = z0 + (z1 - z0) * t
            px, py = int(sx), int(sy)
            for dx in range(width):
                for dy in range(width):
                    qx, qy = px + dx, py + dy
                    if 0 <= qx < W and 0 <= qy < H:
                        k2 = qy * W + qx
                        if sz < zbuf[k2]:
                            rgb[k2 * 3] = cr
                            rgb[k2 * 3 + 1] = cg
                            rgb[k2 * 3 + 2] = cb


def render_with_toolpath(part_tris, ops_pts, view, W, H):
    """零件（灰）+ 刀路折线（琥珀色，按工序分色）。"""
    dirv, up = R.VIEWS[view]
    fwd = R.norm(dirv)
    right = R.norm(R.cross(fwd, up))
    upv = R.cross(right, fwd)

    pts = [p for t in part_tris for p in t]
    for _, ps in ops_pts:
        pts.extend(ps)
    us = [R.dot(p, right) for p in pts]
    vs = [R.dot(p, upv) for p in pts]
    u0, u1 = min(us), max(us)
    v0, v1 = min(vs), max(vs)
    scale = min(W * 0.88 / max(u1 - u0, 1e-6), H * 0.88 / max(v1 - v0, 1e-6))
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0

    zbuf = [1e18] * (W * H)
    rgb = bytearray(bytes((250, 250, 251)) * (W * H))

    # 零件（三角面，光栅化）
    part_tri_list = part_tris
    L = R.norm((0.45, -0.72, 0.53))
    for t in part_tri_list:
        e1 = R.sub(t[1], t[0])
        e2 = R.sub(t[2], t[0])
        nrm = R.norm(R.cross(e1, e2))
        shade = 0.34 + 0.66 * abs(R.dot(nrm, L))
        col = (int(150 * shade), int(162 * shade), int(175 * shade))
        pp = []
        for p in t:
            pp.append(((R.dot(p, right) - cu) * scale + W / 2.0,
                       H / 2.0 - (R.dot(p, upv) - cv) * scale,
                       R.dot(p, fwd)))
        x0 = max(0, int(min(q[0] for q in pp)))
        x1 = min(W - 1, int(max(q[0] for q in pp)) + 1)
        y0 = max(0, int(min(q[1] for q in pp)))
        y1 = min(H - 1, int(max(q[1] for q in pp)) + 1)
        if x1 < x0 or y1 < y0:
            continue
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = pp
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        inv = 1.0 / den
        for py in range(y0, y1 + 1):
            fy = py + 0.5 - cy
            row = py * W
            for px in range(x0, x1 + 1):
                fx = px + 0.5 - cx
                l1 = ((by - cy) * fx + (cx - bx) * fy) * inv
                if l1 < -1e-6:
                    continue
                l2 = ((cy - ay) * fx + (ax - cx) * fy) * inv
                if l2 < -1e-6:
                    continue
                if 1.0 - l1 - l2 < -1e-6:
                    continue
                z = l1 * az + l2 * bz + (1 - l1 - l2) * cz
                k = row + px
                if z < zbuf[k]:
                    zbuf[k] = z
                    rgb[k * 3] = col[0]
                    rgb[k * 3 + 1] = col[1]
                    rgb[k * 3 + 2] = col[2]

    # 刀路折线（按工序分色）
    OPCOLORS = [(0.95, 0.45, 0.10), (0.95, 0.75, 0.10), (0.30, 0.85, 0.55),
                (0.40, 0.70, 0.98), (0.90, 0.40, 0.75), (0.60, 0.95, 0.30)]
    for oi, (name, ps) in enumerate(ops_pts):
        col = OPCOLORS[oi % len(OPCOLORS)]
        # 降采样：太多点会拖慢
        stepn = max(1, len(ps) // 2500)
        sub = ps[::stepn]
        draw_polyline(rgb, zbuf, W, H, right, upv, fwd, cu, cv, scale,
                      sub, color=col, width=2)
    return bytes(rgb)


def write_png(path, w, h, rgb):
    return R.write_png(path, w, h, rgb)


def main(argv=None):
    ap = argparse.ArgumentParser(description="零件 + 刀路 叠加渲染")
    ap.add_argument("--clsf", required=True)
    ap.add_argument("--stl", required=True)
    ap.add_argument("--view", default="iso")
    ap.add_argument("--size", type=int, default=700)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default="")
    args = ap.parse_args(argv)

    ops = read_clsf(args.clsf)
    mcs = mcs_origin(args.clsf)
    total = sum(len(ps) for _, ps in ops)
    print("CLSF：%d 道工序 ｜ %d 刀位点 ｜ MCS %s"
          % (len(ops), total, mcs))

    tris = R.stl_tris(args.stl)
    # 把 CLSF 坐标平移回 STL 局部坐标
    ops_local = [(name, [(p[0] - mcs[0], p[1] - mcs[1], p[2] - mcs[2])
                         for p in ps]) for (name, ps) in ops]

    # STL 顶点范围 vs 刀路范围 应该重合（错开就是坐标系没对上）
    vs = [v for t in tris for v in t]
    print("STL 范围 X[%.0f,%.0f] Y[%.0f,%.0f] Z[%.0f,%.0f]"
          % (min(v[0] for v in vs), max(v[0] for v in vs),
             min(v[1] for v in vs), max(v[1] for v in vs),
             min(v[2] for v in vs), max(v[2] for v in vs)))
    for name, ps in ops_local[:2]:
        if not ps:
            continue
        print("刀路[%s] 范围 X[%.0f,%.0f] Y[%.0f,%.0f] Z[%.0f,%.0f]"
              % (name, min(p[0] for p in ps), max(p[0] for p in ps),
                 min(p[1] for p in ps), max(p[1] for p in ps),
                 min(p[2] for p in ps), max(p[2] for p in ps)))

    # ★★ 自动对齐：CLSF 的坐标系是电极模板留下的 MCS 框，姿态和 STL 对不上。
    #   暴力试 24 种轴映射（3! 排列 × 2³ 符号），选「刀路点落在零件半径内」
    #   比例最高的那个，再把刀路中心平移到零件中心。
    import itertools
    vs = [v for t in tris for v in t]
    sc = [(sum(v[k] for v in vs) / len(vs)) for k in range(3)]
    sr = max(math.hypot(v[0] - sc[0], v[2] - sc[2]) for v in vs
             if True) if False else 0.0
    # 零件最大半径（以自身中心为准）
    sr = max(math.sqrt((v[0] - sc[0]) ** 2 + (v[2] - sc[2]) ** 2) for v in vs)
    all_tp = [p for _, ps in ops_local for p in ps]
    # ★ 聚类找「主刀路簇」：CLSF 里可能混有模板遗留工序（位置在别处，
    #   比如电极工位），它们的点会把对齐中心拽偏。取最大连通簇对齐，
    #   渲染时丢弃落在零件之外的簇。
    from collections import deque
    CELL = 40.0
    grid = {}
    for p in all_tp:
        key = (int(p[0] // CELL), int(p[1] // CELL), int(p[2] // CELL))
        grid.setdefault(key, []).append(p)
    seen = set()
    clusters = []
    for key in list(grid.keys()):
        if key in seen:
            continue
        q = deque([key]); seen.add(key); n = 0; acc = [0.0, 0.0, 0.0]
        while q:
            k = q.popleft()
            for pp in grid.get(k, []):
                n += 1
                acc[0] += pp[0]; acc[1] += pp[1]; acc[2] += pp[2]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        nk = (k[0] + dx, k[1] + dy, k[2] + dz)
                        if nk in grid and nk not in seen:
                            seen.add(nk); q.append(nk)
        clusters.append((n, (acc[0] / n, acc[1] / n, acc[2] / n)))
    clusters.sort(reverse=True)
    tc = clusters[0][1]
    print("刀路簇：%d 个，最大簇 %d 点（占 %.0f%%）—— 用它对齐"
          % (len(clusters), clusters[0][0],
             clusters[0][0] * 100.0 / max(1, len(all_tp))))
    sample = all_tp[::max(1, len(all_tp) // 3000)]
    best = None
    for perm in itertools.permutations(range(3)):
        for sg in itertools.product((1, -1), repeat=3):
            ax_half = max(abs(v[1] - sc[1]) for v in vs)
            good = 0
            for p in sample:
                # q 已是「以零件中心为原点」的坐标（扣掉刀路自身中心）
                q = ((p[perm[0]] - tc[0]) * sg[0],
                     (p[perm[1]] - tc[1]) * sg[1],
                     (p[perm[2]] - tc[2]) * sg[2])
                r = math.hypot(q[0], q[2])
                if r <= sr + 15 and abs(q[1]) <= ax_half + 15:
                    good += 1
            frac = good / float(len(sample))
            if best is None or frac > best[0]:
                best = (frac, perm, sg)
    frac, perm, sg = best
    print("自动对齐：轴映射 %s 符号 %s ｜ 贴合率 %.0f%%"
          % (perm, sg, frac * 100))

    def xform(p):
        return ((p[perm[0]] - tc[0]) * sg[0] + sc[0],
                (p[perm[1]] - tc[1]) * sg[1] + sc[1],
                (p[perm[2]] - tc[2]) * sg[2] + sc[2])

    ops_local = [(name, [xform(p) for p in ps]) for (name, ps) in ops_local]
    # 过滤：变换后落在零件附近（圆柱 r≤rmax+15，轴向 ≤ 半长+15）的才算本零件刀路
    kept = []
    for (name, ps) in ops_local:
        kp = [q for q in ps
              if math.hypot(q[0] - sc[0], q[2] - sc[2]) <= sr + 15
              and abs(q[1] - sc[1]) <= ax_half + 15]
        if len(kp) >= 10:
            kept.append((name, kp))
    dropped = len(ops_local) - len(kept)
    if dropped:
        print("丢弃 %d 个不在零件上的工序簇（模板遗留工位）" % dropped)
    ops_local = kept

    W = args.size
    H = int(W * 1.25)
    rgb = render_with_toolpath(tris, ops_local, args.view, W, H)
    n = write_png(args.out, W, H, rgb)
    print("已写 %s（%.0f KB）" % (args.out, n / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())