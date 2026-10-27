# -*- coding: utf-8 -*-
"""★ 装配可视化渲染器：数据全部来自 NX 导出的 JSON，本文件不编任何几何。

和上一版（build_assembly_html.py）的根本区别：
  上一版的零件坐标是我手写的，被用户当场看穿（底板写成 92×52×6 而 NX 是
  100×60×8；引擎舱是竖着的烟囱，因为坐标是从「单件加工版」抄来的）。
  这一版：
    - 位置/朝向 → NX Component.GetPosition() 读回的 origin + matrix
    - 尺寸校验 → NX UF.ModlGeneral.AskBoundingBox() 给的真实包围盒
    - 本渲染器只做「把 NX 给的数据画出来」这一件事

★ 顺带修了上一版的圆柱画法：之前只描了顶圆和底圆的边，侧面没描线，
  看起来就是「两个圆形边」。现在侧面按法线着色，并算出真正的轮廓线。

用法：
    python tools/render_assembly.py --asm output/enterprise/assembly_from_nx.json \
                                    --bbox output/enterprise/assembly_bbox_from_nx.json \
                                    -o output/enterprise/assembly.html

退出码：0 = 渲染包围盒与 NX 包围盒一致；1 = 不一致（HTML 仍会生成，但别信）
"""

from __future__ import print_function

import argparse
import json
import math
import os
import sys

