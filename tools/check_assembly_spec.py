# -*- coding: utf-8 -*-
"""★ 一致性核对器：证明「装配用的零件」==「NX 里建模的零件」。

背景（用户原话）：
    「配件是否都是正确的模型，是和刚才建模一样完全相同的内容，你要保证一致性嘛。」

口头保证不算数。这个脚本用 ast 解析建模 journal（j_build.py）里**真实发生的**
make_block / make_cylinder 调用，把数字抠出来，和 assembly_spec.json 逐字段比对。
任何一处被改过（建模脚本改了 spec 没改，或反过来），这里立刻报红。

循环会被展开：`for x in (-24.0, 24.0): make_cylinder((x,-22,6),...)` → 两条记录。

用法：
    python tools/check_assembly_spec.py <j_build.py> <assembly_spec.json>

退出码：0 = 一致；1 = 发现不一致；2 = 用法/文件问题
"""

from __future__ import print_function

import argparse
import ast
import json
import os
import sys


def _value(node):
    """字面数字（含 -24.0 这种一元负号）；不是字面量则 None。"""
    v = None
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        v = float(node.value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _value(node.operand)
        v = -inner if inner is not None else None
    return v


def _is_enumerate(node):
    return isinstance(node, ast.Call) and getattr(node.func, "id", None) == "enumerate"


def _loop_bindings(for_node):
    """`for x in (-24,24)` / `for i,x in enumerate((-24,24),1)` → {变量名: [取值]}。

    认不出来就返回 {}（宁可少认一条，也不能瞎猜）。
    """
    tgt = for_node.target
    if isinstance(tgt, ast.Name):
        names = [tgt.id]
    elif isinstance(tgt, (ast.Tuple, ast.List)):
        names = [n.id for n in tgt.elts if isinstance(n, ast.Name)]
    else:
        names = []

    it = for_node.iter
    vals = None
    src = it
    if _is_enumerate(it) and it.args:
        src = it.args[0]
    if isinstance(src, (ast.Tuple, ast.List)):
        v = [_value(e) for e in src.elts]
        vals = v if all(x is not None for x in v) else None

    if vals is None:
        return {}
    # enumerate：第二个名字才是值，第一个是下标
    if _is_enumerate(it) and len(names) == 2:
        return {names[1]: vals}
    if len(names) == 1:
        return {names[0]: vals}
    return {}


def _vec(node):
    """(x,y,z) 三元组；元素必须是字面量。"""
    if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) < 3:
        return None
    out = [_value(e) for e in node.elts[:3]]
    return None if any(v is None for v in out) else out


def extract_calls(src_path):
    """抠出建模脚本里所有 make_block / make_cylinder 的几何（循环按取值展开）。"""
    with open(src_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=src_path)

    out = []

    def rec(node, loops):
        if isinstance(node, ast.For):
            inner = dict(loops)
            inner.update(_loop_bindings(node))
            for ch in ast.iter_child_nodes(node):
                rec(ch, inner)
            return

        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if name in ("make_block", "make_cylinder"):
                args = node.args
                # 本调用里出现的「多值循环变量」→ 需要笛卡尔展开
                multi = {}
                for a in args:
                    for sub in ast.walk(a):
                        if isinstance(sub, ast.Name) and sub.id in loops \
                                and len(loops[sub.id]) > 1:
                            multi[sub.id] = loops[sub.id]
                keys = sorted(multi.keys())
                combos = [[]]
                for k in keys:
                    combos = [c + [v] for c in combos for v in multi[k]]

                for combo in combos:
                    env = dict(zip(keys, combo))

                    def ev(n):
                        if isinstance(n, ast.Name):
                            return env.get(n.id)
                        return _value(n)

                    if name == "make_block" and len(args) >= 2:
                        o = [ev(e) for e in args[0].elts[:3]] \
                            if isinstance(args[0], (ast.Tuple, ast.List)) else []
                        l = [ev(e) for e in args[1].elts[:3]] \
                            if isinstance(args[1], (ast.Tuple, ast.List)) else []
                        if len(o) == 3 and len(l) == 3 and \
                                None not in o and None not in l:
                            out.append({"kind": "box", "origin": o, "lengths": l,
                                        "line": node.lineno})
                    elif name == "make_cylinder" and len(args) >= 4:
                        o = [ev(e) for e in args[0].elts[:3]] \
                            if isinstance(args[0], (ast.Tuple, ast.List)) else []
                        d, h = ev(args[2]), ev(args[3])
                        if len(o) == 3 and None not in o and \
                                d is not None and h is not None:
                            out.append({"kind": "cyl", "origin": o,
                                        "diameter": d, "height": h,
                                        "line": node.lineno})

        for ch in ast.iter_child_nodes(node):
            rec(ch, loops)

    rec(tree, {})
    return out


