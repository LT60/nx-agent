# -*- coding: utf-8 -*-
"""★ 土星五号「一页看完」总览页：打开就自动播，不用点。

用户要求：「我有一点懒得去自己手动点看，可以帮我可视化一下吗」
→ 把 NX 的所有产出收进**一个自包含 HTML**：
   ① 成品大图（NX 渲染）
   ② 装配过程 —— 10 张 NX 分步图**自动循环播放**
   ③ 刀路 —— 内嵌、**自动开播**（真刀位数据，来自 NX 的 CLSF）
   ④ 材料切除仿真 —— 内嵌、**自动开播**（含撞刀检测）
   ⑤ 各级实测尺寸表（NX 包围盒）

★ 网页只负责展示 NX 的输出，不做任何几何计算。
★ 图片全部内嵌成 data URI（自包含，放哪都能看），并用 tools/png_thumb.py
  先缩小（纯标准库，项目不引第三方库）。

用法：
    python tools/build_saturn_dashboard.py -o output/enterprise/saturn_dashboard.html
"""

from __future__ import print_function

import argparse
import base64
import html as _html
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import png_thumb  # noqa: E402

ENT = os.path.join(ROOT, "output", "enterprise")
SHOTS = os.path.join(ENT, "saturn_shots")
BBOX = os.path.join(ENT, "saturn_bbox.json")
ASM = os.path.join(ENT, "saturn_from_nx.json")

# 装配顺序（与 j_saturn.py 一致）—— 用来给每帧配名字
STEPS = [
    ("step01_S-IC_bottom", "① S-IC 第一级·底部", "Ø124.6 × 203.1 mm"),
    ("step02_S-IC_top", "② S-IC 第一级·顶部", "Ø90.1 × 167.3 mm"),
    ("step03_stage_1-2_coupler", "③ 1-2 级间段", "Ø90.4 × 96.9 mm"),
    ("step04_S-II", "④ S-II 第二级", "Ø93.2 × 264.7 mm"),
    ("step05_stage_1-2_coupler_v2", "⑤ 2-3 级间段", "Ø90.4 × 96.9 mm"),
    ("step06_S-IV_b", "⑥ S-IVB 第三级", "Ø63.5 × 181.7 mm"),
    ("step07_lem_shroud", "⑦ LEM 整流罩", "Ø58.3 × 63.7 mm"),
    ("step08_service_module", "⑧ 服务舱", "Ø39.9 × 81.4 mm"),
    ("step09_command_moduel", "⑨ 指令舱", "Ø33.9 × 20.1 mm"),
    ("step10_escape_tower", "⑩ 逃逸塔", "Ø38.3 × 110.9 mm"),
]

