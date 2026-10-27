# -*- coding: utf-8 -*-
"""★ 土星五号堆叠核验：NX 实测的每级 Z 区间必须与布局预期首尾相接。

土星五号的装配坐标不是手写的，是 `tools/saturn_layout.py` 从**零件实测长度**
累加出来的（级序来自公开级构）。所以这里要证明的是：
  NX 里装完之后，每一级的实际 Z 区间 == 布局预期的 Z 区间，
  且相邻级首尾相接（上一级顶面 == 下一级底面），全箭总高 == 预期。

用法：
    python tools/check_saturn_stack.py output/enterprise/saturn_bbox.json \
                                      output/enterprise/saturn_from_nx.json

退出码：0 = 一致；1 = 有偏差；2 = 用法/文件问题
"""

from __future__ import print_function

import argparse
import json
import os
import sys

TOL = 0.5      # mm


def main(argv=None):
    ap = argparse.ArgumentParser(description="核验土星五号各级堆叠是否正确")
    ap.add_argument("bbox")
    ap.add_argument("asm")
    ns = ap.parse_args(argv)

    for f in (ns.bbox, ns.asm):
        if not os.path.isfile(f):
            print("找不到文件: %s" % f)
            return 2
    with open(ns.bbox, "r", encoding="utf-8") as fh:
        bb = json.load(fh)
    with open(ns.asm, "r", encoding="utf-8") as fh:
        asm = json.load(fh)

    # 布局预期：label → (z_bottom, z_top, diameter)
    exp = {}
    for c in asm.get("components") or []:
        e = c.get("expected") or {}
        exp[c["label"]] = (e.get("z_bottom"), e.get("z_top"), e.get("diameter"))

    print("=" * 78)
    print("土星五号堆叠核验（NX 实测 vs 离线布局预期）")
    print("  源: %s" % (asm.get("source") or "—"))
    print("=" * 78)
    print("%-16s %-20s %-20s %8s %8s  %s"
          % ("级", "预期 Z 区间", "实测 Z 区间", "Δ底", "Δ顶", "判定"))

    bad = 0
    rows = []
    for r in bb.get("components") or []:
        label = r.get("label")
        bodies = r.get("bodies") or []
        if not bodies:
            print("  %-14s %s" % (label, "NX 没给包围盒"))
            bad += 1
            continue
        a = bodies[0]["asm"]
        z0, z1 = a["min"][2], a["max"][2]
        dia = a["max"][0] - a["min"][0]
        e = exp.get(label)
        if not e or e[0] is None:
            print("  %-14s （布局里没有预期值）" % label)
            bad += 1
            continue
        d0, d1 = abs(z0 - e[0]), abs(z1 - e[1])
        ok = (d0 <= TOL and d1 <= TOL)
        if not ok:
            bad += 1
        rows.append((label, e[0], z0, e[1], z1, dia, ok))
        print("  %-14s [%8.1f,%8.1f] [%8.1f,%8.1f] %8.2f %8.2f  %s"
              % (label, e[0], e[1], z0, z1, d0, d1, "✔" if ok else "✘"))

    # 相邻级必须"对接或套接"，**不能有空隙**
    #   gap = 本级底面 - 上一级顶面;  gap>0 = 空隙(错)  gap<=0 = 对接/套接(对)
    print("-" * 78)
    gaps, ols = [], []
    for i in range(1, len(rows)):
        g = rows[i][2] - rows[i - 1][4]
        gaps.append(g)
        if g > TOL:
            bad += 1
            print("  ✘ %s → %s 有 %.2f mm 空隙" % (rows[i - 1][0], rows[i][0], g))
        elif g < -TOL:
            ols.append((-g, rows[i - 1][0], rows[i][0]))
    if gaps:
        worst = max(gaps)
        print("  相邻级最大空隙：%.3f mm（必须 ≤ %.1f；套接会让它是负数）%s"
              % (worst, TOL, "✔" if worst <= TOL else "✘"))
    if ols:
        print("  其中套接（设计意图，用于遮住细脖的）：")
        for d, a, b in sorted(ols, reverse=True)[:6]:
            print("    %6.1f mm  %s → %s" % (d, a[:14], b[:14]))

    if bb.get("envelope"):
        tot = bb["envelope"]["size"][2]
        want = asm.get("components") and exp[rows[-1][0]][1]
        print("  全箭总高：实测 %.1f mm ｜ 预期 %.1f mm ｜ Δ%.2f  %s"
              % (tot, want, abs(tot - want),
                 "✔" if abs(tot - want) <= TOL else "✘"))
        if abs(tot - want) > TOL:
            bad += 1
        dm = bb["envelope"]["size"][0]
        print("  最大直径：实测 %.1f mm（第一级）" % dm)

    print("-" * 78)
    if bad == 0:
        print("✔ 装配正确：每级 Z 区间与预期一致，相邻级无空隙（对接/套接），总高相符。")
        print("  含义：装配坐标不是手写的，是从零件实测长度算出来并被 NX 复核过的。")
        return 0
    print("✘ %d 处不符。" % bad)
    return 1


if __name__ == "__main__":
    sys.exit(main())
