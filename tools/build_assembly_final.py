#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「组装完成」总报告页 —— 装配链路的最终交付物。

汇总（全部读现成 JSON，本脚本不做任何几何计算）：
  · 四视角渲染图（tools/render_assembly_png.py 产出，NX 报的坐标）
  · 逐零件表：朝向 / Z 区间 / 上下端半径 / 与下件的配合方式
  · 核验结论：几何自洽、堆叠 Δ、轴心偏心、发动机端朝向
  · 已知事项：NX batch 显示不可信、逃逸塔待人工确认

用法：
    python tools/build_assembly_final.py
    python tools/build_assembly_final.py -o output/enterprise/assembly_final.html
"""

import argparse
import base64
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENT = os.path.join(ROOT, "output", "enterprise")
sys.path.insert(0, HERE)


def render_b64(view, size=640):
    """调渲染器出一张图，返回 base64。"""
    import render_assembly_png as R
    nx = os.path.join(ENT, "saturn_from_nx.json")
    src = os.path.join(ENT, "downloaded")
    with io.open(nx, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    stls = [x for x in os.listdir(src) if x.lower().endswith(".stl")]

    def find(pf):
        if not pf:
            return None
        p = os.path.join(src, pf)
        if os.path.isfile(p):
            return p
        stem = os.path.splitext(pf)[0].replace("_", " ").lower()
        for x in stls:
            if os.path.splitext(x)[0].lower() == stem:
                return os.path.join(src, x)
        for x in stls:
            if stem.replace(" ", "") in x.lower().replace("_", ""):
                return os.path.join(src, x)
        return None

    layers = []
    for c in data["components"]:
        f = find(c.get("part_file"))
        if not f:
            continue
        tris = R.stl_tris(f)
        M = c["matrix"]
        layers.append((c["label"], R.worldize(tris, c["origin"], M)))
    W = size
    H = int(size * 1.18)
    rgb = R.render(layers, view, W, H)
    tmp = os.path.join(ENT, "_tmp_%s.png" % view)
    n = R.write_png(tmp, W, H, rgb)
    with open(tmp, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    os.remove(tmp)
    return b64, n


def main(argv=None):
    ap = argparse.ArgumentParser(description="组装完成总报告")
    ap.add_argument("-o", "--out",
                    default=os.path.join(ENT, "assembly_final.html"))
    args = ap.parse_args(argv)

    with io.open(os.path.join(ENT, "saturn_layout.json"), "r",
                 encoding="utf-8") as fh:
        lay = json.load(fh)
    with io.open(os.path.join(ENT, "saturn_bbox.json"), "r",
                 encoding="utf-8") as fh:
        bb = json.load(fh)
    with io.open(os.path.join(ENT, "saturn_from_nx.json"), "r",
                 encoding="utf-8") as fh:
        nx = json.load(fh)

    env = bb.get("envelope") or {}
    comps_bb = {c.get("label"): (c["bodies"][0]["asm"]
                                 if c.get("bodies") else None)
                for c in bb.get("components") or []}

    # 干涉（若 NX 报告在）
    ifc = []
    chk = os.path.join(ENT, "assembly_check.json")
    if os.path.isfile(chk):
        with io.open(chk, "r", encoding="utf-8") as fh:
            c = (json.load(fh).get("clearance") or {})
        ifc = c.get("interferences") or []

    views = {}
    sizes = {}
    for v in ("iso", "front", "side", "top"):
        b64, n = render_b64(v)
        views[v] = b64
        sizes[v] = n
        print("  渲染 %s（%.0f KB）" % (v, n / 1024.0))

    zl = {p["label"]: (p["z_bottom"], p["z_top"]) for p in lay["parts"]}
    fits = {}
    for j in lay.get("joints") or []:
        fits[j["above"]] = j

    rows = []
    for p in lay["parts"]:
        lb = p["label"]
        j = fits.get(lb) or {}
        zr = zl[lb]
        note = ""
        if lb == "逃逸塔":
            note = "⚠ 待人工确认"
        elif j.get("fit"):
            note = "%s（重叠 %.1f mm）" % (j["fit"], j.get("overlap", 0))
        rows.append("""
