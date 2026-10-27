#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nc_diff.py -- NC 程序回归比对（golden file 测试）

这是「验证层」的第二个组件。nc_lint.py 回答"这个程序有没有明显错误"，
本工具回答另一个问题："我改了后处理器之后，输出到底变了哪些地方？"

为什么需要它：改后处理器的 TCL 是有风险的——你以为只加了注释，
结果动到了运动指令。有了这个工具，改完跑一次比对，
差异全在注释行就放心；差异出现在 G00/G01/换刀上就必须人工看。

这也是在没有真机的情况下，最有价值的一种"模拟"：
把后处理验证变成一个纯文本的回归测试，完全在开发机上完成。

用法:
    python nc_diff.py 旧.nc 新.nc
    python nc_diff.py golden.nc new.nc --ignore-regex "TIME=" --ignore-regex "DATE"
    python nc_diff.py golden.nc new.nc --strip-comments --json report.json

退出码:
    0 = 归一化后无差异
    1 = 有差异
    2 = 参数或文件错误
"""

import argparse
import difflib
import json
import os
import re
import sys

# 出现这些内容的差异算「高危」——它们直接决定机床怎么动
HIGH_RISK_PATTERNS = [
    (re.compile(r"\bG0*[0-3]\b"), "运动指令"),
    (re.compile(r"\bG4[123]\b"), "刀补指令"),
    (re.compile(r"\bG4[39]\b"), "刀长补偿"),
    (re.compile(r"\bM0*[36]\b"), "换刀/主轴指令"),
    (re.compile(r"\bM0*(2|30)\b"), "程序结束"),
    (re.compile(r"\b[XYZIJK][-+]?\d"), "坐标值"),
    (re.compile(r"\b[SF][-+]?\d"), "转速或进给"),
    (re.compile(r"\bT\d+\b"), "刀具号"),
]

COMMENT_RE = re.compile(r"\(.*?\)")
LINE_NO_RE = re.compile(r"^\s*N\d+\s*")


def classify(text):
    """返回 (是否高危, 命中的类别列表)"""
    upper = text.upper()
    hits = []
    for pattern, label in HIGH_RISK_PATTERNS:
        if pattern.search(upper):
            if label not in hits:
                hits.append(label)
    return bool(hits), hits


def normalize(raw, strip_comments=False, strip_line_no=True, ignore_res=()):
    """
    归一化一行，用于比对。返回 None 表示该行应被忽略。
    注意：只影响比对，展示时仍然用原始文本。
    """
    text = raw
    for rx in ignore_res:
        if rx.search(text):
            return None
    if strip_comments:
        text = COMMENT_RE.sub(" ", text)
    if strip_line_no:
        text = LINE_NO_RE.sub("", text)
    text = text.split(";")[0]
    text = re.sub(r"\s+", " ", text).strip().upper()
    if not text or text == "%":
        return None
    return text


def load(path, opts):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for idx, raw in enumerate(fh.read().splitlines(), start=1):
            norm = normalize(raw, opts.strip_comments, opts.strip_line_no, opts.ignore_res)
            if norm is None:
                continue
            rows.append({"line": idx, "raw": raw.rstrip(), "norm": norm})
    return rows


def build_diff(old_rows, new_rows):
    sm = difflib.SequenceMatcher(a=[r["norm"] for r in old_rows], b=[r["norm"] for r in new_rows], autojunk=False)

    changes = []
    stats = {"equal": 0, "insert": 0, "delete": 0, "replace": 0}

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            stats["equal"] += i2 - i1
            continue
        stats[tag] += max(i2 - i1, j2 - j1)
        if tag == "insert":
            for j in range(j1, j2):
                high, hits = classify(new_rows[j]["raw"])
                changes.append(
                    {"kind": "insert", "new_line": new_rows[j]["line"], "old_line": None,
                     "new": new_rows[j]["raw"], "old": None, "high_risk": high, "hits": hits}
                )
        elif tag == "delete":
            for i in range(i1, i2):
                high, hits = classify(old_rows[i]["raw"])
                changes.append(
                    {"kind": "delete", "new_line": None, "old_line": old_rows[i]["line"],
                     "new": None, "old": old_rows[i]["raw"], "high_risk": high, "hits": hits}
                )
        else:  # replace
            pairs = list(zip(range(i1, i2), range(j1, j2)))
            # 长度不一致时，多出来的按新增/删除处理
            for i, j in pairs:
                old_raw = old_rows[i]["raw"]
                new_raw = new_rows[j]["raw"]
                h1, hit1 = classify(old_raw)
                h2, hit2 = classify(new_raw)
                changes.append(
                    {
                        "kind": "replace",
                        "old_line": old_rows[i]["line"],
                        "new_line": new_rows[j]["line"],
                        "old": old_raw,
                        "new": new_raw,
                        "high_risk": h1 or h2,
                        "hits": sorted(set(hit1 + hit2)),
                    }
                )
            for i in range(i1 + len(pairs), i2):
                high, hits = classify(old_rows[i]["raw"])
                changes.append({"kind": "delete", "old_line": old_rows[i]["line"], "new_line": None,
                                "old": old_rows[i]["raw"], "new": None, "high_risk": high, "hits": hits})
            for j in range(j1 + len(pairs), j2):
                high, hits = classify(new_rows[j]["raw"])
                changes.append({"kind": "insert", "old_line": None, "new_line": new_rows[j]["line"],
                                "old": None, "new": new_rows[j]["raw"], "high_risk": high, "hits": hits})

    return stats, changes


def format_report(old_path, new_path, old_rows, new_rows, stats, changes):
    high = [c for c in changes if c["high_risk"]]
    lines = []
    lines.append("NC 回归比对报告")
    lines.append("  golden : %s（%d 行有效指令）" % (old_path, len(old_rows)))
    lines.append("  new    : %s（%d 行有效指令）" % (new_path, len(new_rows)))
    lines.append("  相同 %d / 新增 %d / 删除 %d / 修改 %d"
                 % (stats["equal"], stats["insert"], stats["delete"], stats["replace"]))
    lines.append("")

    if not changes:
        lines.append("  归一化后完全一致。")
        lines.append("  结论：低风险 —— 改动的效果符合预期，输出没有意外变化。")
        return "\n".join(lines)

    if high:
        lines.append("  结论：有差异")
        kinds = sorted({k for c in high for k in c["hits"]})
        lines.append("  风险：**高** —— 差异落在 %s 上，这些直接决定机床怎么走，必须人工逐条确认。"
                     % "、".join(kinds))
    else:
        lines.append("  结论：有差异")
        lines.append("  风险：低 —— 差异只出现在注释/非运动内容上，通常可以接受。")
    lines.append("")

    labels = {"insert": "新增", "delete": "删除", "replace": "修改"}
    for idx, c in enumerate(changes, start=1):
        tag = "高危" if c["high_risk"] else "低危"
        pos = []
        if c["old_line"]:
            pos.append("golden#%d" % c["old_line"])
        if c["new_line"]:
            pos.append("new#%d" % c["new_line"])
        lines.append("[%d] %s %s  %s" % (idx, labels[c["kind"]], tag, " -> ".join(pos)))
        if c["old"] is not None:
            lines.append("     - %s" % c["old"])
        if c["new"] is not None:
            lines.append("     + %s" % c["new"])
        if c["hits"]:
            lines.append("     ! 命中：%s" % "、".join(c["hits"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NC 程序回归比对")
    ap.add_argument("golden", help="基准（已验证过的旧程序）")
    ap.add_argument("new", help="新生成的程序")
    ap.add_argument("--strip-comments", action="store_true", help="比对时忽略 (...) 注释内容")
    ap.add_argument("--keep-line-numbers", action="store_true", help="不剥离行号 N123")
    ap.add_argument("--ignore-regex", action="append", default=[],
                    help="忽略命中该正则的行，可重复。例如时间戳、操作员姓名")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.golden, args.new):
        if not os.path.isfile(p):
            sys.stderr.write("找不到文件: %s\n" % p)
            return 2

    opts = argparse.Namespace(
        strip_comments=args.strip_comments,
        strip_line_no=not args.keep_line_numbers,
        ignore_res=[re.compile(p) for p in args.ignore_regex],
    )

    old_rows = load(args.golden, opts)
    new_rows = load(args.new, opts)
    stats, changes = build_diff(old_rows, new_rows)
    high = [c for c in changes if c["high_risk"]]

    report = {
        "golden": os.path.abspath(args.golden),
        "new": os.path.abspath(args.new),
        "identical": not changes,
        "stats": stats,
        "high_risk_count": len(high),
        "risk": "高" if high else ("低" if changes else "无差异"),
        "changes": changes,
    }

    if args.quiet:
        sys.stdout.write("%s vs %s: %s（差异 %d，其中高危 %d）\n"
                         % (os.path.basename(args.golden), os.path.basename(args.new),
                            "一致" if not changes else "有差异", len(changes), len(high)))
    else:
        sys.stdout.write(format_report(args.golden, args.new, old_rows, new_rows, stats, changes) + "\n")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            sys.stdout.write("JSON 报告已写入: %s\n" % args.json_out)

    return 0 if not changes else 1


if __name__ == "__main__":
    sys.exit(main())
