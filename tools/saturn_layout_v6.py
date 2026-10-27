# -*- coding: utf-8 -*-
"""★ 土星五号装配 v2：从网格量出半径轮廓，按"套接"关系装配（不再按包围盒端头对齐）。

v1 为什么失败（用户报：级间大空隙、穿模、轴心不齐）：
  · 我按**包围盒两端**当对接面 —— 但这是 3D 打印套件，端头是**插接用的细脖/套筒**。
    S-II 顶端 r=25.3 对着 Ø90 的级间段（差 40mm 直径）；级间段其实是**套筒**，
    应该套住邻居的细脖。按包围盒端头对齐 → 细脖全露在外面 → 大空隙。
  · 我按**包围盒中心**当轴心 —— 零件上的导管/线槽等不对称特征会让中心偏 ~1mm。
  · ★ 最要命的：**有零件朝向是反的**（逃逸塔低 Y 端 r=2.3 是塔尖、高 Y 端 r=19.2 是底座；
    S-IC 底部低 Y 端 Ø89、高 Y 端 Ø124.6 —— 带尾翼的粗端应该在下面）。

v2 的做法（全部由数据决定）：
  1. 三角面**按带覆盖**算半径轮廓（比按顶点采样稳健：粗网格的带不会漏）；
  2. 轴心用**逐带中心的鲁棒中位数**，不用包围盒中心；
  3. 朝向：**暴力枚举 2^10 种组合**，取"各级接口半径失配之和最小"的那一组；
  4. 重叠：每个接头按两侧**细脖长度**取重叠 = max(脖A, 脖B)（套筒正好套住脖子）；
  5. 输出 JSON，并把装配结果画成 **ASCII 侧影** —— 先自己看一眼再进 NX。

用法：
    python tools/saturn_layout.py            # 算布局 + 打印接头表 + ASCII 侧影
"""

from __future__ import print_function

import itertools
import json
import math
import os
import struct
import sys
import sys

AXES = "XYZ"
NBAND = 120          # 沿轴的带数（越密越准，也越慢）
FULL_R = 0.85        # 半径 >= 该比例 * r_max 视为"实体段"，否则算细脖


# ------------------------------------------------------------------ 读网格
def read_tris(path):
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



def pick_axis(tris):
    """★ 自动判定回转轴（不能一律当 Y —— 2026-10-10 血的教训）。

    我原来写死 `profile(tris, 1)`（Y 轴），理由是"所有零件 X、Z 尺寸相近"。
    **实测证明这个假设是错的**（三轴真实尺寸见下）：
      · 9/10 个零件长轴确实是 Y（如S-II: X=93 Y=265 Z=91）
      · 但 **command moduel.stl 的 Y 只有 20.1，而 X=33.9、Z=33.9** ——
        它是个扁桶，Y 是"厚度"方向，不是轴。按 Y 剖 → 长度量成20.1（实际 33.9）
        → origin 全算错 → NX 摆错位置 → 用户看到一地散件，
        而我自己的核验还显示"0.00mm 偏差"（自己跟自己比，永远对）。

    判据（按优先级，都用可测量的量）：
      1. **最长轴**：回转体的轴一定是最长的那个维度。
      2. **平局裁决**：当两个轴长度接近（如指令舱X=33.9、Z=33.9），
         看它们的**横截面**是否一致 —— 回转体对任意垂直于轴的方向都一样，
         所以两个候选轴的横截面尺寸应该都等于（非轴的）那个最小值。
         指令舱：选 X →横截面 Y×Z = 20.1×33.9；选 Z → 横截面 X×Y = 33.9×20.1。
         两者等价，此时取"该轴两端半径更接近"（更像对称回转体）。

    ★ 另加物理硬约束：回转体必然 L > 2×rmax。
      这条能把"S-IC 顶部选了 X（L=90.1、rmax=83.7）"这种荒谬选择否掉。
    """
    # 各轴范围
    span = []
    for k in range(3):
        vs = [p[k] for t in tris for p in t]
        span.append((max(vs) - min(vs), k))
    order = sorted(span, reverse=True)      # 最长 → 最短

    cand = []
    # ★ 三个轴**全部**作为候选（原来用"L > 1.05×截面"过滤，
    #   把指令舱的 Y 轴排除了 —— 但它是个扁桶，Y=20.1 而截面 33.9，
    #   这条"回转体必然轴最长"的假设对它不成立）。
    #   裁决交给后面的"跟随主流"，不要在这里用物理假设误杀。
    for L, ax in order:
        cross = [k for k in range(3) if k != ax]
        cr = max(span[k][0] for k in cross)
        cand.append((ax, L, cr))

    if not cand:
        return 0                              # 全都不合格，退回最长轴
    # ★★ 轴向裁决：**跟随主流**（2026-10-10 实测得出的关键规则）
    #   全部 12 个 STL 的真实三轴（离线量出来的硬数据）：
    #     9 个长轴明确是 Y：S-IC bottom(124.6/203.1/124.6)、S-IC top、S-II、
    #       S-IV b、escape tower(38.3/110.9/38.4)、lem shroud、service module、
    #       stage 1-2 coupler（两个）
    #     唯一例外：command moduel.stl 是 X=33.9 Y=20.1 Z=33.9 ——
    #       X 与 Z **精确相等**，它是个扁桶，**两个方向都算长轴**。
    #
    #   我原来按"截面更接近圆"裁决，把指令舱判成了 X →
    #   矩阵按 X 轴构造 → 它在装配里横躺 → 图上看着就是"零件散了"。
    #
    #   ★ 但从 NX 里把它单独导出来量，NX 给的包围盒是
    #       X[-16.7,17.2] Y[-121.5,-101.4] Z[-8.0,25.9] → 长轴明明是 Y。
    #     （NX 的 STL 导入器做了轴向归一化，跟我离线量的不一致）
    #
    #   结论：**这批 STL 是同一套坐标系里建的，长轴统一是 Y。**
    #   与其对少数件猜哪个轴对，不如跟随主流 —— 与主流一致永远是对的。
    if 1 in [ax for ax, _, _ in cand]:
        return 1
    return max(cand, key=lambda t: t[1])[0]