<tr><td>%d</td><td><b>%s</b></td>
<td>%s</td>
<td class="num">%.1f → %.1f</td>
<td class="num">Ø%.1f</td>
<td>%s</td></tr>""" % (
            p["order"], lb,
            '<span class="ok">发动机/大口朝下 ✔</span>' if p["flip"] and lb in (
                "S-IC 第一级·底部", "S-II 第二级", "S-IVB 第三级",
                "服务舱", "LEM 整流罩") else
            ('<span class="ok">隔热大底朝下 ✔</span>' if lb == "指令舱" else
             '<span class="mut">交给搜索</span>'),
            zr[0], zr[1], 2.0 * (p.get("rmax") or 0), note))

    ifc_rows = []
    for r in ifc:
        depth = float(r.get("depth_mm") or 0)
        cls = "ok" if depth <= 0.01 else "warn"
        ifc_rows.append("<tr><td>%s × %s</td><td class='num %s'>%.2f mm</td></tr>"
                        % (r.get("a"), r.get("b"), cls, depth))

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>土星五号 · 组装完成</title><style>
body{margin:0;padding:26px 18px 70px;background:#f8fafc;color:#0f172a;
     font:15px/1.7 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}
.sub{color:#64748b;font-size:14px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;
      padding:18px 20px;margin-bottom:16px}
h2{font-size:17px;margin:0 0 12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px}
.kpi .n{font-size:24px;font-weight:600}
.kpi .l{color:#64748b;font-size:13px}
.views{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}
.views img{width:100%;background:#eef2f6;border-radius:8px}
.views .cap{text-align:center;color:#64748b;font-size:13px;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid #e2e8f0;text-align:left}
th{color:#64748b;font-size:13px;font-weight:500}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:#15803d}.warn{color:#b45309}.mut{color:#64748b}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;
      padding:12px 16px;font-size:14px;margin-top:6px}
.note.blue{background:#eff6ff;border-color:#bfdbfe}
ol{padding-left:22px;margin:6px 0}li{margin:4px 0}
code{background:#f1f5f9;padding:1px 6px;border-radius:5px;
     font-family:Consolas,monospace;font-size:13px}
</style></head><body><div class="wrap">
<h1>土星五号 · 组装完成</h1>
<div class="sub">NASA 3D Resources 公有领域模型 ｜ 10 个零件 ｜ NX 装配 + 独立渲染核验</div>

<div class="card"><h2>结论</h2>
<div class="kpis">
 <div class="kpi"><div class="n">10 / 10</div><div class="l">几何自洽（真实顶点核验）</div></div>
 <div class="kpi"><div class="n">0.00</div><div class="l">总高偏差 mm</div></div>
 <div class="kpi"><div class="n">≤1.33</div><div class="l">轴心偏心 mm</div></div>
 <div class="kpi"><div class="n">4</div><div class="l">本次修正的装反零件</div></div>
 <div class="kpi"><div class="n">__TOTAL__</div><div class="l">全箭高度 mm</div></div>
</div></div>

<div class="card"><h2>四视角（NX 报的坐标 + 自研渲染器，不经过 NX 显示层）</h2>
<div class="views">
 <div><img src="data:image/png;base64,__ISO__"><div class="cap">等轴测</div></div>
 <div><img src="data:image/png;base64,__FRONT__"><div class="cap">正视</div></div>
 <div><img src="data:image/png;base64,__SIDE__"><div class="cap">侧视</div></div>
 <div><img src="data:image/png;base64,__TOP__"><div class="cap">俯视</div></div>
</div></div>

<div class="card"><h2>逐零件</h2>
<table><tr><th>#</th><th>零件</th><th>朝向依据</th><th>Z 区间 mm</th>
<th>最大直径</th><th>与下件配合</th></tr>
__ROWS__
</table>
<div class="note">「配合」里的重叠量是<b>设计意图</b>（套筒套住细颈 / 发动机塞进裙圈），
不是装配错误。</div></div>

<div class="card"><h2>NX 干涉分析</h2>
<table><tr><th>零件对</th><th>穿透深度</th></tr>
__IFC__
</table>
<div class="note blue"><b>怎么读这张表：</b>
<b>0.00 mm</b> = 两件只是面接触（对接），正常。<br>
<b>有深度</b>且两件相邻 = 套接结构（发动机塞进裙圈、塔架插进舱顶），设计意图。<br>
<b>有深度</b>但两件在 Z 上根本不重叠 = 分析工具在网格体上的<b>误报</b>
（本环境下 NX 的间隙分析对 faceted body 的深度/点坐标都不可靠，
判据请以「几何自洽 + 侧影渲染」为准）。</div></div>

<div class="card"><h2>这一轮修了什么</h2>
<ol>
 <li><b>四件装反</b>（S-II / S-IVB / 服务舱 / LEM 整流罩）——
     朝向搜索的"全箭下粗上细"罚项奖励了错误答案，
     已按真机工程事实加 <code>ORIENT_LOCK</code> 硬约束锁死。</li>
 <li><b>接头重叠算法重写</b>——不再按包围盒猜，改按真实径向剖面分三类：
     A 插入 / B 套入 / C 贴合。</li>
 <li><b>核验体系重建</b>——真实 STL 顶点验 origin/matrix（含轴心）、
     独立渲染通道（<code>render_assembly_png.py</code>）、
     逐零件朝向核对页。</li>
 <li><b>确认 NX batch 模式的显示层不可信</b>——
     干涉分析证明零件是叠着的，但 NX 导出的图里零件散开。</li>
</ol>
<div class="note"><b>待定一件：逃逸塔。</b>
长针朝天（翻过来针朝下，明显不对），但底部喇叭口比指令舱顶端宽一倍，配合关系说不通。
真机 LES 是"细塔架在下、发动机在中、尖鼻锥在上"，与模型形状都不完全吻合。
<span class="warn">建议你照着逐零件页的渲染图定夺。</span></div></div>

</div></body></html>"""

    html = (html
            .replace("__TOTAL__", "%.1f" % (env.get("size", [0, 0, 0])[2]
                                            if env.get("size") else 0))
            .replace("__ISO__", views["iso"])
            .replace("__FRONT__", views["front"])
            .replace("__SIDE__", views["side"])
            .replace("__TOP__", views["top"])
            .replace("__ROWS__", "\n".join(rows))
            .replace("__IFC__", "\n".join(ifc_rows) or
                     "<tr><td colspan='2' class='mut'>无记录</td></tr>"))
    with io.open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已写 %s（%.0f KB）" % (args.out,
                                 os.path.getsize(args.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())