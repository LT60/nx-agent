#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 **NX 报出的装配变换** + 原始 STL 顶点，自己光栅化渲染一张 PNG。

★★★ 为什么需要这个（2026-10-10）★★★
  现象：NX 报告的所有数字都对
        （几何自检 10/10、包络连续、轴心偏心 0~1.3mm、相邻级空隙 0 处），
        但 NX 渲染出来的 `final_iso.png` 里零件是**散的**。
  → 数据与图互相矛盾，必须开一条**独立的渲染通道**来判断谁对。

  本工具就是那条通道：
    · 坐标来自 NX 自己（Component.GetPosition() → origin + matrix）
    · 顶点来自原始 STL（已实测：NX 的 STL 导入器**不改朝向**，
      NX 给的包围盒三轴跨度与原始 STL 完全一致，只差原点平移）
    · 渲染用自带 z-buffer 光栅化 + 平行光着色，纯标准库

  判读：
    · 本工具画出来连续 → 几何对，问题在 NX 的显示层
    · 本工具画出来也散 → 几何还错，回去查布局

用法：
    python tools/render_assembly_png.py
    python tools/render_assembly_png.py --view front --size 1200
"""

import argparse
import io
import json
import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")


def stl_tris(path):
    """读 STL 三角面 [(v0,v1,v2), ...]。"""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
    tris = []
    if 84 + n * 50 != size:
        cur = []
        for line in io.open(path, "r", errors="ignore"):
            t = line.strip()
            if t.startswith("vertex"):
                q = t.split()
                cur.append((float(q[1]), float(q[2]), float(q[3])))
                if len(cur) == 3:
                    tris.append(tuple(cur))
                    cur = []
        return tris
    with open(path, "rb") as fh:
        fh.seek(84)
        while True:
            rec = fh.read(50)
            if len(rec) < 50:
                break
            v = struct.unpack("<12fH", rec)
            tris.append(((v[3], v[4], v[5]),
                         (v[6], v[7], v[8]),
                         (v[9], v[10], v[11])))
    return tris


def worldize(tris, origin, mat9):
    """局部三角面 → 世界坐标。

    NX 报的 matrix 是 9 个数，**行优先**：[Xx,Xy,Xz, Yx,Yy,Yz, Zx,Zy,Zz]
    语义：world = origin + M · local
        wx = ox + Xx*lx + Xy*ly + Xz*lz
        wy = oy + Yx*lx + Yy*ly + Yz*lz
        wz = oz + Zx*lx + Zy*ly + Zz*lz
    （已由 j_probe_anchor.py 实测确认：传"局部原点"当 basePoint 时差 0.0mm）
    """
    M = mat9
    ox, oy, oz = origin
    out = []
    for (a, b, c) in tris:
        p = []
        for (lx, ly, lz) in (a, b, c):
            p.append((ox + M[0] * lx + M[1] * ly + M[2] * lz,
                      oy + M[3] * lx + M[4] * ly + M[5] * lz,
                      oz + M[6] * lx + M[7] * ly + M[8] * lz))
        out.append(tuple(p))
    return out


VIEWS = {
    "iso":   ((1.0, -1.0, 0.45), (0.0, 0.0, 1.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "side":  ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top":   ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
}

PALETTE = [
    (0.62, 0.70, 0.79), (0.50, 0.61, 0.72), (0.73, 0.75, 0.70),
    (0.57, 0.68, 0.66), (0.72, 0.65, 0.55), (0.61, 0.66, 0.75),
    (0.77, 0.73, 0.63), (0.54, 0.63, 0.70), (0.69, 0.71, 0.66),
    (0.59, 0.66, 0.73),
]


def norm(v):
    L = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / L for x in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def render(layers, view, W, H, bg=(0.95, 0.96, 0.97)):
    dirv, up = VIEWS[view]
    fwd = norm(dirv)
    right = norm(cross(fwd, up))
    upv = cross(right, fwd)

    pts = [p for _, ts in layers for t in ts for p in t]
    if not pts:
        return bytes(W * H * 3)
    us = [dot(p, right) for p in pts]
    vs = [dot(p, upv) for p in pts]
    u0, u1 = min(us), max(us)
    v0, v1 = min(vs), max(vs)
    du = max(u1 - u0, 1e-6)
    dv = max(v1 - v0, 1e-6)
    pad = 0.06
    scale = min(W * (1 - 2 * pad) / du, H * (1 - 2 * pad) / dv)
    cu = (u0 + u1) / 2.0
    cv = (v0 + v1) / 2.0

    zbuf = [1e18] * (W * H)
    rgb = bytearray()
    px_bg = bytes(int(c * 255) for c in bg)
    rgb = bytearray(px_bg * (W * H))

    L = norm((0.45, -0.72, 0.53))

    for li, (label, tris) in enumerate(layers):
        base = PALETTE[li % len(PALETTE)]
        for t in tris:
            e1 = sub(t[1], t[0])
            e2 = sub(t[2], t[0])
            nrm = norm(cross(e1, e2))
            shade = 0.34 + 0.66 * abs(dot(nrm, L))
            col = (min(1.0, base[0] * shade),
                   min(1.0, base[1] * shade),
                   min(1.0, base[2] * shade))
            cr, cg, cb = (int(col[0] * 255), int(col[1] * 255),
                          int(col[2] * 255))

            pp = []
            for p in t:
                sx = (dot(p, right) - cu) * scale + W / 2.0
                sy = H / 2.0 - (dot(p, upv) - cv) * scale
                pp.append((sx, sy, dot(p, fwd)))
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
                    l3 = 1.0 - l1 - l2
                    if l3 < -1e-6:
                        continue
                    z = l1 * az + l2 * bz + l3 * cz
                    k = row + px
                    if z < zbuf[k]:
                        zbuf[k] = z
                        rgb[k * 3] = cr
                        rgb[k * 3 + 1] = cg
                        rgb[k * 3 + 2] = cb
    return bytes(rgb)


def write_png(path, w, h, rgb):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 NX 报的变换自己渲染装配")
    ap.add_argument("--nx", default=os.path.join(ENT, "saturn_from_nx.json"))
    ap.add_argument("--src", default=os.path.join(ENT, "downloaded"))
    ap.add_argument("--view", default="iso", choices=list(VIEWS))
    ap.add_argument("--size", type=int, default=900)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--all-views", action="store_true")
    args = ap.parse_args(argv)

    with io.open(args.nx, "r", encoding="utf-8") as fh:
        nx = json.load(fh)

    stls = [x for x in os.listdir(args.src) if x.lower().endswith(".stl")]

    def find_stl(part_file):
        if part_file and os.path.isfile(os.path.join(args.src, part_file)):
            return os.path.join(args.src, part_file)
        if not part_file:
            return None
        stem = os.path.splitext(part_file)[0]
        norm_stem = stem.replace("_", " ").lower()
        for x in stls:
            if os.path.splitext(x)[0].lower() == norm_stem:
                return os.path.join(args.src, x)
        for x in stls:
            if norm_stem.replace(" ", "") in x.lower().replace("_", ""):
                return os.path.join(args.src, x)
        return None

    layers = []
    print("%-22s %10s  %-24s %s" % ("零件", "NX原点z", "世界 Z 区间", "三角面"))
    print("-" * 78)
    for c in nx["components"]:
        src = find_stl(c.get("part_file"))
        if not src:
            print("%-22s 找不到 STL（part_file=%r）" % (c["label"][:22],
                                                    c.get("part_file")))
            continue
        tris = stl_tris(src)
        M = c.get("matrix")
        if M is None:
            print("%-22s 没有 matrix" % c["label"][:22])
            continue
        Wt = worldize(tris, c["origin"], M)
        zs = [p[2] for t in Wt for p in t]
        layers.append((c["label"], Wt))
        print("%-22s %10.1f  %-24s %d"
              % (c["label"][:22], c["origin"][2],
                 "[%8.1f, %8.1f]" % (min(zs), max(zs)), len(Wt)))

    views = list(VIEWS) if args.all_views else [args.view]
    W = args.size
    H = int(W * 1.18)
    for v in views:
        print("\n渲染 %s 视角 %dx%d ..." % (v, W, H))
        rgb = render(layers, v, W, H)
        out = (args.out if (args.out and not args.all_views)
               else os.path.join(ENT, "render_%s.png" % v))
        n = write_png(out, W, H, rgb)
        print("已写 %s（%.0f KB）" % (out, n / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())