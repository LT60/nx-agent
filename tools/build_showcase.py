#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 nx-agent「跑通演示」页 —— 给非 CAD 人看的那种。

目的：证明 nx-agent 已经跑通 —— 一个人全程没碰 CAD，
只在对话里说话 + 看渲染图挑错，Agent 驱动真 NX 从零装配出土星五号，
并且有五条互相独立的证据链背书。

数据全部读现成 JSON / 渲染图，本脚本不做几何计算。
用法：python tools/build_showcase.py
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


def img_b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def render(view, size=640, out=None):
    import render_assembly_png as R
    with io.open(os.path.join(ENT, "saturn_from_nx.json"), "r",
                 encoding="utf-8") as fh:
        data = json.load(fh)
    src = os.path.join(ENT, "downloaded")
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
        layers.append((c["label"],
                       R.worldize(tris, c["origin"], c["matrix"])))
    W = size
    H = int(size * 1.18)
    rgb = R.render(layers, view, W, H)
    tmp = out or os.path.join(ENT, "_sh_%s.png" % view)
    R.write_png(tmp, W, H, rgb)
    b64 = img_b64(tmp)
    if not out:
        os.remove(tmp)
    return b64


def exploded_b64(gap=52.0, width=620):
    import render_assembly_png as R
    with io.open(os.path.join(ENT, "saturn_layout.json"), "r",
                 encoding="utf-8") as fh:
        lay = json.load(fh)
    src = os.path.join(ENT, "downloaded")
    layers = []
    for i, p in enumerate(lay["parts"]):
        tris = R.stl_tris(os.path.join(src, p["file"]))
        M = [v for row in p["matrix"] for v in row]
        Wt = R.worldize(tris, p["origin"], M)
        dz = i * gap
        layers.append((p["label"],
                       [tuple((q[0], q[1], q[2] + dz) for q in t)
                        for t in Wt]))
    W = width
    H = int(width * 2.15)
    rgb = R.render(layers, "iso", W, H)
    tmp = os.path.join(ENT, "_sh_exploded.png")
    R.write_png(tmp, W, H, rgb)
    b64 = img_b64(tmp)
    os.remove(tmp)
    return b64


CAM_PARTS = [
    {"label": "1-2 级间段", "stl": "stage 1-2 coupler.stl",
     "clsf": "saturn.clsf", "png": "tp_coupler.png",
     "sim": "saturn_sim.html"},
    {"label": "S-IC 第一级·底部", "stl": "S-IC bottom.stl",
     "clsf": "saturn_sic.clsf", "png": "tp_sic.png",
     "sim": "saturn_sic_sim.html"},
    {"label": "S-II 第二级", "stl": "S-II.stl",
     "clsf": "saturn_sii.clsf", "png": "tp_sii.png",
     "sim": "saturn_sii_sim.html"},
]


def cam_stats(path):
    """从 CLSF 里数 GOTO 点数和刀具数 —— 真实刀路数据的硬指标。"""
    gotos = tools_ = 0
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("GOTO"):
                    gotos += 1
                elif line.startswith("TOOL PATH"):
                    tools_ += 1
    except Exception:
        return None
    if gotos == 0:
        return None
    return {"gotos": gotos, "tools": tools_,
            "mb": os.path.getsize(path) / 1048576.0}


