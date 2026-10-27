#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量每个装配接头「物理上最多能插多深」—— 判断是否还有插入余量。

★★★ 为什么需要（2026-10-11，用户指出三处「对齐了但没完整插入」）★★★
  用户在展示页上发现三处接头只是搭在一起，没有插到底：
    ① S-II ↔ 1-2 级间段   ② S-IVB ↔ LEM 整流罩   ③ 指令舱 ↔ 逃逸塔

  「完整插入」的物理定义（全靠数据，不靠感觉）：
    把上面零件 U 往下面零件 L 的腔里插，深度 d 处不产生**径向干涉**的
    最大值 = D_max。判据：U 在其底以上 h 处的外半径，必须 ≤ L 在
    同一世界高度处的**内腔半径**（两者同心于世界 Z 轴，装配偏心 ≤1.33mm）。
      r_out_U(底以上 h) ≤ r_in_L(顶以下 d-h)   对所有 h ∈ [0, d]

  ★ 半径以**每档自己的轴心**算（这批 STL 建模原点全是偏的——血泪教训）；
  ★ 用 95/5 分位数抗毛刺。

用法：
    python tools/measure_insertion.py            # 全部接头
    python tools/measure_insertion.py --json     # 结构化输出
"""

import argparse
import io
import json
import math
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")


def stl_tris(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
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
            rec = fh.read(50)
            if len(rec) < 50:
                break
            v = struct.unpack("<12fH", rec)
            out.append(((v[3], v[4], v[5]), (v[6], v[7], v[8]),
                        (v[9], v[10], v[11])))
    return out


def world_pts(part, src_dir):
    """零件顶点的世界坐标（用装配矩阵）。"""
    tris = stl_tris(os.path.join(src_dir, part["file"]))
    M = [v for row in part["matrix"] for v in row]
    o = part["origin"]
    pts = []
    for t in tris:
        for (a, b, c) in t:
            pts.append((o[0] + M[0] * a + M[1] * b + M[2] * c,
                        o[1] + M[3] * a + M[4] * b + M[5] * c,
                        o[2] + M[6] * a + M[7] * b + M[8] * c))
    return pts


def z_profile(pts, band=2.0, z0=None, z1=None):
    """世界 Z 分档 → 每档 r_in(5%) / r_out(95%)，半径以**该档轴心**为准。"""
    zs = [p[2] for p in pts]
    if z0 is None:
        z0 = min(zs)
    if z1 is None:
        z1 = max(zs)
    h = max(z1 - z0, 1e-6)
    bands = []
    z = z0
    while z < z1 - 1e-6:
        z2 = min(z + band, z1)
        sel = [p for p in pts if z <= p[2] <= z2]
        if len(sel) >= 8:
            cx = sum(p[0] for p in sel) / len(sel)
            cy = sum(p[1] for p in sel) / len(sel)
            rr = sorted(math.hypot(p[0] - cx, p[1] - cy) for p in sel)
            bands.append({"z0": z, "z1": z2,
                          "r_in": rr[int(len(rr) * 0.05)],
                          "r_out": rr[int(len(rr) * 0.95)]})
        z = z2
    return bands


def band_at(bands, z):
    """取覆盖 z 的档；没有就取最近的一档。"""
    best, bd = None, 1e18
    for b in bands:
        if b["z0"] <= z <= b["z1"]:
            return b
        d = min(abs(z - b["z0"]), abs(z - b["z1"]))
        if d < bd:
            best, bd = b, d
    return best


def max_insertion(L_pts, U_pts, zL_top, o_cur, step=2.0, limit=None):
    """接头还能再插多深？—— 两种咬合方向都扫（2026-10-11 教训）：

    方向 A（上件插进下件的腔）：更多搭接时，U 的外半径必须 ≤ L 在同一
      世界高度的内腔半径。约束：r_out_U(U0+h) ≤ r_in_L(L1-o+h), h∈[0,o]
    方向 B（下件的凸台插进上件的座）：更多搭接时，L 顶部新进入搭接区的
      外半径必须 ≤ U 在同一世界高度的内腔半径。
      约束：r_out_L(L1-h) ≤ r_in_U(L1-h), h∈[o_cur, o]
      ★ U 的内腔只在 U 的跨度内存在（h ≤ o），自动成立。

    两种方向取更大者（接头实际按装得下的那种方式咬合）。
    半径以每档自己的轴心为准，95/5 分位数。
    返回 (D_max, mode)。
    """
    bL = z_profile(L_pts, 2.0)
    bU = z_profile(U_pts, 2.0)
    u0 = min(p[2] for p in U_pts)
    u1 = max(p[2] for p in U_pts)
    l0 = min(p[2] for p in L_pts)
    l1 = max(p[2] for p in L_pts)
    d_cap = min(u1 - u0, l1 - l0)
    if limit:
        d_cap = min(d_cap, limit)

    def clash_A(o):
        """方向 A：U 插进 L 的腔，搭接量 o。"""
        h = 0.0
        while h <= o + 1e-9:
            bU_ = band_at(bU, u0 + h)
            bL_ = band_at(bL, l1 - o + h)
            if bU_ is not None and bL_ is not None:
                if bU_["r_out"] > bL_["r_in"] + 0.3:
                    return True
            h += step
        return False

    def clash_B(o):
        """方向 B：L 的凸台插进 U 的座，搭接量 o。"""
        h = o_cur
        while h <= o + 1e-9:
            bL_ = band_at(bL, l1 - h)
            bU_ = band_at(bU, l1 - h)
            if bL_ is not None and bU_ is not None:
                if bL_["r_out"] > bU_["r_in"] + 0.3:
                    return True
            h += step
        return False

    # 方向 A：向上找最大可行（还能插多深）
    best_a, o = o_cur, o_cur
    while o <= d_cap + 1e-9:
        if clash_A(o):
            break
        best_a = o
        o += step
    # 方向 B：同上
    best_b, o = o_cur, o_cur
    while o <= d_cap + 1e-9:
        if clash_B(o):
            break
        best_b = o
        o += step
    # ★ 方向 C：向下扫描 —— 当前搭接可能已超过物理可行（径向干涉），
    #   找当前深度以下的最大可行值 = 修正值
    fix_c, o = None, o_cur
    while o >= 0:
        if not clash_A(o) and not clash_B(o):
            fix_c = o
            break
        o -= step
    cands = [x for x in (best_a, best_b, fix_c) if x is not None]
    dmax = max(cands) if cands else 0.0
    mode = ("A 上件入下件腔" if dmax == best_a else
            "B 下件凸台入上件座" if dmax == best_b else "C 修正")
    return dmax, mode


def main(argv=None):
    ap = argparse.ArgumentParser(description="量接头的最大插入深度")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with io.open(os.path.join(ENT, "saturn_layout.json"), "r",
                 encoding="utf-8") as fh:
        lay = json.load(fh)
    src = os.path.join(ENT, "downloaded")

    parts = {p["label"]: p for p in lay["parts"]}
    pts = {lb: world_pts(p, src) for lb, p in parts.items()}
    zr = {lb: (min(p[2] for p in q), max(p[2] for p in q))
          for lb, q in pts.items()}

    print("=" * 100)
    print("接头最大插入深度（逐高度扫描径向干涉）—— 判断「是否还有插入余量」")
    print("=" * 100)
    print("%-20s %-20s %10s %10s %10s  %s"
          % ("下件(腔)", "上件(柱)", "当前搭接", "最大可插", "余量", "结论"))
    print("-" * 100)

    out = []
    for j in lay["joints"]:
        lo, hi = j["below"], j["above"]
        cur = float(j.get("overlap") or 0.0)
        zL_top = zr[lo][1]
        # 限制搜索范围：上件长度 + 下件长度（足够）
        lim = (zr[hi][1] - zr[hi][0]) + (zr[lo][1] - zr[lo][0])
        dmax, mode = max_insertion(pts[lo], pts[hi], zL_top, cur,
                                   step=2.0, limit=lim)
        margin = dmax - cur
        mode_s = mode
        if margin > 4:
            if margin > 4:
                v = "★ 还有 %.0fmm 插入余量 → 应加深（%s）" % (margin, mode_s)
            elif margin >= -4:
                v = "已在物理最大插入处 ✔（%s）" % mode_s
            else:
                v = "⚠ 超物理最大 %.0fmm → 应退到 %.0fmm（%s）" % (-margin, dmax, mode_s)
        elif margin >= -4:
            v = "已接近/达到最大插入 ✔"
        else:
            v = "⚠ 超过物理最大 %.0fmm（径向干涉！）" % (-margin)
        print("%-20s %-20s %10.1f %10.1f %10.1f  %s"
              % (lo[:20], hi[:20], cur, dmax, margin, v))
        out.append({"below": lo, "above": hi, "current": cur,
                    "d_max": round(dmax, 1), "margin": round(margin, 1),
                    "mode": mode_s, "verdict": v})

    print("-" * 100)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    with io.open(os.path.join(ENT, "insertion_report.json"), "w",
                 encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("已写 insertion_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())