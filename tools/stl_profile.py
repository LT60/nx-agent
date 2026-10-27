# -*- coding: utf-8 -*-
"""★ 量出每个 STL 的「径向轮廓」——用数据看清零件的真实形状与轴心。

为什么需要：土星五号第一次装配失败（用户报：级间有大空隙、有穿模、轴心没对齐）。
根因猜想：我用**包围盒两端**当对接面、用**包围盒中心**当轴心 —— 但
  · 打印件常在端头做插接小凸台/凹槽 → 包围盒端头 ≠ 对接面
  · 零件在自己坐标系里可能是偏心的 → 包围盒中心 ≠ 轴心

本工具沿零件长轴切片，逐片算出：
  · 该片截面中心（两个横轴上的 (min+max)/2）→ 若逐片中心在漂移，说明轴心要拟合
  · 该片最大半径 → 能看出凸台 / 颈部 / 裙边
并把半径画成字符条，肉眼可读。

用法：
    python tools/stl_profile.py [目录] [--slices 22]
"""

from __future__ import print_function

import argparse
import os
import struct
import sys

AXES = "XYZ"


def read_tris(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
    is_ascii = (84 + n * 50 != size)
    tris = []
    if is_ascii:
        cur = []
        for line in open(path, "r", errors="ignore"):
            s = line.strip()
            if s.startswith("vertex"):
                cur.append([float(x) for x in s.split()[1:4]])
                if len(cur) == 3:
                    tris.append(cur); cur = []
    else:
        with open(path, "rb") as fh:
            fh.seek(84)
            while True:
                rec = fh.read(50)
                if len(rec) < 50:
                    break
                v = struct.unpack("<12fH", rec)
                tris.append([list(v[3:6]), list(v[6:9]), list(v[9:12])])
    return tris


def analyse(path, nslice=22):
    tris = read_tris(path)
    if not tris:
        return None
    lo = [1e18] * 3
    hi = [-1e18] * 3
    pts = []
    for t in tris:
        for p in t:
            pts.append(p)
            for i in range(3):
                lo[i] = min(lo[i], p[i]); hi[i] = max(hi[i], p[i])
    size = [hi[i] - lo[i] for i in range(3)]
    # 长轴 = 尺寸最大的轴；另两轴为横轴
    ax = max(range(3), key=lambda i: size[i])
    cross = [i for i in range(3) if i != ax]
    a0, a1 = lo[ax], hi[ax]
    step = (a1 - a0) / nslice if a1 > a0 else 1.0

    slices = []
    for k in range(nslice):
        s0 = a0 + k * step
        s1 = s0 + step
        mn = [1e18, 1e18]
        mx = [-1e18, -1e18]
        cnt = 0
        for p in pts:
            if s0 - 1e-6 <= p[ax] <= s1 + 1e-6:
                cnt += 1
                for j, ci in enumerate(cross):
                    mn[j] = min(mn[j], p[ci]); mx[j] = max(mx[j], p[ci])
        if cnt == 0:
            slices.append(None)
            continue
        c = [(mn[j] + mx[j]) / 2.0 for j in range(2)]
        r = max((mx[j] - mn[j]) / 2.0 for j in range(2))
        slices.append({"c": c, "r": r, "n": cnt})
    return {"axis": ax, "cross": cross, "lo": lo, "hi": hi, "size": size,
            "slices": slices, "step": step, "a0": a0}


def main(argv=None):
    ap = argparse.ArgumentParser(description="量 STL 的径向轮廓（看形状 & 轴心）")
    ap.add_argument("folder", nargs="?", default=None)
    ap.add_argument("--slices", type=int, default=22)
    ns = ap.parse_args(argv)

    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(HERE)
    d = ns.folder or os.path.join(ROOT, "output", "enterprise", "downloaded")

    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".stl"))
    for fn in files:
        info = analyse(os.path.join(d, fn), ns.slices)
        if not info:
            print("%s 解析失败" % fn); continue
        ax, cross = info["axis"], info["cross"]
        lo, hi, size = info["lo"], info["hi"], info["size"]
        print("=" * 92)
        print("%s" % fn)
        print("  长轴 = %s ｜ 尺寸 %s=%.1f  %s=%.1f  %s=%.1f"
              % (AXES[ax], AXES[0], size[0], AXES[1], size[1], AXES[2], size[2]))
        print("  局部范围 %s[%.1f, %.1f]" % (AXES[ax], lo[ax], hi[ax]))
        # 轴心：逐片截面中心的平均 + 漂移
        cs = [s["c"] for s in info["slices"] if s]
        if cs:
            avg = [sum(c[j] for c in cs) / len(cs) for j in range(2)]
            drift = [max(c[j] for c in cs) - min(c[j] for c in cs) for j in range(2)]
            print("  逐片截面中心均值 = (%s=%.2f, %s=%.2f) ｜ 漂移 (%s=%.2f, %s=%.2f)"
                  % (AXES[cross[0]], avg[0], AXES[cross[1]], avg[1],
                     AXES[cross[0]], drift[0], AXES[cross[1]], drift[1]))
            rmax = max(s["r"] for s in info["slices"] if s)
            print("  最大半径 = %.1f（= 直径 %.1f）" % (rmax, rmax * 2))
            print("  ── 径向轮廓（%s 从小到大 →）" % AXES[ax])
            for k, s in enumerate(info["slices"]):
                a = info["a0"] + (k + 0.5) * info["step"]
                if not s:
                    print("    %8.1f  |" % a); continue
                barlen = int(round(s["r"] / rmax * 46)) if rmax else 0
                bar = "#" * barlen
                print("    %8.1f  |%-46s| r=%6.1f 心(%6.1f,%6.1f)"
                      % (a, bar, s["r"], s["c"][0], s["c"][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
