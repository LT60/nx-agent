#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每个零件单独一页：渲染图 + 侧影轮廓 + 形状数据 —— 用来看"上下朝向对不对"。

★★★ 为什么需要（2026-10-10，用户提问）★★★
  用户：「每一个部分的上下方向我不确定是否正确，感觉有一些部件上下翻转了」

  朝向是由 `saturn_layout_v2.py` 的**朝向搜索**定的，代价函数是
  「相邻接口半径失配 + 全箭下粗上细」—— 这是**全局**判据，
  完全可能为了整体最优把某个零件单独推错。
  所以必须**逐个零件**看。

  本工具给每件三样东西：
    1. **单独渲染**（用 NX 报的坐标，正视图）—— 像不像真机一眼可见
    2. **侧影轮廓**（半径 vs 高度，左右镜像）—— 就是侧视图外轮廓，
       锥体朝哪边、细脖在哪一头，看得清清楚楚
    3. **数据**：z 区间、上下端半径、角向覆盖（喷管端有空档）

用法：
    python tools/parts_orientation_sheet.py
    python tools/parts_orientation_sheet.py --size 280
"""

import argparse
import base64
import io
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_assembly_png as R  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")
SRC = os.path.join(ENT, "downloaded")


def read_tris(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        h = fh.read(84)
    n = struct.unpack("<I", h[80:84])[0] if len(h) >= 84 else 0
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
            r = fh.read(50)
            if len(r) < 50:
                break
            v = struct.unpack("<12fH", r)
            out.append(((v[3], v[4], v[5]), (v[6], v[7], v[8]),
                        (v[9], v[10], v[11])))
    return out


def profile_curve(part, tris, nbins=54):
    """世界坐标下的 (z, r) 轮廓，按高度分档取 max/min 半径。"""
    # ★ matrix 是 3×3 嵌套列表，要**展平**成一维 9 个
    M = [v for row in part["matrix"] for v in row]
    o = part["origin"]
    pts = []
    for t in tris:
        for (a, b, c) in t:
            pts.append((o[0] + M[0] * a + M[1] * b + M[2] * c,
                        o[1] + M[3] * a + M[4] * b + M[5] * c,
                        o[2] + M[6] * a + M[7] * b + M[8] * c))
    zs = [p[2] for p in pts]
    z0, z1 = min(zs), max(zs)
    if z1 - z0 < 1e-6:
        return [], z0, z1
    step = (z1 - z0) / nbins
    bands = []
    for i in range(nbins):
        a, b = z0 + i * step, z0 + (i + 1) * step
        rr = [math.hypot(p[0], p[1]) for p in pts if a <= p[2] <= b]
        if rr:
            bands.append(((a + b) / 2.0, min(rr), max(rr)))
    return bands, z0, z1


def end_stats(part, tris, which, band=8.0):
    """端面角向覆盖：喷管端有空档/多簇，平端是整圈。"""
    M = [v for row in part["matrix"] for v in row]
    o = part["origin"]
    pts = []
    for t in tris:
        for (a, b, c) in t:
            pts.append((o[0] + M[0] * a + M[1] * b + M[2] * c,
                        o[1] + M[3] * a + M[4] * b + M[5] * c,
                        o[2] + M[6] * a + M[7] * b + M[8] * c))
    zs = [p[2] for p in pts]
    z0, z1 = min(zs), max(zs)
    sel = ([p for p in pts if p[2] <= z0 + band] if which == "bot"
           else [p for p in pts if p[2] >= z1 - band])
    if not sel:
        return None
    NB = 72
    bins = [0] * NB
    rs = []
    for (x, y, _z) in sel:
        r = math.hypot(x, y)
        rs.append(r)
        if r < 0.5:
            continue
        k = int((math.atan2(y, x) + math.pi) / (2 * math.pi) * NB) % NB
        bins[k] = 1
    runs = sum(1 for k in range(NB) if bins[k] and not bins[(k - 1) % NB])
    return {"cover": sum(bins) / float(NB), "clusters": runs,
            "rmax": max(rs), "n": len(sel)}


def sil_svg(bands, z0, z1, w=190, h=250, ramp=None):
    """把 (z, r) 轮廓画成侧影（左右镜像）。"""
    if not bands:
        return "<svg width='%d' height='%d'></svg>" % (w, h)
    rmax = max(b[2] for b in bands) or 1.0
    rmin = min(b[1] for b in bands)
    pad = 8
    cx = w / 2.0
    scale_x = (w / 2.0 - pad) / max(rmax, 1e-6)
    scale_y = (h - 2 * pad) / max(z1 - z0, 1e-6)
    right, left = [], []
    for (z, rlo, rhi) in bands:
        y = h - pad - (z - z0) * scale_y
        right.append((cx + rhi * scale_x, y))
        left.append((cx - rhi * scale_x, y))
    # 从下到上：右侧上行 + 左侧下行
    poly = right + left[::-1]
    pts = " ".join("%.1f,%.1f" % p for p in poly)
    axis = ("<line x1='%.1f' y1='%d' x2='%.1f' y2='%d' stroke='#cbd5e1' "
            "stroke-width='1' stroke-dasharray='3 3'/>" % (cx, pad, cx, h - pad))
    return ("<svg width='%d' height='%d' viewBox='0 0 %d %d'>%s"
            "<polygon points='%s' fill='%s' stroke='#334155' stroke-width='0.8'/>"
            "<text x='%d' y='%d' font-size='10' fill='#94a3b8'>顶</text>"
            "<text x='%d' y='%d' font-size='10' fill='#94a3b8'>底</text>"
            "</svg>" % (w, h, w, h, axis, pts, ramp or "#cbd5e1",
                        cx + rmax * scale_x + 2 if cx + rmax * scale_x + 2 < w - 14 else w - 14,
                        pad + 9, cx + rmax * scale_x + 2 if cx + rmax * scale_x + 2 < w - 14 else w - 14,
                        h - pad + 1))


def main(argv=None):
    ap = argparse.ArgumentParser(description="逐零件朝向检查页")
    ap.add_argument("--size", type=int, default=260)
    ap.add_argument("-o", "--out",
                    default=os.path.join(ENT, "parts_orientation.html"))
    args = ap.parse_args(argv)

    with io.open(os.path.join(ENT, "saturn_layout.json"), "r",
                 encoding="utf-8") as fh:
        lay = json.load(fh)

    cards = []
    report = []
    for i, p in enumerate(lay["parts"]):
        src = os.path.join(SRC, p["file"])
        if not os.path.isfile(src):
            continue
        tris = read_tris(src)
        M = [v for row in p["matrix"] for v in row]
        Wt = R.worldize(tris, p["origin"], M)

        # 1) 单独渲染
        sw, sh = args.size, int(args.size * 1.25)
        rgb = R.render([(p["label"], Wt)], "front", sw, sh)
        tmp = os.path.join(ENT, "_part_%02d.png" % i)
        R.write_png(tmp, sw, sh, rgb)
        with open(tmp, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        os.remove(tmp)

        # 2) 轮廓
        bands, z0, z1 = profile_curve(p, tris)
        bot = end_stats(p, tris, "bot")
        top = end_stats(p, tris, "top")

        rb = p["r_bottom"]
        rt = p["r_top"]
        if rb > rt + 1.5:
            shape = "下粗上细"
        elif rt > rb + 1.5:
            shape = "上粗下细"
        else:
            shape = "近似等径"

        def ef(s):
            return ("覆盖 %.0f%% ｜ 簇 %d ｜ r %.1f"
                    % (s["cover"] * 100, s["clusters"], s["rmax"])) if s else "—"

        report.append({
            "order": p["order"], "label": p["label"], "flip": bool(p["flip"]),
            "z": [round(z0, 1), round(z1, 1)],
            "r_bottom": rb, "r_top": rt, "shape": shape,
            "bot": bot, "top": top,
        })

        ramp = ["#dbeafe", "#dcfce7", "#fef3c7", "#fae8ff", "#ffe4e6",
                "#e0f2fe", "#f0fdf4", "#fefce8", "#fdf4ff", "#fff1f2"][i % 10]
        cards.append("""
