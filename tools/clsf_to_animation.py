# -*- coding: utf-8 -*-
"""把 CLSF 刀位源文件变成一个可在浏览器里播放的**刀路动画**（HTML）。

为什么要这个：
  NX 批处理模式下拿不到逐帧画面 —— 实测 ReplayToolPath / Show3dWorkpiece
  每次调用后画面都停在最终状态（6 帧截图字节数一模一样）。而 CLSF 里
  有全部真实刀位坐标，所以干脆自己渲染动画，反而更可控、能暂停能拖进度。

用法：
    python tools/clsf_to_animation.py <in.clsf> [-o out.html] [--max-points 900]

纯标准库，不依赖 NX / ffmpeg / PIL。
"""

from __future__ import print_function

import argparse
import json
import os
import sys

# ----------------------------------------------------------------- 解析 CLSF
def parse_clsf(path):
    """返回 (operations, msys)。

    operations: [{name, dia, pts:[[x,y,z,rapid], ...]}]
    msys:       [x,y,z] —— 刀路坐标要加上它才回到零件坐标系
                （实测：GOTO/-104.8,-10.1,-71.6 + MSYS/100,50,111.6
                  -> -4.8, 39.9, 40.0，正好落在零件范围里）
    """
    ops = []
    cur = None
    msys = [0.0, 0.0, 0.0]
    dia = None
    rapid = False

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue

            if line.startswith("TOOL PATH/"):
                rest = line.split("/", 1)[1]
                name = rest.split(",")[0].strip()
                cur = {"name": name, "dia": None, "pts": []}
                ops.append(cur)
                dia = None
                rapid = False
            elif line.startswith("TLDATA/"):
                parts = line.split("/", 1)[1].split(",")
                try:
                    dia = float(parts[1])
                except (IndexError, ValueError):
                    pass
                if cur is not None and dia is not None:
                    cur["dia"] = dia
            elif line.startswith("MSYS/"):
                parts = line.split("/", 1)[1].split(",")
                try:
                    msys = [float(parts[0]), float(parts[1]), float(parts[2])]
                except (IndexError, ValueError):
                    pass
            elif line.startswith("RAPID"):
                rapid = True
            elif line.startswith("FEDRAT"):
                rapid = False          # 有进给 = 真在切削
            elif line.startswith("GOTO/"):
                if cur is None:
                    continue
                nums = line.split("/", 1)[1].split(",")
                try:
                    x = float(nums[0])
                    y = float(nums[1])
                    z = float(nums[2])
                except (IndexError, ValueError):
                    continue
                cur["pts"].append([x + msys[0], y + msys[1], z + msys[2],
                                   1 if rapid else 0])
    return ops, msys


def thin(pts, max_n):
    """等距抽稀（保留首尾）。刀路点动辄十几万，全塞进 HTML 太大。"""
    if len(pts) <= max_n:
        return pts
    step = float(len(pts)) / max_n
    out = []
    i = 0.0
    while int(i) < len(pts):
        out.append(pts[int(i)])
        i += step
    out.append(pts[-1])
    return out