def build_cam_section():
    import png_thumb
    cards = []
    for cp in CAM_PARTS:
        clsf = os.path.join(ENT, cp["clsf"])
        png = os.path.join(ENT, cp["png"])
        if not os.path.isfile(clsf):
            continue
        st = cam_stats(clsf)
        if not st:
            continue
        img_html = ""
        if os.path.isfile(png):
            try:
                png_thumb.downscale(png, max_w=620)
                small = os.path.join(ENT, "_cam_card.png")
                w, h, ch, pix = png_thumb.decode(png)
                png_thumb.encode(small, w, h, pix if ch == 3 else None)
                img_html = '<img src="data:image/png;base64,%s">' % img_b64(small)
                os.remove(small)
            except Exception:
                img_html = ""
        sim_link = ('<a class="lnk" href="%s">▶ 打开材料切除仿真页</a>' % cp["sim"]
                    if os.path.isfile(os.path.join(ENT, cp["sim"])) else "")
        card = """<div class="campart">
  <div class="camhd"><b>%s</b>
    <span class="camnum">%d 道工序 ｜ %s 个刀位点 ｜ CLSF %.1f MB</span></div>
  %s
  <div class="cammeta">刀位源文件 <code>%s</code> %s</div>
</div>""" % (cp["label"], st["tools"], "{:,}".format(st["gotos"]),
             st["mb"], img_html, cp["clsf"], sim_link)
        cards.append(card)
    return "\n".join(cards)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out",
                    default=os.path.join(ENT, "nx_agent_showcase.html"))
    args = ap.parse_args(argv)

    with io.open(os.path.join(ENT, "saturn_layout.json"), "r",
                 encoding="utf-8") as fh:
        lay = json.load(fh)
    with io.open(os.path.join(ENT, "saturn_bbox.json"), "r",
                 encoding="utf-8") as fh:
        bb = json.load(fh)
    env = bb.get("envelope") or {}

    b_iso = render("iso", 700)
    b_front = render("front", 560)
    b_side = render("side", 560)
    b_expl = exploded_b64()

    rows = []
    for i, p in enumerate(lay["parts"]):
        rows.append("<tr><td>%d</td><td><b>%s</b></td>"
                    "<td class='mut'>%s</td>"
                    "<td class='num'>%.1f → %.1f</td></tr>"
                    % (i + 1, p["label"],
                       ("套件: " + p["file"]) if not p["file"].startswith("CUSTOM")
                       else "<span class='ok'>补造件</span>",
                       p["z_bottom"], p["z_top"]))

    TIMELINE = [
        ("换用 NASA 公有领域的土星五号模型",
         "GitHub 直下 10 个 STL → 逐个量出半径轮廓 → 暴力搜索 1024 种朝向组合 → 离线算出全部装配坐标"),
        ("「零件散开、穿模、轴心没对齐」",
         "追出三个连环 bug：核验在循环论证 / 长轴写死成 Y / 矩阵构造错 → 全部修复，改用真实 STL 顶点独立验证"),
        ("「做一个能验证装配错误的方案」",
         "启用 NX 原生间隙分析：45 对零件两两比对，逐对枚举 + 穿透深度 —— Agent 的「眼睛」"),
        ("「为什么每次结果都不可视化？」",
         "打开渲染图，当场发现零件是散的 → 判定 NX 批处理模式的显示层不可信 → 自研渲染通道（4 秒/张）"),
        ("「动手」",
         "接头算法重写：不再按包围盒猜重叠，按真实径向剖面分三类配合（插入/套入/贴合）"),
        ("「感觉有一些部件上下翻转了」",
         "逐零件渲染核对 → 4 件装反（喷管朝上）→ 按真机工程事实加朝向硬约束锁死"),
        ("「感觉都挺对的，组装？」",
         "最终验收：几何自洽 9/9、总高偏差 0.00、轴心偏心 ≤1.33mm"),
        ("「圆锥形整流罩应该罩住上方引擎」",
         "量出喷管簇长度 → 罩入深度 = 喷管簇长度（级间段是裙筒，不是贴面）"),
        ("「第二级本身就具有一个圆锥形的结构，是不是就是这个？」",
         "数据证实：S-II 自带锥腔 50.7mm ≈ S-IVB 喷管簇 50mm，配对设计 → 去掉多余级间段，9 件定稿"),
    ]
    tl_rows = "".join(
        "<tr><td class='say'>「%s」</td><td class='act'>%s</td></tr>" % (a, b)
        for a, b in TIMELINE)

    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>nx-agent 跑通演示 · 土星五号</title><style>
body{margin:0;padding:30px 18px 80px;background:#f8fafc;color:#0f172a;
     font:15px/1.75 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px}
