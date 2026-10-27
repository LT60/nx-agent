#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nc_lint.py -- NX 后处理产物（G 代码）静态校验器

这是整套 NX-Agent 里的「验证层」。Agent IDE 能全自动，是因为编译器和测试
会立刻报错；NX 侧没有这个东西，所以必须自己造一个。

它只做静态检查：不碰 NX、不需要仿真、不需要装任何第三方库。
用来在 G 代码上机床之前，先把「明显会撞刀 / 会报废 / 会被机床报警」的问题拦住。

用法:
    python nc_lint.py program.NC
    python nc_lint.py program.NC --config config/machine_fanuc.json
    python nc_lint.py program.NC --json report.json
    python nc_lint.py program.NC --quiet          # 只输出汇总行

退出码:
    0 = 通过（可以有 warning）
    1 = 存在 error，禁止上机
    2 = 参数或文件错误

设计原则:
    1. 只跑标准库，能塞进 NX 自带的那点 Python 里。
    2. 规则按机床配置开关，不做万能假设。
    3. 只报「能给出证据」的问题，不猜工艺。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 数据结构

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warning"

# 规则编号 -> 说明。规则号是给 Agent 看的，报错时可以直接把编号喂回去让它改。
RULES = {
    "R001": ("error", "程序缺少结束指令（M30 / M02）"),
    "R002": ("error", "使用了刀具表中未登记的刀具"),
    "R003": ("error", "切削移动（G01/G02/G03）没有可用进给 F"),
    "R004": ("error", "主轴转速超出机床允许范围"),
    "R005": ("error", "进给率超出机床允许范围"),
    "R006": ("error", "坐标超出机床行程"),
    "R007": ("warning", "G00 快速移动到安全平面以下"),
    "R008": ("warning", "换刀后未出现 G43 刀长补偿"),
    "R009": ("warning", "使用了 G41/G42 但未用 G40 取消"),
    "R010": ("error", "S 或 F 为 0"),
    "R011": ("warning", "处于 G91 增量模式，已跳过行程检查"),
    "R012": ("warning", "首行缺少程序号（O / N 开头）"),
    "R013": ("warning", "同一次装夹内重复调用同一刀具号"),
}


class Issue(object):
    def __init__(self, rule, line_no, block, message, severity=None):
        self.rule = rule
        self.line_no = line_no
        self.block = block
        self.message = message
        self.severity = severity or RULES.get(rule, (SEVERITY_WARN, ""))[0]

    def to_dict(self):
        return {
            "rule": self.rule,
            "severity": self.severity,
            "line": self.line_no,
            "block": self.block,
            "message": self.message,
        }


DEFAULT_CONFIG = {
    "machine": "未命名机床",
    "travel": {},
    "spindle": {"min": 1, "max": 24000},
    "feed": {"min": 1, "max": 20000},
    "safe_z": 5.0,
    "tool_table": {},
    "rules": {},          # 规则号 -> false 可单独关闭，例如 {"R013": false}
    "unit": "mm",
}


# ---------------------------------------------------------------- 解析器

_WORD_RE = re.compile(r"([A-Z])\s*([-+]?\d*\.?\d+)")


def _strip_comment(line):
    """去掉 (...) 注释与行尾 ; 注释。返回 (代码部分, 被删掉的注释文本)。"""
    out = []
    comments = []
    depth = 0
    buf = []
    for ch in line:
        if ch == "(":
            depth += 1
            buf = []
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
                comments.append("".join(buf))
                buf = []
            continue
        if depth > 0:
            buf.append(ch)
            continue
        if ch == ";":
            break
        out.append(ch)
    return "".join(out), comments


def parse_blocks(text):
    """把 NC 文本切成 block 列表：[{line, raw, words:{A:值}, comments:[]}]"""
    blocks = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        code, comments = _strip_comment(raw)
        code = code.strip()
        if not code or code == "%":
            continue
        words = {}
        order = []
        glist = []
        mlist = []
        for letter, num in _WORD_RE.findall(code.upper()):
            try:
                val = float(num)
            except ValueError:
                continue
            if letter == "G":
                glist.append(int(round(val)))
                continue
            if letter == "M":
                mlist.append(int(round(val)))
                continue
            if letter not in words:
                order.append(letter)
            words[letter] = val
        if not glist and not mlist and not words:
            continue
        blocks.append(
            {
                "line": idx,
                "raw": raw.strip(),
                "words": words,
                "order": order,
                "glist": glist,
                "mlist": mlist,
                "comments": comments,
            }
        )
    return blocks


def g_codes(block):
    """取本 block 里全部 G 代码。注意一行可能有多个：G91 G28 Z0."""
    return list(block.get("glist") or [])