PAGE = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>土星五号 · 一页看完（NX 输出）</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;background:#0f1419;color:#e6edf3;
       font-family:"Microsoft YaHei","PingFang SC",sans-serif}
  .wrap{max-width:1080px;margin:0 auto;padding:22px 18px 60px}
  h1{font-size:22px;margin:0 0 6px}
  .sub{color:#8b98a5;font-size:12.5px;line-height:1.9;margin-bottom:18px}
  h2{font-size:15px;margin:30px 0 10px;padding-left:9px;border-left:3px solid #3b82f6}
  .card{background:#161b22;border:1px solid #262d36;border-radius:10px;overflow:hidden}
  .card img{display:block;width:100%}
  .cap{padding:9px 12px;font-size:12.5px;color:#a8b3bf;line-height:1.7}
  .cap b{color:#e6edf3}
  .row{display:flex;gap:10px;flex-wrap:wrap}
  .row .card{flex:1 1 320px}
  .stagewrap{position:relative}
  .badge{position:absolute;left:12px;top:12px;background:rgba(15,20,25,.82);
         border:1px solid #30363d;border-radius:7px;padding:7px 11px;font-size:13px}
  .badge b{color:#58a6ff}
  .bar{height:5px;background:#21262d}
  .bar > i{display:block;height:100%;width:0;background:#3b82f6;transition:width .12s linear}
  button{font-family:inherit;font-size:12.5px;padding:6px 13px;margin:9px 6px 0 0;
         border:1px solid #30363d;background:#21262d;color:#e6edf3;border-radius:6px;cursor:pointer}
  button:hover{background:#2b333d}
  button.on{background:#3b82f6;border-color:#3b82f6;color:#fff}
  iframe{width:100%;border:0;background:#fff;display:block}
  table{border-collapse:collapse;width:100%;font-size:12.5px;background:#161b22}
  th,td{border:1px solid #262d36;padding:6px 9px;text-align:left}
  th{background:#1c2230;color:#a8b3bf;font-weight:500}
  td.num{font-family:ui-monospace,Consolas,monospace}
  .ok{color:#3fb950}
  .foot{color:#6e7781;font-size:11.5px;line-height:1.9;margin-top:26px}
  .strip .card{flex:1 1 200px}
</style>
</head>
<body>
<div class="wrap">
  <h1>土星五号 · 一页看完</h1>
  <div class="sub">
    模型来源：<b>NASA 3D Resources</b>（公有领域，github.com/nasa/NASA-3D-Resources）。
    所有图片、刀路、尺寸都是 <b>NX 自己产出的</b>；这一页只负责摆出来，不做任何几何计算。<br>
    装配坐标不是手写的 —— 由零件实测长度累加算出（级序用公开级构），并经 NX 复核：
    <span class="ok">10 级 Z 区间全部 0.00&nbsp;mm 偏差，接缝 0.000&nbsp;mm，全箭 124.6 × 1286.6&nbsp;mm</span>。
  </div>

  <h2>① 成品（NX 渲染）</h2>
  <div class="card"><img src="__HERO__" alt="土星五号成品"></div>
  <div class="cap"><b>10 个零件</b>组成的装配体。NX 打开：<code>saturn_v_from_nx.prt</code></div>

  <h2>② 装配过程 —— 自动播放（NX 每装一级自己截一张）</h2>
  <div class="card stagewrap">
    <img id="stageimg" src="__FIRST__" alt="装配过程">
    <div class="badge"><b id="stagename">—</b><br><span id="stagedim" style="color:#8b98a5"></span></div>
  </div>
  <div class="bar"><i id="bar"></i></div>
  <div style="padding:0 0 12px">
    <button id="pp" class="on">暂停</button>
    <button id="prev">上一个</button>
    <button id="next">下一个</button>
    <button id="restart">从头播</button>
  </div>

  <h2>③ 刀路（NX 算出的 43620 个真实刀位点）</h2>
  <iframe id="f_tool" srcdoc="__TOOL__" height="620"></iframe>
  <div class="cap">
    <b>粗加工（ROUGH_ALL）是真的在切这个零件</b>：16227 个切削点，范围与零件位置吻合。
    <br>⚠️ 但模板自带的其余几道工序，切削区域还是模板自己的几何 —— 要让刀路完整正确，
    需要为这个零件<b>重编工序</b>（选刀、设切削区域）。
  </div>

  <h2>④ 材料切除仿真（粗加工 · 含撞刀检测）</h2>
  <iframe id="f_sim" srcdoc="__SIM__" height="700"></iframe>
  <div class="cap">
    毛坯和目标形状都是<b>从这个零件本身算出来的</b>（目标形状由零件网格自动生成，
    MCS 偏移 100,50,111.6）。撞刀判定 = 快移时刀尖插进材料。
  </div>

  <h2>⑤ 各级实测尺寸（NX 包围盒）</h2>
  <table id="tbl"></table>

  <h2>⑥ 其它视角</h2>
  <div class="row strip" id="strip"></div>

  <div class="foot">
    这一页是自包含的：图片已内嵌，断网、换目录都能看。<br>
    数据来源：NX <code>Component.GetPosition()</code> / <code>UF.ModlGeneral.AskBoundingBox()</code> /
    <code>OutputClsf</code> ｜ 生成工具：<code>tools/build_saturn_dashboard.py</code>
  </div>
</div>

<script>
const STEPS = __STEPS__;
const nStep = STEPS.length;
let si = 0, playing = true, timer = null;

function show(i){
  si = (i + nStep) % nStep;
  document.getElementById('stageimg').src = STEPS[si].data;
  document.getElementById('stagename').textContent = STEPS[si].label;
  document.getElementById('stagedim').textContent = STEPS[si].dim;
  document.getElementById('bar').style.width = ((si+1)/nStep*100)+'%';
}
function tick(){ if(playing) show(si+1); }
function start(){ if(timer) clearInterval(timer); timer = setInterval(tick, 1100); }
function setBtn(){ const b=document.getElementById('pp');
  b.textContent = playing ? '暂停' : '继续'; b.classList.toggle('on', playing); }

document.getElementById('pp').onclick = function(){
  playing = !playing; setBtn(); };
document.getElementById('prev').onclick = function(){ playing=false; setBtn(); show(si-1); };
document.getElementById('next').onclick = function(){ playing=false; setBtn(); show(si+1); };
document.getElementById('restart').onclick = function(){ playing=true; setBtn(); show(0); };

show(0); setBtn(); start();

const COMP = __COMPS__;
let h = '<tr><th>#</th><th>级</th><th>直径 (mm)</th><th>高度区间 z (mm)</th></tr>';
COMP.forEach(function(c,i){
  h += '<tr><td>'+c.idx+'</td><td>'+c.label+'</td>'
     + '<td class="num">'+c.dia.toFixed(1)+'</td>'
     + '<td class="num">'+c.z0.toFixed(1)+' → '+c.z1.toFixed(1)+'</td></tr>';
});
document.getElementById('tbl').innerHTML = h;

const STRIP = __STRIP__;
const st = document.getElementById('strip');
STRIP.forEach(function(v){
  const d = document.createElement('div'); d.className = 'card';
  d.innerHTML = '<img src="'+v.data+'"><div class="cap">'+v.title+'</div>';
  st.appendChild(d);
});
</script>
</body>
</html>
"""


def data_uri(path, max_w):
    w, h, rgb = png_thumb.downscale(path, max_w=max_w)
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_thumb_tmp.png")
    png_thumb.encode(tmp, w, h, rgb)
    with open(tmp, "rb") as fh:
        raw = fh.read()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii"), len(raw)


def embed_page(path, autoplay_js):
    """把子页面读进来、注入自动播放、再转义成 srcdoc。"""
    if not os.path.isfile(path):
        return "", 0
    with io.open(path, "r", encoding="utf-8") as fh:
        s = fh.read()
    s = s.replace("</body>", "<script>%s</script></body>" % autoplay_js)
    return _html.escape(s, quote=True), len(s)


def main(argv=None):
    ap = argparse.ArgumentParser(description="土星五号一页看完（自包含，自动播放）")
    ap.add_argument("-o", "--out", default=os.path.join(ENT, "saturn_dashboard.html"))
    ns = ap.parse_args(argv)

    # 各级实测尺寸
    comps = []
    if os.path.isfile(BBOX):
        with io.open(BBOX, "r", encoding="utf-8") as fh:
            bb = json.load(fh)
        for i, r in enumerate(bb.get("components") or [], 1):
            b = (r.get("bodies") or [{}])[0].get("asm")
            if not b:
                continue
            comps.append({"idx": i, "label": r.get("label", "?"),
                          "dia": b["max"][0] - b["min"][0],
                          "z0": b["min"][2], "z1": b["max"][2]})

    # 各级尺寸从 NX 导出的 JSON 读，别在页面里写死（会过期）
    dims = {}
    if os.path.isfile(ASM):
        with io.open(ASM, "r", encoding="utf-8") as fh:
            a = json.load(fh)
        for i, c in enumerate(a.get("components") or []):
            e = c.get("expected") or {}
            if e.get("diameter"):
                dims[i] = "Ø%.1f × %.1f mm" % (e["diameter"],
                                               e["z_top"] - e["z_bottom"])

    steps, n_img = [], 0
    for si, (fn, label, dim) in enumerate(STEPS):
        dim = dims.get(si, dim)
        p = os.path.join(SHOTS, fn + ".png")
        if not os.path.isfile(p):
            print("缺图: %s" % fn)
            continue
        uri, n = data_uri(p, 620)
        n_img += n
        steps.append({"data": uri, "label": label, "dim": dim})

    hero, n = data_uri(os.path.join(SHOTS, "final_iso.png"), 900)
    n_img += n

    strip = []
    for fn, title in (("final_front.png", "前视"), ("final_top.png", "俯视"),
                      ("explosion.png", "NX 爆炸图"),
                      ("cam_toolpath.png", "NX 里的刀路")):
        p = os.path.join(SHOTS, fn)
        if os.path.isfile(p):
            uri, n = data_uri(p, 460)
            n_img += n
            strip.append({"data": uri, "title": title})

    tool, n_tool = embed_page(
        os.path.join(ENT, "saturn_toolpath.html"),
        "window.addEventListener('load',function(){var b=document.getElementById('play');"
        "if(b){b.click();}});")
    sim, n_sim = embed_page(
        os.path.join(ENT, "saturn_sim.html"),
        "window.addEventListener('load',function(){var b=document.getElementById('play');"
        "if(b){b.click();}});")

    html = (PAGE.replace("__HERO__", hero)
                .replace("__FIRST__", steps[0]["data"] if steps else "")
                .replace("__STEPS__", json.dumps(steps, ensure_ascii=False))
                .replace("__COMPS__", json.dumps(comps, ensure_ascii=False))
                .replace("__STRIP__", json.dumps(strip, ensure_ascii=False))
                .replace("__TOOL__", tool)
                .replace("__SIM__", sim))

    out = os.path.abspath(ns.out)
    with io.open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("一页看完：%s" % out)
    print("  装配帧 %d 张 ｜ 其它视角 %d 张 ｜ 尺寸表 %d 行"
          % (len(steps), len(strip), len(comps)))
    print("  内嵌图片 %.1f MB（原图 5.5 MB，已用纯标准库缩放）"
          % (n_img / 1048576.0))
    print("  内嵌子页：刀路 %.0f KB ｜ 仿真 %.0f KB" % (n_tool / 1024.0, n_sim / 1024.0))
    print("  → 单文件总大小 %.1f MB（自包含，断网可看）" % (os.path.getsize(out) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