TEMPLATE = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>企业号 · 装配可视化（数据来自 NX）</title>
<style>
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; font-family:"Microsoft YaHei","PingFang SC",sans-serif;
         background:#f6f7f9; color:#1f2328; }
  h1 { font-size:17px; margin:0 0 3px; font-weight:600; }
  .sub { font-size:12px; color:#6b7280; margin-bottom:12px; line-height:1.8; }
  .wrap { display:flex; gap:14px; align-items:flex-start; flex-wrap:wrap; }
  canvas { background:#fff; border:1px solid #d7dbe0; border-radius:8px; display:block; }
  .panel { flex:1; min-width:300px; font-size:13px; }
  button { font-size:13px; padding:6px 14px; margin:0 6px 6px 0; border:1px solid #c9ced6;
           background:#fff; border-radius:6px; cursor:pointer; }
  button:hover { background:#eef1f5; }
  button.on { background:#2563eb; color:#fff; border-color:#2563eb; }
  input[type=range] { width:100%; }
  label { display:block; font-size:12px; color:#4b5563; margin:10px 0 3px; }
  .sw { width:15px; height:11px; border-radius:2px; border:1px solid rgba(0,0,0,.25);
        display:inline-block; }
  .prow { margin:7px 0; font-size:12.5px; line-height:1.55; }
  .pdim { color:#6b7280; font-family:ui-monospace,Consolas,monospace; font-size:11.5px; }
  .stat { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#374151;
          background:#fff; border:1px solid #d7dbe0; border-radius:6px;
          padding:8px 10px; margin-top:10px; line-height:1.8; }
  table { border-collapse:collapse; font-size:11.5px; width:100%;
          font-family:ui-monospace,Consolas,monospace; }
  th,td { border:1px solid #e2e6ec; padding:3px 6px; text-align:left; }
  th { background:#f3f4f6; }
  .ok { color:#065f46; font-weight:600; }
  .bad { color:#b91c1c; font-weight:600; }
  .why { background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; padding:9px 11px;
         font-size:12px; line-height:1.75; margin-top:12px; color:#1e3a8a; }
  .src { background:#ecfdf5; border:1px solid #a7f3d0; border-radius:6px; padding:9px 11px;
         font-size:11.5px; line-height:1.7; margin-top:10px; color:#065f46;
         font-family:ui-monospace,Consolas,monospace; word-break:break-all; }
  .hint { font-size:12px; color:#6b7280; margin-top:12px; line-height:1.8; }
</style>
</head>
<body>
<h1>企业号 · 装配可视化</h1>
<div class="sub">
  <b>这份图的每一个坐标都来自 NX</b>：零件是 NX 建的 4 个 .prt，装配是 NX 的
  <code>ComponentAssembly.AddComponent</code> 装的，位置和朝向是 NX 的
  <code>Component.GetPosition()</code> 读回的，尺寸拿 NX 的
  <code>UF.ModlGeneral.AskBoundingBox()</code> 验的。本页只负责画。
</div>

<div class="wrap">
  <canvas id="cv" width="800" height="560"></canvas>
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

    <div style="margin-top:14px"><b>★ 渲染结果 vs NX 实测包围盒</b>
      <div id="ver"></div>
    </div>

    <div class="src"><b>数据来源</b><br>__SOURCE__</div>

    <div class="why">
      <b>为什么要分件、为什么引擎舱是横的</b><br>
      三轴铣刀只能从上往下切。引擎舱细长，立着才铣得出（或车削），
      但装到船上它得横躺 —— <b>加工姿态 ≠ 装配姿态</b>，这一步由 NX 的旋转矩阵完成，
      不是我在图上挪的。同一个零件（ent_nacelle.prt）被装了两次，左右各一。
    </div>
    <div class="hint">
      <b>看什么</b><br>
      1) 拖进度条看装配顺序：底板 → 碟形 → 舰桥 → 左右引擎舱。<br>
      2) 上面那张对照表必须<b>全绿</b>；红任何一个，说明画的和 NX 建的不是一回事。
    </div>
  </div>
</div>

<script>
const A = __ASM__;
const B = __BBOX__;
const COMPS = A.components;

/* ---- 数学：world = origin + M·p（M 行主序，来自 NX） ---- */
function tf(org, m, p){
  return [
    org[0] + m[0]*p[0] + m[1]*p[1] + m[2]*p[2],
    org[1] + m[3]*p[0] + m[4]*p[1] + m[5]*p[2],
    org[2] + m[6]*p[0] + m[7]*p[1] + m[8]*p[2]
  ];
}
function sub(a,b){ return [a[0]-b[0],a[1]-b[1],a[2]-b[2]]; }
function norm(a){ const L=Math.hypot(a[0],a[1],a[2])||1; return [a[0]/L,a[1]/L,a[2]/L]; }
function cross(a,b){ return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]; }
function dot(a,b){ return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
/* 相机方向：等轴测投影 proj(x,y,z)=(CA(x-y), SA(x+y)-z) 的零方向是 (1,1,1) */
const CAM = norm([1,1,1]);

/* ---- 每个组件算出世界坐标下的面（盒：6 面；柱：侧面 N 片 + 两端盖） ---- */
const NRIM = 40;
COMPS.forEach(function(c){
  const org=c.origin, m=c.matrix, L=c.local;
  if(L.kind==='box'){
    const o=L.origin, s=L.lengths;
    const corners=[];
    for(let i=0;i<8;i++)
      corners.push(tf(org,m,[o[0]+((i&1)?s[0]:0), o[1]+((i&2)?s[1]:0), o[2]+((i&4)?s[2]:0)]));
    // 面：按位定义，法线用本地 ±XYZ 经旋转变换
    const defs=[
      {idx:[4,5,7,6], n:[0,0, 1]}, {idx:[0,2,3,1], n:[0,0,-1]},
      {idx:[2,6,7,3], n:[0, 1,0]}, {idx:[0,1,5,4], n:[0,-1,0]},
      {idx:[1,3,7,5], n:[ 1,0,0]}, {idx:[0,4,6,2], n:[-1,0,0]}
    ];
    c.faces = defs.map(function(d){
      const pts=d.idx.map(function(i){ return corners[i]; });
      return {pts:pts, n:norm([m[0]*d.n[0]+m[1]*d.n[1]+m[2]*d.n[2],
                               m[3]*d.n[0]+m[4]*d.n[1]+m[5]*d.n[2],
                               m[6]*d.n[0]+m[7]*d.n[1]+m[8]*d.n[2]])};
    });
    c.pts = corners;
  } else {
    const r=L.diameter/2;
    const A0=tf(org,m,L.origin);
    const B0=tf(org,m,[L.origin[0], L.origin[1], L.origin[2]+L.height]);
    const ax=norm(sub(B0,A0));
    let seed = Math.abs(ax[2])<0.9 ? [0,0,1] : [1,0,0];
    const u=norm(sub(seed,[ax[0]*dot(seed,ax),ax[1]*dot(seed,ax),ax[2]*dot(seed,ax)]));
    const v=cross(ax,u);
    const rim=function(ctr,t){ return [ctr[0]+r*(Math.cos(t)*u[0]+Math.sin(t)*v[0]),
                                        ctr[1]+r*(Math.cos(t)*u[1]+Math.sin(t)*v[1]),
                                        ctr[2]+r*(Math.cos(t)*u[2]+Math.sin(t)*v[2])]; };
    c.faces=[]; c.pts=[];
    for(let i=0;i<NRIM;i++){
      const t0=2*Math.PI*i/NRIM, t1=2*Math.PI*(i+1)/NRIM, tm=(t0+t1)/2;
      const nrm=[Math.cos(tm)*u[0]+Math.sin(tm)*v[0],
                 Math.cos(tm)*u[1]+Math.sin(tm)*v[1],
                 Math.cos(tm)*u[2]+Math.sin(tm)*v[2]];
      c.faces.push({pts:[rim(A0,t0),rim(A0,t1),rim(B0,t1),rim(B0,t0)], n:nrm});
      c.pts.push(rim(A0,t0)); c.pts.push(rim(B0,t0));
    }
    const capA=[], capB=[];
    for(let i=0;i<NRIM;i++){ const t=2*Math.PI*i/NRIM; capA.push(rim(A0,t)); capB.push(rim(B0,t)); }
    c.faces.push({pts:capA, n:[-ax[0],-ax[1],-ax[2]]});
    c.faces.push({pts:capB, n:ax});
    c.axis=[A0,B0];
  }
  // 质心
  const g=[0,0,0];
  c.pts.forEach(function(p){ g[0]+=p[0]; g[1]+=p[1]; g[2]+=p[2]; });
  c.center=[g[0]/c.pts.length, g[1]/c.pts.length, g[2]/c.pts.length];
});

/* ---- 拆解方向：从装配质心指向零件质心，再抬一点 ---- */
(function(){
  const g=[0,0,0]; let n=0;
  COMPS.forEach(function(c){ g[0]+=c.center[0]; g[1]+=c.center[1]; g[2]+=c.center[2]; n++; });
  g[0]/=n; g[1]/=n; g[2]/=n;
  COMPS.forEach(function(c){
    let d=sub(c.center,g); d[2]+=8;
    const L=Math.hypot(d[0],d[1],d[2])||1;
    const k=95/L;
    c.from=[d[0]*k, d[1]*k, d[2]*k];
  });
})();

/* ---- 投影 ---- */
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const CA=Math.cos(Math.PI/6), SA=Math.sin(Math.PI/6);
function proj(x,y,z){ return [CA*(x-y), SA*(x+y)-z]; }
let SC=1, OX=0, OY=0;
(function fit(){
  let a=1e9,b=-1e9,c=1e9,d=-1e9;
  COMPS.forEach(function(cc){
    cc.pts.forEach(function(p){
      const q=proj(p[0]+cc.from[0], p[1]+cc.from[1], p[2]+cc.from[2]);
      a=Math.min(a,q[0]); b=Math.max(b,q[0]); c=Math.min(c,q[1]); d=Math.max(d,q[1]);
      const r=proj(p[0],p[1],p[2]);
      a=Math.min(a,r[0]); b=Math.max(b,r[0]); c=Math.min(c,r[1]); d=Math.max(d,r[1]);
    });
  });
  const pad=46;
  SC=Math.min((cv.width-pad*2)/(b-a),(cv.height-pad*2)/(d-c));
  OX=pad-a*SC+(cv.width-pad*2-(b-a)*SC)/2;
  OY=pad-c*SC+(cv.height-pad*2-(d-c)*SC)/2;
})();
function P(p){ const q=proj(p[0],p[1],p[2]); return [q[0]*SC+OX, q[1]*SC+OY]; }

function hex2rgb(h){ const s=h.replace('#','');
  return [parseInt(s.slice(0,2),16), parseInt(s.slice(2,4),16), parseInt(s.slice(4,6),16)]; }
function shade(h, br){
  const c=hex2rgb(h), k=0.32+0.68*Math.max(0,Math.min(1,br));
  return 'rgb('+Math.round(c[0]*k)+','+Math.round(c[1]*k)+','+Math.round(c[2]*k)+')';
}

const NP=COMPS.length;
function offFor(i,t){
  const a=i/NP, b=(i+1)/NP;
  let u=(t-a)/(b-a); u=Math.max(0,Math.min(1,u));
  u = u<0.5 ? 2*u*u : 1-Math.pow(-2*u+2,2)/2;
  const k=1-u, f=COMPS[i].from;
  return [f[0]*k, f[1]*k, f[2]*k];
}

function drawGrid(){
  ctx.strokeStyle='#e8eaee'; ctx.lineWidth=1;
  for(let g=-60;g<=60;g+=20){
    const a=P([g,-40,0]), b=P([g,40,0]);
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    const c=P([-60,g,0]), d=P([60,g,0]);
    ctx.beginPath(); ctx.moveTo(c[0],c[1]); ctx.lineTo(d[0],d[1]); ctx.stroke();
  }
}

let t=0, playing=false, lastT=0, acc=0;
function render(){
  ctx.clearRect(0,0,cv.width,cv.height);
  drawGrid();

  // 收集所有面（含爆炸偏移），按深度从远到近画
  const all=[];
  COMPS.forEach(function(c,i){
    const f=offFor(i,t);
    c.faces.forEach(function(face){
      const pts=face.pts.map(function(p){ return [p[0]+f[0],p[1]+f[1],p[2]+f[2]]; });
      let dep=0; pts.forEach(function(p){ dep+=dot(p,CAM); }); dep/=pts.length;
      all.push({pts:pts, n:face.n, col:c.color, dep:dep});
    });
  });
  all.sort(function(x,y){ return x.dep-y.dep; });
  all.forEach(function(f){
    const q=f.pts.map(P);
    ctx.beginPath();
    q.forEach(function(p,i){ i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]); });
    ctx.closePath();
    ctx.fillStyle=shade(f.col, dot(f.n,CAM));
    ctx.fill();
    ctx.strokeStyle='rgba(0,0,0,0.22)'; ctx.lineWidth=0.6; ctx.stroke();
  });

  // 未就位的零件画一条虚线连回目标位
  let placed=0;
  COMPS.forEach(function(c,i){
    const f=offFor(i,t);
    if(Math.abs(f[0])<0.5&&Math.abs(f[1])<0.5&&Math.abs(f[2])<0.5){ placed++; return; }
    const a=P([c.center[0]+f[0],c.center[1]+f[1],c.center[2]+f[2]]), b=P(c.center);
    ctx.setLineDash([4,4]); ctx.strokeStyle=c.color; ctx.lineWidth=1.2; ctx.globalAlpha=.55;
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha=1;
  });

  document.getElementById('stat').innerHTML =
    '装配进度 '+(t*100).toFixed(0)+'%　｜　已就位 '+placed+' / '+NP+' 个组件<br>'
    + (placed===NP ? '<span class="ok">✔ 装配完成（坐标全部来自 NX）</span>'
                   : '正在装入：'+(COMPS[placed]?COMPS[placed].name:'—'));
  document.getElementById('bar').value=Math.round(t*1000);
}

/* ---- 渲染结果 vs NX 实测包围盒 ---- */
(function verify(){
  const key=function(p){ return p.map(function(v){ return v.toFixed(2); }).join(','); };
  const nxmap={};
  (B.components||[]).forEach(function(r){
    const bb=(r.bodies&&r.bodies[0]&&r.bodies[0].asm)?r.bodies[0].asm:null;
    if(bb) nxmap[key(r.origin)]=bb;
  });
  let html='<table><tr><th>零件</th><th>NX 实测</th><th>本页渲染</th><th>最大偏差</th><th></th></tr>';
  let allok=true;
  COMPS.forEach(function(c){
    const mn=[1e9,1e9,1e9], mx=[-1e9,-1e9,-1e9];
    c.pts.forEach(function(p){
      for(let i=0;i<3;i++){ mn[i]=Math.min(mn[i],p[i]); mx[i]=Math.max(mx[i],p[i]); }
    });
    const bb=nxmap[key(c.origin)];
    if(!bb){ html+='<tr><td>'+c.name+'</td><td colspan="4">NX 没给包围盒</td></tr>'; return; }
    let dev=0;
    for(let i=0;i<3;i++){
      dev=Math.max(dev, Math.abs(mn[i]-bb.min[i]), Math.abs(mx[i]-bb.max[i]));
    }
    const ok = dev < 0.05;
    if(!ok) allok=false;
    const f=function(v){ return v.map(function(x){ return x.toFixed(1); }).join(','); };
    html+='<tr><td>'+c.name+'</td><td>['+f(bb.min)+' → '+f(bb.max)+']</td>'
        + '<td>['+f(mn)+' → '+f(mx)+']</td>'
        + '<td>'+dev.toFixed(3)+'mm</td>'
        + '<td class="'+(ok?'ok':'bad')+'">'+(ok?'✔':'✘')+'</td></tr>';
  });
  html+='</table>';
  html+='<div style="margin-top:6px" class="'+(allok?'ok':'bad')+'">'
      + (allok?'✔ 全部一致：画出来的就是 NX 里建的那一个'
              :'✘ 有零件对不上 —— 别信这张图，去查 NX 那边')+'</div>';
  document.getElementById('ver').innerHTML=html;
})();

/* ---- 零件清单 ---- */
(function list(){
  const pl=document.getElementById('pl');
  COMPS.forEach(function(c){
    const d=document.createElement('div'); d.className='prow';
    const dim = c.local.kind==='box'
      ? c.local.lengths[0]+'×'+c.local.lengths[1]+'×'+c.local.lengths[2]+'mm'
      : 'Ø'+c.local.diameter+'×'+c.local.height+'mm';
    const rot = (c.matrix[0]===1&&c.matrix[4]===1&&c.matrix[8]===1)
      ? '无旋转' : '<b>有旋转（加工姿态→装配姿态）</b>';
    d.innerHTML='<span class="sw" style="background:'+c.color+'"></span> '
      + '<b>'+c.name+'</b>　<span class="pdim">'+c.part_file+'</span><br>'
      + '<span class="pdim">　'+dim+'　'+rot+'</span><br>'
      + '<span class="pdim">　工艺：'+(c.machining||'—')+'</span>';
    pl.appendChild(d);
  });
})();

function loop(ts){
  if(playing){
    if(!lastT) lastT=ts;
    acc+=(ts-lastT);
    while(acc>16){ t+=0.0030; acc-=16; }
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


# ------------------------------------------------------------------ 离线校验
def _tf(org, m, p):
    return (org[0] + m[0] * p[0] + m[1] * p[1] + m[2] * p[2],
            org[1] + m[3] * p[0] + m[4] * p[1] + m[5] * p[2],
            org[2] + m[6] * p[0] + m[7] * p[1] + m[8] * p[2])


def rendered_bbox(comp, n_rim=40):
    """按渲染器同样的算法算出包围盒（和 HTML 里的 JS 逻辑一致）。"""
    org, m, loc = comp["origin"], comp["matrix"], comp["local"]
    pts = []
    if loc["kind"] == "box":
        o, s = loc["origin"], loc["lengths"]
        for i in range(8):
            pts.append(_tf(org, m, (o[0] + (s[0] if i & 1 else 0),
                                    o[1] + (s[1] if i & 2 else 0),
                                    o[2] + (s[2] if i & 4 else 0))))
    else:
        r = loc["diameter"] / 2.0
        a0 = _tf(org, m, tuple(loc["origin"]))
        b0 = _tf(org, m, (loc["origin"][0], loc["origin"][1],
                          loc["origin"][2] + loc["height"]))
        ax = (b0[0] - a0[0], b0[1] - a0[1], b0[2] - a0[2])
        L = math.sqrt(sum(v * v for v in ax)) or 1.0
        ax = tuple(v / L for v in ax)
        seed = (0.0, 0.0, 1.0) if abs(ax[2]) < 0.9 else (1.0, 0.0, 0.0)
        d = sum(seed[i] * ax[i] for i in range(3))
        u = tuple(seed[i] - ax[i] * d for i in range(3))
        ul = math.sqrt(sum(v * v for v in u)) or 1.0
        u = tuple(v / ul for v in u)
        v = (ax[1] * u[2] - ax[2] * u[1], ax[2] * u[0] - ax[0] * u[2],
             ax[0] * u[1] - ax[1] * u[0])
        for i in range(n_rim):
            t = 2 * math.pi * i / n_rim
            cs, sn = math.cos(t), math.sin(t)
            for ctr in (a0, b0):
                pts.append((ctr[0] + r * (cs * u[0] + sn * v[0]),
                            ctr[1] + r * (cs * u[1] + sn * v[1]),
                            ctr[2] + r * (cs * u[2] + sn * v[2])))
    mn = [min(p[i] for p in pts) for i in range(3)]
    mx = [max(p[i] for p in pts) for i in range(3)]
    return mn, mx


def verify(asm, bbox):
    """渲染包围盒 vs NX 实测包围盒。返回 (ok, 明细列表)。"""
    nx = {}
    for r in (bbox.get("components") or []):
        bodies = r.get("bodies") or []
        if bodies and bodies[0].get("asm"):
            nx[tuple(round(v, 2) for v in r["origin"])] = bodies[0]["asm"]
    rows = []
    ok = True
    for c in asm.get("components") or []:
        key = tuple(round(v, 2) for v in c["origin"])
        bb = nx.get(key)
        if bb is None:
            rows.append((c["name"], None, None, None, False))
            ok = False
            continue
        mn, mx = rendered_bbox(c)
        dev = 0.0
        for i in range(3):
            dev = max(dev, abs(mn[i] - bb["min"][i]), abs(mx[i] - bb["max"][i]))
        good = dev < 0.05
        ok = ok and good
        rows.append((c["name"], bb, (mn, mx), dev, good))
    return ok, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 NX 导出的数据渲染装配可视化")
    ap.add_argument("--asm", required=True, help="assembly_from_nx.json")
    ap.add_argument("--bbox", required=True, help="assembly_bbox_from_nx.json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--no-html", action="store_true",
                    help="只做几何核对，不生成网页（★ 默认工作流：可视化交给 NX，"
                         "这里只用来验证「NX 建的东西」和「几何数据」对不对）")
    ns = ap.parse_args(argv)
    if ns.no_html and not ns.out:
        ns.out = None

    for f in (ns.asm, ns.bbox):
        if not os.path.isfile(f):
            print("找不到文件: %s" % f)
            return 2
    with open(ns.asm, "r", encoding="utf-8") as fh:
        asm = json.load(fh)
    with open(ns.bbox, "r", encoding="utf-8") as fh:
        bbox = json.load(fh)

    ok, rows = verify(asm, bbox)

    print("=" * 72)
    print("渲染前先自检：本页要画的 vs NX 实测包围盒")
    print("=" * 72)
    print("%-12s %-34s %-34s %s" % ("零件", "NX 实测（NX 说的）", "本页渲染", "偏差"))
    for name, bb, mine, dev, good in rows:
        if bb is None:
            print("  %-10s ✘ NX 没给包围盒（无法验证）" % name)
            continue
        f = lambda v: "[%s → %s]" % (",".join("%.1f" % x for x in v[0]),
                                     ",".join("%.1f" % x for x in v[1]))
        print("  %-10s %-32s %-32s %.3fmm %s"
              % (name, f((bb["min"], bb["max"])), f(mine), dev, "✔" if good else "✘"))

    if ns.no_html:
        print("-" * 72)
        print("（--no-html：只核对，不出网页。装配可视化由 NX 自己做："
              "分步截图 + 爆炸图）")
    else:
        if not ns.out:
            print("需要 -o/--out（或者加 --no-html 只做核对）")
            return 2
        src = ("装配 %s ｜ 位置/朝向 = NX Component.GetPosition() "
               "｜ 尺寸校验 = NX UF.ModlGeneral.AskBoundingBox()" % asm.get("assembly"))
        html = (TEMPLATE.replace("__ASM__", json.dumps(asm, ensure_ascii=False))
                        .replace("__BBOX__", json.dumps(bbox, ensure_ascii=False))
                        .replace("__SOURCE__", src))
        out = os.path.abspath(ns.out)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print("-" * 72)
        print("已生成: %s（%.0f KB）" % (out, os.path.getsize(out) / 1024.0))

    if not ok:
        print("\n✘ 渲染结果和 NX 对不上 —— 这张图不可信，别拿去给人看。")
        return 1
    print("\n✔ 全部一致：画出来的就是 NX 里建的那一个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
