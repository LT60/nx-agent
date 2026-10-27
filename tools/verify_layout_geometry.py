#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用**真实 STL 顶点**验证布局的 origin + matrix —— 这才是不自欺欺人的核验。

★★★ 为什么必须用真实顶点（2026-10-10，我踩过的最大坑）★★★
  我之前的核验全是"布局自己算的 z_bottom"跟"布局自己算的 z_top"比 ——
  **循环论证**，永远显示 0.00mm 偏差，于是我三次宣布"装配正确"。
  用户看到的却是一地散落的零件。

  真实事故：`saturn_layout_v2.py` 写死"长轴是局部 Y"，
  但这批 STL 里 **command moduel.stl 的长轴是 X**（X=33.9、Y=20.1、Z=33.9）。
  对它按 Y 造矩阵 → 变换后落在 z=1159，而声称 z=1040 → **错 118mm**。

  ★ 另一条教训：**不要用近似包围盒验证**。
    我先用 rmax/len 造近似盒子做检查，结果 10/10 全报"不自洽"——
    那是误报（近似盒子的半高不等于真实半高），真错只有 1 处。
    凡是能用真实几何，就别用近似。

用法：
    python tools/verify_layout_geometry.py output/enterprise/saturn_layout.json
退出码 0 = 全部自洽，1 = 有不自洽
"""

import io
import json
import os
import struct
import sys

TOL = 0.5
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stl_vertices(path):
    """读 STL 的所有顶点（ASCII / 二进制都支持）。"""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
    is_ascii = (84 + n * 50 != size)
    out = []
    if is_ascii:
        for line in io.open(path, "r", errors="ignore"):
            t = line.strip()
            if t.startswith("vertex"):
                p = t.split()
                out.append((float(p[1]), float(p[2]), float(p[3])))
    else:
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    lay_path = argv[0]
    with io.open(lay_path, "r", encoding="utf-8") as fh:
        lay = json.load(fh)
    src_dir = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(lay_path), "downloaded")

    parts = lay.get("parts") or []
    print("=" * 92)
    print("布局几何自检：用真实 STL 顶点验证 origin + matrix")
    print("=" * 92)
    print("%-22s %-4s %22s %22s %s"
          % ("零件", "长轴", "顶点算出的 Z", "声称 Z", "判定"))
    print("-" * 92)

    bad = 0
    for p in parts:
        src = os.path.join(src_dir, p["file"])
        if not os.path.isfile(src):
            print("%-22s 缺文件 %s" % (str(p["label"])[:22], src))
            bad += 1
            continue
        vs = stl_vertices(src)
        M, o = p["matrix"], p["origin"]
        # ★★★ 三个轴都要算 —— 我曾经只验Z，结果漏掉"所有零件沿 Y 偏 16mm"
        #   （center 的第二个分量是横轴中心，不是世界 Y，我当成了 Y 用）
        pts = [(o[k] + M[k][0] * x + M[k][1] * y + M[k][2] * z)
               for (x, y, z) in vs for k in (0,)]
        tx = [o[0] + M[0][0] * x + M[0][1] * y + M[0][2] * z for (x, y, z) in vs]
        ty = [o[1] + M[1][0] * x + M[1][1] * y + M[1][2] * z for (x, y, z) in vs]
        tz = [o[2] + M[2][0] * x + M[2][1] * y + M[2][2] * z for (x, y, z) in vs]

        lo, hi = min(tz), max(tz)
        e0, e1 = float(p["z_bottom"]), float(p["z_top"])
        ok = abs(lo - e0) <= TOL and abs(hi - e1) <= TOL
        # ★ 轴心检查：变换后的轴心必须落在世界原点附近（火箭是竖直的，轴心应在 XY 原点）
        cxm = (min(tx) + max(tx)) / 2.0
        cym = (min(ty) + max(ty)) / 2.0
        ecc = (cxm ** 2 + cym ** 2) ** 0.5
        AXIS_TOL = 2.0
        if not ok:
            bad += 1
        elif ecc > AXIS_TOL:
            bad += 1
        note = ""
        if not ok:
            note = "MISMATCH %+.1f / %+.1f" % (lo - e0, hi - e1)
        elif ecc > AXIS_TOL:
            note = "OFF-AXIS  偏心 %.1fmm (%.1f, %.1f)" % (ecc, cxm, cym)
        print("%-22s %-4s %22s %22s %s"
              % (str(p["label"])[:22], p.get("local_axis", "?"),
                 "[%8.1f,%8.1f]" % (lo, hi),
                 "[%8.1f,%8.1f]" % (e0, e1),
                 ("OK  偏心 %.2f" % ecc) if not note else note))

    print("-" * 92)
    if bad:
        print("不自洽 %d / %d 件 —— 这些零件的 origin/matrix 算错了，"
              "NX 会摆在错位置。" % (bad, len(parts)))
        print("\n排查顺序：")
        print("  1 该件的长轴判定对不对（打印里 local_axis 与实测三轴尺寸比对）")
        print("  2 matrix 的长轴→世界Z 映射对不对（不同轴需要不同的行列）")
        print("  3 center（横轴中心）有没有按新轴重新映射")
        print("  4 变换后轴心是否还在世界原点附近（OFF-AXIS 就是偏了）")
        return 1
    print("全部 %d 件自洽：真实几何的 Z 区间一致，且轴心都在原点附近。" % len(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())