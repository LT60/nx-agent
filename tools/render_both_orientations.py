#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每个零件渲染**两个朝向**并排对比 —— 用来判断有没有装反。

★★★ 为什么需要（2026-10-10，用户提问）★★★
  用户：「每一个部分的上下方向我不确定是否正确，感觉有一些部件上下翻转了」

  朝向是 `saturn_layout_v2.py` 的**朝向搜索**选的，代价函数是
  「相邻接口半径失配 + 全箭下粗上细」——**全局**判据，
  完全可能为了整体最优把某个零件单独推错。必须逐个看。

  但"看单张渲染图"很难判断对错（没有参照）。
  **两个朝向并排**就直观了：发动机喷管、雷达罩尖头、锥体收口
  这些特征一旦摆错边，一眼就能看出来。

用法：
    python tools/render_both_orientations.py
    python tools/render_both_orientations.py --size 250 --cols 4
"""

import argparse
import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_assembly_png as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")
SRC = os.path.join(ENT, "downloaded")


def flip_matrix(rows):
    """把朝向矩阵做"上下翻"：长轴取负（绕世界 X 轴 180°）。"""
    return [[rows[0][0], rows[0][1], rows[0][2]],
            [-rows[1][0], -rows[1][1], -rows[1][2]],
            [-rows[2][0], -rows[2][1], -rows[2][2]]]


def main(argv=None):
    ap = argparse.ArgumentParser(description="零件两朝向并排渲染")
    ap.add_argument("--size", type=int, default=230)
    ap.add_argument("--cols", type=int, default=4, help="每行放几格（每件占2格）")
    ap.add_argument("-o", "--out", default=os.path.join(ENT, "orient_compare.png"))
    args = ap.parse_args(argv)

    with io.open(os.path.join(ENT, "saturn_layout.json"), "r",
                 encoding="utf-8") as fh:
        lay = json.load(fh)

    CW = args.size
    CH = int(CW * 1.45)
    GAP = 6
    cells_per_part = 2
    parts_per_row = max(1, args.cols // cells_per_part)
    parts = lay["parts"]
    rows = int(math.ceil(len(parts) / float(parts_per_row)))
    W = args.cols * CW + (args.cols + 1) * GAP
    H = rows * CH + (rows + 1) * GAP
    buf = bytearray(bytes((248, 249, 250)) * (W * H))

    print("每件两格：[当前] [反着]")
    print("%-22s %-8s %s" % ("零件", "当前", "判断依据提示"))
    print("-" * 78)

    for i, p in enumerate(parts):
        src = os.path.join(SRC, p["file"])
        tris = R.stl_tris(src)

        cur = p["matrix"]
        alt = flip_matrix(cur)

        # 先量"当前朝向"的世界 Z 区间，作为对齐基准
        Mc = [v for row in cur for v in row]
        Wc = R.worldize(tris, p["origin"], Mc)
        zc = [q[2] for t in Wc for q in t]
        base_z = min(zc)

        imgs = []
        for mat in (cur, alt):
            M = [v for row in mat for v in row]
            Wt = R.worldize(tris, p["origin"], M)
            zz = [q[2] for t in Wt for q in t]
            # ★ 翻转后零件会整体移位 → 把它的底面拉回同一高度，
            #   两格才能直接对比形状（不然一个上一个下，看不出翻转）
            dz = base_z - min(zz)
            if abs(dz) > 1e-9:
                Wt = [tuple((q[0], q[1], q[2] + dz)) for t in Wt for q in t]
                # worldize 返回的是三角面列表，上面这行把结构拍平了，重建：
                Wt = []
                for t in R.worldize(tris, p["origin"], M):
                    Wt.append(tuple((q[0], q[1], q[2] + dz) for q in t))
            imgs.append(R.render([(p["label"], Wt)], "front", CW, CH))

        base = i * cells_per_part
        for k, rgb in enumerate(imgs):
            idx = base + k
            col = idx % args.cols
            row = idx // args.cols
            x0 = GAP + col * (CW + GAP)
            y0 = GAP + row * (CH + GAP)
            for y in range(CH):
                d = ((y0 + y) * W + x0) * 3
                buf[d:d + CW * 3] = rgb[y * CW * 3:(y + 1) * CW * 3]
            # 第二格（反着）左下角画一小段红条做区分
            if k == 1:
                for y in range(CH - 5, CH - 1):
                    d = ((y0 + y) * W + x0) * 3
                    buf[d:d + 26 * 3] = bytes((226, 75, 74)) * 26

        rb, rt = p["r_bottom"], p["r_top"]
        hint = ("下粗上细" if rb > rt + 1.5
                else "上粗下细" if rt > rb + 1.5 else "近似等径")
        print("%-22s %-8s %s（底r%.1f 顶r%.1f）"
              % (p["label"][:22], "已翻转" if p["flip"] else "保持", hint, rb, rt))

    n = R.write_png(args.out, W, H, bytes(buf))
    print("\n已写 %s（%dx%d，%.0f KB）" % (args.out, W, H, n / 1024.0))
    print("排列：[件1当前][件1反着][件2当前][件2反着] … 每行 %d 格" % args.cols)
    print("★ 每对的**第二格左下角有红条** = 反着的那个方向")
    return 0


if __name__ == "__main__":
    sys.exit(main())