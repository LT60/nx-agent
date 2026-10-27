# -*- coding: utf-8 -*-
"""把 CLSF 刀位文件变成**真正的材料切除仿真**（不是只画线）。

和 clsf_to_animation.py 的区别：那个只画刀具轨迹，看不出材料被削掉多少，
也就查不了撞刀。这个用 **dexel 高度场**（三轴铣削仿真的经典做法，
VERICUT 那类软件就是这么干的）逐点把毛坯削掉，于是能：

  1. 看到材料一刀刀被削掉、零件逐渐成形
  2. **自动判撞刀**：快移段（RAPID）如果刀尖插进了材料 -> 直接报出来
  3. **自动判漏切**：播完后比对目标形状，还高的地方标橙 = 没切到位

用法：
    python tools/clsf_to_sim.py <in.clsf> [-o out.html] [--max-points 4000] [--grid 1.0]

纯标准库，不依赖 NX / ffmpeg / PIL。
"""

from __future__ import print_function

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clsf_to_animation import parse_clsf, thin  # noqa: E402

HTML = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; font-family:"Microsoft YaHei","PingFang SC",sans-serif;
         background:#f6f7f9; color:#1f2328; }
  h1 { font-size:17px; margin:0 0 3px; font-weight:600; }
  .sub { font-size:12px; color:#6b7280; margin-bottom:12px; line-height:1.7; }
  .wrap { display:flex; gap:14px; align-items:flex-start; flex-wrap:wrap; }
  canvas { background:#fff; border:1px solid #d7dbe0; border-radius:8px; display:block; }
  .panel { flex:1; min-width:280px; font-size:13px; }
  button { font-size:13px; padding:6px 14px; margin:0 6px 6px 0; border:1px solid #c9ced6;
           background:#fff; border-radius:6px; cursor:pointer; }
  button:hover { background:#eef1f5; }
  button.on { background:#2563eb; color:#fff; border-color:#2563eb; }
  input[type=range] { width:100%; }
  label { display:block; font-size:12px; color:#4b5563; margin:10px 0 3px; }
  .stat { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#374151;
          background:#fff; border:1px solid #d7dbe0; border-radius:6px;
          padding:8px 10px; margin-top:10px; line-height:1.85; }
  .alert { margin-top:10px; padding:8px 10px; border-radius:6px; font-size:12px; line-height:1.7; }
  .ok { background:#ecfdf5; border:1px solid #a7f3d0; color:#065f46; }
  .bad { background:#fef2f2; border:1px solid #fecaca; color:#991b1b; }
  .warn { background:#fffbeb; border:1px solid #fde68a; color:#92400e; }
  .legend div { display:flex; align-items:center; gap:7px; margin:4px 0; font-size:12px; }
  .sw { width:15px; height:11px; border-radius:2px; border:1px solid rgba(0,0,0,.15); }
  .hint { font-size:12px; color:#6b7280; margin-top:12px; line-height:1.75; }
  .kv { display:flex; justify-content:space-between; }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<div class="sub">
  这是<b>切除仿真</b>：用 dexel 高度场逐点把毛坯削掉（和 VERICUT 类软件同一思路）。
  能看到材料一层层消失、零件逐渐成形 —— 而不是只画一条轨迹线。
  原始刀位 __TOTAL__ 点，这里抽稀到 __USED__ 点。
</div>

<div class="wrap">
  <canvas id="cv" width="780" height="560"></canvas>
  <div class="panel">
    <div>
      <button id="play">播放</button>
      <button id="reset">重来</button>
      <button id="end">直接看结果</button>
    </div>
    <label>进度（拖到任意时刻看当时的切除状态）</label>
    <input type="range" id="bar" min="0" max="1000" value="0">
    <label>速度 <span id="spdv">1.0×</span></label>
    <input type="range" id="spd" min="1" max="40" value="10">

    <div class="legend" style="margin-top:12px">
      <div><span class="sw" style="background:#93a3b8"></span>毛坯（还没切到）</div>
      <div><span class="sw" style="background:#5b8def"></span>已切到位</div>
      <div><span class="sw" style="background:#f59e0b"></span>还剩料（漏切 / 未切到位）</div>
      <div><span class="sw" style="background:#dc2626"></span>刀具当前位置</div>
    </div>

    <div class="stat" id="stat"></div>
    <div id="hit" class="alert ok">碰撞检测：等待开始…</div>
    <div id="miss" class="alert ok">漏切检测：等待开始…</div>

    <div class="hint">
      <b>怎么判断有问题</b><br>
      · <b>撞刀</b>：快移（抬刀换位）时刀尖插进材料 —— 程序自动检测并报警<br>
      · <b>漏切</b>：播完后还有橙色区域 = 该切掉的料没切掉<br>
      · <b>过切</b>：切到低于目标形状（本演示目标面以下无材料，故不判）
    </div>
  </div>
</div>

<script>
const D = __DATA__;
const ops = D.ops, B = D.blank, G = D.grid;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');

const NX = Math.round((B.x1-B.x0)/G), NY = Math.round((B.y1-B.y0)/G);
const N  = NX*NY;
let H = new Float32Array(N);          // 当前材料顶面高度
const TGT = new Float32Array(N);      // 目标（零件）顶面高度
const H0  = new Float32Array(N);      // 毛坯初始高度

/* ★ 目标（零件）顶面高度。
   HAS_TGT = true 时用外部传入的 TGT_IN（由零件网格 STL 自动算出来）；
   false 时**不做漏切判定** —— 以前这里硬编码的是企业号的形状，
   换个零件就会给出错误结论（2026-10-10 发现并改掉）。 */
const HAS_TGT = __HAS_TGT__;
const TGT_IN = __TGT__;
(function init(){
  for(let j=0;j<NY;j++) for(let i=0;i<NX;i++){
    const k = j*NX+i;
    H0[k] = B.z1; H[k] = B.z1;
    TGT[k] = HAS_TGT ? TGT_IN[k] : B.z1;
  }
})();

/* ---- 等轴测投影 ---- */
const CA = Math.cos(Math.PI/6), SA = Math.sin(Math.PI/6);
function proj(x,y,z){ return [CA*(x-y), SA*(x+y) - z]; }
(function fit(){
  let a=1e9,b=-1e9,c=1e9,d=-1e9;
  [[B.x0,B.y0,B.z0],[B.x1,B.y1,B.z1],[B.x0,B.y1,B.z1],[B.x1,B.y0,B.z0]]
    .forEach(p=>{ const q=proj(p[0],p[1],p[2]);
      a=Math.min(a,q[0]); b=Math.max(b,q[0]); c=Math.min(c,q[1]); d=Math.max(d,q[1]); });
  const pad=42;
  const s=Math.min((cv.width-pad*2)/(b-a), (cv.height-pad*2)/(d-c));
  window.SC = s; window.OX = pad - a*s + (cv.width-pad*2-(b-a)*s)/2;
  window.OY = pad - c*s + (cv.height-pad*2-(d-c)*s)/2;
})();
function P(x,y,z){ const q=proj(x,y,z); return [q[0]*SC+OX, q[1]*SC+OY]; }

/* ---- 切削 / 碰撞 ---- */
let collisions = [];
let prevCut = null;
/* ★ 刀位点是抽稀过的（8 万 -> 7500），相邻两点可能隔着好几格。
   刀具是**连续扫过去**的，不是瞬移，所以要沿轨迹补点，
   否则会漏掉中间格子、被误判成「漏切」。 */
function cutTo(x,y,z,r){
  if(prevCut){
    const dx=x-prevCut[0], dy=y-prevCut[1], dz=z-prevCut[2];
    const dist=Math.sqrt(dx*dx+dy*dy);
    const steps=Math.min(400, Math.max(1, Math.ceil(dist/G)));
    for(let s=1;s<=steps;s++){
      const t=s/steps;
      cut(prevCut[0]+dx*t, prevCut[1]+dy*t, prevCut[2]+dz*t, r);
    }
  } else {
    cut(x,y,z,r);
  }
  prevCut=[x,y,z];
}
function cut(x,y,z,r){
  const r2=r*r;
  let i0=Math.floor((x-r-B.x0)/G), i1=Math.ceil((x+r-B.x0)/G);
  let j0=Math.floor((y-r-B.y0)/G), j1=Math.ceil((y+r-B.y0)/G);
  i0=Math.max(0,i0); i1=Math.min(NX-1,i1); j0=Math.max(0,j0); j1=Math.min(NY-1,j1);
  for(let j=j0;j<=j1;j++) for(let i=i0;i<=i1;i++){
    const cx=B.x0+i*G, cy=B.y0+j*G;
    const dx=cx-x, dy=cy-y;
    if(dx*dx+dy*dy > r2) continue;
    const k=j*NX+i;
    if(z < H[k]) H[k] = z;      // 平底刀：材料顶面被削到刀尖高度
  }
}
function hitsMaterial(x,y,z,r){   // 快移时刀尖插进材料 = 撞刀
  const r2=r*r;
  let i0=Math.max(0,Math.floor((x-r-B.x0)/G)), i1=Math.min(NX-1,Math.ceil((x+r-B.x0)/G));
  let j0=Math.max(0,Math.floor((y-r-B.y0)/G)), j1=Math.min(NY-1,Math.ceil((y+r-B.y0)/G));
  for(let j=j0;j<=j1;j++) for(let i=i0;i<=i1;i++){
    const cx=B.x0+i*G, cy=B.y0+j*G, dx=cx-x, dy=cy-y;
    if(dx*dx+dy*dy > r2) continue;
    if(z < H[j*NX+i] - 2.0) return true;   // 2mm 容差：滤掉抽稀/模型误差造成的伪影
  }
  return false;
}

/* ---- 重建到某个进度 ---- */
let upto = -1;
function rebuild(target){
  if(target === upto) return;
  if(target < upto){                       // 往回拖只能从头算
    for(let k=0;k<N;k++) H[k] = H0[k];
    upto = -1; collisions = []; prevCut = null;
  }
  let n = upto + 1;
  for(let oi=0; oi<ops.length && n<=target; oi++){
    const o = ops[oi], r = (o.dia||4)/2;
    const end = Math.min(o.pts.length, target - n + 1);
    for(let k=0;k<end;k++){
      const p = o.pts[k];
      if(p[3] === 1){                      // RAPID 快移：不切削，但要查撞
        if(hitsMaterial(p[0],p[1],p[2],r)) collisions.push([p[0],p[1],p[2],oi]);
        prevCut = null;                    // 抬刀换位了，下一段重新起刀
      } else {
        cutTo(p[0],p[1],p[2],r);
      }
      n++;
    }
  }
  upto = target;
}

/* ---- 渲染 ---- */
function render(){
  ctx.clearRect(0,0,cv.width,cv.height);
  // 毛坯轮廓
  (function(){
    const c=[[B.x0,B.y0,B.z0],[B.x1,B.y0,B.z0],[B.x1,B.y1,B.z0],[B.x0,B.y1,B.z0]];
    const p=c.map(v=>P(v[0],v[1],v[2]));
    ctx.strokeStyle='#d1d5db'; ctx.lineWidth=1; ctx.beginPath();
    p.forEach((q,i)=> i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1]));
    ctx.closePath(); ctx.stroke();
  })();

  // 从后往前画每个 dexel 的顶面（painter）
  for(let j=0;j<NY;j++) for(let i=0;i<NX;i++){
    const k=j*NX+i, h=H[k];
    if(h <= 0.05) continue;                 // 切没了
    const x=B.x0+i*G, y=B.y0+j*G;
    const g=G*1.02;
    const a=P(x,y,h), b=P(x+g,y,h), c=P(x+g,y+g,h), d=P(x,y+g,h);
    const remain = h - TGT[k];
    let col;
    if(remain > 1.0)      col = '#f59e0b';  // 还剩料 = 漏切
    else if(h >= H0[k]-0.01) col = '#93a3b8'; // 完全没动过 = 毛坯
    else                  col = '#5b8def';  // 已切到位
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]);
    ctx.lineTo(c[0],c[1]); ctx.lineTo(d[0],d[1]); ctx.closePath(); ctx.fill();
  }

  // 当前刀具
  const f = frameFor(upto<0?0:upto);
  const o = ops[f.oi], pt = o.pts[Math.min(f.k,o.pts.length-1)];
  const tip=P(pt[0],pt[1],pt[2]), top=P(pt[0],pt[1],pt[2]+24);
  ctx.strokeStyle='#dc2626'; ctx.lineWidth=3; ctx.beginPath();
  ctx.moveTo(tip[0],tip[1]); ctx.lineTo(top[0],top[1]); ctx.stroke();
  ctx.fillStyle='#dc2626'; ctx.beginPath(); ctx.arc(tip[0],tip[1],4.5,0,6.284); ctx.fill();

  // 碰撞点
  collisions.slice(0,60).forEach(c=>{
    const p=P(c[0],c[1],c[2]);
    ctx.strokeStyle='#dc2626'; ctx.lineWidth=2; ctx.beginPath();
    ctx.arc(p[0],p[1],7,0,6.284); ctx.stroke();
  });

  stat(f, o, pt);
  document.getElementById('bar').value = Math.round((upto+1)/Math.max(1,TOTAL)*1000);
}

function frameFor(i){
  let n=i, oi=0;
  for(; oi<ops.length; oi++){ if(n < ops[oi].pts.length) break; n -= ops[oi].pts.length; }
  return { oi: Math.min(oi,ops.length-1), k: n };
}

const TOTAL = ops.reduce((s,o)=>s+o.pts.length,0);
const COLORS=['#2563eb','#059669','#d97706','#7c3aed','#0891b2'];

function stat(f,o,pt){
  let left=0, done=0;
  for(let k=0;k<N;k++){ const r=H[k]-TGT[k]; if(r>1.0) left++; else done++; }
  const pct = (done/Math.max(1,done+left)*100).toFixed(1);
  document.getElementById('stat').innerHTML =
    '<span class="kv"><span>工序</span><b style="color:'+(COLORS[f.oi%5])+'">'+o.name+'</b></span>'
    + '<span class="kv"><span>刀具</span><span>'+(o.dia?o.dia.toFixed(1):'?')+' mm</span></span>'
    + '<span class="kv"><span>刀尖 Z</span><span>'+pt[2].toFixed(1)+'</span></span>'
    + '<span class="kv"><span>进度</span><span>'+(upto+1)+' / '+TOTAL+'</span></span>'
    + '<span class="kv"><span>已到位</span><span>'+pct+'%</span></span>';

  const hEl = document.getElementById('hit');
  if(collisions.length){
    hEl.className='alert bad';
    hEl.innerHTML='<b>⚠ 检测到 '+collisions.length+' 处疑似撞刀</b><br>'
      + '快移（抬刀换位）时刀尖插进了材料。最早一处：X '
      + collisions[0][0].toFixed(1)+' Y '+collisions[0][1].toFixed(1)
      + ' Z '+collisions[0][2].toFixed(1)+'<br>（画面上红圈标出）';
  } else {
    hEl.className='alert ok'; hEl.innerHTML='碰撞检测：目前未发现撞刀 ✔';
  }

  const mEl = document.getElementById('miss');
  if(!HAS_TGT){
    mEl.className='alert warn';
    mEl.innerHTML='<b>未提供目标形状</b> —— 这一份只做撞刀检测。'
      + '<br>要比对"该切掉的料有没有切掉"，请给 <code>--target-stl</code>'
      + '（零件网格）和 <code>--mcs</code>（NX 的 MCS 原点）。';
  } else if(upto >= TOTAL-2){
    if(left > 0){
      mEl.className='alert bad';
      mEl.innerHTML='<b>⚠ 还有 '+left+' 个格子没切到位</b>（画面上的橙色）'
        + '<br>可能是刀具够不到（比如深腔、窄角）或工序没覆盖';
    } else {
      mEl.className='alert ok'; mEl.innerHTML='漏切检测：全部切到位 ✔';
    }
  } else {
    mEl.className='alert ok'; mEl.innerHTML='漏切检测：切完才判（现在是 '+pct+'% 到位）';
  }
}

/* ---- 播放控制 ---- */
let playing=false, speed=1.0, lastT=0, acc=0;
function step(){ if(upto < TOTAL-1){ rebuild(upto+1); } else stopPlay(); }
function stopPlay(){ playing=false; lastT=0;
  const b=document.getElementById('play'); b.textContent='播放'; b.classList.remove('on'); }
function loop(t){
  if(playing){
    if(!lastT) lastT=t;
    acc += (t-lastT);
    const per=Math.max(1, Math.round(5*speed));
    while(acc>16){ for(let i=0;i<per && upto<TOTAL-1;i++) step(); acc-=16; }
    render();
    if(upto>=TOTAL-1) stopPlay();
  }
  lastT=t; requestAnimationFrame(loop);
}
document.getElementById('play').onclick=function(){
  if(upto>=TOTAL-1) rebuild(-1), rebuild(0);
  playing=!playing; lastT=0;
  this.textContent = playing?'暂停':'播放'; this.classList.toggle('on',playing);
};
document.getElementById('reset').onclick=function(){ stopPlay(); rebuild(-1); upto=-1; rebuild(0); render(); };
document.getElementById('end').onclick=function(){ stopPlay(); rebuild(TOTAL-1); render(); };
document.getElementById('bar').oninput=function(){
  stopPlay(); rebuild(Math.round(this.value/1000*(TOTAL-1))); render();
};
document.getElementById('spd').oninput=function(){
  speed=this.value/10; document.getElementById('spdv').textContent=speed.toFixed(1)+'×';
};

rebuild(0); render(); requestAnimationFrame(loop);
</script>
</body>
</html>
"""


def stl_top_height(path, x0, y0, nx, ny, g, off=None):
    """★ 从零件网格 STL 算出每个格子的目标顶面高度（不再硬编码形状）。

    做法：遍历三角面，把每个面投到 (x,y) 网格上，按重心坐标插值算该格中心的高度，
    取最大值（= 从上方能看到的最高的面）。
    off = NX 的 MCS 原点；STL 坐标减去它才是 CLSF 坐标系。
    垂直面（在 XY 上退化成线）跳过 —— 它们不贡献"顶面"。
    """
    import struct as _struct

    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(84)
    n = _struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
    is_ascii = (84 + n * 50 != size)

    tris = []
    if is_ascii:
        cur = []
        with open(path, "r", errors="ignore") as fh:
            for line in fh:
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
                v = _struct.unpack("<12fH", rec)
                tris.append([list(v[3:6]), list(v[6:9]), list(v[9:12])])

    tgt = [0.0] * (nx * ny)
    hit = [False] * (nx * ny)
    for a, b, c in tris:
        ax, ay, az = a; bx, by, bz = b; cx, cy, cz = c
        if off:
            ax -= off[0]; ay -= off[1]; az -= off[2]
            bx -= off[0]; by -= off[1]; bz -= off[2]
            cx -= off[0]; cy -= off[1]; cz -= off[2]
        # 面法线的 z 分量（决定能不能当"顶面"）
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nz = ux * vy - uy * vx
        if abs(nz) < 1e-9:
            continue
        i0 = max(0, int((min(ax, bx, cx) - x0) / g))
        i1 = min(nx - 1, int((max(ax, bx, cx) - x0) / g) + 1)
        j0 = max(0, int((min(ay, by, cy) - y0) / g))
        j1 = min(ny - 1, int((max(ay, by, cy) - y0) / g) + 1)
        for j in range(j0, j1 + 1):
            py = y0 + (j + 0.5) * g
            for i in range(i0, i1 + 1):
                px = x0 + (i + 0.5) * g
                # 重心坐标判内外
                d = ((by - cy) * (ax - cx) + (cx - bx) * (ay - cy))
                if abs(d) < 1e-12:
                    continue
                l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                # ★ STL 三角面绕向不统一：d<0 时重心坐标整体反号，
                #   不修的话大量面会被判成"在外部"，目标形状会大面积缺失。
                if d < 0:
                    l1, l2 = -l1, -l2
                l3 = 1.0 - l1 - l2
                if l1 < -1e-6 or l2 < -1e-6 or l3 < -1e-6:
                    continue
                z = l1 * az + l2 * bz + l3 * cz
                k = j * nx + i
                if (not hit[k]) or z > tgt[k]:
                    tgt[k] = z
                    hit[k] = True
    # 没被任何面盖住的格子 = 该处最终无料 → 目标高度取一个远低于毛坯的值
    for k in range(len(tgt)):
        if not hit[k]:
            tgt[k] = -1e6
    return tgt


def main(argv=None):
    ap = argparse.ArgumentParser(description="CLSF -> 材料切除仿真 HTML（含撞刀/漏切自动检测）")
    ap.add_argument("clsf")
    ap.add_argument("-o", "--out")
    ap.add_argument("--max-points", type=int, default=4000)
    ap.add_argument("--grid", type=float, default=1.0, help="dexel 网格边长(mm)，越小越精细越慢")
    ap.add_argument("--title", help="页面标题（默认用 CLSF 文件名）")
    ap.add_argument("--target-stl", help="零件网格 STL：用它自动算出目标形状（不给就不判漏切）")
    ap.add_argument("--mcs", help="NX 的 MCS 原点 x,y,z（把 STL 坐标平移到 CLSF 坐标系），逗号分隔")
    ap.add_argument("--blank", help="毛坯 x0,y0,z0,x1,y1,z1（默认按刀位点范围留 3mm 余量）")
    ap.add_argument("--only", help="只保留名字里含该子串的工序，逗号分隔")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.clsf)
    if not os.path.isfile(src):
        print("找不到 CLSF: %s" % src)
        return 2

    ops, msys = parse_clsf(src)
    if not ops:
        print("没解析出刀路")
        return 3

    order, best = [], {}
    for o in ops:
        if o["name"] not in best:
            order.append(o["name"])
            best[o["name"]] = o
        elif len(o["pts"]) > len(best[o["name"]]["pts"]):
            best[o["name"]] = o
    ops = [best[n] for n in order]

    if args.only:
        keys = [k.strip().upper() for k in args.only.split(",") if k.strip()]
        kept = [o for o in ops if any(k in o["name"].upper() for k in keys)]
        if kept:
            print("只保留工序: %s" % [o["name"] for o in kept])
            ops = kept
        else:
            print("--only 没匹配到任何工序，保持全部")

    total = sum(len(o["pts"]) for o in ops)
    for o in ops:
        o["pts"] = thin(o["pts"], args.max_points)
    used = sum(len(o["pts"]) for o in ops)

    print("MSYS=%s ｜ %d 道工序 ｜ 原始 %d 点 -> %d 点 ｜ 网格 %.1fmm"
          % (msys, len(ops), total, used, args.grid))
    for o in ops:
        print("   · %-34s %s mm 刀 ｜ %d 点" % (o["name"], o["dia"], len(o["pts"])))

    # ★ 毛坯：以前硬编码成企业号的 100×60×42，换零件就错。
    #   现在默认按**刀位点范围**留 3mm 余量算出来。
    if args.blank:
        vals = [float(v) for v in args.blank.split(",")]
        blank = {"x0": vals[0], "y0": vals[1], "z0": vals[2],
                 "x1": vals[3], "y1": vals[4], "z1": vals[5]}
    elif args.target_stl and os.path.isfile(args.target_stl):
        # ★ 毛坯要跟着**零件**走，不能跟着刀路走 —— CLSF 里的快移会跑到很远的地方，
        #   按刀路范围算会把毛坯撑得巨大，目标形状只剩几个格子（2026-10-10 踩过）。
        import struct as _st
        with open(args.target_stl, "rb") as fh:
            head = fh.read(84)
        n_tri = _st.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
        asc = (84 + n_tri * 50 != os.path.getsize(args.target_stl))
        lo = [1e18] * 3
        hi = [-1e18] * 3
        with open(args.target_stl, "rb") as fh:
            if asc:
                for line in io.open(args.target_stl, "r", errors="ignore"):
                    t = line.strip()
                    if t.startswith("vertex"):
                        v = [float(x) for x in t.split()[1:4]]
                        for i in range(3):
                            lo[i] = min(lo[i], v[i]); hi[i] = max(hi[i], v[i])
            else:
                fh.seek(84)
                while True:
                    rec = fh.read(50)
                    if len(rec) < 50:
                        break
                    v = _st.unpack("<12fH", rec)
                    for k in range(3):
                        for i in range(3):
                            x = v[3 + k * 3 + i]
                            lo[i] = min(lo[i], x); hi[i] = max(hi[i], x)
        off = [float(x) for x in args.mcs.split(",")] if args.mcs else [0.0, 0.0, 0.0]
        blank = {"x0": lo[0] - off[0] - 5, "y0": lo[1] - off[1] - 5,
                 "z0": lo[2] - off[2] - 3,
                 "x1": hi[0] - off[0] + 5, "y1": hi[1] - off[1] + 5,
                 "z1": hi[2] - off[2] + 3}
        print("毛坯取自零件网格（%s）并留 5/3mm 余量" % os.path.basename(args.target_stl))
    else:
        xs = [p[0] for o in ops for p in o["pts"]]
        ys = [p[1] for o in ops for p in o["pts"]]
        zs = [p[2] for o in ops for p in o["pts"]]
        blank = {"x0": min(xs) - 3, "y0": min(ys) - 3, "z0": min(zs) - 2,
                 "x1": max(xs) + 3, "y1": max(ys) + 3, "z1": max(zs) + 3}
    print("毛坯（按刀位点范围+余量）= X[%.1f,%.1f] Y[%.1f,%.1f] Z[%.1f,%.1f]"
          % (blank["x0"], blank["x1"], blank["y0"], blank["y1"],
             blank["z0"], blank["z1"]))

    # ★ 目标形状：有零件网格就算，没有就不判漏切
    nx = max(1, int(round((blank["x1"] - blank["x0"]) / args.grid)))
    ny = max(1, int(round((blank["y1"] - blank["y0"]) / args.grid)))
    tgt = None
    if args.target_stl:
        if not os.path.isfile(args.target_stl):
            print("找不到 --target-stl: %s（改为不判漏切）" % args.target_stl)
        else:
            off = [0.0, 0.0, 0.0]
            if args.mcs:
                off = [float(v) for v in args.mcs.split(",")]
            print("目标形状：从 %s 算（MCS 偏移 %s，网格 %d×%d）"
                  % (os.path.basename(args.target_stl), off, nx, ny))
            # 先把 STL 平移到 CLSF 坐标系：clsf = stl - mcs
            tgt = stl_top_height(args.target_stl, blank["x0"], blank["y0"],
                                 nx, ny, args.grid, off=off)
            print("  目标形状已算出（非空网格 %d 个）" % sum(1 for v in tgt if v > -1e5))

    payload = {"ops": ops, "blank": blank, "grid": args.grid}

    title = args.title or ("%s · 三轴铣削材料切除仿真"
                           % os.path.splitext(os.path.basename(src))[0])
    html = (HTML.replace("__DATA__", json.dumps(payload))
                .replace("__TOTAL__", "{:,}".format(total))
                .replace("__USED__", "{:,}".format(used))
                .replace("__TITLE__", title)
                .replace("__HAS_TGT__", "true" if tgt else "false")
                .replace("__TGT__", json.dumps(tgt) if tgt else "[]"))

    out = os.path.abspath(args.out) if args.out else \
        os.path.join(os.path.dirname(src), "machining_sim.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已生成: %s（%.0f KB）" % (out, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
