# -*- coding: utf-8 -*-
r"""
block_blank.py —— 参数化毛坯方块（Agent 建模的最小可跑示例）

这是「录制 → 参数化 → 重放」路线里的"参数化"成品：
手工在 NX 里点一遍建长方体，录出来的是写死尺寸的脚本；
把尺寸换成 PARAMS 之后，Agent 就能按不同参数反复重放。

用法（由桥接的 run_journal 命令执行，PARAMS 由它注入）：
    {"cmd": "run_journal", "args": {"path": ".../block_blank.py",
                                    "params": {"length": 100, "width": 60, "height": 20}}}

幂等：同名特征先删再建，所以"改参数重跑"等于"改模型"，不会堆出一堆方块。
"""

import NXOpen

P = PARAMS  # 桥接注入

LENGTH = float(P.get("length", 100.0))
WIDTH = float(P.get("width", 60.0))
HEIGHT = float(P.get("height", 20.0))
NAME = P.get("name", "AGENT_BLANK")
ORIGIN = P.get("origin", [0.0, 0.0, 0.0])

session = NXOpen.Session.GetSession()
part = session.Parts.Work
if part is None:
    raise RuntimeError("没有工作部件，先 new_part 或 open_part")

# ---- 1) 幂等：找同名特征。找到了就「编辑」，绝不「删除」。
# ⚠️ 2606 实测：删除特征（UpdateManager.AddToDeleteList + DoUpdate）会弹模态框，
#    把常驻桥接卡死（第二次迭代直接 timeout）。NX 的标准做法是把已有特征
#    传给构建器来就地编辑 —— 既没有弹窗，也符合 NX 的习惯。
existing = None
for feat in part.Features:
    try:
        if str(feat.Name) == NAME:
            existing = feat
            break
    except Exception:
        continue

# ---- 2) 建（或改）长方体 ----
# ⚠️ 2606 实测（在 NX 里反射出来的，不是猜的）：
#   · Length / Width / Height 在设置之前是 **None**，直接赋值会炸
#   · 正路是 SetOriginAndLengths(原点, 长, 宽, 高) —— 四个都是字符串
#   · BooleanOption 不设也行，新建特征默认就是 Create
builder = part.Features.CreateBlockFeatureBuilder(existing)  # None=新建，有特征=编辑
try:
    builder.Type = NXOpen.Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths
    origin_pt = NXOpen.Point3d(float(ORIGIN[0]), float(ORIGIN[1]), float(ORIGIN[2]))
    builder.SetOriginAndLengths(origin_pt, str(LENGTH), str(WIDTH), str(HEIGHT))
    feature = builder.Commit()
finally:
    builder.Destroy()

if existing is None:
    try:
        feature.SetName(NAME)
    except Exception:
        pass

# ---- 3) 让视图适配到模型，截图才有东西看 ----
def fit_view():
    """尝试把视图缩放到模型大小。能做就做，做不了如实回报，不炸。"""
    try:
        ufs = NXOpen.UF.UFSession.GetUFSession()
        v = ufs.View
        fit = getattr(v, "FitView", None)
        if not callable(fit):
            return "UF.View 没有 FitView 方法（可用：%s）" % \
                   [a for a in dir(v) if "fit" in a.lower()]
        # UF_VIEW_USE_ACTIVE_VIEW 常量按官方 UF 手册是 1；先试常量再试数字
        for tag in (getattr(NXOpen.UF.UFConstants, "UF_VIEW_USE_ACTIVE_VIEW", 1), 1):
            try:
                fit(tag, 1.0)
                return "ok"
            except Exception as exc:
                last = str(exc)
        return "FitView 两种参数都失败：%s" % last
    except Exception as exc:
        return str(exc)


fit_result = fit_view()

RESULT = {
    "feature": NAME,
    "length": LENGTH,
    "width": WIDTH,
    "height": HEIGHT,
    "mode": "edited" if existing is not None else "created",
    "feature_count": len(list(part.Features)),
    "fit_view": fit_result,
}
