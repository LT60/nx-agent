# -*- coding: utf-8 -*-
"""离线复核 NX 报出的每一处装配干涉 —— 区分"真错"和"套接误报"。

为什么需要这一步（2026-10-10）：
  NX 的间隙分析只看**实体有没有互相插进去**，它不知道你**故意**让它们套接。
  土星五号这类打印套件，端头本来就是"套筒套住细脖"，所以"插进去"往往是设计意图。
  直接照抄 NX 的结论去改坐标，会把正确的套接改成错的对接。

判据（全用数据，不用感觉）：
  看干涉点的 Z 落在哪一对的**接头区间**里，以及那一对的 Z 关系是
  "对接（gap≈0）" 还是 "套接（gap<0，即本来就有意重叠）"。

  · 干涉点落在**套接区**里  → 预期之内，报成 INFO
  · 干涉点落在**对接界面**  → 上方零件压到下方零件，是真错，报 ERROR

用法：
  python tools/check_interference.py assembly_check.json saturn_layout.json
"""

import argparse
import io
import json
import os
import sys

TOL = 0.5
# 穿深比"设计的重叠量"多出这么多，才算真插进去了。
# 网格体（小平面体）的干涉深度本身有误差，不能要求精确相等。
DEPTH_SLACK = 2.0
# 套接量超过这个值就不合理（打印套件的插接通常只有几 mm 到几十 mm）。
# 不设上限的话，一个荒谬的"重叠 164mm"会把任何穿深都判成预期内 —— 真错被掩盖。
MAX_PLAUSIBLE_OVERLAP = 60.0