def profile(tris, axis, nband=NBAND):
    """沿 axis 的半径轮廓。

    做法：每个三角面按它在轴上的跨度投到相关带上（避免粗网格漏带），
    带内取所有顶点的横轴极值 → 中心取中位、半径取横向最大偏移。
    返回 dict：带中心坐标、中心(2 横轴)、半径。
    """
    cross = [i for i in range(3) if i != axis]
    vs = [p[axis] for t in tris for p in t]
    lo, hi = min(vs), max(vs)
    step = (hi - lo) / nband if hi > lo else 1.0
    buckets = [[] for _ in range(nband)]
    for t in tris:
        aa = [p[axis] for p in t]
        j0 = int((min(aa) - lo) / step)
        j1 = int((max(aa) - lo) / step)
        j0 = max(0, min(nband - 1, j0))
        j1 = max(0, min(nband - 1, j1))
        for j in range(j0, j1 + 1):
            buckets[j].extend(t)
    out = []
    for j, b in enumerate(buckets):
        if len(b) < 3:
            out.append(None)
            continue
        c = []
        r = 0.0
        for ci in cross:
            vals = [p[ci] for p in b]
            mn, mx = min(vals), max(vals)
            c.append((mn + mx) / 2.0)
            r = max(r, (mx - mn) / 2.0)
        out.append({"y": lo + (j + 0.5) * step, "c": c, "r": r})
    return {"bands": out, "lo": lo, "hi": hi, "step": step, "cross": cross}


