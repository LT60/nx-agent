# -*- coding: utf-8 -*-
"""从 assembly_spec.json 生成装配可视化 HTML。

★ 为什么要走 spec 而不是直接写坐标：
  之前 assembly.html 里的零件坐标是我手写的，和 NX 里真正建的模型**对不上**
  （底板写成 92x52x6 而 NX 是 100x60x8，引擎舱写成 ±30 而 NX 是 ±24，
  还凭空多了两个 NX 里不存在的"支架"）。用户一眼看穿：装配的零件必须和
  建模的是同一份东西。
  正解是**单一数据源**：NX 建模脚本和装配可视化都读同一份 spec，
  几何就不可能不一致。

用法：
    python tools/build_assembly_html.py <spec.json> [-o out.html]
"""

from __future__ import print_function

import argparse
import json
import os
import sys

HTML = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>企业号 · 装配过程可视化</title>
<style>
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; font-family:"Microsoft YaHei","PingFang SC",sans-serif;
         background:#f6f7f9; color:#1f2328; }
  h1 { font-size:17px; margin:0 0 3px; font-weight:600; }
  .sub { font-size:12px; color:#6b7280; margin-bottom:12px; line-height:1.75; }
  .wrap { display:flex; gap:14px; align-items:flex-start; flex-wrap:wrap; }
  canvas { background:#fff; border:1px solid #d7dbe0; border-radius:8px; display:block; }
  .panel { flex:1; min-width:280px; font-size:13px; }
  button { font-size:13px; padding:6px 14px; margin:0 6px 6px 0; border:1px solid #c9ced6;
           background:#fff; border-radius:6px; cursor:pointer; }
  button:hover { background:#eef1f5; }
  button.on { background:#2563eb; color:#fff; border-color:#2563eb; }
  input[type=range] { width:100%; }
  label { display:block; font-size:12px; color:#4b5563; margin:10px 0 3px; }
  .sw { width:15px; height:11px; border-radius:2px; border:1px solid rgba(0,0,0,.2);
        display:inline-block; }
  .prow { margin:7px 0; font-size:12.5px; line-height:1.5; }
  .pname { font-weight:600; }
  .pdim { color:#6b7280; font-family:ui-monospace,Consolas,monospace; font-size:11.5px; }
  .stat { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#374151;
          background:#fff; border:1px solid #d7dbe0; border-radius:6px;
          padding:8px 10px; margin-top:10px; line-height:1.8; }
  .hint { font-size:12px; color:#6b7280; margin-top:12px; line-height:1.8; }
  .why { background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:9px 11px;
         font-size:12px; line-height:1.75; margin-top:12px; color:#1e3a8a; }
  .src { background:#ecfdf5; border:1px solid #a7f3d0; border-radius:6px; padding:9px 11px;
         font-size:11.5px; line-height:1.7; margin-top:10px; color:#065f46;
         font-family:ui-monospace,Consolas,monospace; word-break:break-all; }
</style>
</head>
<body>
<h1>企业号 · 装配过程可视化</h1>
<div class="sub">
  __NOTE__
</div>

<div class="wrap">
  <canvas id="cv" width="780" height="540"></canvas>
  <div class="panel">
    <div>
      <button id="play">播放装配</button>
      <button id="reset">拆开</button>
      <button id="end">直接看成品</button>
    </div>
    <label>装配进度（可拖动查看任意阶段）</label>
    <input type="range" id="bar" min="0" max="1000" value="0">

    <div style="margin-top:14px"><b>零件清单</b><div id="pl"></div></div>
    <div class="stat" id="stat"></div>

    <div class="src"><b>数据来源（单一数据源）</b><br>__SOURCE__</div>

    <div class="why">
      <b>为什么必须分件</b><br>
      三轴铣刀只能「从上往下」切。引擎舱细长且悬在底板上方，
      若和舰体连成一体，它底下那块料刀具够不到 —— 只能做成独立零件单独加工再装上去。
      这正是模具厂的日常：一套模具几十上百个零件，<b>没有一个是整块铣出来的</b>。
    </div>
    <div class="hint">
      <b>和单件版的关系</b><br>
      单件版 <code>enterprise_final.prt</code>：这 5 个零件<b>合并成一体</b>，一刀流铣成（已跑通）。<br>
      装配版：同样这 5 个零件，<b>分开加工再组装</b> —— 几何完全相同，差别只在「合体」还是「分件」。
    </div>
  </div>
</div>

<script>
const SPEC = __SPEC__;
const PARTS = SPEC.parts;
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');

/* 由 spec 推出几何（box: origin+lengths / cyl: center+dia+z0+height） */
PARTS.forEach(p=>{
  if(p.type==='box'){
    p.x0=p.origin[0]; p.y0=p.origin[1]; p.z0=p.origin[2];
    p.x1=p.origin[0]+p.lengths[0]; p.y1=p.origin[1]+p.lengths[1]; p.z1=p.origin[2]+p.lengths[2];
    p.cx=(p.x0+p.x1)/2; p.cy=(p.y0+p.y1)/2;
  } else {
    p.r=p.diameter/2; p.cx=p.center[0]; p.cy=p.center[1];
    p.z0=p.z0; p.z1=p.z0+p.height;
  }
  // 拆解待命位：沿"离开装配中心"的方向散开
  const dx = p.cx>=0 ? 1 : -1, dy = p.cy>=0 ? 1 : -1;
  p.from = [ dx*(120+Math.abs(p.cx)*1.4), dy*(90+Math.abs(p.cy)*1.6), 40+p.z1*1.1 ];
});

const pl=document.getElementById('pl');
PARTS.forEach(p=>{
  const d=document.createElement('div'); d.className='prow';
  const dim = p.type==='box'
    ? p.lengths[0]+'×'+p.lengths[1]+'×'+p.lengths[2]+'mm　起点('+p.x0+','+p.y0+','+p.z0+')'
    : 'Ø'+p.diameter+'×'+p.height+'mm　中心('+p.cx+','+p.cy+') Z'+p.z0+'→'+p.z1;
  d.innerHTML='<span class="sw" style="background:'+p.color+'"></span> '
    + '<span class="pname">'+p.name+'</span><br>'
    + '<span class="pdim">　'+dim+'</span><br>'
    + '<span class="pdim">　工艺：'+(p.machining||'—')+'</span>';
  pl.appendChild(d);
});

/* ---- 等轴测投影 ---- */
const CA=Math.cos(Math.PI/6), SA=Math.sin(Math.PI/6);
function proj(x,y,z){ return [CA*(x-y), SA*(x+y)-z]; }
(function fit(){
  let a=1e9,b=-1e9,c=1e9,d=-1e9;
  PARTS.forEach(p=>{
    const pts = p.type==='box'
      ? [[p.x0,p.y0,p.z0],[p.x1,p.y1,p.z1]] : [[p.cx-p.r,p.cy-p.r,p.z0],[p.cx+p.r,p.cy+p.r,p.z1]];
    pts.concat([[p.cx+p.from[0], p.cy+p.from[1], p.z1+p.from[2]]]).forEach(v=>{
      const q=proj(v[0],v[1],v[2]);
      a=Math.min(a,q[0]); b=Math.max(b,q[0]); c=Math.min(c,q[1]); d=Math.max(d,q[1]);
    });
  });
  const pad=52;
  const s=Math.min((cv.width-pad*2)/(b-a),(cv.height-pad*2)/(d-c));
  window.SC=s; window.OX=pad-a*s+(cv.width-pad*2-(b-a)*s)/2;
  window.OY=pad-c*s+(cv.height-pad*2-(d-c)*s)/2;
})();
function P(x,y,z){ const q=proj(x,y,z); return [q[0]*SC+OX, q[1]*SC+OY]; }

function drawBox(x0,y0,z0,x1,y1,z1,stroke,lw){
  const c=[[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
           [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]].map(v=>P(v[0],v[1],v[2]));
  const E=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
  [[[4,5,6,7],'rgba(255,255,255,0.35)'],[[0,1,5,4],'rgba(0,0,0,0.10)'],[[1,2,6,5],'rgba(0,0,0,0.18)']]
    .forEach(f=>{ ctx.beginPath();
      f[0].forEach((idx,i)=>{ const p=c[idx]; i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]); });
      ctx.closePath(); ctx.fillStyle=f[1]; ctx.fill(); });
  ctx.strokeStyle=stroke; ctx.lineWidth=lw||1.5; ctx.beginPath();
  E.forEach(e=>{ ctx.moveTo(c[e[0]][0],c[e[0]][1]); ctx.lineTo(c[e[1]][0],c[e[1]][1]); });
  ctx.stroke();
}

function drawCyl(cx,cy,r,z0,z1,stroke,lw){
  const N=32, top=[], bot=[];
  for(let i=0;i<N;i++){ const a=2*Math.PI*i/N;
    top.push(P(cx+r*Math.cos(a), cy+r*Math.sin(a), z1));
    bot.push(P(cx+r*Math.cos(a), cy+r*Math.sin(a), z0)); }
  ctx.beginPath();
  for(let i=0;i<N;i++){ const j=(i+1)%N;
    ctx.moveTo(top[i][0],top[i][1]); ctx.lineTo(top[j][0],top[j][1]);
    ctx.lineTo(bot[j][0],bot[j][1]); ctx.lineTo(bot[i][0],bot[i][1]); ctx.closePath(); }
  ctx.fillStyle='rgba(255,255,255,0.28)'; ctx.fill();
  ctx.beginPath(); top.forEach((p,i)=> i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
  ctx.closePath(); ctx.fillStyle='rgba(255,255,255,0.55)'; ctx.fill();
  ctx.strokeStyle=stroke; ctx.lineWidth=lw||1.5; ctx.stroke();
  ctx.beginPath(); bot.forEach((p,i)=> i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
  ctx.closePath(); ctx.stroke();
}

const NP=PARTS.length;
function offFor(p,i,t){
  const a=i/NP, b=(i+1)/NP;
  let u=(t-a)/(b-a); u=Math.max(0,Math.min(1,u));
  u = u<0.5 ? 2*u*u : 1-Math.pow(-2*u+2,2)/2;
  const k=1-u;
  return [p.from[0]*k, p.from[1]*k, p.from[2]*k];
}

let t=0, playing=false, lastT=0, acc=0;
function render(){
  ctx.clearRect(0,0,cv.width,cv.height);
  ctx.strokeStyle='#e5e7eb'; ctx.lineWidth=1;
  for(let g=-40;g<=40;g+=20){
    const a=P(g,-40,0), b=P(g,40,0);
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    const c=P(-40,g,0), d=P(40,g,0);
    ctx.beginPath(); ctx.moveTo(c[0],c[1]); ctx.lineTo(d[0],d[1]); ctx.stroke();
  }
  const order = PARTS.map((p,i)=>({p,i,d:(p.cx+p.cy)+offFor(p,i,t)[0]+offFor(p,i,t)[1]}))
                     .sort((A,B)=>A.d-B.d);
  let placed=0;
  order.forEach(o=>{
    const f=offFor(o.p,o.i,t);
    if(Math.abs(f[0])<0.5&&Math.abs(f[1])<0.5&&Math.abs(f[2])<0.5) placed++;
    if(o.p.type==='box') drawBox(o.p.x0+f[0],o.p.y0+f[1],o.p.z0+f[2],
                                 o.p.x1+f[0],o.p.y1+f[1],o.p.z1+f[2], o.p.color, 1.5);
    else drawCyl(o.p.cx+f[0],o.p.cy+f[1],o.p.r,o.p.z0+f[2],o.p.z1+f[2], o.p.color, 1.5);
    if(Math.abs(f[0])>0.5||Math.abs(f[1])>0.5||Math.abs(f[2])>0.5){
      const tgt=[o.p.cx,o.p.cy,o.p.z1], src=[tgt[0]+f[0],tgt[1]+f[1],tgt[2]+f[2]];
      const a=P(src[0],src[1],src[2]), b=P(tgt[0],tgt[1],tgt[2]);
      ctx.setLineDash([4,4]); ctx.strokeStyle=o.p.color; ctx.lineWidth=1; ctx.globalAlpha=.5;
      ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha=1;
    }
  });
  document.getElementById('stat').innerHTML =
    '装配进度 '+(t*100).toFixed(0)+'%　｜　已就位 '+placed+' / '+NP+' 个零件<br>'
    + (placed===NP ? '<b style="color:#065f46">✔ 装配完成（几何与单件版完全一致）</b>'
                   : '正在装入：'+(PARTS[placed]?PARTS[placed].name:'—'));
  document.getElementById('bar').value=Math.round(t*1000);
}
function loop(ts){
  if(playing){
    if(!lastT) lastT=ts;
    acc+=(ts-lastT);
    while(acc>16){ t+=0.0032; acc-=16; }
    if(t>=1){ t=1; playing=false;
      const b=document.getElementById('play'); b.textContent='播放装配'; b.classList.remove('on'); }
    render();
  }
  lastT=ts; requestAnimationFrame(loop);
}
const btn=document.getElementById('play');
btn.onclick=function(){ if(t>=1) t=0; playing=!playing; lastT=0;
  this.textContent=playing?'暂停':'播放装配'; this.classList.toggle('on',playing); };
document.getElementById('reset').onclick=function(){ playing=false; t=0; render();
  btn.textContent='播放装配'; btn.classList.remove('on'); };
document.getElementById('end').onclick=function(){ playing=false; t=1; render();
  btn.textContent='播放装配'; btn.classList.remove('on'); };
document.getElementById('bar').oninput=function(){ playing=false; t=this.value/1000; render(); };

render(); requestAnimationFrame(loop);
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="从 spec 生成装配可视化 HTML")
    ap.add_argument("spec")
    ap.add_argument("-o", "--out")
    ns = ap.parse_args(argv)

    spec_path = os.path.abspath(ns.spec)
    if not os.path.isfile(spec_path):
        print("找不到 spec: %s" % spec_path)
        return 2
    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    parts = spec.get("parts") or []
    if not parts:
        print("spec 里没有零件")
        return 3

    print("零件 %d 个（全部来自 spec，不再手写坐标）:" % len(parts))
    for p in parts:
        if p["type"] == "box":
            d = "%g×%g×%g 起点(%g,%g,%g)" % tuple(p["lengths"] + p["origin"])
        else:
            d = "Ø%g×%g 中心(%g,%g) Z%g→%g" % (
                p["diameter"], p["height"], p["center"][0], p["center"][1],
                p["z0"], p["z0"] + p["height"])
        print("   · %-14s %s" % (p["name"], d))

    note = spec.get("note", "")
    src = spec.get("_source", "")
    html = (HTML.replace("__SPEC__", json.dumps(spec, ensure_ascii=False))
                .replace("__NOTE__", note)
                .replace("__SOURCE__", src))

    out = os.path.abspath(ns.out) if ns.out else \
        os.path.join(os.path.dirname(spec_path), "assembly.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已生成: %s（%.0f KB）" % (out, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
