# -*- coding: utf-8 -*-
"""★ NX 输出展示台：一个**只显示 NX 产出的东西**的网页。

定位（用户原话）：「可以适当使用网页来对 nx 的输出进行展示，
而非纯粹的 nx 或者纯粹的网页」。

所以这条链是：
    NX 建模 → NX 装配 → NX 截图/爆炸图 → **网页只负责摆出来给人看**
网页**不计算任何几何**：图片是 NX 导出的 PNG，表格是 NX 给的包围盒，
位置和朝向是 NX 的 GetPosition()。网页 = 屏幕，不是建模器。

用法：
    python tools/build_nx_viewer.py --shots output/enterprise/asm3_shots \
        --asm output/enterprise/assembly_v3_from_nx.json \
        --bbox output/enterprise/assembly_v3_bbox.json \
        -o output/enterprise/nx_viewer.html
"""

from __future__ import print_function

import argparse
import base64
import json
import os
import sys

PAGE = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>企业号装配 · NX 输出展示台</title>
<style>
  * { box-sizing:border-box; }
  body { margin:0; padding:18px; background:#f6f7f9; color:#1f2328;
         font-family:"Microsoft YaHei","PingFang SC",sans-serif; }
  h1 { font-size:18px; margin:0 0 4px; font-weight:600; }
  .lead { font-size:12.5px; color:#4b5563; line-height:1.9; margin-bottom:16px;
          background:#ecfdf5; border:1px solid #a7f3d0; border-radius:7px;
          padding:10px 13px; }
  h2 { font-size:14px; margin:22px 0 8px; padding-left:8px;
       border-left:3px solid #2563eb; }
  .grid { display:flex; flex-wrap:wrap; gap:12px; }
  .card { background:#fff; border:1px solid #dde1e7; border-radius:8px;
          padding:8px; width:302px; cursor:pointer; }
  .card:hover { border-color:#2563eb; }
  .card img { width:100%; display:block; border-radius:5px; background:#fff; }
  .cap { font-size:12px; color:#374151; margin-top:6px; line-height:1.5; }
  .cap b { color:#111827; }
  .tag { display:inline-block; font-size:10.5px; padding:1px 6px; border-radius:9px;
         background:#eef2ff; color:#4338ca; margin-right:4px; }
  table { border-collapse:collapse; font-size:12px; width:100%;
          font-family:ui-monospace,Consolas,monospace; background:#fff; }
  th,td { border:1px solid #e2e6ec; padding:4px 8px; text-align:left; }
  th { background:#f3f4f6; }
  .sw { width:13px; height:10px; border-radius:2px; display:inline-block;
        border:1px solid rgba(0,0,0,.25); }
  .small { font-size:11.5px; color:#6b7280; line-height:1.8; margin-top:6px; }
  #lightbox { position:fixed; inset:0; background:rgba(15,20,28,.88); display:none;
              align-items:center; justify-content:center; z-index:99; padding:24px; }
  #lightbox.on { display:flex; }
  #lightbox img { max-width:96%; max-height:92%; border-radius:6px; background:#fff; }
  #lightbox .x { position:absolute; top:14px; right:20px; color:#fff; font-size:26px;
                 cursor:pointer; }
  #lightbox .t { position:absolute; bottom:18px; color:#e5e7eb; font-size:13px; }
</style>
</head>
<body>
<h1>企业号装配 · NX 输出展示台</h1>
<div class="lead">
  <b>这一页不计算任何几何。</b>下面每一张图都是 <b>NX 自己导出</b>的 PNG，
  每一条尺寸都是 <b>NX 给的</b>（位置/朝向 = NX <code>Component.GetPosition()</code>，
  尺寸校验 = NX <code>UF.ModlGeneral.AskBoundingBox()</code>）。
  网页在这里只当<b>屏幕</b>用 —— 建模、装配、出图全在 NX 里完成。
</div>

<h2>成品（NX 渲染的四视角）</h2>
<div class="grid" id="g_views"></div>

<h2>装配过程（NX 每装一个零件自己截一张图）</h2>
<div class="grid" id="g_steps"></div>

<h2>NX 实测包围盒（这才是判断"形状对不对"的依据）</h2>
<table id="tbl"></table>
<div class="small">
  总包围盒：<span id="total"></span>。
  想看剪影轮廓，直接在 NX 里打开 <code>enterprise_asm_v3.prt</code>，
  或在终端跑 <code>python tools/asm_silhouette.py &lt;assembly_v3_from_nx.json&gt;</code>。
</div>

<div id="lightbox"><span class="x">&times;</span><img alt=""><div class="t"></div></div>

<script>
const DATA = __DATA__;
function card(src, title, tags){
  const d=document.createElement('div'); d.className='card';
  let ts=''; (tags||[]).forEach(function(t){ ts+='<span class="tag">'+t+'</span>'; });
  d.innerHTML='<img loading="lazy" src="'+src+'"><div class="cap"><b>'+title+'</b><br>'+ts+'</div>';
  d.title = title;
  d.onclick=function(){ const lb=document.getElementById('lightbox');
    lb.querySelector('img').src=src; lb.querySelector('.t').textContent=title;
    lb.classList.add('on'); };
  return d;
}
const gv=document.getElementById('g_views');
DATA.views.forEach(function(v){ gv.appendChild(card(v.data, v.title, v.tags)); });
const gs=document.getElementById('g_steps');
DATA.steps.forEach(function(v){ gs.appendChild(card(v.data, v.title, v.tags)); });

const tbl=document.getElementById('tbl');
let html='<tr><th>零件</th><th>零件文件</th><th>NX 实测包围盒（装配坐标系）</th>'
        + '<th>来源 / 尺寸</th></tr>';
let mn=[1e9,1e9,1e9], mx=[-1e9,-1e9,-1e9];
DATA.comps.forEach(function(c){
  const a=c.bbox;
  if(a){ for(let i=0;i<3;i++){ mn[i]=Math.min(mn[i],a.min[i]); mx[i]=Math.max(mx[i],a.max[i]); } }
  const f=function(v){ return '['+v.map(function(x){ return x.toFixed(1); }).join(', ')+']'; };
  html+='<tr><td><span class="sw" style="background:'+c.color+'"></span> '+c.name+'</td>'
      + '<td>'+(c.part_file||'—')+'</td>'
      + '<td>'+(a? (f(a.min)+' → '+f(a.max)) : '—')+'</td>'
      + '<td>'+(c.machining||'—')+'</td></tr>';
});
tbl.innerHTML=html;
if(mn[0]<1e8){
  const f=function(v){ return '['+v.map(function(x){ return x.toFixed(1); }).join(', ')+']'; };
  document.getElementById('total').textContent = f(mn)+' → '+f(mx);
}
document.getElementById('lightbox').onclick=function(){ this.classList.remove('on'); };
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 NX 的产出摆成一个网页（只展示，不计算）")
    ap.add_argument("--shots", required=True, help="NX 导出的 PNG 目录")
    ap.add_argument("--asm", required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("-o", "--out", required=True)
    ns = ap.parse_args(argv)

    for f in (ns.asm, ns.bbox):
        if not os.path.isfile(f):
            print("找不到: %s" % f)
            return 2
    if not os.path.isdir(ns.shots):
        print("找不到图片目录: %s" % ns.shots)
        return 2

    with open(ns.asm, "r", encoding="utf-8") as fh:
        asm = json.load(fh)
    with open(ns.bbox, "r", encoding="utf-8") as fh:
        bbox = json.load(fh)

    # NX 实测包围盒：按 origin 匹配到组件
    nx = {}
    for r in bbox.get("components") or []:
        bs = r.get("bodies") or []
        if bs and bs[0].get("asm"):
            nx[tuple(round(v, 2) for v in r["origin"])] = bs[0]["asm"]

    # 兼容两种导出格式：参数化件用 name/color，导入的网格件用 label
    PALETTE = ["#94a3b8", "#64748b", "#0891b2", "#2563eb", "#0ea5e9",
               "#059669", "#7c3aed", "#d97706", "#dc2626", "#f59e0b"]
    comps = []
    for i, c in enumerate(asm.get("components") or []):
        comps.append({
            "name": c.get("name") or c.get("label") or ("零件%d" % (i + 1)),
            "color": c.get("color") or PALETTE[i % len(PALETTE)],
            "part_file": c.get("part_file") or c.get("prt_name"),
            "machining": c.get("machining") or (c.get("expected") or {}).get("real"),
            "bbox": nx.get(tuple(round(v, 2) for v in c["origin"])),
        })

    shots = sorted(os.listdir(ns.shots))
    views, steps = [], []
    VIEW_TITLES = {
        "final_iso.png": ("成品 · 等轴测", ["NX 渲染"]),
        "final_top.png": ("成品 · 俯视", ["NX 渲染"]),
        "final_front.png": ("成品 · 前视（从船头看）", ["NX 渲染"]),
        "final_side.png": ("成品 · 侧视（看剪影）", ["NX 渲染"]),
        "explosion.png": ("NX 爆炸图", ["NX 爆炸视图", "已存进 .prt"]),
    }
    def embed(name):
        """★ 把 PNG 内嵌成 data URI。

        为什么：HTML 和 PNG 不在同一层目录（HTML 在 output/enterprise/，
        图在 output/enterprise/asm3_shots/），而且预览面板是从临时目录伺服的 ——
        相对路径两头都可能断。内嵌之后这一页自包含，放哪都能显示。
        """
        p = os.path.join(os.path.abspath(ns.shots), name)
        with open(p, "rb") as fh:
            raw = fh.read()
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")

    total_bytes = 0
    for f in shots:
        if not f.lower().endswith(".png"):
            continue
        rel = os.path.basename(f)
        total_bytes += os.path.getsize(os.path.join(ns.shots, f))
        if f in VIEW_TITLES:
            t, tags = VIEW_TITLES[f]
            views.append({"file": rel, "title": t, "tags": tags, "data": embed(f)})
        elif f.startswith("step"):
            idx = f[4:6]
            steps.append({"file": rel, "title": "第 %d 步" % int(idx),
                          "tags": ["NX 装配到此为止的状态"], "data": embed(f)})

    # 按文件名排序，保证步骤有序
    steps.sort(key=lambda v: v["file"])
    # 给步骤配上零件名（按顺序对应 components）
    for i, s in enumerate(steps):
        if i < len(comps):
            s["title"] = "第 %d 步：装入 %s" % (i + 1, comps[i]["name"])
            s["tags"] = ["零件 " + (comps[i]["part_file"] or "—")]

    data = {"views": views, "steps": steps, "comps": comps}
    out = os.path.abspath(ns.out)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)))

    print("NX 输出展示台：%s" % out)
    print("  成品视角 %d 张 ｜ 装配步骤 %d 张 ｜ 零件 %d 个（尺寸全部取自 NX）"
          % (len(views), len(steps), len(comps)))
    print("  图片已内嵌为 data URI（自包含，放哪都能显示）：原图 %.1f MB → HTML %.1f MB"
          % (total_bytes / 1048576.0, os.path.getsize(out) / 1048576.0))
    if not views:
        print("  ⚠ 没有成品图：先在 NX 里跑 j_asm3_views.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