<div class="card">
  <div class="hd"><span class="idx">%d</span><b>%s</b>
    <span class="tag %s">%s</span></div>
  <div class="body">
    <img src="data:image/png;base64,%s" alt="%s">
    <div class="prof">
      %s
      <div class="pn">半径轮廓（左右镜像＝侧影）</div>
    </div>
    <div class="meta">
      <div><span>Z 区间</span><b>%.1f → %.1f</b></div>
      <div><span>底端半径</span><b>%.1f</b></div>
      <div><span>顶端半径</span><b>%.1f</b></div>
      <div><span>形状</span><b>%s</b></div>
      <div><span>下端特征</span><b>%s</b></div>
      <div><span>上端特征</span><b>%s</b></div>
    </div>
  </div>
</div>""" % (p["order"], p["label"],
             "flip" if p["flip"] else "keep",
             "模型原始方向已翻转" if p["flip"] else "保持模型原始方向",
             b64, p["label"], sil_svg(bands, z0, z1, 190, 250, ramp),
             z0, z1, rb, rt, shape, ef(bot), ef(top)))

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>逐零件朝向检查</title><style>
body{margin:0;padding:26px 18px 60px;background:#f8fafc;color:#0f172a;
     font:15px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1120px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}
.sub{color:#64748b;font-size:14px;margin-bottom:8px}
.note{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;
      padding:12px 16px;font-size:14px;margin:14px 0 20px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;
      padding:14px 16px;margin-bottom:14px}
.hd{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.idx{display:inline-flex;width:24px;height:24px;border-radius:50%;
     background:#0f172a;color:#fff;align-items:center;justify-content:center;
     font-size:12px;font-weight:600}
.hd b{font-size:16px}
.tag{margin-left:auto;font-size:12px;padding:2px 10px;border-radius:999px;border:1px solid}
.tag.flip{background:#fef3c7;border-color:#fcd34d;color:#92400e}
.tag.keep{background:#f1f5f9;border-color:#cbd5e1;color:#475569}
.body{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}
.body img{width:230px;background:#f1f5f9;border-radius:8px}
.prof{display:flex;flex-direction:column;align-items:center}
.pn{font-size:12px;color:#94a3b8;margin-top:2px}
.meta{flex:1;min-width:240px;display:grid;
      grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.meta>div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
          padding:8px 10px;display:flex;justify-content:space-between;
          align-items:baseline;gap:8px}
.meta span{color:#64748b;font-size:13px}
.meta b{font-variant-numeric:tabular-nums}
</style></head><body><div class="wrap">
<h1>逐零件朝向检查</h1>
<div class="sub">渲染用 NX 报出的坐标；轮廓由原始模型顶点算出，左右镜像后就是侧影。</div>
<div class="note">
<b>怎么看：</b>每张卡片左边是该零件按当前朝向的渲染图，中间是它的<b>侧影轮廓</b>
（上=朝上，下=朝下）。拿它跟真机照片比 —— <b>哪一件翻反了，直接看轮廓形状就知道</b>。<br>
「下端/上端特征」那一行给的是横截面的角向覆盖：<b>覆盖不满 100%、簇数多</b>，
说明那一端是<b>分离的喷管/支架</b>（不是平整的环）——真机的发动机端、逃逸塔支杆端都是这种特征。
</div>
__CARDS__
</div></body></html>"""
    html = html.replace("__CARDS__", "\n".join(cards))

    with io.open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已写 %s（%.1f KB，%d 件）"
          % (args.out, os.path.getsize(args.out) / 1024.0, len(cards)))
    for r in report:
        print("  %2d %-22s %-5s %s 底r%6.1f 顶r%6.1f  下端[%s] 上端[%s]"
              % (r["order"], r["label"][:22],
                 "翻转" if r["flip"] else "保持",
                 r["shape"], r["r_bottom"], r["r_top"],
                 ("覆盖%3.0f%% 簇%2d" % (r["bot"]["cover"] * 100,
                                       r["bot"]["clusters"])) if r["bot"] else "—",
                 ("覆盖%3.0f%% 簇%2d" % (r["top"]["cover"] * 100,
                                       r["top"]["clusters"])) if r["top"] else "—"))
    return 0


if __name__ == "__main__":
    sys.exit(main())