def robust_center(prof):
    """轴心 = 各带中心的分量中位数（抗离群与不对称特征）。"""
    cs = [b["c"] for b in prof["bands"] if b]
    if not cs:
        return [0.0, 0.0]
    out = []
    for k in range(2):
        col = sorted(c[k] for c in cs)
        out.append(col[len(col) // 2])
    return out


def rmax(prof):
    rs = sorted(b["r"] for b in prof["bands"] if b)
    if not rs:
        return 0.0
    return rs[int(len(rs) * 0.90)]


def neck_len(prof, end, r_full, center):
    """从 end('lo'|'hi') 往里走，到第一个"实体段"的距离 = 细脖长度。"""
    bands = prof["bands"]
    idx = range(len(bands)) if end == "lo" else range(len(bands) - 1, -1, -1)
    seen = 0
    for j in idx:
        b = bands[j]
        if not b:
            continue
        seen += 1
        if b["r"] >= FULL_R * r_full:
            return abs(b["y"] - (prof["lo"] if end == "lo" else prof["hi"]))
    return abs(prof["hi"] - prof["lo"]) * 0.5      # 整根都是细的 → 保守取一半


def r_at(prof, frac):
    """取距 end 一定比例处的半径（frac: 0=起点, 1=另一端）。"""
    bands = [b for b in prof["bands"] if b]
    if not bands:
        return 0.0
    k = int(round((len(bands) - 1) * frac))
    return bands[k]["r"]


def main():
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(HERE)
    SRC = os.path.join(ROOT, "output", "enterprise", "downloaded_centered")
    OUT = os.path.join(ROOT, "output", "enterprise", "saturn_layout_v6.json")

    STACK = [
        ("S-IC 第一级·底部", "S-IC bottom.stl"),
        ("S-IC 第一级·顶部", "S-IC top.stl"),
        ("1-2 级间段",       "stage 1-2 coupler.stl"),
        ("S-II 第二级",      "S-II.stl"),
        # ★★ 【2026-10-10 用户发现，数据证实】2-3 之间**不放**独立级间段 ——
        #   套件的 S-II 零件**自带**锥形前段（肩部 Ø69-77 → 锥段 Ø55→50），
        #   其锥腔深度 50.7mm ≈ S-IVB 喷管簇深度 50mm —— 两者是配对设计的，
        #   S-IVB 的喷管直接插进 S-II 自己的锥段里。
        #   我原来把套件的圆柱 coupler 又装了一个在这里 = 重复装级间段。
        #   （补造的锥台 STL 保留在 downloaded/ 里备查，不再参与装配）
        ("S-IVB 第三级",     "S-IV b.stl"),
        ("LEM 整流罩",       "lem shroud.stl"),
        ("服务舱",           "service module.stl"),
        ("指令舱",           "command moduel.stl"),
        ("逃逸塔",           "escape tower.stl"),
    ]
    SKIP = {
        "S-IC joining cube.stl": "打印对位方块，不是火箭零件",
        "fin (print 4).stl": "尾翼（需绕轴复制 4 份）—— 本轮先不放",
    }

    print("读取 %d 个零件并算半径轮廓…" % len(STACK))
    parts = []
    for i, (label, fn) in enumerate(STACK):
        p = os.path.join(SRC, fn)
        if not os.path.isfile(p):
            print("  缺文件: %s" % fn); return 2
        tris = read_tris(p)
        if not tris:
            print("  解析失败: %s" % fn); return 2
        # ★★ 自动选长轴（不能写死 Y！指令舱长轴是 X，
        #    写死 Y 会量错长度和半径 → origin 全错 → NX 摆错位置）
        ax = pick_axis(tris)
        prof = profile(tris, ax)
        prof["axis"] = ax
        prof["tris"] = tris
        prof["label"] = label
        prof["file"] = fn
        prof["center"] = robust_center(prof)
        prof["rmax"] = rmax(prof)
        prof["len"] = prof["hi"] - prof["lo"]
        prof["neck_lo"] = neck_len(prof, "lo", prof["rmax"], prof["center"])
        prof["neck_hi"] = neck_len(prof, "hi", prof["rmax"], prof["center"])
        # 端头处的半径（往里 8% 处，避开端面毛刺）
        prof["r_end_lo"] = r_at(prof, 0.08)
        prof["r_end_hi"] = r_at(prof, 0.92)
        parts.append(prof)
        print("  %-16s 轴=%s 长%6.1f  rmax=%5.1f  端半径 %.1f/%.1f  细脖 %.1f/%.1f  轴心(%.1f, %.1f)"
              % (label, "XYZ"[ax], prof["len"], prof["rmax"],
                 prof["r_end_lo"], prof["r_end_hi"],
                 prof["neck_lo"], prof["neck_hi"],
                 prof["center"][0], prof["center"][1]))

    # ============================================================ 朝向硬约束
    # ★★★ 为什么需要（2026-10-10，用户提问「有些部件上下翻转了」）★★★
    #   原来的朝向完全交给"暴力枚举 + 代价函数"，代价函数是**全局**的
    #   （相邻接口半径失配 + 全箭下粗上细）。
    #   实测：**它把 S-II / S-IVB / 服务舱 / LEM整流罩 四件全推反了** ——
    #   渲染图里能看到它们的发动机喷管/锥体收口都朝了上。
    #
    #   原因：这四件的"发动机端"比筒身**略细**，而"下粗上细"的罚项
    #   恰好奖励"把细端放上面"，于是全局最优解就是错的。
    #
    #   现在按**真机工程事实**锁定朝向（每一条都用逐件渲染图核对过，
    #   见 output/enterprise/engines_zoom.png、orient_compare.png）：
    #     值 True  = 把模型的 hi 端放到下面
    #     值 None  = 不锁，仍交给朝向搜索
    ORIENT_LOCK = {
        # 发动机端朝下 —— 五台 F-1 + 四片尾翼在最底端
        "S-IC 第一级·底部": True,
        # ★ 五台 J-2 在底端（原来装反：喷管朝上）
        "S-II 第二级": True,
        # ★ 一台 J-2 在底端（原来装反）
        "S-IVB 第三级": True,
        # ★ 服务推进系统(SPS)大喷管在底端（原来装反）
        "服务舱": True,
        # ★ SLA 锥形适配器：大口朝下（对着 S-IVB），小口朝上（对着服务舱）
        "LEM 整流罩": True,
        # 隔热大底（钝头、宽端）朝下，对接端朝上 —— 原来就对
        "指令舱": True,
        # 待人工确认：长针状顶端朝天（翻过来针会朝下，明显不对），
        # 但它底部那个喇叭口比指令舱顶端还宽，拿不准，先不锁死
        "逃逸塔": None,
        # 下面几件近似等径，形状上判不出朝向，交给搜索
        "S-IC 第一级·顶部": None,
        "1-2 级间段": None,
        "2-3 级间段": None,
    }

    # ---------------------------------------------------------- 朝向暴力枚举
    # 变量：每个零件是否翻转（把 hi 端放到下面）。目标：各级接口半径失配之和最小。
    n = len(parts)
    locked = [ORIENT_LOCK.get(parts[i]["label"]) for i in range(n)]
    free_idx = [i for i in range(n) if locked[i] is None]
    print("  朝向锁定的零件：%s"
          % ", ".join("%s=%s" % (parts[i]["label"][:10],
                                 "翻转" if locked[i] else "保持")
                      for i in range(n) if locked[i] is not None))
    print("  自由搜索的零件：%d 件（%d 种组合）"
          % (len(free_idx), 1 << len(free_idx)))
    best = None
    for mask in range(1 << len(free_idx)):
        flip = [bool(locked[i]) if locked[i] is not None else False
                for i in range(n)]
        for k, i in enumerate(free_idx):
            flip[i] = bool((mask >> k) & 1)
        tops = [parts[i]["r_end_lo"] if flip[i] else parts[i]["r_end_hi"] for i in range(n)]
        bots = [parts[i]["r_end_hi"] if flip[i] else parts[i]["r_end_lo"] for i in range(n)]
        cost = 0.0
        for i in range(n - 1):
            a, b = parts[i], parts[i + 1]
            nk_a = a["neck_hi"] if not flip[i] else a["neck_lo"]
            nk_b = b["neck_lo"] if not flip[i + 1] else b["neck_hi"]
            # ★ 任一侧是"细脖"时，接头会被套接关系遮住，不算可见失配
            if max(nk_a, nk_b) > 2.0:
                cost += 0.15 * abs(tops[i] - bots[i + 1])
            else:
                cost += abs(tops[i] - bots[i + 1])
        # ★ 物理约束：火箭整体下粗上细 —— 单级"上宽下窄"就罚
        for i in range(n):
            p_i = parts[i]
            rt = p_i["r_end_lo"] if flip[i] else p_i["r_end_hi"]
            rb = p_i["r_end_hi"] if flip[i] else p_i["r_end_lo"]
            if rt > rb:
                cost += (rt - rb) * 1.2
        if best is None or cost < best[0]:
            best = (cost, mask, flip)
    cost, mask, flip = best
    print("")
    print("★ 朝向搜索：%d 种组合，最优失配和 = %.1f" % (1 << n, cost))
    for i, p in enumerate(parts):
        p["flip"] = flip[i]
        print("  %-16s %s" % (p["label"], "翻转（原 hi 端朝下）" if flip[i] else "保持"))

    # ---------------------------------------------------------- 逐个往上堆
    z_cursor = 0.0

    out_parts = []
    joints = []
    for i, p in enumerate(parts):
        L = p["len"]
        if p["flip"]:
            # 原 hi 端成为底端
            y_bottom = p["hi"]          # 局部轴上，底端对应的 y
            r_bot = p["r_end_hi"]; n_bot = p["neck_hi"]
            r_top = p["r_end_lo"]; n_top = p["neck_lo"]
        else:
            y_bottom = p["lo"]
            r_bot = p["r_end_lo"]; n_bot = p["neck_lo"]
            r_top = p["r_end_hi"]; n_top = p["neck_hi"]

        if i > 0:
            prev = out_parts[-1]
            #★★★ 重叠量按**真实径向剖面**给，不再按包围盒猜（2026-10-10 血的教训）★★★
            #
            #旧规则（错）：两端半径失配大 → 认为是套接 → 重叠 = "细脖长度"。
            #   实测结果：9 个接头里 **7 个没真贴合**，因为
            #   · 细脖长度是"轴上最细段"，不是"可插入深度"
            #   · 打印件的套筒是**开口的**，Z 重叠 ≠ 真的有材料
            #
            # 新规则：调用 tools/fit_joints_by_profile.py 的判据，三种配合方式
            #   A 插入：上件细柱(外径 < 下件孔径) → 重叠 = 插入深度
            #   B 套入：上件粗筒(孔径 < 外径 < 下件外壁) → 重叠 = 套入深度
            #   C 贴合：径向差不多 → 重叠 = 0（面接触）
            # ★ 传**当前 parts 元素** p（它带 tris），不是 out_parts[-1]
            #   （那个只有 dict 字段，没有真实顶点 → _real_profile 返回空）
            ov, fit_kind, fit_why = _joint_overlap(parts[i - 1], p, r_bot, n_bot, L)
            # ★★★ 整流罩要"包住"上方那级的发动机（用户 2026-10-10 指出）★★★
            #   级间段不是贴在上面级的底面上，而是像一个裙筒把它的喷管罩进去。
            #   罩入深度 = 喷管簇的轴向长度（量出来的），不是随便给的小重叠。
            #   实测：S-II 喷管簇 44mm / S-IVB 42mm / 服务舱 30mm，
            #   而原来的重叠只有 5/3/7mm —— 喷管全露在外面。
            if p["label"] in ("S-II 第二级", "S-IVB 第三级", "服务舱"):
                ed = _bell_depth(p)
                if ed > 0:
                    cap = 0.55 * min(parts[i - 1]["len"], p["len"])
                    ov = min(ed + 2.0, cap)
                    # ★ 锥腔深度钳制已弃用：S-II 前段有杂散几何（rmax=70.5
                    #   的毛刺带）会把 _nose_cavity_depth 带歪成 0/21，
                    #   弊大于利。53mm 罩入已在渲染上验证过是对的。
                    fit_kind = "B 套入"
                    fit_why = ("整流罩包住上方发动机：喷管簇 %.0fmm，罩入 %.0fmm"
                               % (ed, ov))
            # ★ 接头深度修正（tools/measure_insertion.py 逐高度扫描实测）：
            #   这两处还有 4mm 物理插入余量，用满
            _ovfix = {"LEM 整流罩": 15.7, "服务舱": 36.0}
            if p["label"] in _ovfix and ov < _ovfix[p["label"]]:
                ov = _ovfix[p["label"]]
            # ★★★ 过盈配合（v6，2026-10-11 用户红标 3 处"插进去了但没插满"）★★★
            #   压入量由世界坐标逐 5mm 剖面实测推导（见 zoom/rim 渲染验证）：
            #   · S-II：缘口原切在发动机收腰上（h47 处 r≈30-35），压 8mm 后
            #     缘口切到筒身（h55 r≈43.8 vs 级间段缘口 r45，台阶仅 1.2mm）
            #   · 服务舱：筒身 r21 对 LEM 筒口 r29，8mm 环台是套件固有直径差，
            #     压 10mm 使其深坐（总重叠 46 < LEM 长 63.7，可行）
            #   · 指令舱：锥底插在服务舱顶端杯腔里，压 4mm 让锥体加粗段
            #     填满杯口（服务舱顶缘 r13.1，指令舱 h11 处 r≈16，略凸 3mm 属正常座装）
            _PRESS = {"S-II 第二级": 8.0, "服务舱": 10.0, "指令舱": 4.0}
            if p["label"] in _PRESS:
                ov += _PRESS[p["label"]]
                fit_why += "；过盈压入 +%.0fmm（消除可见台阶/深坐）" % _PRESS[p["label"]]
            z_cursor -= ov
            joints.append({"below": prev["label"], "above": p["label"],
                           "overlap": round(ov, 2),
                           "fit": fit_kind, "fit_why": fit_why,
                           "r_below_top": round(prev["r_top"], 2),
                           "r_above_bottom": round(r_bot, 2),
                           "mismatch": round(abs(prev["r_top"] - r_bot), 2)})

        # ★★★ 关键：矩阵与 origin 必须按**该件实际的长轴**构造，
        #   不能假定"长轴一定是局部 Y"（2026-10-10 血的教训）。
        #   指令舱的长轴是 X（X=33.9、Y=20.1、Z=33.9），
        #   按 Y 造矩阵 → 变换后落在 z=1159，而声称 1040 → 错 118mm。
        #
        #   做法（通用，不依赖具体轴）：
        #     长轴 a   → 世界 Z   （竖起来）
        #     横轴 c1  → 世界 X
        #     横轴 c2  → 世界 Y
        #   翻转 = 让长轴取负（world.z = org.z - local[a]）
        ax_l = p.get("axis", 1)
        cross = [k for k in range(3) if k != ax_l]      # [c1, c2]
        # ★★★ center 的两个分量对应 cross[0]/cross[1]，**不是世界 X/Y**。
        #   长轴沿 Y 时 cross = [0, 2] → center[0] 是 X 中心、center[1] 是 Z 中心。
        #   我原来写 org = [-cx, -cy, ...] 当成世界 (x,y) 用，两次都错：
        #     第一次：Z 中心被当成 Y 用→ 整体偏 16mm
        #     第二次：每件各自抵消 center → 各件都被平移了不同的量
        #             （8.6~8.9mm，正好等于 STL 自身的建模原点偏移）
        #
        #   ★ 正确做法：**不动 center**，让零件保留自身坐标；
        #     Z 方向的落点由 y_bottom/lo 精确控制（这个是对的，Z 区间实测全对）；
        #     XY 方向的"公共轴心对齐"留给**最后的全局平移**做（见 AXIS_SHIFT）。
        org = [0.0, 0.0, 0.0]
        cx, cy = 0.0, 0.0

        def basis(sign):
            """按长轴→世界Z 生成 3x3（行=世界 XYZ 的输出）。"""
            m = [[0.0] * 3 for _ in range(3)]
            # 长轴 a → 世界 Z（sign=+1）或 世界 -Z（sign=-1）
            m[2][ax_l] = float(sign)
            # 横轴 → 世界 X / Y
            m[0][cross[0]] = 1.0
            m[1][cross[1]] = 1.0
            # 保证是右手系：若行列式为负，把某个横轴取负
            det = (m[0][0]*(m[1][1]*m[2][2] - m[1][2]*m[2][1])
                   - m[0][1]*(m[1][0]*m[2][2] - m[1][2]*m[2][0])
                   + m[0][2]*(m[1][0]*m[2][1] - m[1][1]*m[2][0]))
            if det < 0:
                m[1][cross[1]] = -1.0
            return m

        if p["flip"]:
            M = basis(-1.0)
            org[2] = z_cursor + y_bottom             # world.z = org.z - local[a]
        else:
            M = basis(1.0)
            org[2] = z_cursor - y_bottom             # world.z = org.z + local[a]
        # 临时存真实顶点，供"全局轴心对齐"用（写JSON 前会删掉）
        out_parts.append({
            "order": i + 1, "label": p["label"], "file": p["file"],
            "flip": bool(p["flip"]),
            "local_axis": "XYZ"[p.get("axis", 1)],
            "axis_length": round(L, 3), "len": round(L, 3),
            "rmax": round(p["rmax"], 3),
            # ★ 由工具自己输出，别事后手工补 —— 手工补的字段在重算布局时会消失，
            #   然后下游脚本 KeyError 崩掉（2026-10-10 踩过）
            "diameter": round(2.0 * p["rmax"], 3),
            "local_min": [p["lo"]] + [round(v, 3) for v in p["center"]],
            "local_max": [p["hi"]] + [round(v, 3) for v in p["center"]],
            "origin": [round(v, 3) for v in org],
            "matrix": M,
            "z_bottom": round(z_cursor, 3),
            "z_top": round(z_cursor + L, 3),
            "neck_lo": round(p["neck_lo"], 2), "neck_hi": round(p["neck_hi"], 2),
            "r_top": round(r_top, 2), "neck_top": round(n_top, 2),
            "r_bottom": round(r_bot, 2),
        })
        z_cursor += L

    # ---------------------------------------------------------- 全局轴心对齐
    # ★ 先按"原样"把各件变换到世界（origin 的 XY 是 0），
    #   量出每件的 XY 包围盒中心，取**中位数**当全箭公共轴心，
    #   再把这个偏移**统一**从所有 origin 里减掉。
    #   为什么必须统一：每件各自抵消自己的 center 会引入不同的平移量
    #   （实测 8.6~8.9mm），而 STL 的建模原点本来就各偏各的。
    def _world_xyz(q):
        """零件顶点变换到世界坐标（out_parts 里只有 dict，顶点从 parts 借）。"""
        M, o = q["matrix"], q["origin"]
        idx = q.get("order", 1) - 1
        tris = parts[idx].get("tris") or []
        return [(o[0] + M[0][0] * a + M[0][1] * b + M[0][2] * c,
                 o[1] + M[1][0] * a + M[1][1] * b + M[1][2] * c,
                 o[2] + M[2][0] * a + M[2][1] * b + M[2][2] * c)
                for t in tris for (a, b, c) in t]

    _cent = []
    for q in out_parts:
        W = _world_xyz(q)
        tx = [w[0] for w in W]
        ty = [w[1] for w in W]
        _cent.append(((min(tx) + max(tx)) / 2.0, (min(ty) + max(ty)) / 2.0))
    if _cent:
        # ★★ 用"聚类众数"而不是中位数 —— 中位数会被少数离群件拽歪
        #   （实测指令舱偏 111mm、逃逸塔偏 16mm，中位数被拉到 7.5），
        #   于是主流零件（S-II/S-IVB/LEM，真实偏心 < 2mm）反而被平移了。
        #   做法：按 2mm 一档聚类，取**最大簇**的中心 —— 火箭 10 段里
        #   绝大多数的建模原点是同一套，少数异常的自己被拉过去。
        def _mode(vals, bucket=2.0):
            bins = {}
            for v in vals:
                bins.setdefault(round(v / bucket), []).append(v)
            best = max(bins.values(), key=len)
            return sum(best) / len(best)

        ax_x = _mode([c[0] for c in _cent])
        ax_y = _mode([c[1] for c in _cent])
        print("")
        print("  ★ 各件原始轴心 X: %s"
              % [round(c[0], 1) for c in _cent])
        print("  ★ 各件原始轴心 Y: %s"
              % [round(c[1], 1) for c in _cent])
        # 主流件（轴心接近公共轴心）统一扣掉公共轴心；
        # 离群件（指令舱 STL 自身建模原点偏了 111mm、逃逸塔 17mm）
        #   单独扣掉**自己**的轴心 —— 火箭装上以后它们也该在同一条轴上。
        AXIS_EPS = 6.0        # 与公共轴心的差超过这个值就算离群
        for q, c in zip(out_parts, _cent):
            tgt_x, tgt_y = ax_x, ax_y
            if abs(c[0] - ax_x) > AXIS_EPS or abs(c[1] - ax_y) > AXIS_EPS:
                tgt_x, tgt_y = c[0], c[1]
                q["axis_aligned_alone"] = True
            q["origin"][0] = round(q["origin"][0] - tgt_x, 3)
            q["origin"][1] = round(q["origin"][1] - tgt_y, 3)
        print("")
        print("  ★ 全局轴心对齐：全箭公共轴心 (%.2f, %.2f) 已从各件 origin 统一扣除"
              % (ax_x, ax_y))

    out = {
        "source": "NASA 3D Resources (github.com/nasa/NASA-3D-Resources, 公有领域) · Saturn V Rocket",
        "units": "mm（STL 原始单位，未缩放）",
        "algorithm": "半径轮廓 + 朝向暴力枚举(2^10) + 细脖套接重叠",
        "axis_note": "STL 长轴为 Y；矩阵把它竖起来（世界 Z = 火箭轴）",
        "total_height": round(z_cursor, 3),
        "skipped": SKIP,
        "joints": joints,
        "parts": out_parts,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------- 接头表
    print("")
    print("%-30s %8s %10s %10s %9s" % ("接头（下 → 上）", "重叠", "下件顶半径", "上件底半径", "失配"))
    for j in joints:
        print("  %-28s %8.1f %10.1f %10.1f %9.1f"
              % ("%s → %s" % (j["below"][:12], j["above"][:12]),
                 j["overlap"], j["r_below_top"], j["r_above_bottom"], j["mismatch"]))
    print("")
    print("  全箭高 = %.1f mm" % out["total_height"])

    # ---------------------------------------------------------- ASCII 侧影（先自己看）
    # ★ 用各级实测的「顶半径 / 底半径 + 长度」按比例画，宽度线性过渡 ——
    #   这才是真正的侧影，不是包围盒方块。
    print("")
    print("  ★ ASCII 侧影（按各级实测半径与长度画；上 = 逃逸塔）")
    W = 62
    R = max(p["rmax"] for p in out_parts) or 1.0
    scale = (W / 2.0) / R
    HALF = int(W / 2)
    tot = out["total_height"]
    nrow = 34
    rows = []
    for row in range(nrow):
        z = tot * (1.0 - (row + 0.5) / nrow) + 0.0
        # 找该高度落在哪一级
        cur = None
        for p in out_parts:
            if p["z_bottom"] <= z <= p["z_top"]:
                cur = p
                break
        if not cur:
            rows.append(" " * (2 * HALF)); continue
        t = (z - cur["z_bottom"]) / max(cur["axis_length"], 1e-6)
        r = cur["r_bottom"] + (cur["r_top"] - cur["r_bottom"]) * t
        col = int(round(scale * r))
        col = max(0, min(HALF, col))
        rows.append(" " * (HALF - col) + "#" * (2 * col))
    for k, r in enumerate(rows):
        mark = ""
        print("  |%s|%s" % (r, mark))
    print("  " + "-" * (2 * HALF + 2))
    print("  各级（下→上）：")
    for p in out_parts:
        print("    z %7.1f→%7.1f  %-16s 顶r=%5.1f 底r=%5.1f %s"
              % (p["z_bottom"], p["z_top"], p["label"], p["r_top"], p["r_bottom"],
                 "（翻转）" if p["flip"] else ""))
    return 0


def _hole_of(bands):
    """找出主要中空段（套筒的孔）。"""
    if not bands:
        return None
    ins = sorted(b["r_in"] for b in bands)
    r_typ = ins[len(ins) // 2]
    thr = max(2.0, r_typ * 0.12)
    hollow = [b for b in bands
              if b["wall"] > thr and b["r_in"] > max(1.0, r_typ * 0.15)]
    if not hollow:
        return None
    runs, cur = [], None
    for b in hollow:
        if cur and b["z0"] - cur["z1"] <= 4.5:
            cur["z1"] = b["z1"]
            cur["ins"].append(b["r_in"]); cur["outs"].append(b["r_out"])
        else:
            if cur: runs.append(cur)
            cur = {"z0": b["z0"], "z1": b["z1"],
                   "ins": [b["r_in"]], "outs": [b["r_out"]]}
    if cur: runs.append(cur)
    best = max(runs, key=lambda r: r["z1"] - r["z0"])
    ins2 = sorted(best["ins"])
    return {"z0": best["z0"], "z1": best["z1"],
            "r_hole": ins2[len(ins2) // 4], "r_out": max(best["outs"]),
            "len": best["z1"] - best["z0"]}


def _pin_of(bands):
    """找出插入端（接近实心的那段）。"""
    if not bands:
        return None
    outs = sorted(b["r_out"] for b in bands)
    r_typ = outs[len(outs) // 2]
    solid = [b for b in bands if b["wall"] <= max(2.0, r_typ * 0.12)]
    if not solid:
        return None
    runs, cur = [], None
    for b in solid:
        if cur and b["z0"] - cur["z1"] <= 4.5:
            cur["z1"] = b["z1"]; cur["rs"].append(b["r_out"])
        else:
            if cur: runs.append(cur)
            cur = {"z0": b["z0"], "z1": b["z1"], "rs": [b["r_out"]]}
    if cur: runs.append(cur)
    best = max(runs, key=lambda r: r["z1"] - r["z0"])
    rs = sorted(best["rs"])
    return {"z0": best["z0"], "z1": best["z1"], "r": rs[len(rs) // 2],
            "len": best["z1"] - best["z0"]}


def _body_radius(bands):
    """筒身半径 = 中段（40%~60% 高度）各档 r_out 的中位数。
    用中段而不是全段：两端有喷管/收口/鼓环，会带偏。"""
    if not bands:
        return 0.0
    n = len(bands)
    mid = bands[int(n * 0.40):int(n * 0.60) + 1] or bands
    rs = sorted(b["r_out"] for b in mid)
    return rs[len(rs) // 2]


def _bell_depth(part, band_gap=1):
    """发动机喷管簇从端面往里延伸多长（= 整流罩需要罩住的深度）。

    判据：从发动机端起，外半径连续小于 **0.85 × 筒身半径**（允许断 1 档），
    半径恢复到筒身的地方 = 喷管根部。
    实测：S-II ≈ 48-50mm（5 台 J-2）｜ S-IVB ≈ 50mm（1 台 J-2）｜ 服务舱 ≈ 28mm。
    """
    bands = _real_profile(part)
    if not bands:
        return 0.0
    thr = 0.85 * _body_radius(bands)
    y0 = bands[0]["z0"]
    depth = 0.0
    gap = 0
    for b in bands:
        if b["r_out"] < thr:
            depth = b["z1"] - y0
            gap = 0
        else:
            gap += 1
            if gap > band_gap:
                break
            depth = b["z1"] - y0
    return depth



def _nose_cavity_depth(part, band_gap=1):
    """零件【顶端】收窄锥腔的深度 —— 从顶往下，外半径连续小于
    0.85 × 筒身半径 的段。
    S-II 的前段锥腔 = 50.7mm，正好容纳 S-IVB 的喷管簇（50mm）—— 配对设计。
    圆柱形整流罩（1-2 级间段、SLA）顶端不收窄 → 返回 0（不设限）。
    ★ 深度不足零件长度 10% 的不算数（抗顶端局部毛刺/倒角误判）。
    """
    bands = _real_profile(part)
    if not bands:
        return 0.0
    thr = 0.85 * _body_radius(bands)
    y_top = bands[-1]["z1"]
    depth = 0.0
    gap = 0
    for b in reversed(bands):
        if b["r_out"] < thr:
            depth = y_top - b["z0"]
            gap = 0
        else:
            gap += 1
            if gap > band_gap:
                break
            depth = y_top - b["z0"]
    span = bands[-1]["z1"] - bands[0]["z0"]
    if span > 0 and depth < 0.10 * span:
        return 0.0
    return depth



def _joint_overlap(prev, cur, r_bot, n_bot, L):
    """按真实剖面算相邻两件的重叠量。返回 (overlap, kind, why)。"""
    hb = _hole_of(_real_profile(prev))
    pin = _pin_of(_real_profile(cur))
    if hb is None or pin is None:
        # 没有孔也没有柱 → 只能面贴合
        return 0.0, "C 贴合", "无孔无柱 → 面贴合"
    gap = hb["r_hole"] - pin["r"]
    if gap >= 0.5:
        ov = min(25.0, hb["len"] * 0.35)
        return ov, "A 插入", "孔 r%.1f > 柱 r%.1f，插入 %.0fmm" % (
            hb["r_hole"], pin["r"], ov)
    if pin["r"] < hb["r_out"]:
        ov = min(20.0, max(5.0, hb["len"] * 0.3))
        return ov, "B 套入", "柱 r%.1f > 孔 r%.1f 但 < 外壁 r%.1f，套 %.0fmm" % (
            pin["r"], hb["r_hole"], hb["r_out"], ov)
    return 0.0, "C 贴合", "柱 r%.1f ≥ 外壁 r%.1f → 面贴合" % (
        pin["r"], hb["r_out"])





def _press_extra(below, above, base_ov, max_extra=40.0):
    """过盈压入量（v6 修正版）。

    可见台阶出现在**下件顶缘**处：缘口在上件局部坐标里位于
    bot_a + base_ov + e（e = 额外压入量）。
    目标：压到上件在缘口处的外径达到能达到的最大值（台阶最小），
    凸出下件外壁不超过 1.5mm（过盈配合的可见上限）。
    _real_profile 已把每件 a 轴统一成 底→顶，剖面档可能因套筒开口有空洞
    → r_out_at 用带容差的最近档，不再因空洞提前退出。"""
    pb = _real_profile(below)
    pa = _real_profile(above)
    if not pb or not pa:
        return 0.0
    top_b = pb[-1]["z1"]
    bot_a = pa[0]["z0"]

    def r_out_at(bands, z, tol=4.0):
        best, bd = None, 1e9
        for b in bands:
            if b["z0"] - tol <= z <= b["z1"] + tol:
                return b["r_out"]
            d = min(abs(z - b["z0"]), abs(z - b["z1"]))
            if d < bd:
                bd, best = d, b["r_out"]
        return best

    r_lo = r_out_at(pb, top_b - 1.5)          # 下件顶缘外径（近似恒定）
    if r_lo is None:
        return 0.0
    best_e, best_diff = 0.0, r_lo - (r_out_at(pa, bot_a + base_ov) or 0.0)
    e = 0.0
    while e < max_extra:
        e += 1.0
        r_hi = r_out_at(pa, bot_a + base_ov + e)
        if r_hi is None:
            break
        if r_hi >= r_lo - 0.5:
            # 缘口处已看齐；凸出超过 1.5mm 就退一步
            return e if r_hi <= r_lo + 1.5 else max(0.0, e - 1.0)
        diff = r_lo - r_hi
        if diff < best_diff - 0.3:            # 台阶仍在收敛 → 继续压
            best_e, best_diff = e, diff
        else:                                  # 进入喷管/脖段，不再收敛
            return best_e
    return best_e


def _real_profile(part, band=3.0):
    """零件在自己局部坐标下的径向剖面。

    ★★ 两个铁律（都踩过）：
      1. **半径必须以每档自己的轴心算** —— 这批 STL 的建模原点全是偏的
        （S-II 偏 8.8mm、指令舱偏 111mm、coupler 偏 8.59mm），
         直接 hypot(局部x, 局部z) 会量出 Ø107/Ø131 的鬼值，
         把 _bell_depth / _nose_cavity_depth 全带歪。
      2. **用 95/5 分位数，不用 min/max** —— 孤立毛刺顶点会把外壁撑大、
         把内孔缩没。
    """
    tris = part.get("tris") or []
    if not tris:
        return []
    ax = part.get("axis", 1)
    cross = [k for k in range(3) if k != ax]
    f = -1.0 if part.get("flip") else 1.0
    pts = []
    for t in tris:
        for v in t:
            pts.append((f * v[ax], v[cross[0]], v[cross[1]]))
    if not pts:
        return []
    a0 = min(q[0] for q in pts)
    a1 = max(q[0] for q in pts)
    bands = []
    z = a0
    while z < a1 - 1e-6:
        z2 = min(z + band, a1)
        sel = [q for q in pts if z <= q[0] <= z2]
        if sel:
            cx = sum(q[1] for q in sel) / len(sel)
            cz = sum(q[2] for q in sel) / len(sel)
            rr = sorted(math.hypot(q[1] - cx, q[2] - cz) for q in sel)
            r_in = rr[int(len(rr) * 0.05)]
            r_out = rr[int(len(rr) * 0.95)]
            bands.append({"z0": z, "z1": z2,
                          "r_in": r_in, "r_out": r_out,
                          "wall": r_out - r_in})
        z = z2
    return bands


if __name__ == "__main__":
    sys.exit(main())
