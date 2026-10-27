# -*- coding: utf-8 -*-
r"""
electrode_demo.py —— 示例项目：一只最简单的电极（模具行业的"hello world"）

为什么选电极做示例：
模具厂里电极是**数量最多、重复劳动最重**的东西 —— 一只模仁往往配十几只电极，
每只都是"底座 + 放电头"这个形状，只是尺寸不同。这正是本项目最能省力的地方：
手工做一只，之后 Agent 按清单批量出。

结构（两只放电头，方便看出"批量"）：
    ┌────────────────┐  ← 放电头 ×2（宽 25 × 深 25 × 高 18，间距 45）
    │  ██      ██    │
    ├────────────────┤
    │   底座          │  ← 底座 120 × 80 × 12
    └────────────────┘

参数（都由 Agent 通过 PARAMS 传入）：
    base_l / base_w / base_h      底座 长/宽/高
    boss_l / boss_w / boss_h      放电头 长/宽/高
    boss_count / boss_gap         放电头数量 / 间距
    name                          特征名前缀（幂等用）

幂等：重跑时按名字找到旧特征，用构建器**就地改尺寸**，不堆新的。
"""

import NXOpen

P = PARAMS

BASE_L = float(P.get("base_l", 120.0))
BASE_W = float(P.get("base_w", 80.0))
BASE_H = float(P.get("base_h", 12.0))
BOSS_L = float(P.get("boss_l", 25.0))
BOSS_W = float(P.get("boss_w", 25.0))
BOSS_H = float(P.get("boss_h", 18.0))
BOSS_COUNT = int(P.get("boss_count", 2))
BOSS_GAP = float(P.get("boss_gap", 45.0))
NAME = P.get("name", "AGENT_ELECTRODE")

session = NXOpen.Session.GetSession()
part = session.Parts.Work
if part is None:
    raise RuntimeError("没有工作部件 —— 先 nx_new_part")


def find(name):
    for f in part.Features:
        try:
            if str(f.Name) == name:
                return f
        except Exception:
            continue
    return None


def make_block(name, origin, length, width, height):
    """建（或改）一个方块。同名特征存在时就地改尺寸 —— 不删除，删除会弹框卡死桥接。"""
    existing = find(name)
    builder = part.Features.CreateBlockFeatureBuilder(existing)
    try:
        builder.Type = NXOpen.Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths
        builder.SetOriginAndLengths(
            NXOpen.Point3d(float(origin[0]), float(origin[1]), float(origin[2])),
            str(length), str(width), str(height))
        feature = builder.Commit()
    finally:
        builder.Destroy()
    if existing is None:
        try:
            feature.SetName(name)
        except Exception:
            pass
    return "edited" if existing is not None else "created"


# ---- 1) 底座 ----
base_mode = make_block(NAME + "_BASE", (0.0, 0.0, 0.0), BASE_L, BASE_W, BASE_H)

# ---- 2) 放电头（批量：这就是"重复劳动"被自动化掉的地方）----
bosses = []
for i in range(BOSS_COUNT):
    # 沿底座长度方向居中排开
    x = (BASE_L - (BOSS_COUNT - 1) * BOSS_GAP) / 2.0 - BOSS_L / 2.0 + i * BOSS_GAP
    boss_name = "%s_BOSS%d" % (NAME, i + 1)
    mode = make_block(boss_name, (x, (BASE_W - BOSS_W) / 2.0, BASE_H),
                      BOSS_L, BOSS_W, BOSS_H)
    bosses.append({"name": boss_name, "x": round(x, 2), "mode": mode})

RESULT = {
    "project": "简单电极示例",
    "base": {"l": BASE_L, "w": BASE_W, "h": BASE_H, "mode": base_mode},
    "boss": {"l": BOSS_L, "w": BOSS_W, "h": BOSS_H,
             "count": BOSS_COUNT, "gap": BOSS_GAP},
    "bosses": bosses,
    "feature_count": len(list(part.Features)),
}
RESULT_NOTE = "底座 %gx%gx%g + %d 个放电头；重跑=改尺寸，不是重画" % (
    BASE_L, BASE_W, BASE_H, BOSS_COUNT)
