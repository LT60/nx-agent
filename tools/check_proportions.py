# -*- coding: utf-8 -*-
"""★ 核对：模型比例是否符合**公开的真实尺寸数据**。

用户定调：「用公开的真实尺寸数据把比例定准」。
那就不能只是"我照着数据建了"，必须**可核对**：脚本从 NX 实测包围盒里量出
每个关键尺寸，除以缩放比换算回真实米数，再和公开数据逐项比。

公开数据（多来源一致：Trekipedia / Wikitrek / HyperTrek / en-academic）：
    全长 288.6 m ｜ 全宽 127.1 m ｜ 全高 72.6 m
    碟形主舰体 127.1 m ｜ 工程舰体 103.6 × 34.1 m ｜ 引擎舱 153.6 × 17.3 m ｜ 舰桥 18 m

用法：
    python tools/check_proportions.py output/enterprise/assembly_v4_bbox.json \
                                       output/enterprise/assembly_v4_from_nx.json

退出码：0 = 全部符合；1 = 有偏差；2 = 用法/文件问题
"""

from __future__ import print_function

import argparse
import json
import os
import sys

TOL = 0.01          # 1% 容差


def extent(bb):
    return [bb["max"][i] - bb["min"][i] for i in range(3)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="核对模型比例 vs 公开真实尺寸")
    ap.add_argument("bbox")
    ap.add_argument("asm")
    ns = ap.parse_args(argv)

    for f in (ns.bbox, ns.asm):
        if not os.path.isfile(f):
            print("找不到文件: %s" % f)
            return 2
    with open(ns.bbox, "r", encoding="utf-8") as fh:
        bbox = json.load(fh)
    with open(ns.asm, "r", encoding="utf-8") as fh:
        asm = json.load(fh)

    pub = asm.get("published") or {}
    scale = asm.get("scale", "1:100")
    # 模型 1 m 真实 → mm 模型
    try:
        num = float(str(scale).split(":")[1])
    except Exception:
        num = 100.0
    mm_per_m = 1000.0 / num

    # ★ 同一 id 可能有多个组件（左右引擎舱各一个）。
    #   求"直径"必须**逐个组件**算再取最小，不能取并集 ——
    #   并集会得到两个舱的横向跨度（实测 112.3 m），不是直径（17.3 m）。
    def comp_list(cid):
        out = []
        for r in bbox.get("components") or []:
            if r.get("id") != cid:
                continue
            for b in r.get("bodies") or []:
                if b.get("asm"):
                    out.append((b["asm"]["min"], b["asm"]["max"]))
        return out

    def min_extent(cid, axis):
        vs = [hi[axis] - lo[axis] for lo, hi in comp_list(cid)]
        return min(vs) if vs else None

    # 按 id 取组件包围盒（可能多个组件同 id，取并集）
    def comp_bbox(cid):
        lo = [1e9] * 3
        hi = [-1e9] * 3
        hit = False
        for r in bbox.get("components") or []:
            if r.get("id") != cid:
                continue
            for b in r.get("bodies") or []:
                a = b.get("asm")
                if not a:
                    continue
                hit = True
                for i in range(3):
                    lo[i] = min(lo[i], a["min"][i])
                    hi[i] = max(hi[i], a["max"][i])
        return (lo, hi) if hit else (None, None)

    def to_m(v):
        return v / mm_per_m

    rows = []   # (名称, 实测 m, 公开 m, 判定)

    # 全舰包络
    if bbox.get("envelope"):
        sz = bbox["envelope"]["size"]
        rows.append(("全宽（X，应为碟形直径）", to_m(sz[0]), pub.get("overall_beam_m")))
        rows.append(("全长（Y）", to_m(sz[1]), pub.get("overall_length_m")))
        rows.append(("全高（Z）", to_m(sz[2]), pub.get("overall_height_m")))

    # 碟形主舰体：直径 = X 方向尺寸
    lo, hi = comp_bbox("saucer")
    if lo:
        rows.append(("碟形主舰体 直径", to_m(hi[0] - lo[0]), pub.get("saucer_diameter_m")))

    # 工程舰体：横躺 → 长 = Y，直径 = X（也应等于 Z）
    lo, hi = comp_bbox("hull2")
    if lo:
        rows.append(("工程舰体 长度", to_m(hi[1] - lo[1]), pub.get("secondary_hull_length_m")))
        rows.append(("工程舰体 直径", to_m(hi[0] - lo[0]), pub.get("secondary_hull_diameter_m")))

    # 引擎舱：横躺 → 长 = Y，直径 = X
    nl = comp_list("nacelle")
    if nl:
        rows.append(("引擎舱 长度", to_m(max(hi[1] - lo[1] for lo, hi in nl)),
                     pub.get("nacelle_length_m")))
        rows.append(("引擎舱 直径", to_m(min_extent("nacelle", 0)),
                     pub.get("nacelle_diameter_m")))

    # 舰桥：直径
    lo, hi = comp_bbox("bridge")
    if lo:
        rows.append(("舰桥 直径", to_m(hi[0] - lo[0]), pub.get("bridge_diameter_m")))

    print("=" * 76)
    print("比例核对：模型实测（换算回真实米）  vs  公开尺寸数据")
    print("  缩放 %s ｜ 1 m 真实 = %g mm 模型" % (scale, mm_per_m))
    print("  公开数据来源：%s" % (pub.get("_source") or "—"))
    print("=" * 76)
    print("%-24s %10s %12s %9s  %s" % ("项目", "实测", "公开", "偏差", "判定"))

    bad = 0
    for name, got, want in rows:
        if got is None or want is None:
            print("  %-22s %10s %12s %9s  %s" % (name, "—", "—", "—", "跳过"))
            continue
        dev = abs(got - want) / want if want else 1.0
        ok = dev <= TOL
        if not ok:
            bad += 1
        print("  %-22s %9.1f m %10.1f m %8.2f%%  %s"
              % (name, got, want, dev * 100, "✔" if ok else "✘"))

    # 派生比例（这些才是"形状像不像"的关键）
    print("-" * 76)
    print("派生比例（形状相似性的关键）：")
    lo_s, hi_s = comp_bbox("saucer")
    lo_h, hi_h = comp_bbox("hull2")
    lo_n, hi_n = comp_bbox("nacelle")
    if lo_s and lo_h:
        got = (hi_h[0] - lo_h[0]) / (hi_s[0] - lo_s[0])
        want = pub["secondary_hull_diameter_m"] / pub["saucer_diameter_m"]
        dev = abs(got - want) / want
        if dev > TOL:
            bad += 1
        print("  工程舰体直径 / 碟形直径 = %.3f（公开 %.3f）%s"
              % (got, want, "✔" if dev <= TOL else "✘"))
        print("      ↳ 我 v2 曾做成 0.565、v3 做成 0.348 —— 公开数据是 0.268")
    if lo_s and lo_n:
        got = max(h[1] - l[1] for l, h in comp_list("nacelle")) / (hi_s[0] - lo_s[0])
        want = pub["nacelle_length_m"] / pub["saucer_diameter_m"]
        dev = abs(got - want) / want
        if dev > TOL:
            bad += 1
        print("  引擎舱长度 / 碟形直径 = %.3f（公开 %.3f）%s"
              % (got, want, "✔" if dev <= TOL else "✘"))
    if bbox.get("envelope"):
        sz = bbox["envelope"]["size"]
        got = to_m(sz[1]) / to_m(sz[0])
        want = pub["overall_length_m"] / pub["overall_beam_m"]
        dev = abs(got - want) / want
        if dev > TOL:
            bad += 1
        print("  全长 / 全宽             = %.3f（公开 %.3f）%s"
              % (got, want, "✔" if dev <= TOL else "✘"))

    print("-" * 76)
    if bad == 0:
        print("✔ 全部符合公开数据（容差 %.0f%%）：形状不是「我觉得像」，是可核对的。" % (TOL * 100))
        return 0
    print("✘ %d 项不符合公开数据。" % bad)
    return 1


if __name__ == "__main__":
    sys.exit(main())