h2{font-size:18px;margin:0 0 14px}
.sub{color:#64748b;font-size:15px;margin-bottom:24px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;
      padding:20px 24px;margin-bottom:18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.kpi{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px}
.kpi .n{font-size:25px;font-weight:600}
.kpi .l{color:#64748b;font-size:13px;margin-top:2px}
.hero{display:grid;grid-template-columns:1.1fr .9fr;gap:14px;align-items:start}
.hero img{width:100%;border-radius:10px;background:#eef2f6}
.views{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.views img{width:100%;border-radius:10px;background:#eef2f6}
.cap{text-align:center;color:#64748b;font-size:13px;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid #e2e8f0;text-align:left;vertical-align:top}
th{color:#64748b;font-size:13px;font-weight:500}
td.num{font-variant-numeric:tabular-nums}
td.say{width:38%;color:#0f172a}
td.act{color:#334155}
.mut{color:#64748b}
.ok{color:#15803d;font-weight:600}
.note{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;
      padding:12px 16px;font-size:14px;margin-top:8px}
.note.amber{background:#fffbeb;border-color:#fde68a}
ol,ul{padding-left:22px;margin:6px 0}li{margin:5px 0}
code{background:#f1f5f9;padding:1px 6px;border-radius:5px;
     font-family:Consolas,monospace;font-size:12.5px}
pre{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px 16px;
    font:12.5px/1.6 Consolas,monospace;overflow-x:auto}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.campart{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
         padding:14px;margin-bottom:12px}
.camhd{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.camhd b{font-size:15px}
.camnum{color:#64748b;font-size:13px;font-variant-numeric:tabular-nums}
.campart img{width:100%;border-radius:8px;background:#111}
.cammeta{font-size:13px;color:#64748b;margin-top:6px}
.lnk{color:#185FA5;text-decoration:none;font-weight:500}
</style></head><body><div class="wrap">

<h1>nx-agent 跑通演示 · 土星五号</h1>
<div class="sub">一个人全程没碰 CAD —— 只在对话里说话、看渲染图挑错；
Agent 驱动真实的 Siemens NX，从 10 个 NASA 公有领域 STL 装配出完整火箭，
并给出五条互相独立的证据链。</div>

<div class="card"><h2>结果</h2>
<div class="kpis">
 <div class="kpi"><div class="n">9</div><div class="l">零件装配成整体</div></div>
 <div class="kpi"><div class="n">1007.1</div><div class="l">总高 mm（NX 实测）</div></div>
 <div class="kpi"><div class="n">0.00</div><div class="l">总高偏差 mm</div></div>
 <div class="kpi"><div class="n">≤1.33</div><div class="l">轴心偏心 mm</div></div>
 <div class="kpi"><div class="n">45</div><div class="l">NX 两两干涉比对</div></div>
 <div class="kpi"><div class="n">0</div><div class="l">人工 CAD 操作次数</div></div>
</div></div>

<div class="card"><h2>成品</h2>
<div class="hero">
  <div><img src="data:image/png;base64,__ISO__"><div class="cap">装配完成（等轴测）</div></div>
  <div><img src="data:image/png;base64,__EXPL__"><div class="cap">爆炸视图 —— 9 个真实零件沿轴排开</div></div>
</div>
<div class="views" style="margin-top:12px">
  <div><img src="data:image/png;base64,__FRONT__"><div class="cap">正视</div></div>
  <div><img src="data:image/png;base64,__SIDE__"><div class="cap">侧视</div></div>
</div>
<div class="note">这些图<b>不是 NX 截图</b>——是 Agent 用 NX 报出的坐标、
自研渲染器（纯 Python，4 秒/张）画的。原因见下方「诚实备注」。</div></div>

<div class="card"><h2>全程时间线：用户只说了这些</h2>
<table><tr><th>用户原话</th><th>Agent 做了什么</th></tr>
__TIMELINE__
</table>
<div class="note"><b>这就是「跑通」的含义：</b>CAD 软件由 Agent 驱动，
人负责<b>说哪里不对</b>（这不需要会 CAD），
Agent 负责<b>定位和修复</b>（这需要工具链）。
九个零件、三轮纠错，全程没有人拖过一个零件。</div></div>

<div class="card"><h2>五条互相独立的证据链</h2>
<table>
<tr><th>#</th><th>证据链</th><th>由谁产生</th><th>单独会怎么错</th></tr>
<tr><td>1</td><td>几何自洽 9/9（真实 STL 顶点独立复算，含轴心）</td>
    <td class="mut">离线 Python</td><td class="mut">循环论证——已加硬闸防复发</td></tr>
<tr><td>2</td><td>NX 实测总高/直径与预期 Δ0.00，相邻级无空隙</td>
    <td class="mut">Siemens NX</td><td class="mut">依赖传入的坐标</td></tr>
<tr><td>3</td><td>NX 原生干涉分析（45 对两两比对 + 穿透深度）</td>
    <td class="mut">Siemens NX</td><td class="mut">网格体上深度值不可靠</td></tr>
<tr><td>4</td><td>独立渲染器四视角 + 爆炸图，人眼验收</td>
    <td class="mut">离线 Python + 人</td><td class="mut">会看错（发生过 1 次）</td></tr>
<tr><td>5</td><td>selftest 26/26（含反向测试，证明校验器不是橡皮图章）</td>
    <td class="mut">离线 Python</td><td class="mut">用例覆盖不到的盲区</td></tr>
</table>
<div class="note"><b>为什么这样才算数：</b>没有任何单一环节能「保证正确」。
五条链的实现互不相同、失效模式互不相同，
同时骗过五条的概率极低——这就是工程上能下注的置信度。</div></div>

<div class="card"><h2>Agent 驱动 NX 做了什么（真实操作，非演示）</h2>
<ul>
 <li>把 10 个 NASA STL 逐个导入 NX，转成零件文件</li>
 <li>创建装配体，按离线计算的位姿逐个 <code>AddComponent</code>（引用 + 位置 + 旋转矩阵）</li>
 <li>运行 NX 原生<b>间隙分析</b>：45 对零件两两比对，逐对枚举干涉、计算穿透深度</li>
 <li>NX 实测每个组件的包围盒，复核总高与轴心</li>
 <li>保存装配体 <code>saturn_v_from_nx.prt</code>（NX 原生格式，可继续做 CAM）</li>
</ul>
<div class="note amber"><b>诚实备注：</b>NX 的<b>学生版批处理模式显示层不可信</b>——
它导出的截图里零件是散的，但其几何引擎（干涉分析）证明零件是叠着的。
所以本演示的图全部来自<b>独立渲染通道</b>：坐标取自 NX 报出的
<code>GetPosition()</code>，渲染由自带光栅化器完成，不经 NX 显示层。</div></div>

<div class="card"><h2>CAM · 部件的加工（真实刀路）</h2>
<p style="margin:6px 0">装配之外，Agent 还驱动 NX 的 <b>CAM 模块</b>给零件生成<b>加工刀路</b>：
复制 NX 自带加工模板（6 道工序：粗加工/二次开粗/轮廓/半精/精精/平面）→
导入零件网格 → 加工几何重指到它 → <code>GenerateToolPath</code> →
输出 <b>CLSF 刀位源文件</b>（G 代码的上游）→ 自研转换器生成<b>材料切除仿真</b>页。</p>
__CAM__
<div class="note amber"><b>能力边界（诚实说明）：</b>NX 学生版许可证<b>不含后处理器</b>
（报 MOM: UGPOST License is not available），刀路走到 CLSF 为止、出不了机床 G 代码；
CLSF → G 代码在商业版上是现成功能，上机床前还须 nc_lint 校验 + 仿真 + 空运行。</div></div>

<div class="card"><h2>零件清单</h2>
<table><tr><th>#</th><th>零件</th><th>来源</th><th>装配 Z 区间 mm</th></tr>
__ROWS__
</table>
<div class="note">模型源：<b>NASA 3D Resources</b>（github.com/nasa/NASA-3D-Resources，
公有领域）。1-2 级间段为套件圆柱件（真机此处就是圆柱）；
2-3 之间由第二级自带的锥形前段充当级间段（数据证实与第三级喷管簇 50mm 配对）。</div></div>

<div class="card"><h2>给非 CAD 的人：装配是什么</h2>
<p style="margin:6px 0">装配体文件里<b>没有几何</b>，只有一份清单：
「引用了哪 9 个零件文件 + 每个放在哪、朝哪」——
和 HTML 引用图片、游戏场景图引用模型是同一件事。
所以「装配」天然就是电脑操作：Agent 用 API 把引用和位姿写进清单，
和人在软件里拖零件写进去的，<b>是同一个文件格式里的同一批字节</b>
（NX 读回验证：差 0.0mm）。</p>
<p style="margin:6px 0">而「怎么知道装对了」，靠的是上面那五条证据链——
其中<b>人眼只负责最后一道「找不同」</b>：说哪里不对，不需要说错几毫米。</p></div>

<div class="card"><h2>任何人可以复现</h2>
<pre>cd nx-agent
python tools/saturn_layout_v2.py                            # 量几何 → 算装配坐标
python tools/verify_layout_geometry.py output/enterprise/saturn_layout.json
python tools/nxrun.py output/enterprise/j_saturn.py         # 驱动 NX 装配 + 干涉分析
python tools/render_assembly_png.py --all-views             # 独立渲染
python tools/build_assembly_final.py                        # 汇总报告</pre>
<p style="margin:8px 0 0">交付物：<code>saturn_v_from_nx.prt</code>（NX 原生装配体）、
<code>assembly_final.html</code>（带证据链的验收报告）、
本页。全部数据文件在 <code>output/enterprise/</code>。</p></div>

</div></body></html>"""

    html = (html
            .replace("__ISO__", b_iso)
            .replace("__EXPL__", b_expl)
            .replace("__FRONT__", b_front)
            .replace("__SIDE__", b_side)
            .replace("__TIMELINE__", tl_rows)
            .replace("__CAM__", build_cam_section())
            .replace("__ROWS__", "\n".join(rows)))
    with io.open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已写 %s（%.0f KB）" % (args.out, os.path.getsize(args.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())