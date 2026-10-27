#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按**真实径向剖面**计算装配落点 —— 替换"按包围盒猜重叠"的旧做法。

★★★ 为什么必须重写（2026-10-10，我踩的最大的坑）★★★
  我原来按"包围盒重叠"算套接量，结果 **9 个接头里 7 个没真正贴合**。
  原因：打印件的接头是**开口套筒**，不是实心柱。
  实测级间段 `stage 1-2 coupler.stl`：
      z370-467，内孔 r=35.8，外壁 r=45.5~51.0，上下都开口
  实测 S-II：
      z442 处 外壁 r=29.7  →  29.7 < 35.8，能穿过孔
  所以「套接」= **上面的件伸进下面的件的孔里**，落点由**孔口位置**决定，
  不是由"包围盒重叠多少"决定。

本工具的做法（全部按真实顶点算，不拍脑袋）：
  1. 每件算真实 (z, r) 剖面 → 每个高度档的 `r_in`（内孔）/ `r_out`（外壁）
  2. 相邻两件对接时，找**下面件**的孔（某档 `r_in` 明显大于该档 `r_out`）
     与**上面件**的柱（某档 `r_out` 明显小于孔径）配对
  3. 让柱的顶端**伸到孔口的某个确定深度**（默认 15% 孔长，可配）
  4. 输出每对的「孔 z 区间 / 孔内径 / 柱外径 / 插入深度 / 径向间隙」

用法：
    python tools/fit_joints_by_profile.py output/enterprise/saturn_layout.json
    python tools/fit_joints_by_profile.py --depth 12-o. json