# ----------------------------------------------------------------- HTML
HTML = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>企业号 · 刀路仿真动画</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; padding:18px; font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         background:#f6f7f9; color:#1f2328; }
  h1 { font-size:17px; margin:0 0 4px; font-weight:600; }
  .sub { font-size:12px; color:#6b7280; margin-bottom:12px; line-height:1.7; }
  .wrap { display:flex; gap:14px; align-items:flex-start; flex-wrap:wrap; }
  canvas { background:#fff; border:1px solid #d7dbe0; border-radius:8px; display:block; }
  .panel { flex:1; min-width:260px; font-size:13px; }
  .ctrl { margin-bottom:12px; }
  button { font-size:13px; padding:6px 14px; margin-right:6px; border:1px solid #c9ced6;
           background:#fff; border-radius:6px; cursor:pointer; }
  button:hover { background:#eef1f5; }
  button.on { background:#2563eb; color:#fff; border-color:#2563eb; }
  input[type=range] { width:100%; }
  label { display:block; font-size:12px; color:#4b5563; margin:10px 0 3px; }
  .legend { margin-top:14px; font-size:12px; }
  .legend div { display:flex; align-items:center; gap:7px; margin:5px 0; }
  .sw { width:16px; height:4px; border-radius:2px; }
  .stat { font-family: ui-monospace, Consolas, monospace; font-size:12px;
          color:#374151; background:#fff; border:1px solid #d7dbe0;
          border-radius:6px; padding:8px 10px; margin-top:10px; line-height:1.8; }
  .hint { font-size:12px; color:#6b7280; margin-top:12px; line-height:1.7; }
</style>
</head>
<body>
<h1>企业号 · 三轴铣削刀路仿真</h1>
<div class="sub">
  数据来自 NX 输出的 CLSF 刀位源文件（真实刀路坐标，共 __TOTAL__ 个刀位点，抽稀后 __USED__ 个）。
  这是<b>切削仿真</b>：看刀具怎么一步步走完全部工序 —— 用来提前发现撞刀、进刀过度、漏切。
</div>

<div class="wrap">
  <canvas id="cv" width="760" height="560"></canvas>
  <div class="panel">
    <div class="ctrl">
      <button id="play">播放</button>
      <button id="reset">重来</button>
    </div>
    <label>进度（可拖动查看任意时刻）</label>
    <input type="range" id="bar" min="0" max="1000" value="0">
    <label>速度 <span id="spdv">1.0×</span></label>
    <input type="range" id="spd" min="1" max="40" value="10">

    <div class="legend"><b>工序</b><div id="lg"></div></div>

    <div class="stat" id="stat"></div>

    <div class="hint">
      红色小球 = 刀尖，竖线 = 刀杆。<br>
      <b>实线</b> = 真在切削（有进给）；<b>虚线/浅色</b> = 快速移动（抬刀换位，不切削）。<br>
      灰色半透明盒子 = 毛坯（加工前的整块料）；深色线框 = 最终零件。
    </div>
  </div>
</div>

<script>
const DATA = __DATA__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const ops = DATA.ops, blank = DATA.blank, parts = DATA.parts;

const COLORS = ['#2563eb','#059669','#d97706','#dc2626','#7c3aed','#0891b2'];
ops.forEach((o,i)=> o.color = COLORS[i % COLORS.length]);

const lg = document.getElementById('lg');
ops.forEach(o=>{
  const d = document.createElement('div');
  d.innerHTML = '<span class="sw" style="background:'+o.color+'"></span>'
    + '<span>'+o.name+'</span><span style="color:#9ca3af">（'+(o.dia?o.dia.toFixed(1)+'mm 刀':'刀')+'，'+o.pts.length+' 点）</span>';
  lg.appendChild(d);
});

let idx = 0, playing = false, speed = 1.0, lastT = 0, acc = 0;
const total = ops.reduce((s,o)=> s + o.pts.length, 0);

// 切削点前缀和：cutPrefix[k] = 该操作前 k 个点里有多少个在真切削
ops.forEach(o=>{ o.cutPrefix = [0]; let c = 0;
  for(let i=0;i<o.pts.length;i++){ if(o.pts[i][3]===0) c++; o.cutPrefix.push(c); } });
const opCutSum = []; (function(){ let s=0;
  ops.forEach(o=>{ opCutSum.push(s); s += o.cutPrefix[o.pts.length]; }); })();

function proj(x, y, z){
  const a = Math.PI/6;
  return [ Math.cos(a)*(x-y), Math.sin(a)*(x+y) - z ];
}

function fitScale(){
  let minX=1e9,maxX=-1e9,minY=1e9,maxY=-1e9;
  const sample = [];
  sample.push([blank.x0,blank.y0,blank.z0],[blank.x1,blank.y1,blank.z1]);
  ops.forEach(o=>{ if(o.pts.length) sample.push([o.pts[0][0],o.pts[0][1],o.pts[0][2]]); });
  sample.forEach(p=>{ const q = proj(p[0],p[1],p[2]);
    minX=Math.min(minX,q[0]); maxX=Math.max(maxX,q[0]);
    minY=Math.min(minY,q[1]); maxY=Math.max(maxY,q[1]); });
  const pad = 46;
  const sx = (cv.width - pad*2) / Math.max(1, maxX-minX);
  const sy = (cv.height - pad*2) / Math.max(1, maxY-minY);
  const s = Math.min(sx, sy);
  return { s: s, ox: pad - minX*s + (cv.width - pad*2 - (maxX-minX)*s)/2,
                 oy: pad - minY*s + (cv.height - pad*2 - (maxY-minY)*s)/2 };
}
const T = fitScale();
function P(x,y,z){ const q = proj(x,y,z); return [q[0]*T.s+T.ox, q[1]*T.s+T.oy]; }

function drawBox(b, stroke, fill, lw){
  const c = [[b.x0,b.y0,b.z0],[b.x1,b.y0,b.z0],[b.x1,b.y1,b.z0],[b.x0,b.y1,b.z0],
             [b.x0,b.y0,b.z1],[b.x1,b.y0,b.z1],[b.x1,b.y1,b.z1],[b.x0,b.y1,b.z1]];
  const p = c.map(v=>P(v[0],v[1],v[2]));
  const E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
  if(fill){ ctx.beginPath();
    ctx.moveTo(p[4][0],p[4][1]); ctx.lineTo(p[5][0],p[5][1]);
    ctx.lineTo(p[6][0],p[6][1]); ctx.lineTo(p[7][0],p[7][1]); ctx.closePath();
    ctx.fillStyle = fill; ctx.fill();
    ctx.beginPath(); ctx.moveTo(p[0][0],p[0][1]); ctx.lineTo(p[1][0],p[1][1]);
    ctx.lineTo(p[2][0],p[2][1]); ctx.lineTo(p[3][0],p[3][1]); ctx.closePath();
    ctx.fillStyle = fill; ctx.fill();
  }
  ctx.strokeStyle = stroke; ctx.lineWidth = lw||1;
  ctx.beginPath();
  E.forEach(e=>{ ctx.moveTo(p[e[0]][0],p[e[0]][1]); ctx.lineTo(p[e[1]][0],p[e[1]][1]); });
  ctx.stroke();
}

function drawCyl(c, stroke, lw){
  const N = 28, top=[], bot=[];
  for(let i=0;i<N;i++){ const a = 2*Math.PI*i/N;
    top.push(P(c.cx+c.r*Math.cos(a), c.cy+c.r*Math.sin(a), c.z1));
    bot.push(P(c.cx+c.r*Math.cos(a), c.cy+c.r*Math.sin(a), c.z0)); }
  ctx.strokeStyle = stroke; ctx.lineWidth = lw||1;
  ctx.beginPath();
  top.forEach((p,i)=> i? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1]));
  ctx.closePath(); ctx.stroke();
  ctx.beginPath();
  bot.forEach((p,i)=> i? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1]));
  ctx.closePath(); ctx.stroke();
  ctx.beginPath();
  for(let i=0;i<N;i+=3){ ctx.moveTo(top[i][0],top[i][1]); ctx.lineTo(bot[i][0],bot[i][1]); }
  ctx.stroke();
}

function frameFor(i){
  let n = i, oi = 0;
  for(; oi<ops.length; oi++){ if(n < ops[oi].pts.length) break; n -= ops[oi].pts.length; }
  return { oi: Math.min(oi, ops.length-1), k: n };
}

function render(){
  ctx.clearRect(0,0,cv.width,cv.height);
  drawBox(blank, '#c3c8cf', 'rgba(200,205,212,0.30)', 1);
  parts.forEach(p=>{ if(p.type==='box') drawBox(p,'#4b5563',null,1.2);
                     else drawCyl(p,'#4b5563',1.2); });

  const f = frameFor(idx);
  for(let oi=0; oi<ops.length; oi++){
    const o = ops[oi];
    const end = (oi < f.oi) ? o.pts.length : (oi===f.oi ? f.k : 0);
    if(end < 2) continue;
    ctx.lineWidth = 1.4; ctx.strokeStyle = o.color; ctx.globalAlpha = 0.85;
    ctx.beginPath();
    let started = false;
    for(let k=1;k<end;k++){
      const a = o.pts[k-1], b = o.pts[k];
      if(b[3] === 1){ started = false; continue; }   // 快移不画
      const pa = P(a[0],a[1],a[2]), pb = P(b[0],b[1],b[2]);
      if(!started){ ctx.moveTo(pa[0],pa[1]); started = true; }
      ctx.lineTo(pb[0],pb[1]);
    }
    ctx.stroke(); ctx.globalAlpha = 1;
  }

  // 当前刀具
  const o = ops[f.oi], pt = o.pts[Math.min(f.k, o.pts.length-1)];
  const tip = P(pt[0], pt[1], pt[2]);
  const shaftTop = P(pt[0], pt[1], pt[2] + 26);
  ctx.strokeStyle = '#dc2626'; ctx.lineWidth = 3; ctx.beginPath();
  ctx.moveTo(tip[0],tip[1]); ctx.lineTo(shaftTop[0],shaftTop[1]); ctx.stroke();
  ctx.fillStyle = '#dc2626'; ctx.beginPath();
  ctx.arc(tip[0],tip[1],4.5,0,Math.PI*2); ctx.fill();

  // 状态（用前缀和，别每帧 O(n) 重算，否则点多了会卡）
  const cut = opCutSum[f.oi] + o.cutPrefix[Math.min(f.k, o.pts.length-1)];
  document.getElementById('stat').innerHTML =
    '工序：<b style="color:'+o.color+'">'+o.name+'</b>　（第 '+(f.oi+1)+'/'+ops.length+' 道）<br>'
    + '刀尖 X '+pt[0].toFixed(1)+'　Y '+pt[1].toFixed(1)+'　Z '+pt[2].toFixed(1)+'<br>'
    + '刀径 '+(o.dia?o.dia.toFixed(1):'?')+' mm　已走 '+idx+' / '+total+' 点<br>'
    + '切削点 '+cut+' 个（其余是快移换位）';

  document.getElementById('bar').value = Math.round(idx/Math.max(1,total-1)*1000);
}

function step(){
  if(idx < total-1) idx++; else { playing=false; document.getElementById('play').textContent='播放';
    document.getElementById('play').classList.remove('on'); }
}

function loop(t){
  if(playing){
    if(!lastT) lastT = t;
    acc += (t - lastT);
    // 60fps 下每帧约 16ms；每帧走 5*speed 个点 —— 1.0× 时 1.2 万点约 40 秒播完
    const per = Math.max(1, Math.round(5 * speed));
    while(acc > 16){ for(let i=0;i<per;i++) step(); acc -= 16; }
    render();
  }
  lastT = t;
  requestAnimationFrame(loop);
}

document.getElementById('play').onclick = function(){
  playing = !playing; lastT = 0;
  this.textContent = playing ? '暂停' : '播放';
  this.classList.toggle('on', playing);
};
document.getElementById('reset').onclick = function(){ idx = 0; render(); };
document.getElementById('bar').oninput = function(){
  idx = Math.round(this.value/1000*(total-1)); render();
};
document.getElementById('spd').oninput = function(){
  speed = this.value/10; document.getElementById('spdv').textContent = speed.toFixed(1)+'×';
};

render();
requestAnimationFrame(loop);
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="CLSF 刀位文件 -> 浏览器动画 HTML")
    ap.add_argument("clsf", help="输入 .clsf 文件")
    ap.add_argument("-o", "--out", help="输出 HTML（默认与输入同目录）")
    ap.add_argument("--max-points", type=int, default=900, help="每道工序最多保留多少点")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.clsf)
    if not os.path.isfile(src):
        print("找不到 CLSF: %s" % src)
        return 2

    ops, msys = parse_clsf(src)
    if not ops:
        print("CLSF 里没解析出任何刀路")
        return 3

    # ★ CLSF 会把同一批操作完整输出两遍（实测：ROUGH_ALL 出现在第 1 行和第
    #   114812 行，结构完全一样）。不去掉的话动画会播两遍。按名字去重，
    #   保留点最多的那一份，并保持首次出现的顺序（那就是加工顺序）。
    order = []
    best = {}
    for o in ops:
        if o["name"] not in best:
            order.append(o["name"])
            best[o["name"]] = o
        elif len(o["pts"]) > len(best[o["name"]]["pts"]):
            best[o["name"]] = o
    if len(order) != len(ops):
        print("去重：%d 段 -> %d 道工序（CLSF 会重复输出）" % (len(ops), len(order)))
    ops = [best[n] for n in order]

    total = sum(len(o["pts"]) for o in ops)
    for o in ops:
        o["pts"] = thin(o["pts"], args.max_points)
    used = sum(len(o["pts"]) for o in ops)

    print("解析：MSYS=%s ｜ %d 道工序 ｜ 原始 %d 点 -> 抽稀后 %d 点"
          % (msys, len(ops), total, used))
    for o in ops:
        print("   · %-34s 刀径 %s mm ｜ %d 点"
              % (o["name"], o["dia"], len(o["pts"])))

    # 企业号的几何（与建模脚本一致：底板/碟形/舰桥/双引擎舱）
    shapes = [
        {"type": "box", "x0": -50, "y0": -30, "z0": 0, "x1": 50, "y1": 30, "z1": 8},
        {"type": "cyl", "cx": 0, "cy": 8, "r": 23, "z0": 6, "z1": 15},
        {"type": "box", "x0": -8, "y0": 20, "z0": 13, "x1": 8, "y1": 32, "z1": 19},
        {"type": "cyl", "cx": -24, "cy": -22, "r": 5, "z0": 6, "z1": 38},
        {"type": "cyl", "cx": 24, "cy": -22, "r": 5, "z0": 6, "z1": 38},
    ]
    blank = {"x0": -50, "y0": -30, "z0": 0, "x1": 50, "y1": 30, "z1": 42}

    payload = {"ops": ops, "parts": shapes, "blank": blank}
    html = (HTML
            .replace("__DATA__", json.dumps(payload))
            .replace("__TOTAL__", "{:,}".format(total))
            .replace("__USED__", "{:,}".format(used)))

    out = os.path.abspath(args.out) if args.out else \
        os.path.join(os.path.dirname(src), "toolpath_animation.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)

    print("已生成动画: %s（%.0f KB）" % (out, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
