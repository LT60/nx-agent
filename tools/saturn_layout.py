# -*- coding: utf-8 -*-
"""★ 土星五号：从 STL 实测数据算出装配布局（离线，不开 NX）。

为什么不用"手写坐标"：
  这批 NASA STL 是**各零件独立建模/打印姿态**，坐标互不相干 ——
  实测长轴（Y）区间从 -216 交错到 +437，根本不是装配位置。
  所以必须自己堆叠。但堆叠的**每一级落点由零件实测长度累加得出**，
  不靠我目测；只有"级的先后顺序"来自土星五号公开的级构。

    底部 → 顶部的级序（公开级构）：
      S-IC 底部 → S-IC 顶部 → 1-2 级间段 → S-II → 2-3 级间段
      → S-IVB → LEM 整流罩 → 服务舱 → 指令舱 → 逃逸塔

输出：output/enterprise/saturn_layout.json
      每个零件：源文件、轴向区间、长度、以及算好的**放置原点 + 姿态矩阵**
      （矩阵把零件的局部 +Y 轴竖起来对到世界 +Z，即火箭立起来）
"""

from __future__ import print_function

import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SRC = os.path.join(ROOT, "output", "enterprise", "downloaded")
OUT = os.path.join(ROOT, "output", "enterprise", "saturn_layout.json")

# 底部 → 顶部。名字用下载到的实际文件名（NASA 仓库里有拼写错误，照抄）。
STACK = [
    ("S-IC 第一级·底部", "S-IC bottom.stl"),
    ("S-IC 第一级·顶部", "S-IC top.stl"),
    ("1-2 级间段",       "stage 1-2 coupler.stl"),
    ("S-II 第二级",      "S-II.stl"),
    ("2-3 级间段",       "stage 1-2 coupler v2.stl"),
    ("S-IVB 第三级",     "S-IV b.stl"),
    ("LEM 整流罩",       "lem shroud.stl"),
    ("服务舱",           "service module.stl"),
    ("指令舱",           "command moduel.stl"),
    ("逃逸塔",           "escape tower.stl"),
]
# 明确不参与装配的（打印辅助件 / 需按角度复制的附件）
SKIP = {
    "S-IC joining cube.stl": "打印用的对位方块，不是火箭零件",
    "fin (print 4).stl": "尾翼（需绕轴复制 4 份，且局部朝向需另行确定）—— 本轮先不放",
}

# 姿态矩阵（行主序）：把零件局部 +Y 轴竖起来对到世界 +Z，另外两轴跟着转。
#   col1=局部X→世界X (1,0,0)；col2=局部Y→世界Z (0,0,1)；col3=局部X×局部Y=(0,-1,0)
STAND_UP = [[1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
            [0.0, 1.0, 0.0]]


def stl_bbox(path):
    """读 STL（自动认 ASCII/二进制）返回 (min[3], max[3])。"""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
        if len(head) < 84:
            return None
        n = struct.unpack("<I", head[80:84])[0]
        is_ascii = (84 + n * 50 != size)
    lo = [1e18] * 3
    hi = [-1e18] * 3
    if is_ascii:
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
                s = line.strip()
                if s.startswith("vertex"):
                    v = [float(x) for x in s.split()[1:4]]
                    for i in range(3):
                        lo[i] = min(lo[i], v[i]); hi[i] = max(hi[i], v[i])
    else:
        with open(path, "rb") as fh:
            fh.seek(84)
            while True:
                rec = fh.read(50)
                if len(rec) < 50:
                    break
                vals = struct.unpack("<12fH", rec)
                for k in range(3):
                    v = vals[3 + k * 3: 6 + k * 3]
                    for i in range(3):
                        lo[i] = min(lo[i], v[i]); hi[i] = max(hi[i], v[i])
    return lo, hi


def main():
    if not os.path.isdir(SRC):
        print("找不到目录: %s" % SRC)
        return 2

    parts = []
    z_cursor = 0.0          # 从底座往上累加
    problems = []
    for order, (label, fn) in enumerate(STACK, 1):
        path = os.path.join(SRC, fn)
        if not os.path.isfile(path):
            problems.append("缺文件: %s" % fn)
            continue
        r = stl_bbox(path)
        if not r:
            problems.append("解析失败: %s" % fn)
            continue
        lo, hi = r
        # 轴向 = Y（实测：截面在 XZ 上成圆）
        ax_len = hi[1] - lo[1]
        cx = (lo[0] + hi[0]) / 2.0
        cz = (lo[2] + hi[2]) / 2.0
        dia = max(hi[0] - lo[0], hi[2] - lo[2])

        # 世界 = 原点 + M·局部。
        #   STAND_UP 把局部 y 送到世界 z；局部 x→世界 x；局部 z→世界 -y
        #   → 世界 x = org.x + x      ⇒ 要把零件横向居中：org.x = -cx
        #   → 世界 y = org.y - z      ⇒ 要把零件纵向居中：org.y = +cz
        #   → 世界 z = org.z + y      ⇒ 让零件底端(lo[1])落在 z_cursor：org.z = z_cursor - lo[1]
        origin = [-cx, cz, z_cursor - lo[1]]
        parts.append({
            "order": order, "label": label, "file": fn,
            "local_min": [round(v, 3) for v in lo],
            "local_max": [round(v, 3) for v in hi],
            "axis_length": round(ax_len, 3),
            "diameter": round(dia, 3),
            "origin": [round(v, 3) for v in origin],
            "matrix": STAND_UP,
            "z_bottom": round(z_cursor, 3),
            "z_top": round(z_cursor + ax_len, 3),
        })
        z_cursor += ax_len

    out = {
        "source": "NASA 3D Resources (github.com/nasa/NASA-3D-Resources, 公有领域) · Saturn V Rocket",
        "units": "mm（STL 原始单位，未缩放）",
        "axis_note": "STL 里长轴是 Y，装配时用 STAND_UP 矩阵竖起来（世界 Z 为火箭轴）",
        "stack_note": "级序来自土星五号公开级构；每级落点由零件实测长度累加得出",
        "total_height": round(z_cursor, 3),
        "skipped": SKIP,
        "parts": parts,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print("土星五号装配布局（离线由 STL 实测数据算出）")
    print("  源: %s" % out["source"])
    print("  %-16s %-24s %8s %8s %10s" % ("级", "文件", "直径", "长度", "落点 z"))
    for p in parts:
        print("  %-16s %-24s %8.1f %8.1f %10.1f"
              % (p["label"], p["file"], p["diameter"], p["axis_length"], p["z_bottom"]))
    print("  " + "-" * 70)
    print("  全箭高 = %.1f mm" % out["total_height"])
    for fn, why in SKIP.items():
        print("  跳过: %-24s %s" % (fn, why))
    if problems:
        print("  问题: %s" % problems)
    print("  已写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