def m_codes(block):
    return list(block.get("mlist") or [])


# ---------------------------------------------------------------- 检查器

MOTION_CODES = {0, 1, 2, 3}
CUT_CODES = {1, 2, 3}


class Checker(object):
    def __init__(self, config):
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(config or {})
        self.issues = []
        self.rule_switch = self.cfg.get("rules") or {}

        # 模态状态
        self.modal_motion = None
        self.modal_f = None
        self.modal_s = None
        self.modal_tool = None
        self.modal_abs = True
        self.modal_comp = None
        self.cur_pos = {"X": None, "Y": None, "Z": None}
        self.pos_known = False
        self.saw_end = False
        self.tool_comp_seen = None       # 换刀后是否见过 G43
        self.used_tools = []
        self.incremental_warned = False

    def enabled(self, rule):
        return self.rule_switch.get(rule, True)

    def add(self, rule, block, message):
        if not self.enabled(rule):
            return
        self.issues.append(Issue(rule, block["line"], block["raw"], message))

    # -------------------------------------------------- 单项规则

    def check_program_number(self, blocks):
        if not blocks:
            return
        first = blocks[0]["raw"]
        if not re.match(r"^\s*[ON]\s*\d+", first.upper()):
            self.issues.append(Issue("R012", blocks[0]["line"], first, "首行既不是 O 也不是 N 开头"))

    def check_tool(self, block):
        if "T" not in block["words"]:
            return
        t = int(round(block["words"]["T"]))
        key = "T%02d" % t
        table = self.cfg.get("tool_table") or {}
        if table and key not in table and ("T%d" % t) not in table:
            self.add("R002", block, "刀具 %s 不在刀具表 %s 中" % (key, sorted(table.keys())))
        if t == self.modal_tool:
            self.add("R013", block, "重复调用刀具 T%02d" % t)
        self.modal_tool = t
        self.used_tools.append(key)
        self.tool_comp_seen = False      # 换刀后重置刀补状态

    def check_spindle(self, block):
        if "S" not in block["words"]:
            return
        s = block["words"]["S"]
        self.modal_s = s
        if s == 0:
            self.add("R010", block, "主轴转速为 0")
            return
        lo = self.cfg["spindle"].get("min", 0)
        hi = self.cfg["spindle"].get("max", 10 ** 9)
        if s < lo or s > hi:
            self.add("R004", block, "S=%.0f 不在 %s 允许的 %s-%s 之间" % (s, self.cfg.get("machine"), lo, hi))

    def check_feed(self, block):
        if "F" not in block["words"]:
            return
        f = block["words"]["F"]
        self.modal_f = f
        if f == 0:
            self.add("R010", block, "进给 F 为 0")
            return
        lo = self.cfg["feed"].get("min", 0)
        hi = self.cfg["feed"].get("max", 10 ** 9)
        if f < lo or f > hi:
            self.add("R005", block, "F=%.0f 不在 %s 允许的 %s-%s 之间" % (f, self.cfg.get("machine"), lo, hi))

    def check_motion(self, block):
        codes = g_codes(block)
        # G28/G30 回参考点、G53 机床坐标移动：语义特殊，不做行程与安全平面判断
        is_reference = any(c in (28, 29, 30, 53) for c in codes)
        for c in codes:
            if c == 90:
                self.modal_abs = True
            elif c == 91:
                self.modal_abs = False
                if not self.incremental_warned:
                    self.incremental_warned = True
                    self.add("R011", block, "进入 G91 增量模式，行程检查已跳过")
            elif c in MOTION_CODES:
                self.modal_motion = c
            elif c == 40:
                self.modal_comp = None
            elif c in (41, 42):
                self.modal_comp = c
            elif c == 43:
                self.tool_comp_seen = True
            elif c == 49:
                self.tool_comp_seen = None

        if self.modal_motion is None:
            return

        # 切削移动必须有模态进给
        if self.modal_motion in CUT_CODES and self.modal_f is None:
            self.add("R003", block, "G%02d 切削移动，但此前从未出现过 F" % self.modal_motion)

        # 坐标更新与行程检查
        target = {}
        for ax in ("X", "Y", "Z"):
            if ax in block["words"]:
                target[ax] = block["words"][ax]

        if not target:
            return

        if self.modal_abs:
            for ax, v in target.items():
                self.cur_pos[ax] = v
            self.pos_known = True
            if not is_reference:
                self.check_travel(block, target)
        else:
            # 增量模式不累加（容易算错反而误报），只跳过
            pass

        # G00 快速移动下探到安全平面以下
        if self.modal_motion == 0 and "Z" in target and self.modal_abs and not is_reference:
            safe_z = self.cfg.get("safe_z")
            if safe_z is not None and target["Z"] < safe_z:
                self.add(
                    "R007",
                    block,
                    "G00 快速下探到 Z=%.3f，低于安全平面 Z=%.3f，存在撞刀风险" % (target["Z"], safe_z),
                )

    def check_travel(self, block, target):
        travel = self.cfg.get("travel") or {}
        for ax, v in target.items():
            rng = travel.get(ax)
            if not rng or len(rng) != 2:
                continue
            lo, hi = rng
            if v < lo or v > hi:
                self.add("R006", block, "%s=%.3f 超出机床行程 %s ~ %s" % (ax, v, lo, hi))

    def check_end(self, block):
        for m in m_codes(block):
            if m in (2, 30):
                self.saw_end = True

    def check_comp_close(self, blocks):
        if self.modal_comp is not None:
            tail = blocks[-1]
            self.issues.append(
                Issue(
                    "R009",
                    tail["line"],
                    tail["raw"],
                    "程序结束了但仍处于 G%d 刀具半径补偿状态，未见 G40" % self.modal_comp,
                )
            )

    def check_tool_comp(self, blocks):
        """换刀后一路到程序结束都没见过 G43。只在配置要求时检查。"""
        if self.tool_comp_seen is False and self.enabled("R008") and (self.cfg.get("tool_table") or {}):
            last = blocks[-1]
            self.issues.append(
                Issue("R008", last["line"], last["raw"], "最后一次换刀后未出现 G43 刀长补偿指令")
            )

    # -------------------------------------------------- 主流程

    def run(self, text):
        blocks = parse_blocks(text)
        if not blocks:
            self.issues.append(Issue("R001", 0, "", "文件中没有可解析的 NC 指令块"))
            return self.issues, blocks

        self.check_program_number(blocks)

        for block in blocks:
            self.check_tool(block)
            self.check_spindle(block)
            self.check_feed(block)
            self.check_motion(block)
            self.check_end(block)

        if not self.saw_end:
            self.issues.append(Issue("R001", blocks[-1]["line"], blocks[-1]["raw"], "程序未出现 M30 / M02"))

        self.check_comp_close(blocks)
        self.check_tool_comp(blocks)
        return self.issues, blocks


