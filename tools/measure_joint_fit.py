#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★★ 真正的尺子：量相邻两段在**每个高度**上是否真的有材料 ★★
（2026-10-10 定位到"看起来散开"的唯一真凶）

之前所有核验都只看 **Z 区间是否重叠**。但 Z 重叠 ≠ 接合：
打印件的套筒和细颈，Z 上重叠、径向上错开，同一高度上根本没有材料。
→ 图上就是"中间有洞"。这就是我被"0.00mm 偏差"骗了三次的原因。

本工具按高度分档，报每一档上各段的**实体存在性**与最大半径：
  · 两段都有材料 → 真接合（这里才谈"重叠多少"）
  · 只有一段有料 → 中间是空的 → 就是你在图上看到的洞

用法：
    python tools/measure_joint_fit.py output/enterprise/saturn_layout.json [高度档mm]
"""
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def world_points(part, src_dir):
    vs = stl_vertices(os.path.join(src_dir, part["file"]))
    M, o = part["matrix"], part["origin"]
    return [(o[0] + M[0][0] * a + M[0][1] * b + M[0][2] * c,
             o[1] + M[1][0] * a + M[1][1] * b + M[1][2] * c,
             o[2] + M[2][0] * a + M[2][1] * b + M[2][2] * c)
            for (a, b, c) in vs]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    lay_path = (argv[0] if argv else
                os.path.join(ROOT, "output", "enterprise", "saturn_layout.json"))
    band = float(argv[1]) if len(argv) > 1 else 5.0

    with io.open(lay_path, "r", encoding="utf-8") as fh:
        lay = json.load(fh)
    src_dir = os.path.join(os.path.dirname(lay_path), "downloaded")

    segs = []
    for p in lay["parts"]:
        try:
            W = world_points(p, src_dir)
        except Exception as exc:
            print("跳过 %s: %s" % (p.get("label"), exc))
            continue
        zs = [w[2] for w in W]
        segs.append({"label": p.get("label", "?"), "pts": W,
                     "z0": min(zs), "z1": max(zs)})
    segs.sort(key=lambda s: s["z0"])

    print("=" * 96)
    print("相邻接头真实贴合度（每 %g mm 一档；只看两段 Z 区间有重叠的地方）" % band)
    print("=" * 96)

    n_hole = 0
    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        lo, hi = max(a["z0"], b["z0"]), min(a["z1"], b["z1"])
        print("\n%s → %s  Z 重叠 %.1f mm"
              % (a["label"][:22], b["label"][:22], max(0.0, hi - lo)))
        if hi - lo <= 0.5:
            print("   Z 区间不重叠 → **空隙 %.1f mm**（图上就是一道大洞）"
                  % (b["z0"] - a["z1"]))
            n_hole += 1
            continue
        z = lo
        only_a = only_b = both = neither = 0
        rows = []
        while z < hi - 1e-6:
            z2 = min(z + band, hi)
            ra = [((w[0] ** 2 + w[1] ** 2) ** 0.5)
                  for w in a["pts"] if z <= w[2] <= z2]
            rb = [((w[0] ** 2 + w[1] ** 2) ** 0.5)
                  for w in b["pts"] if z <= w[2] <= z2]
            if ra and rb:
                both += 1
                tag = "两段都有料 rA=%.1f rB=%.1f" % (max(ra), max(rb))
            elif ra:
                only_a += 1
                tag = "只有下段有料 r=%.1f ← 中间是空的" % max(ra)
            elif rb:
                only_b += 1
                tag = "只有上段有料 r=%.1f ← 中间是空的" % max(rb)
            else:
                neither += 1
                tag = "两段都没料"
            rows.append("   %6.0f-%6.0f  %s" % (z, z2, tag))
            z = z2
        for r in rows[:14]:
            print(r)
        if len(rows) > 14:
            print("   ...（还有 %d 档）" % (len(rows) - 14))
        if only_a or only_b or neither:
            n_hole += 1
            print("   ★ 结论：这个接头**没有真正贴合** —— "
                  "%d/%d 档只有单侧有料" % (only_a + only_b, len(rows)))
        else:
            print("   ✔ 每一档两段都有料，是真贴合")

    print("\n" + "=" * 96)
    print("没贴合的接头：%d / %d 个" % (n_hole, max(1, len(segs) - 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())