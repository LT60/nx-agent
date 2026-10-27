#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
electrode_batch.py -- 录制脚本参数化模板

这不是一段"我猜出来的 NXOpen 代码"，而是一个改造模板。
用法：用户在 NX 里录一遍电极操作，把录出来的代码粘到下面标了
「粘贴区」的地方，再把写死的数字换成从表格里读出来的变量。

为什么要这么教：AI 猜 NXOpen 的 API 命中率不高（网上样本少、版本差异大），
但 AI 改「已经跑通过的代码」命中率很高。所以正确姿势是
    人录制 → AI 参数化 → 人验证 → 入库 → Agent 调用
而不是让 AI 从零写。自主性是靠积累录制出来的能力长出来的。

在 NX 里怎么跑：
    Developer -> Journal Manager -> Browse 选本文件 -> Play
"""

import json
import os
import sys

import NXOpen


HOME = os.environ.get("NX_AGENT_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_FILE = os.path.join(HOME, "state", "electrode_task.json")


def load_task():
    """
    待加工的电极清单由 Agent 生成，长这样：
    {
      "part": "D:/mold/E-0021.prt",
      "post": "fanuc_mill_4A_metric",
      "out_dir": "D:/mold/nc/E-0021",
      "electrodes": [
        {"name": "E-0021-01", "x": 12.5, "y": -3.0, "z": 0.0, "tool": 1, "stock": 0.3},
        {"name": "E-0021-02", "x": 40.0, "y": 10.0, "z": 0.0, "tool": 2, "stock": 0.2}
      ]
    }
    """
    if not os.path.isfile(TASK_FILE):
        raise SystemExit("找不到任务清单: %s" % TASK_FILE)
    with open(TASK_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def open_part(path):
    session = NXOpen.Session.GetSession()
    session.Parts.OpenActiveDisplay(path, NXOpen.DisplayPartOption.AllowAdditional)
    return session.Parts.Work


def process_one(session, part, electrode):
    """
    处理单个电极。

    ↓↓↓ 粘贴区 ↓↓↓
    把用户录制的 journal 代码粘贴到这里，然后把写死的数字替换成 electrode 里的变量。
    例如录制出来是：
        markId1 = session.SetUndoMark(...)
        blockFeatureBuilder1 = workPart.Features.CreateBlockFeatureBuilder(NXOpen.Features.BlockFeature.Null)
        blockFeatureBuilder1.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
        ...
        blockFeatureBuilder1.SetOriginAndLengths(point3d1, "30", "20", "15")
    改成：
        blockFeatureBuilder1.SetOriginAndLengths(
            point3d(electrode["x"], electrode["y"], electrode["z"]),
            electrode["stock"] * 100, electrode["stock"] * 80, electrode["stock"] * 60)
    原则只有一条：录制出来的每一行都保留，只把数字换成变量。
    ↑↑↑ 粘贴区 ↑↑↑
    """
    name = electrode["name"]

    # 命名：批量作业里，"每个对象都正确命名"比"画得快"重要得多
    # 下游的 BOM、程序单、电极图框全靠这个名字对齐
    try:
        session.SetUndoMark(NXOpen.Session.MarkVisibility.Visible, "electrode %s" % name)
    except Exception:
        pass

    # TODO(粘贴区): 建模 / 建操作 / 生成刀路的录制代码

    # 出 NC：优先走 API；API 不存在时报错要能看懂
    return {"electrode": name, "status": "todo"}


def main():
    task = load_task()
    session = NXOpen.Session.GetSession()
    part = open_part(task["part"])

    results = []
    for idx, electrode in enumerate(task.get("electrodes", []), start=1):
        sys.stderr.write("[%d/%d] %s\n" % (idx, len(task["electrodes"]), electrode["name"]))
        try:
            results.append(process_one(session, part, electrode))
        except Exception as exc:
            # 一个电极失败不能拖垮整批：记下来继续，最后统一汇报
            results.append({"electrode": electrode["name"], "status": "failed", "error": str(exc)})

    out = os.path.join(HOME, "state", "electrode_result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r.get("status") != "failed")
    sys.stderr.write("完成 %d/%d，结果写入 %s\n" % (ok, len(results), out))


if __name__ == "__main__":
    main()