def spec_to_records(spec):
    """spec 的 parts → 与 extract_calls 同构的记录。"""
    out = []
    for p in spec.get("parts") or []:
        nm = p.get("name", p.get("id", "?"))
        if p["type"] == "box":
            out.append({"kind": "box",
                        "origin": [float(v) for v in p["origin"]],
                        "lengths": [float(v) for v in p["lengths"]],
                        "name": nm})
        else:
            out.append({"kind": "cyl",
                        "origin": [float(p["center"][0]), float(p["center"][1]),
                                   float(p["z0"])],
                        "diameter": float(p["diameter"]),
                        "height": float(p["height"]),
                        "name": nm})
    return out


def sig(r):
    """几何指纹：只比几何，不比名字/颜色/工艺说明。"""
    if r["kind"] == "box":
        return ("box", tuple(round(v, 4) for v in r["origin"]),
                tuple(round(v, 4) for v in r["lengths"]))
    return ("cyl", tuple(round(v, 4) for v in r["origin"]),
            round(r["diameter"], 4), round(r["height"], 4))


def fmt(r):
    if r["kind"] == "box":
        return "方块 %g×%g×%g 起点(%g,%g,%g)" % tuple(
            list(r["lengths"]) + list(r["origin"]))
    return "圆柱 Ø%g×%g 起点(%g,%g,%g)" % (
        r["diameter"], r["height"], r["origin"][0], r["origin"][1], r["origin"][2])


def main(argv=None):
    p = argparse.ArgumentParser(description="核对装配 spec 与建模 journal 的一致性")
    p.add_argument("journal")
    p.add_argument("spec")
    ns = p.parse_args(argv)

    jp, spath = os.path.abspath(ns.journal), os.path.abspath(ns.spec)
    for f in (jp, spath):
        if not os.path.isfile(f):
            print("找不到文件: %s" % f)
            return 2

    with open(spath, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    nx_recs = extract_calls(jp)
    sp_recs = spec_to_records(spec)

    print("=" * 70)
    print("一致性核对：装配 spec  vs  NX 建模脚本")
    print("  建模脚本 : %s" % os.path.basename(jp))
    print("  spec     : %s" % os.path.basename(spath))
    print("=" * 70)
    print("建模脚本里实际发生的建模调用：%d 条" % len(nx_recs))
    for r in nx_recs:
        print("   第%-4d行  %s" % (r.get("line", 0), fmt(r)))
    print("spec 里声明的零件：%d 个" % len(sp_recs))
    for r in sp_recs:
        print("   %-12s %s" % (r["name"], fmt(r)))

    a, b = {}, {}
    for r in nx_recs:
        a[sig(r)] = a.get(sig(r), 0) + 1
    for r in sp_recs:
        b[sig(r)] = b.get(sig(r), 0) + 1

    print("-" * 70)
    bad = [(k, a.get(k, 0), b.get(k, 0))
           for k in sorted(set(a) | set(b)) if a.get(k, 0) != b.get(k, 0)]

    if not bad:
        print("✔ 完全一致：%d 个零件，几何逐字段相同。" % len(sp_recs))
        print("  多一个、少一个、差 0.1mm，都会被这里抓出来。")
        print("  含义：装配可视化里画的每一个零件，就是 NX 里真正建出来的那一个。")
        return 0

    print("✘ 发现 %d 处不一致：" % len(bad))
    for k, na, nb in bad:
        print("   几何 %s" % (k,))
        print("     建模脚本里 %d 个 / spec 里 %d 个" % (na, nb))
    print("")
    print("修法：改 assembly_spec.json 使其逐字等于建模脚本的调用，或改建模脚本；")
    print("      两份必须同时改，然后重跑本脚本直到全绿。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