# ---------------------------------------------------------------- 输出

def format_text(issues, blocks, cfg, path):
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]
    warns = [i for i in issues if i.severity == SEVERITY_WARN]
    lines = []
    lines.append("NC 校验报告")
    lines.append("  文件    : %s" % path)
    lines.append("  机床    : %s" % cfg.get("machine"))
    lines.append("  指令块  : %d" % len(blocks))
    lines.append("  结果    : %s" % ("通过" if not errors else "不通过"))
    lines.append("  error %d / warning %d" % (len(errors), len(warns)))
    lines.append("")
    if not issues:
        lines.append("  没有发现问题。")
        return "\n".join(lines)
    for issue in sorted(issues, key=lambda x: (x.severity != SEVERITY_ERROR, x.line_no)):
        tag = "E" if issue.severity == SEVERITY_ERROR else "W"
        lines.append("[%s][%s] 第 %d 行: %s" % (tag, issue.rule, issue.line_no, issue.message))
        if issue.block:
            lines.append("        > %s" % issue.block)
    return "\n".join(lines)


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path:
        return cfg
    with open(path, "r", encoding="utf-8") as fh:
        user = json.load(fh)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description="NX 后处理 G 代码静态校验")
    ap.add_argument("nc_file")
    ap.add_argument("--config", default=None, help="机床配置文件路径 (JSON)")
    ap.add_argument("--json", dest="json_out", default=None, help="把结果另存为 JSON")
    ap.add_argument("--quiet", action="store_true", help="只输出一行汇总")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.nc_file):
        sys.stderr.write("找不到文件: %s\n" % args.nc_file)
        return 2

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        sys.stderr.write("配置文件读取失败: %s\n" % exc)
        return 2

    with open(args.nc_file, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    checker = Checker(cfg)
    issues, blocks = checker.run(text)
    errors = [i for i in issues if i.severity == SEVERITY_ERROR]

    report = {
        "file": os.path.abspath(args.nc_file),
        "machine": cfg.get("machine"),
        "blocks": len(blocks),
        "passed": not errors,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "issues": [i.to_dict() for i in issues],
    }

    if args.quiet:
        sys.stdout.write(
            "%s: %s (error %d / warning %d)\n"
            % (os.path.basename(args.nc_file), "通过" if not errors else "不通过", len(errors), len(issues) - len(errors))
        )
    else:
        sys.stdout.write(format_text(issues, blocks, cfg, args.nc_file) + "\n")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            sys.stdout.write("JSON 报告已写入: %s\n" % args.json_out)

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
