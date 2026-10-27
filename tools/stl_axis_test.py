# -*- coding: utf-8 -*-
"""★ 判定每个 STL 的**真实回转轴**：三条轴逐条打分，别再靠"尺寸最大"猜。

方法：沿候选轴切片，算每片截面中心；真正的回转轴下，各片中心几乎不漂移。
用**中位数**和**分位数**抗离群点（网格常有飞点/内部管路）。
另给一个"圆度"指标：该轴下 (横轴1 半径) 与 (横轴2 半径) 的差异。

用法：
    python tools/stl_axis_test.py [目录]
"""

from __future__ import print_function

import argparse
import os
import sys

AXES = "XYZ"


def read_tris(path):
    import struct
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


def axis_score(pts, k, nband=60):
    """沿轴 k 切片，返回 (中心漂移, 圆度偏差, 有效片数)。"""
    cross = [i for i in range(3) if i != k]
    vs = [p[k] for p in pts]
    lo, hi = min(vs), max(vs)
    if hi - lo <= 0:
        return None
    step = (hi - lo) / nband
    bands = [[] for _ in range(nband)]
    for p in pts:
        j = int((p[k] - lo) / step)
        j = 0 if j < 0 else (nband - 1 if j >= nband else j)
        bands[j].append(p)
    cs, rdiff = [], []
    for b in bands:
        if len(b) < 8:            # 顶点太少 → 该片不可信
            continue
        c = []
        r = []
        for ci in cross:
            vals = [p[ci] for p in b]
            c.append((min(vals) + max(vals)) / 2.0)
            r.append((max(vals) - min(vals)) / 2.0)
        cs.append(c)
        if r[0] > 1.0:
            rdiff.append(abs(r[0] - r[1]) / r[0])
    if len(cs) < 3:
        return None
    drift = 0.0
    for j in range(2):
        col = sorted(c[j] for c in cs)
        n = len(col)
        # 用 10%~90% 分位差，抗离群
        lo_q = col[int(n * 0.10)]
        hi_q = col[min(n - 1, int(n * 0.90))]
        drift = max(drift, hi_q - lo_q)
    rr = sum(rdiff) / len(rdiff) if rdiff else 1.0
    return drift, rr, len(cs)


def main(argv=None):
    ap = argparse.ArgumentParser(description="判定每个 STL 的真实回转轴")
    ap.add_argument("folder", nargs="?", default=None)
    ns = ap.parse_args(argv)
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(HERE)
    d = ns.folder or os.path.join(ROOT, "output", "enterprise", "downloaded")

    print("%-26s %-38s %s" % ("文件", "三条轴的中心漂移（越小越像回转轴）", "判定"))
    print("-" * 96)
    for fn in sorted(f for f in os.listdir(d) if f.lower().endswith(".stl")):
        tris = read_tris(os.path.join(d, fn))
        pts = [p for t in tris for p in t]
        scores = {}
        for k in range(3):
            s = axis_score(pts, k)
            if s:
                scores[k] = s
        if not scores:
            print("%-26s 解析失败" % fn); continue
        best = min(scores, key=lambda k: scores[k][0])
        desc = "  ".join("%s:漂移%6.2f 圆度%.2f" % (AXES[k], scores[k][0], scores[k][1])
                         for k in sorted(scores))
        print("%-26s %s   → 轴=%s" % (fn, desc, AXES[best]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