"""

import argparse
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAND = 3.0            # 剖面分档高度 mm
MIN_HOLE = 2.0        # 壁厚大于这个值才算"有孔"
CLEARANCE = 0.5       # 允许的径向间隙 mm（打印件总有点配合间隙）


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


def profile(part, src_dir):
    """真实 (z, r) → 每档的 r_in / r_out。"""
    vs = stl_vertices(os.path.join(src_dir, part["file"]))
    M, o = part["matrix"], part["origin"]
    ZR = []
    for (a, b, c) in vs:
        wx = o[0] + M[0][0] * a + M[0][1] * b + M[0][2] * c
        wy = o[1] + M[1][0] * a + M[1][1] * b + M[1][2] * c
        wz = o[2] + M[2][0] * a + M[2][1] * b + M[2][2] * c
        ZR.append((wz, (wx * wx + wy * wy) ** 0.5))
    z0, z1 = min(z for z, _ in ZR), max(z for z, _ in ZR)
    bands = []
    z = z0
    while z < z1 - 1e-6:
        z2 = min(z + BAND, z1)
        rs = [r for zz, r in ZR if z <= zz <= z2]
        if rs:
            bands.append({"z0": z, "z1": z2, "r_in": min(rs),
                          "r_out": max(rs), "wall": max(rs) - min(rs)})
        z = z2
    return {"label": part.get("label"), "z0": z0, "z1": z1,
            "bands": bands, "part": part}


def find_hole(prof):
    """找出这段的孔：连续几档 r_in 明显大于 r_out?不对 ——
    套筒的特征是**内孔小、外壁大**（r_in 远小于 r_out），
    而要插进去的柱是**接近实心**（r_in ≈ r_out）。
    所以要找的是「中空段」：r_in 明显小于 r_out。
    返回 (孔口下沿 z, 孔口上沿 z, 孔半径=该段 r_in 的中位, 外壁半径)
    """
    bands = prof["bands"]
    if not bands:
        return None
    # ★★ 判据要"稳健"，不能只看单档（2026-10-10踩过）：
    #   单档的 r_in 会跳变（三角面采样不齐/局部结构），
    #   于是"取最长中空段"只认出9mm 的碎段，
    #   而级间段的孔明明从 z=370 一直通到 467（97mm）。
    #
    #   改成：**整段的代表内孔半径 = 内孔半径的中位数**，
    #   再按"该档壁厚 > 中位内孔的 12%"来判定中空。
    #   这样局部毛刺不会打断整段。
    ins_all = sorted(b["r_in"] for b in bands)
    r_typ = ins_all[len(ins_all) // 2]          # 代表内孔
    hollow_thr = max(MIN_HOLE, r_typ * 0.12)

    hollow = [b for b in bands
              if b["wall"] > hollow_thr and b["r_in"] > max(1.0, r_typ * 0.15)]
    if not hollow:
        return None

    # 合并相邻档（允许一档以内的空隙—— 局部实心不算打断）
    runs = []
    cur = None
    for b in hollow:
        if cur and b["z0"] - cur["z1"] <= BAND * 1.5:
            cur["z1"] = b["z1"]
            cur["ins"].append(b["r_in"])
            cur["outs"].append(b["r_out"])
        else:
            if cur:
                runs.append(cur)
            cur = {"z0": b["z0"], "z1": b["z1"],
                   "ins": [b["r_in"]], "outs": [b["r_out"]]}
    if cur:
        runs.append(cur)
    best = max(runs, key=lambda r: r["z1"] - r["z0"])
    ins = sorted(best["ins"])
    return {"z0": best["z0"], "z1": best["z1"],
            # 用**下四分位**当孔径：毛刺（局部更小）不会把孔算小
            "r_hole": ins[len(ins) // 4],
            "r_out": max(best["outs"]),
            "len": best["z1"] - best["z0"]}


def find_pin(prof):
    """找出这段的柱（插入端）：靠近某一端的**接近实心**部分。
    返回 (柱的 z0, z1, 柱半径)
    """
    bands = prof["bands"]
    if not bands:
        return None
    outs_all = sorted(b["r_out"] for b in bands)
    r_typ = outs_all[len(outs_all) // 2]
    # 柱：壁厚小于代表半径的 12%（接近实心）
    solid = [b for b in bands if b["wall"] <= max(MIN_HOLE, r_typ * 0.12)]
    if not solid:
        return None
    runs = []
    cur = None
    for b in solid:
        if cur and b["z0"] - cur["z1"] <= BAND * 1.5:
            cur["z1"] = b["z1"]
            cur["rs"].append(b["r_out"])
        else:
            if cur:
                runs.append(cur)
            cur = {"z0": b["z0"], "z1": b["z1"], "rs": [b["r_out"]]}
    if cur:
        runs.append(cur)
    best = max(runs, key=lambda r: r["z1"] - r["z0"])
    rs = sorted(best["rs"])
    return {"z0": best["z0"], "z1": best["z1"], "r": rs[len(rs) // 2],
            "len": best["z1"] - best["z0"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="按真实径向剖面算接头落点")
    ap.add_argument("layout", nargs="?",
                    default=os.path.join(ROOT, "output", "enterprise",
                                         "saturn_layout.json"))
    ap.add_argument("--depth", type=float, default=None,
                    help="柱插入孔的深度 mm（默认取孔长的 35%%，上限 25）")
    args = ap.parse_args(argv)

    with io.open(args.layout, "r", encoding="utf-8") as fh:
        lay = json.load(fh)
    src_dir = os.path.join(os.path.dirname(args.layout), "downloaded")

    parts = lay["parts"]
    profs = [profile(p, src_dir) for p in parts]
    for pr, pp in zip(profs, parts):
        pr["part"] = pp

    print("=" * 100)
    print("按真实径向剖面算接头（每 %g mm 一档；孔=内孔，柱=实心插入端）" % BAND)
    print("=" * 100)

    # 找出每件是"孔"还是"柱"，以及它作为上/下件时的角色
    for pr in profs:
        pr["hole"] = find_hole(pr)
        pr["pin"] = find_pin(pr)

    print("\n每件的中空段（孔）与实心段（柱）：")
    print("%-22s %10s %10s   %-26s %-26s"
          % ("零件", "z0", "z1", "孔(内径/外壁/长度)", "柱(半径/长度)"))
    print("-" * 100)
    for pr in profs:
        h = pr["hole"]
        pin = pr["pin"]
        hs = ("r%.1f/%.1f  L=%.0f  z%.0f-%.0f" % (h["r_hole"], h["r_out"], h["len"],
                                                 h["z0"], h["z1"])) if h else "—"
        ps = ("r=%.1f  L=%.0f  z%.0f-%.0f" % (pin["r"], pin["len"], pin["z0"], pin["z1"])) if pin else "—"
        print("%-22s %10.1f %10.1f   %-26s %-26s"
              % (str(pr["label"])[:22], pr["z0"], pr["z1"], hs, ps))

    # 逐对算配对
    print("\n" + "=" * 100)
    print("相邻接头的配对可行性")
    print("=" * 100)
    print("%-20s %-20s %10s %10s %10s %s"
          % ("下件", "上件", "孔内径", "柱外径", "径向间隙", "结论"))
    print("-" * 100)
    pairs = []
    for i in range(len(profs) - 1):
        a, b = profs[i], profs[i + 1]
        h = a["hole"]
        pin = b["pin"]
        if not h or not pin:
            verdict = ("下件无孔/上件无柱 → 只能贴面" if (not h or not pin)
                       else "?")
            print("%-20s %-20s %10s %10s %10s %s"
                  % (str(a["label"])[:20], str(b["label"])[:20],
                     "%.1f" % h["r_hole"] if h else "—",
                     "%.1f" % pin["r"] if pin else "—",
                     "—", verdict))
            pairs.append({"below": a["label"], "above": b["label"],
                          "ok": False, "why": verdict})
            continue
        gap = h["r_hole"] - pin["r"]
        # ★★ 三种配合方式（2026-10-10 实测得出，NASA 打印件三种都有）：
        #   A 插入式：上件是细柱(外径< 孔径)  → 落点由"插多深"决定
        #   B 套入式：上件是粗筒(外径 > 孔径，但 < 下件外壁)
        #              → 上件套在**下件外面**，落点由"套多深"决定
        #   C 贴合式：两者径向都差不多 → 只能面贴合，重叠≈0
        # 我原来只认A，所以 7/9 都判"插不进" —— 其实它们是 B 或 C。
        fit = "?"
        if gap >= CLEARANCE:
            fit = "A 插入"
            depth = (args.depth if args.depth is not None
                     else min(25.0, h["len"] * 0.35))
            verdict = "✔ %s式，插入 %.0f mm" % (fit, depth)
            ok = True
        elif pin["r"] < h["r_out"]:
            # 上件套在下面那个的外壁上 → 上件底要伸进下件套筒区
            depth = min(20.0, max(5.0, (h["z1"] - h["z0"]) * 0.3))
            fit = "B 套入"
            verdict = "✔ %s式，套 %.0f mm（柱 %.1f > 孔 %.1f 但 < 外壁 %.1f）" % (
                fit, depth, pin["r"], h["r_hole"], h["r_out"])
            ok = True
        else:
            fit = "C 贴合"
            verdict = "⚠ %s式：柱 %.1f ≥ 外壁 %.1f，只能面贴合，重叠取 0" % (
                fit, pin["r"], h["r_out"])
            ok = True
            depth = 0.0
        print("%-20s %-20s %10.1f %10.1f %10.2f %s"
              % (str(a["label"])[:20], str(b["label"])[:20],
                 h["r_hole"], pin["r"], gap, verdict))
        pairs.append({
            "fit": fit,
            "below": a["label"], "above": b["label"],
            "ok": ok, "r_hole": h["r_hole"], "r_pin": pin["r"],
            "radial_gap": round(gap, 3),
            "hole_z": [round(h["z0"], 2), round(h["z1"], 2)],
            "pin_z": [round(pin["z0"], 2), round(pin["z1"], 2)],
            "insert_depth": round(depth, 2),
            "why": verdict,
        })

    from collections import Counter
    kinds = Counter(p.get("fit", "?") for p in pairs)
    print("\n配合方式分布：%s" % dict(kinds))
    print("已算出落点的接头：%d / %d"
          % (sum(1 for p in pairs if p.get("fit") != "?"), len(pairs)))
    out = os.path.splitext(args.layout)[0] + "_joints.json"
    with io.open(out, "w", encoding="utf-8") as fh:
        json.dump({"pairs": pairs, "profiles": [
            {"label": pr["label"], "z0": pr["z0"], "z1": pr["z1"],
             "hole": pr["hole"], "pin": pr["pin"]} for pr in profs]},
            fh, ensure_ascii=False, indent=2)
    print("已写 %s" % os.path.basename(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())