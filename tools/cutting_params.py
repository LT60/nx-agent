#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
cutting_params.py -- 在没有厂里实测参数时，先给一个「能用的起点」

================================================================
为什么需要这个
================================================================
用户（2026-10-01）指出：参数问题根本不是卡点 ——
  1. Agent 可以**交互式地问**操作者（对话本身就是通道）；
  2. 参数**留空**也行（NX 模板自带默认值）；
  3. 就像网页 Agent 先按通用规范给 `padding: 16px`，CAM 也可以
     **先凭通用工艺知识定一个值**，不对再改。

这三条都对。本工具就是第 3 条的落地：按材料/刀具/工序查行业通用
切削手册，算出转速、进给、切深切宽，作为**起点**抛给操作者确认。

================================================================
红线（不可协商）
================================================================
算出来的值是「建议」，不是「答案」。输出里永远带 required_checks，
并且首件一律降速（first_article_factor，默认 0.8）。
**上机床前必须过 nc_validate + 人签字。**

================================================================
用法
================================================================
    python tools\cutting_params.py --material copper --diameter 8 --operation rough
    python tools\cutting_params.py --list                 # 看支持哪些材料
    python tools\cutting_params.py --json --material aluminum --diameter 10
"""

import argparse
import json
import os
import sys

try:
    import math
except Exception:  # 不可能发生，留着防御
    math = None


def _resolve_home():
    home = os.environ.get("NX_AGENT_HOME")
    if home and os.path.isdir(home):
        return home
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(here, "config")):
        return here
    return os.getcwd()


def load_table(home=None):
    path = os.path.join(home or _resolve_home(), "config", "cutting_defaults.json")
    if not os.path.isfile(path):
        raise RuntimeError("找不到 config/cutting_defaults.json（%s）" % path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def materials(table):
    return {k: v.get("_name", k) for k, v in table.get("materials", {}).items()}


def feed_per_tooth(table, diameter, operation):
    for band in table["feed_per_tooth_mm"]:
        if diameter <= band["_d_max"]:
            return band[operation]
    return table["feed_per_tooth_mm"][-1][operation]


def suggest(table, material=None, diameter=None, operation="rough", flutes=None,
            first_article=True):
    """返回一整套建议参数 + 必须做的检查清单。"""
    d = table.get("defaults", {})
    # ⚠️ 这里必须用 `is None` 判断，不能用真值判断：
    # diameter=0 是「用户填了 0」而不是「没填」，真值判断会悄悄退回默认 φ10，
    # 在加工场景里等于「你问的是 0，我按 10 给你算了参数」—— 会出事。
    material = material if material is not None else d.get("material", "carbon_steel")
    diameter = float(diameter if diameter is not None else d.get("diameter_mm", 10.0))
    flutes = int(flutes if flutes is not None else d.get("flutes", 4))
    if flutes <= 0:
        raise ValueError("刀齿数必须为正数")
    operation = operation if operation in ("rough", "finish") else "rough"

    if material not in table["materials"]:
        raise KeyError(
            "未知材料 %r。可用：%s" % (material, "、".join(sorted(table["materials"])))
        )
    if diameter <= 0:
        raise ValueError("刀具直径必须为正数")

    mat = table["materials"][material]
    vc = mat["vc_%s" % operation]
    fz = feed_per_tooth(table, diameter, operation)
    cut = table["depth_of_cut_factor"][operation]

    # S = Vc × 1000 / (π × D)   转速（转/分）
    rpm = vc * 1000.0 / (3.141592653589793 * diameter)
    # F = S × Z × fz           进给（毫米/分）
    feed = rpm * flutes * fz
    ap = cut["ap"] * diameter
    ae = cut["ae"] * diameter

    factor = table.get("first_article_factor", 1.0) if first_article else 1.0

    out = {
        "material": material,
        "material_cn": mat.get("_name", material),
        "operation": operation,
        "diameter_mm": round(diameter, 3),
        "flutes": flutes,
        "vc_m_per_min": vc,
        "feed_per_tooth_mm": fz,
        "suggested": {
            "spindle_rpm": int(round(rpm)),
            "feed_mm_per_min": int(round(feed)),
            "ap_mm": round(ap, 3),
            "ae_mm": round(ae, 3),
        },
        "first_article": {
            "applied": bool(first_article),
            "factor": factor,
            "spindle_rpm": int(round(rpm * factor)),
            "feed_mm_per_min": int(round(feed * factor)),
            "ap_mm": round(ap * factor, 3),
            "ae_mm": round(ae * factor, 3),
            "_note": "首件用这一档（转速/进给/切深/切宽一起降），确认无异常后再提到 suggested",
        },
        "notes": [mat.get("_note", "")],
        "required_checks": [
            "这只是行业通用起点，不是厂里的正确答案 —— 必须经操作者确认",
            "必须过 nc_validate（行程/转速/进给/刀具登记/刀补取消）",
            "上机床前必须完成 NX 仿真 + 机床空运行 + 人签字",
        ],
        "ask_the_operator": [
            "这材料实际用的是哪一号？热处理状态（HRC）多少？",
            "你们这台机床多少转以下会振刀？",
            "这把刀是几齿的？刃长多少（悬伸长了要降参数）？",
            "粗加工留多少余量给精加工？电极的话留多少火花位？",
            "冷却是水冷、气冷还是油雾？",
        ],
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="按通用工艺手册给一套切削参数起点（需人工确认）")
    ap.add_argument("--material", help="材料代号，如 copper / aluminum / carbon_steel")
    ap.add_argument("--diameter", type=float, help="刀具直径 mm")
    ap.add_argument("--operation", default="rough", choices=["rough", "finish"])
    ap.add_argument("--flutes", type=int, help="刀齿数")
    ap.add_argument("--no-first-article", action="store_true", help="不降速（别在首件时用）")
    ap.add_argument("--list", action="store_true", help="列出支持的材料")
    ap.add_argument("--json", action="store_true", help="JSON 输出（给 agent）")
    args = ap.parse_args(argv)

    table = load_table()
    if args.list:
        for k, v in sorted(materials(table).items()):
            print("%-22s %s" % (k, v))
        return 0

    try:
        res = suggest(table, args.material, args.diameter, args.operation, args.flutes,
                      not args.no_first_article)
    except (KeyError, ValueError) as exc:
        print(str(exc))
        return 2

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print("材料：%s（%s） ｜ 工序：%s ｜ 刀具：φ%s %d 齿"
          % (res["material_cn"], res["material"], res["operation"],
             res["diameter_mm"], res["flutes"]))
    print("=" * 70)
    s = res["suggested"]
    print("建议值（常规）    转速 S=%d 转/分 ｜ 进给 F=%d mm/分 ｜ 切深 ap=%s ｜ 切宽 ae=%s"
          % (s["spindle_rpm"], s["feed_mm_per_min"], s["ap_mm"], s["ae_mm"]))
    f = res["first_article"]
    print("首件降速档 x%s   转速 S=%d 转/分 ｜ 进给 F=%d mm/分 ｜ 切深 ap=%s ｜ 切宽 ae=%s"
          % (f["factor"], f["spindle_rpm"], f["feed_mm_per_min"],
             f["ap_mm"], f["ae_mm"]))
    if res["notes"] and res["notes"][0]:
        print("提示：%s" % res["notes"][0])
    print("-" * 70)
    print("这只是起点，不是答案：")
    for line in res["required_checks"]:
        print("  · " + line)
    print("建议直接问操作者这几句：")
    for line in res["ask_the_operator"]:
        print("  ? " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