def load(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="复核 NX 报出的每一处装配干涉，区分真错与套接误报")
    ap.add_argument("check", help="assembly_check.json")
    ap.add_argument("layout", nargs="?", help="saturn_layout.json")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出结构化结论（给页面/其它工具消费）")
    args = ap.parse_args(argv)
    chk_path = args.check
    lay_path = args.layout
    as_json = args.json

    chk = load(chk_path)
    clr = chk.get("clearance") or {}
    ifaces = clr.get("interferences") or []
    problems = []
    rows = []

    if not ifaces:
        print("报告中没有干涉记录（%s）" % chk_path)
        return 0

    print("=" * 92)
    print("NX 装配干涉逐处复核")
    print("=" * 92)
    print("净空 %s ｜ Summary: %s" % (clr.get("zone"), json.dumps(
        clr.get("summary") or {}, ensure_ascii=False)))
    print()

    # 布局里每件的 Z 区间与接头类型
    zrange = {}
    joints = {}
    if lay_path and os.path.isfile(lay_path):
        lay = load(lay_path)
        for p in lay.get("parts") or []:
            zrange[p["label"]] = (p["z_bottom"], p["z_top"])
        for j in lay.get("joints") or []:
            joints[frozenset((j.get("below"), j.get("above")))] = j
        print("已读布局 %s：%d 件，%d 个接头"
              % (os.path.basename(lay_path), len(zrange), len(joints)))
        print()

    print("%-22s %-22s %9s  %-10s %s"
          % ("零件 A", "零件 B", "穿深mm", "接头关系", "判定"))
    print("-" * 92)

    for r in ifaces:
        a, b = r.get("a"), r.get("b")
        depth = float(r.get("depth_mm") or 0.0)
        # ★ 不用 point 坐标判位置：实测在网格体上它不可靠
        #   （逃逸塔报 z=822，而它在装配里明明是 z=1037.8~1148.7）。
        #   depth 值是可靠的 —— 两端点距离验算过（33.93 ≈ depth 33.9194）。
        z = None

        key = frozenset((a, b))
        j = joints.get(key)
        verdict, why, kind, zone = "?", "", "—", ""

        # ★ 真实 gap = 上方零件底面 - 下方零件顶面。用实际 Z 区间算，
        #   不信 joints 里的字段（它的键叫 overlap，而且我实测它没反映真实重叠）。
        # ★ 先看这两件在布局里**是否相邻** —— 不相邻就不能拿它们的
        #   Z 区间算"接头重叠"，否则会得出荒谬结论
        #   （例：S-II 与 S-IC顶 中间隔着 1-2 级间段，报"71.5mm 空隙"就是错的）。
        adjacent = (j is not None)
        if a in zrange and b in zrange and not adjacent:
            za, zb = zrange[a], zrange[b]
            # 判据：两件的 Z 区间必须**真的搭上或挨着**，否则不是接头对
            adjacent = (za[0] <= zb[1] + TOL and zb[0] <= za[1] + TOL)

        if a in zrange and b in zrange and adjacent:
            lo_a, hi_a = zrange[a]
            lo_b, hi_b = zrange[b]
            below, above = (a, b) if lo_a <= lo_b else (b, a)
            gap = zrange[above][0] - zrange[below][1]     # 负=重叠(套接)
            overlap = -gap if gap < 0 else 0.0
            kind = ("套接 %.1f" % overlap if gap < -TOL
                    else ("对接" if abs(gap) <= TOL else "空隙 %.1f" % gap))
            zlo = min(zrange[a][0], zrange[b][0])
            zhi = max(zrange[a][1], zrange[b][1])

            if gap > TOL:
                verdict = "ERROR"
                why = "接头本身有 %.1fmm 空隙（还没装上就报干涉了）" % gap
                problems.append((a, b, depth, why))
            elif overlap > MAX_PLAUSIBLE_OVERLAP:
                # ★ 重叠量本身就不合理 —— 报它，别让"大重叠"变成免罪金牌
                verdict = "ERROR"
                why = ("套接量 %.1fmm 远超合理上限 %.0fmm，"
                       "布局这一段本身就错了" % (overlap, MAX_PLAUSIBLE_OVERLAP))
                problems.append((a, b, depth, why))
            elif depth > overlap + DEPTH_SLACK:
                # ★★★ 主判据：穿深**超过**设计的重叠量 = 真插进去了 = 真错
                verdict = "ERROR"
                why = ("只该套接 %.1fmm，却插进 %.1fmm（多 %.1fmm）"
                       % (overlap, depth, depth - overlap))
                problems.append((a, b, depth, why))
            elif depth > TOL:
                verdict = "INFO"
                why = "穿深 %.2fmm ≤ 设计的 %.1fmm 套接量，属预期" % (depth, overlap)
            else:
                verdict = "INFO"
                why = "零穿深，只是面接触"
            zone = "接头区 z∈[%.1f, %.1f]" % (
                zrange[below][1], zrange[above][0])
        else:
            # 不相邻却报干涉 —— 这本身是个信号：两件在 Z 上根本不该碰，
            # 却碰上了，说明有零件的 Z 位置算错了。
            verdict = "WARN"
            why = "两件在布局里不相邻，却检出干涉（可能是别处错位导致）"
            problems.append((a, b, depth, why))

        rows.append({"a": a, "b": b, "depth": depth, "verdict": verdict,
                     "why": why, "kind": kind, "zone": zone,
                     "pair": a + " × " + b})
        print("%-22s %-22s %9.3f  %-10s %s"
              % (str(a)[:22], str(b)[:22], depth, kind if j else "—",
                 "%s（%s）" % (verdict, why)))
        if z is not None:
            print("%-46s %s" % ("", zone))

    print()
    n_err = sum(1 for r in rows if r["verdict"] == "ERROR")
    n_info = sum(1 for r in rows if r["verdict"] == "INFO")
    n_warn = sum(1 for r in rows if r["verdict"] == "WARN")
    print("=" * 92)
    print("复核结果：ERROR %d ｜ WARN %d ｜ INFO %d（INFO=套接误报，属预期）"
          % (n_err, n_warn, n_info))
    if problems:
        print()
        print("需要处理的：")
        for a, b, d, why in problems:
            print("  ✘ %s × %s  穿深 %.3f mm — %s" % (a, b, d, why))
    else:
        print("✔ 所有干涉都落在有意套接上，没有真正的装配错误。")

    # ★ 同一批结论也以 JSON 吐出 —— 页面/其它工具**不要解析上面的文本**
    if as_json:
        print("@@JSON@@" + json.dumps({
            "zone": clr.get("zone"),
            "summary": clr.get("summary") or {},
            "n_interferences": clr.get("n_interferences"),
            "rows": rows,
            "problems": [{"a": a, "b": b, "depth": d, "why": w}
                         for a, b, d, w in problems],
        }, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())