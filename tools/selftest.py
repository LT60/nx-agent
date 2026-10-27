#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py -- 全项目离线自检

一条命令，把 nx-agent 里所有能在「没有 NX、没有机床、没有许可证」的机器上
验证的东西全跑一遍，最后给一张通过/失败清单。

为什么要有这个：整套系统的可验证部分全靠自建，装 NX 之前必须先证明
「校验层本身是对的」——否则等 NX 装好了，出了问题你分不清是
NX 那边的问题还是校验器自己的 bug。

它覆盖：
  1  全部脚本的语法
  2  nc_lint：能不能准确抓出样例里埋的错
  3  nc_diff：能不能把高危差异和低危差异分开
  4  MCP 协议：握手 / 工具列表 / 工具调用
  5  MCP：桥接不在线时是否「立即失败」而不是干等
  6  桥接：NX 外调用是否优雅报错（BridgeError + 原始 traceback）
  7  心跳判活：工具能否区分「桥接死了」和「桥接活着但卡住」
  8  队列过期隔离：过期的 stop 不能让下次启动的桥接自杀
  9  快照：拍快照 → 改坏 → 回滚，内容是否真的回来了
 10  录制参数化模板：批量循环与「单个失败不拖垮整批」
 11  装机自检脚本本身能不能跑

用法:
    python tools/selftest.py
    python tools/selftest.py -v          # 打印每一步的完整输出

退出码:
    0 = 全部通过
    1 = 有失败项
    2 = 运行环境有问题（比如找不到项目根目录）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

VERBOSE = False
RESULTS = []


def log(msg):
    if VERBOSE:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()


def record(name, ok, detail):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})


def run(args, env=None, timeout=60, cwd=None):
    """跑一个子进程，返回 (returncode, stdout, stderr)。"""
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd or ROOT,
        env=child_env,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ---------------------------------------------------------------- 1. 语法

def t_syntax():
    bad = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "downloads", "snapshots")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    compile(fh.read(), path, "exec")
            except SyntaxError as exc:
                bad.append("%s:%s %s" % (os.path.relpath(path, ROOT), exc.lineno, exc.msg))
            except Exception as exc:
                bad.append("%s (%s)" % (os.path.relpath(path, ROOT), exc))
    if bad:
        record("语法检查", False, "有问题的文件：\n      " + "\n      ".join(bad))
    else:
        record("语法检查", True, "全部 .py 通过 compile()")


# ---------------------------------------------------------------- 2. nc_lint

def _lint(nc_file, config=None):
    sys.path.insert(0, os.path.join(ROOT, "verify"))
    import nc_lint
    cfg = nc_lint.load_config(config or os.path.join(ROOT, "config", "machine_fanuc.json"))
    with open(nc_file, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    checker = nc_lint.Checker(cfg)
    issues, blocks = checker.run(text)
    errors = [i for i in issues if i.severity == "error"]
    return issues, errors, blocks


def t_nc_lint_demo():
    demo = os.path.join(ROOT, "samples", "DEMO_V1.NC")
    issues, errors, blocks = _lint(demo)
    warnings = [i for i in issues if i.severity == "warning"]
    detail = ("%s：%d 个指令块 → %d error / %d warning\n      规则命中：%s"
              % (os.path.basename(demo), len(blocks), len(errors), len(warnings),
                 "、".join(sorted({i.rule for i in issues}))))
    ok = len(errors) == 4 and len(warnings) == 3
    record("nc_lint 抓错（样例埋了 4 error + 3 warning）", ok, detail)


def t_nc_lint_golden():
    golden = os.path.join(ROOT, "samples", "POST_GOLDEN.NC")
    issues, errors, blocks = _lint(golden)
    ok = not errors
    record("nc_lint 不误报（基准程序应 0 error）", ok,
           "%s：%d 个指令块 → %d error / %d warning"
           % (os.path.basename(golden), len(blocks), len(errors), len(issues) - len(errors)))


# ---------------------------------------------------------------- 3. nc_diff

def _diff(golden, new, ignore_regex=None):
    sys.path.insert(0, os.path.join(ROOT, "verify"))
    import re
    import nc_diff
    opts = type("O", (), {})()
    opts.strip_comments = False
    opts.strip_line_no = True
    opts.ignore_res = [re.compile(p) for p in (ignore_regex or [])]
    old_rows = nc_diff.load(golden, opts)
    new_rows = nc_diff.load(new, opts)
    stats, changes = nc_diff.build_diff(old_rows, new_rows)
    return stats, changes


def t_nc_diff_highrisk():
    golden = os.path.join(ROOT, "samples", "POST_GOLDEN.NC")
    new = os.path.join(ROOT, "samples", "POST_AFTER_TCL_EDIT.NC")
    stats, changes = _diff(golden, new)
    high = [c for c in changes if c["high_risk"]]
    low = [c for c in changes if not c["high_risk"]]
    hit = sorted({k for c in high for k in c["hits"]})
    detail = ("差异 %d 处（高危 %d / 低危 %d）\n      高危内容：%s\n      高危明细：%s\n      低危明细：%s"
              % (len(changes), len(high), len(low), "、".join(hit),
                 " | ".join("%s→%s" % (c["old"], c["new"]) for c in high),
                 " | ".join(str(c["new"]) for c in low)))
    # 样例里埋的是：F1500→F1200（高危）、新增 G40 G49（高危）、两行注释（低危）
    ok = len(high) == 2 and len(low) == 2
    record("nc_diff 分离高危/低危（应 2 高危 + 2 低危）", ok, detail)


def t_nc_diff_identical():
    golden = os.path.join(ROOT, "samples", "POST_GOLDEN.NC")
    stats, changes = _diff(golden, golden)
    ok = not changes and stats["equal"] > 0
    record("nc_diff 自比对无差异（防误报）", ok,
           "相同 %d 行，差异 %d 处" % (stats["equal"], len(changes)))


def t_nc_diff_ignore():
    golden = os.path.join(ROOT, "samples", "POST_GOLDEN.NC")
    new = os.path.join(ROOT, "samples", "POST_AFTER_TCL_EDIT.NC")
    stats, changes = _diff(golden, new, ignore_regex=[r"DATE:", r"PROGRAMMER:"])
    high = [c for c in changes if c["high_risk"]]
    ok = len(high) == 2 and len(changes) == 2
    record("nc_diff 忽略规则（过滤噪音后只剩真问题）", ok,
           "忽略时间戳/人名后：差异 %d 处，其中高危 %d 处" % (len(changes), len(high)))


# ---------------------------------------------------------------- 4/5. MCP

def _isolated_home(tag):
    """造一个空的临时 HOME，专供「只碰数据、不碰代码」的用例。

    ⚠️ 注意 `NX_AGENT_HOME` 在本项目里是**双重身份**：
    既是数据根（queue/、state/、snapshots/），也是代码根
    （`mcp/server.py` 会 `sys.path.insert(HOME/verify)` 来找 `nc_lint` / `nc_diff`）。
    所以临时 HOME 只能给**不调 nc_validate / nc_diff / nx_snapshot** 的用例用，
    否则会连 `config/machine_fanuc.json` 和 `verify/*.py` 一起找不到。

    这个坑是 2026-10-01 实测撞出来的：一开始我把所有 MCP 用例都换成临时 HOME，
    结果 nc_validate 和 nc_diff 两项当场变红。
    """
    tmp = tempfile.mkdtemp(prefix="nxagent-%s-" % tag)
    for sub in ("queue/in", "queue/out", "queue/stale", "state"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    return tmp


def mcp_call(requests, timeout=60, home=None):
    """跑一次 MCP 服务端，把请求喂进 stdin，收回所有响应。

    服务端读 stdin 到 EOF 就退出，所以一次性把所有请求写进去、关掉 stdin，
    再统一收响应。这样一个进程里能测多个方法，比反复启动快。

    `home=None`（默认）= 不覆盖 `NX_AGENT_HOME`，服务端自定位到项目根。
    需要「保证桥接不在线」的用例才传一个临时 HOME 进来。
    """
    env = dict(os.environ)
    if home:
        env["NX_AGENT_HOME"] = home
    proc = subprocess.run(
        [PY, os.path.join(ROOT, "mcp", "server.py")],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True, text=True, timeout=timeout, cwd=ROOT, env=env,
        encoding="utf-8", errors="replace",
    )
    replies = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            replies.append(json.loads(line))
        except Exception:
            pass
    log("MCP stderr:\n%s" % (proc.stderr or ""))
    return replies


def t_mcp_protocol():
    demo = os.path.join(ROOT, "samples", "DEMO_V1.NC")
    replies = mcp_call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "nc_validate", "arguments": {"nc_file": demo}}},
    ])
    by_id = {r.get("id"): r for r in replies}

    problems = []
    init = by_id.get(1, {}).get("result", {})
    if not init.get("capabilities"):
        problems.append("initialize 没有返回 capabilities")
    if init.get("protocolVersion") != "2024-11-05":
        problems.append("protocolVersion 不是 2024-11-05")

    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    names = [t["name"] for t in tools]
    # 用下限而不是死数字：新增工具不该让自检变红。真正要守住的是「关键工具在」。
    if len(tools) < 11:
        problems.append("工具数 %d，少于预期的 11 个" % len(tools))
    for required in ("nx_status", "nx_bridge_stop", "nc_validate", "nc_diff", "nx_run_journal", "nx_snapshot"):
        if required not in names:
            problems.append("缺工具 %s" % required)

    call = by_id.get(3, {}).get("result", {})
    text = (call.get("content") or [{}])[0].get("text", "")
    if not call.get("isError"):
        problems.append("nc_validate 跑有错的程序却没返回 isError=true")
    if "error 4" not in text:
        problems.append("nc_validate 输出里没有 'error 4'")

    detail = ("握手协议 %s ｜ 工具 %d 个 ｜ nc_validate 返回 isError=%s\n      工具：%s"
              % (init.get("protocolVersion"), len(tools), call.get("isError"),
                 "、".join(names)))
    record("MCP 协议（握手 / 工具列表 / 工具调用）", not problems,
           detail if not problems else detail + "\n      问题：" + "；".join(problems))


def t_mcp_nc_diff_tool():
    golden = os.path.join(ROOT, "samples", "POST_GOLDEN.NC")
    new = os.path.join(ROOT, "samples", "POST_AFTER_TCL_EDIT.NC")
    same = os.path.join(ROOT, "samples", "POST_GOLDEN.NC")
    replies = mcp_call([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "nc_diff", "arguments": {"golden": golden, "new": new}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "nc_diff", "arguments": {"golden": golden, "new": same}}},
    ])
    by_id = {r.get("id"): r for r in replies}
    risky = by_id.get(1, {}).get("result", {})
    clean = by_id.get(2, {}).get("result", {})
    ok = bool(risky.get("isError")) and not clean.get("isError")
    record("nc_diff 作为关卡（高危→isError，无差异→放行）", ok,
           "有高危差异: isError=%s ｜ 无差异: isError=%s"
           % (risky.get("isError"), clean.get("isError")))


def t_mcp_bridge_offline_fastfail():
    """桥接不在线时必须立即报错，不能干等。

    这里必须用**隔离 HOME**：MCP 服务端是自定位的，不隔离就会读到项目根里
    真实存在的 state/heartbeat.json。后果是 —— 弟弟那边桥接正开着的时候跑自检，
    这一项会真的去调 NX，然后要么超时挂住、要么结果随机。
    """
    started = time.time()
    replies = mcp_call([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "nx_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "nx_part_info", "arguments": {}}},
    ], timeout=30, home=_isolated_home("mcp-offline"))
    elapsed = time.time() - started
    by_id = {r.get("id"): r for r in replies}

    status_text = (by_id.get(1, {}).get("result", {}).get("content") or [{}])[0].get("text", "")
    info_res = by_id.get(2, {}).get("result", {})
    info_text = (info_res.get("content") or [{}])[0].get("text", "")

    problems = []
    if '"online": false' not in status_text:
        problems.append("nx_status 没报告 online=false")
    if not info_res.get("isError"):
        problems.append("桥接不在线时 nx_part_info 没有报错")
    if "不在线" not in info_text:
        problems.append("错误信息里没有说明桥接不在线")
    if "从来没有跑过" not in info_text:
        problems.append("读到的不是隔离环境（应该报「从来没有跑过」，说明 MCP 自检没隔离 NX_AGENT_HOME）")
    if "start_bridge" not in info_text:
        problems.append("不在线时的提示没有指向 start_bridge.py")
    if elapsed > 15:
        problems.append("耗时 %.1f 秒，太久了（应该立即失败）" % elapsed)

    record("桥接离线「立即失败」（不能干等）", not problems,
           "两次调用共耗时 %.2f 秒 ｜ nx_part_info isError=%s\n      错误信息：%s"
           % (elapsed, info_res.get("isError"), info_text.splitlines()[0] if info_text else "")
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


# ---------------------------------------------------------------- 6. 桥接

def t_bridge_outside_nx():
    """把请求投进队列，跑一次桥接，分两种情况看它怎么答。

    关键区分：
      ping  —— 只是「桥接进程活着吗」，不需要 NXOpen，所以应该成功
      info  —— 要读当前工作部件，必须有 NXOpen，所以应该优雅地报 BridgeError
    之前我把 ping 也当成该失败，是测试写错了：ping 能在外网回答，恰恰是设计意图
    （好让 Agent 能分辨「桥接没起」和「桥接起了但没 NX」）。
    """
    tmp = tempfile.mkdtemp(prefix="nxagent-bridge-")
    try:
        qin = os.path.join(tmp, "queue", "in")
        qout = os.path.join(tmp, "queue", "out")
        os.makedirs(qin)
        os.makedirs(qout)
        os.makedirs(os.path.join(tmp, "state"))

        # journal 想把数据带回给 Agent，就在作用域里放 RESULT / RESULT_NOTE。
        # 没有这条回话通道，journal 只能说"跑完了"，说不了"做成了什么"。
        mini_journal = os.path.join(tmp, "mini_journal.py")
        with open(mini_journal, "w", encoding="utf-8") as fh:
            fh.write("RESULT = {'made': 'blank', 'size': PARAMS.get('size')}\n"
                     "RESULT_NOTE = '自检用的最小 journal'\n")

        jobs = {
            "selftest-ping": {"cmd": "ping", "args": {}},
            "selftest-info": {"cmd": "info", "args": {}},
            "selftest-journal": {"cmd": "run_journal",
                                 "args": {"path": mini_journal, "params": {"size": 7}}},
        }
        for rid, body in jobs.items():
            with open(os.path.join(qin, rid + ".json"), "w", encoding="utf-8") as fh:
                json.dump({"id": rid, "cmd": body["cmd"], "args": body["args"]}, fh)

        code, out, err = run([PY, os.path.join(ROOT, "bridge", "nx_bridge.py"), "--once"],
                             env={"NX_AGENT_HOME": tmp}, timeout=60)

        responses = {}
        for rid in jobs:
            path = os.path.join(qout, rid + ".json")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    responses[rid] = json.load(fh)

        problems = []
        ping = responses.get("selftest-ping")
        info = responses.get("selftest-info")
        journal = responses.get("selftest-journal")

        if journal is None:
            problems.append("run_journal 没有响应文件")
        else:
            if journal.get("ok") is not True:
                problems.append("不碰 NXOpen 的 journal 在 NX 外也应该能跑通")
            jr = journal.get("result", {}).get("result")
            if not isinstance(jr, dict) or jr.get("made") != "blank" or jr.get("size") != 7:
                problems.append("journal 的 RESULT 没有被带回（%r）" % (jr,))
            if journal.get("result", {}).get("result_note") != "自检用的最小 journal":
                problems.append("journal 的 RESULT_NOTE 没有被带回")

        if ping is None:
            problems.append("ping 没有响应文件")
        else:
            if ping.get("ok") is not True:
                problems.append("ping 在 NX 外应该仍能回答（它只测桥接进程活没活）")
            if ping.get("result", {}).get("nx_available") is not False:
                problems.append("ping 没有报告 nx_available=false")

        if info is None:
            problems.append("info 没有响应文件")
        else:
            if info.get("ok") is not False:
                problems.append("info 在 NX 外应该失败")
            if info.get("kind") != "BridgeError":
                problems.append("info 的错误类型不是 BridgeError，而是 %r" % info.get("kind"))
            if "NX" not in (info.get("error") or ""):
                problems.append("info 的错误信息没说清原因")

        detail = ("ping  → %s\n      info  → %s"
                  % (json.dumps(ping, ensure_ascii=False)[:200] if ping else "无响应",
                     json.dumps(info, ensure_ascii=False)[:220] if info else "无响应"))
        record("桥接在 NX 外：ping 能答 / 需要 NXOpen 的命令优雅报错", not problems,
               detail + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 7. 快照

def t_snapshot_roundtrip():
    """拍快照 → 改坏 → 回滚，验证内容真的退回来了。

    同时验证三种「指定快照」的写法都能用（完整路径 / 文件名 / 序号）。
    序号那条是本次自检逼出来的修复：`list` 输出带序号，但 restore 原来不认序号。
    """
    tmp = tempfile.mkdtemp(prefix="nxagent-snap-")
    try:
        prt = os.path.join(tmp, "demo.prt")
        env = {"NX_AGENT_HOME": tmp}
        snap_py = os.path.join(ROOT, "tools", "snapshot.py")

        def write(content):
            with open(prt, "w", encoding="utf-8") as fh:
                fh.write(content)

        def read():
            with open(prt, "r", encoding="utf-8") as fh:
                return fh.read().strip()

        problems = []

        # --- 第 1 轮：用序号回滚 ---
        write("fake prt v1\n")
        code, out, err = run([PY, snap_py, "snapshot", prt, "--note", "自检-序号"], env=env)
        if code != 0:
            record("快照与回滚（三种指定方式都可用）", False, "拍快照失败：%s" % (err or out))
            return

        write("fake prt v2 BROKEN\n")
        code, out, err = run([PY, snap_py, "restore", "1", "--to", prt], env=env)
        if code != 0:
            problems.append("按序号 1 回滚失败（退出码 %s）：%s" % (code, (err or out).strip()[:160]))
        elif read() != "fake prt v1":
            problems.append("按序号回滚后内容不对：%r" % read())

        prerestore = [s for s in os.listdir(os.path.join(tmp, "snapshots")) if "prerestore" in s]
        if not prerestore:
            problems.append("回滚前没有备份现状（回滚错了就没法回头）")

        # --- 第 2 轮：用文件名回滚 ---
        write("fake prt v3\n")
        code, out, err = run([PY, snap_py, "snapshot", prt, "--note", "自检-文件名"], env=env)
        snaps = sorted(s for s in os.listdir(os.path.join(tmp, "snapshots"))
                       if s.endswith(".bak") and "prerestore" not in s)
        if len(snaps) < 2:
            problems.append("第二次快照没生成，只有 %d 个" % len(snaps))
        else:
            write("fake prt v4 BROKEN\n")
            code, out, err = run([PY, snap_py, "restore", snaps[0], "--to", prt], env=env)
            if code != 0:
                problems.append("按文件名回滚失败：%s" % (err or out).strip()[:160])
            elif read() != "fake prt v1":
                problems.append("按文件名回滚后内容不对：%r" % read())

        # --- 第 3 轮：给一个不存在的序号，应该给出可选清单而不是裸报错 ---
        code, out, err = run([PY, snap_py, "restore", "99", "--to", prt], env=env)
        if code == 0:
            problems.append("不存在的序号 99 居然成功了，应该失败")
        elif "当前可选的快照" not in (err or ""):
            problems.append("报错信息里没有列出可选快照，用户没法自己纠正")

        record("快照与回滚（序号/文件名都能用，回滚前有备份，报错可自纠）", not problems,
               "快照 %d 个 ｜ 回滚后内容=%r ｜ 回滚前备份 %d 个"
               % (len(snaps), read(), len(prerestore))
               + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 8. 模板

def t_template_loop():
    """用假的 NXOpen 桩子跑批量模板，验证循环与「单个失败不拖垮整批」。"""
    tmp = tempfile.mkdtemp(prefix="nxagent-tpl-")
    try:
        os.makedirs(os.path.join(tmp, "state"))
        task = {
            "part": "D:/mold/E-0021.prt",
            "post": "fanuc_mill_4A_metric",
            "out_dir": "D:/mold/nc",
            "electrodes": [
                {"name": "E-0021-01", "x": 1.0, "y": 2.0, "z": 0.0, "tool": 1, "stock": 0.3},
                {"name": "E-0021-02", "x": 3.0, "y": 4.0, "z": 0.0, "tool": 2, "stock": 0.2},
                {"name": "E-0021-03", "x": 5.0, "y": 6.0, "z": 0.0, "tool": 3, "stock": 0.1},
            ],
        }
        with open(os.path.join(tmp, "state", "electrode_task.json"), "w", encoding="utf-8") as fh:
            json.dump(task, fh, ensure_ascii=False)
        os.environ["NX_AGENT_HOME"] = tmp

        # --- 伪造 NXOpen，让模板能在 NX 外面跑起来 ---
        import types
        fake = types.ModuleType("NXOpen")

        class _MarkVisibility:
            Visible = "Visible"

        class _PartCollection:
            Work = object()

            def OpenActiveDisplay(self, path, opt):
                return None

        class _Session:
            MarkVisibility = _MarkVisibility()

            def __init__(self):
                self.Parts = _PartCollection()
                self.marks = []

            def SetUndoMark(self, visibility, label):
                self.marks.append(label)

        class _SessionNs:
            @staticmethod
            def GetSession():
                return _Session()

        fake.Session = _SessionNs
        fake.DisplayPartOption = type("D", (), {"AllowAdditional": 1})
        sys.modules["NXOpen"] = fake

        # --- 导入模板 ---
        import importlib.util
        path = os.path.join(ROOT, "templates", "electrode_batch.py")
        spec = importlib.util.spec_from_file_location("electrode_batch_tpl", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # --- 让第 2 个电极失败，验证失败隔离 ---
        original = mod.process_one
        calls = []

        def patched(session, part, electrode):
            calls.append(electrode["name"])
            if electrode["name"] == "E-0021-02":
                raise RuntimeError("模拟：这个电极的刀路算不出来")
            return original(session, part, electrode)

        mod.process_one = patched
        mod.main()

        with open(os.path.join(tmp, "state", "electrode_result.json"), "r", encoding="utf-8") as fh:
            results = json.load(fh)

        by_name = {r["electrode"]: r for r in results}
        problems = []
        if len(results) != 3:
            problems.append("结果条数 %d，期望 3" % len(results))
        if len(calls) != 3:
            problems.append("只处理了 %d 个电极，说明第 2 个失败后中止了整批" % len(calls))
        if by_name.get("E-0021-02", {}).get("status") != "failed":
            problems.append("失败的电极没有被标记为 failed")
        if by_name.get("E-0021-03", {}).get("status") == "failed":
            problems.append("第 3 个电极被前一个的失败连累了")
        if "算不出来" not in str(by_name.get("E-0021-02", {}).get("error", "")):
            problems.append("失败原因没有带出来给 Agent")

        record("录制模板：循环 + 单个失败不拖垮整批", not problems,
               "处理 3 个电极 → 结果：%s"
               % "、".join("%s=%s" % (r["electrode"], r["status"]) for r in results)
               + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        os.environ.pop("NX_AGENT_HOME", None)
        sys.modules.pop("NXOpen", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 9. 装机自检

def t_preflight_runs():
    tmp = tempfile.mkdtemp(prefix="nxagent-pf-")
    try:
        out_json = os.path.join(tmp, "r.json")
        code, out, err = run([PY, os.path.join(ROOT, "tools", "preflight_check.py"),
                              "--json", out_json], timeout=120)
        problems = []
        if code not in (0, 1):
            problems.append("退出码 %s" % code)
        if not os.path.isfile(out_json):
            problems.append("没有生成 JSON 报告")
        else:
            with open(out_json, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            names = [c["name"] for c in data.get("preflight", [])]
            for want in ("操作系统", "内存", "磁盘", "显卡"):
                if want not in names:
                    problems.append("没检查 %s" % want)
            fails = [c for c in data.get("preflight", []) if c["level"] == "FAIL"]
            detail = ("检查项 %d 个（%d 项 FAIL）\n      本机：%s"
                      % (len(names), len(fails),
                         " ｜ ".join("%s=%s" % (c["name"], c["level"])
                                    for c in data.get("preflight", []))))
            record("装机自检脚本能跑（并给出机器判定）", not problems,
                   detail + ("" if not problems else "\n      问题：" + "；".join(problems)))
            return
        record("装机自检脚本能跑（并给出机器判定）", False,
               "问题：" + "；".join(problems) + "\n      stderr=%s" % err[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 主流程

# ---------------------------------------------------------------- 7. 心跳判活

def t_bridge_probe_heartbeat():
    """心跳判活：必须能区分「桥接死了」和「桥接活着但卡住了」。

    为什么这条要单独测：Agent 完全靠 state/heartbeat.json 判断 NX 那边还能不能干活。
    这里最容易犯的错是「只看文件年龄」—— 桥接刚被 NX 弹窗挡住、或者刚被杀掉的那几十秒里，
    心跳文件仍然是新鲜的，会被误判成在线，然后 Agent 就开始傻等。
    所以判据必须是「PID 还活着吗」+「文件有多旧」两条一起看。
    """
    tmp = tempfile.mkdtemp(prefix="nxagent-hb-")
    try:
        state = os.path.join(tmp, "state")
        os.makedirs(state)
        hb = os.path.join(state, "heartbeat.json")
        probe = os.path.join(ROOT, "tools", "bridge_probe.py")

        def run_probe():
            code, out, err = run([PY, probe, "--quick"], env={"NX_AGENT_HOME": tmp}, timeout=30)
            level = "?"
            for line in out.splitlines():
                if line.startswith("["):
                    level = line[1:line.index("]")]
                    break
            return code, level

        def write_hb(pid):
            with open(hb, "w", encoding="utf-8") as fh:
                json.dump({"pid": pid, "mode": "watch", "processed": 3}, fh)

        problems = []
        lines = []

        code, level = run_probe()
        lines.append("没有心跳文件        期望 FAIL ｜ 实际 %s (退出码 %d)" % (level, code))
        if level != "FAIL" or code != 1:
            problems.append("没有心跳文件时应当 FAIL 且退出码 1")

        write_hb(999999)  # 几乎必然不存在的 PID
        code, level = run_probe()
        lines.append("进程已死+文件新鲜   期望 FAIL ｜ 实际 %s (退出码 %d)" % (level, code))
        if level != "FAIL" or code != 1:
            problems.append("PID 已消失时必须判 FAIL —— 不能因为文件新鲜就说它在线")

        write_hb(os.getpid())  # 本进程，确定存活
        old = time.time() - 300
        os.utime(hb, (old, old))
        code, level = run_probe()
        lines.append("进程存活+心跳陈旧   期望 WARN ｜ 实际 %s (退出码 %d)" % (level, code))
        if level != "WARN" or code != 0:
            problems.append("进程活着但心跳陈旧时应当 WARN（提示去 NX 点掉弹窗），而不是 FAIL")

        write_hb(os.getpid())
        code, level = run_probe()
        lines.append("进程存活+心跳新鲜   期望 PASS ｜ 实际 %s (退出码 %d)" % (level, code))
        if level != "PASS" or code != 0:
            problems.append("进程活着且心跳新鲜时应当 PASS")

        record("心跳判活：区分「死了」和「活着但卡住」", not problems,
               "\n      ".join(lines) + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 8. 队列过期

def t_bridge_stale_queue():
    """排队的过期请求必须被隔离，而不是执行。

    真实踩过的坑：桥接是「重启后接着处理队列」的设计。有一次队列里躺着上一轮
    测试留下的 stop 请求，桥接一启动就把它执行了，0.2 秒就自己退出，
    心跳里写的是 note=clean —— 看起来像"正常结束"，实际是自杀，非常难查。

    所以必须保证：过期的请求不执行、被挪到 queue/stale/、并且说明原因。
    同时不能误伤新鲜的请求。
    """
    tmp = tempfile.mkdtemp(prefix="nxagent-stale-")
    try:
        qin = os.path.join(tmp, "queue", "in")
        qout = os.path.join(tmp, "queue", "out")
        os.makedirs(qin)
        os.makedirs(qout)
        os.makedirs(os.path.join(tmp, "state"))

        def put(name, cmd, age_seconds):
            path = os.path.join(qin, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"id": name[:-5], "cmd": cmd, "args": {}}, fh)
            old = time.time() - age_seconds
            os.utime(path, (old, old))

        put("old-stop.json", "stop", 3600)     # 一小时前的 stop：绝不能执行
        put("fresh-ping.json", "ping", 0)      # 刚投的 ping：必须正常执行

        code, out, err = run(
            [PY, os.path.join(ROOT, "bridge", "nx_bridge.py"), "--once"],
            env={"NX_AGENT_HOME": tmp}, timeout=60)

        problems = []
        stale_dir = os.path.join(tmp, "queue", "stale")

        left_in = [f for f in os.listdir(qin) if f.endswith(".json")]
        if left_in:
            problems.append("queue/in 没被清空：%s" % left_in)

        if not os.path.isdir(stale_dir):
            problems.append("没有创建 queue/stale/ 目录")
            stale_files = []
        else:
            stale_files = sorted(os.listdir(stale_dir))
        if "old-stop.json" not in stale_files:
            problems.append("过期的 stop 没有被隔离（stale 目录里是 %s）" % stale_files)
        if not os.path.isfile(os.path.join(stale_dir, "old-stop.json.why.txt")):
            problems.append("隔离时没有写 .why.txt 说明原因")

        if not os.path.isfile(os.path.join(qout, "fresh-ping.json")):
            problems.append("新鲜的 ping 没有被执行（误伤）")

        try:
            with open(os.path.join(tmp, "state", "heartbeat.json"), "r", encoding="utf-8") as fh:
                hb = json.load(fh)
        except Exception as exc:
            hb = {}
            problems.append("读不到心跳：%s" % exc)

        if hb.get("stale_skipped") != 1:
            problems.append("心跳里的 stale_skipped 应为 1，实际 %r" % hb.get("stale_skipped"))
        if hb.get("processed") != 1:
            problems.append("心跳里的 processed 应为 1（只执行新鲜的 ping），实际 %r" % hb.get("processed"))

        record("队列过期隔离（过期 stop 不能让桥接自杀）", not problems,
               "queue/in 剩余=%s ｜ queue/stale=%s ｜ queue/out=%s\n      "
               "心跳 stale_skipped=%s ｜ processed=%s ｜ note=%s"
               % (left_in, stale_files, [f for f in os.listdir(qout) if f.endswith(".json")],
                  hb.get("stale_skipped"), hb.get("processed"), hb.get("note"))
               + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_bridge_cam_tree():
    """CAM 树遍历：NX 2606 没有 GetOperations，桥接必须会走 GetRoot+GetMembers 递归。

    用假对象模拟 NX 的树（纯离线）：
      - 同一操作同时出现在「程序顺序树」和「机床树」→ 必须按 Tag 去重
      - 组套组 → 必须递归
      - 某个分支 GetRoot 抛异常 → 不能整棵挂掉，跳过继续
      - 有 GetOperations 的版本 → 走快车道
    """
    import importlib.util
    import types

    # --- 假 NXOpen：桥接 import 时会拿到它 ---
    fake = types.ModuleType("NXOpen")
    view = types.SimpleNamespace(ProgramOrder="P", MachineTool="M", Geometry="G", MachineMethod="MM")
    cam_setup_ns = types.SimpleNamespace(View=view)
    fake.CAM = types.ModuleType("NXOpen.CAM")
    fake.CAM.CAMSetup = cam_setup_ns
    fake.UF = types.ModuleType("NXOpen.UF")
    old_modules = {}
    for name in ("NXOpen", "NXOpen.CAM", "NXOpen.UF"):
        old_modules[name] = sys.modules.get(name)
        sys.modules[name] = getattr(fake, name.split(".")[-1]) if "." in name else fake
    try:
        path = os.path.join(ROOT, "bridge", "nx_bridge.py")
        spec = importlib.util.spec_from_file_location("nx_bridge_camtest", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    class _Obj:
        def __init__(self, tag, name, is_op=False, members=None):
            self.Tag = tag
            self.Name = name
            self._is_op = is_op
            self._members = members  # None = 叶子（GetMembers 该抛异常）

        def GetMembers(self):
            if self._members is None:
                raise AttributeError("leaf has no GetMembers")
            return self._members

    class _CAM:
        def __init__(self, roots, get_operations=None):
            self._roots = roots
            if get_operations is not None:
                self.GetOperations = get_operations

        def GetRoot(self, view_name):
            v = self._roots.get(view_name)
            if isinstance(v, Exception):
                raise v
            return v

        def IsOperation(self, obj):
            return getattr(obj, "_is_op", False)

    op_a = _Obj(100, "MILL_ROUGH", is_op=True)
    op_b = _Obj(101, "MILL_FINISH", is_op=True)
    op_c = _Obj(102, "DRILL", is_op=True)
    prog_root = _Obj(1, "PROGRAM_ROOT", members=[
        _Obj(2, "GROUP_1", members=[op_a, op_b]),
        op_c,
    ])
    mach_root = _Obj(3, "MACHINE_ROOT", members=[
        _Obj(4, "M_GROUP", members=[op_a, op_c]),  # 同一批操作换个视角再来一遍
    ])
    method_root = _Obj(5, "METHOD_ROOT", members=[])
    cam = _CAM(
        roots={"P": prog_root, "M": mach_root, "G": RuntimeError("no geometry branch"),
               "MM": method_root},
    )
    ops = mod.iter_operations(cam)
    names = sorted(str(o.Name) for o in ops)

    problems = []
    if names != ["DRILL", "MILL_FINISH", "MILL_ROUGH"]:
        problems.append("树遍历结果不对：%s" % names)
    if len(ops) != 3:
        problems.append("跨树去重失败：拿到 %d 个（应为 3 个）" % len(ops))

    fast = _CAM(roots={}, get_operations=lambda: [op_a, op_b])
    fast_ops = mod.iter_operations(fast)
    if [str(o.Name) for o in fast_ops] != ["MILL_ROUGH", "MILL_FINISH"]:
        problems.append("GetOperations 快车道没走：%s" % [str(o.Name) for o in fast_ops])

    record("桥接 CAM 树遍历（2606 没有 GetOperations，走递归+去重）", not problems,
           "四棵树递归 → %d 个唯一操作（%s）｜ 快车道也验证过"
           % (len(ops), "、".join(names))
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


FAKE_API_DOC = """<?xml version="1.0"?>
<doc>
  <members>
    <member name="M:NXOpen.CAM.CAMSetup.Postprocess(NXOpen.CAM.CAMObject[],System.String,System.String,NXOpen.CAM.CAMSetup.OutputUnits)">
      <summary>Postprocess</summary>
      <remarks><para>Created in NX4.0.0</para><para>License requirements: cam_base ("CAM BASE")</para></remarks>
      <param name="objects">array of objects</param>
    </member>
    <member name="M:NXOpen.CAM.CAMSetup.GenerateToolPath(NXOpen.CAM.CAMObject[])">
      <summary>Generate</summary>
    </member>
    <member name="P:NXOpen.CAM.CAMSetup.IsOperation">
      <summary>tell if operation</summary>
    </member>
    <member name="F:NXOpen.CAM.CAMSetup.OutputUnits.Metric">
      <summary>metric</summary>
    </member>
    <member name="M:NXOpen.CAM.CAMFeature.GetOperations">
      <summary>Get operations of the feature</summary>
    </member>
  </members>
</doc>
"""


def t_nx_api_lookup():
    """离线 API 查询：用一份假 XML 验证解析/检索/缓存都对。

    这个工具的必要性：2026-10-01 因为「凭记忆写 NXOpen」连踩 6 个坑
    （CAMSetup 上没 GetOperations、Postprocess 是 4 参、截图 API 已删除...）。
    它现在能在 0.4 秒内给出真实签名，不用联网也不用装模型。
    """
    import importlib.util

    tmp = tempfile.mkdtemp(prefix="nxagent-api-")
    try:
        doc = os.path.join(tmp, "NXOpen.xml")
        with open(doc, "w", encoding="utf-8") as fh:
            fh.write(FAKE_API_DOC)

        path = os.path.join(ROOT, "tools", "nx_api_lookup.py")
        spec = importlib.util.spec_from_file_location("nx_api_lookup_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 用环境变量指向假文档；缓存落到 temp 的 state/
        old_doc, old_home = os.environ.get("NX_API_DOC"), os.environ.get("NX_AGENT_HOME")
        os.environ["NX_API_DOC"] = doc
        os.environ["NX_AGENT_HOME"] = tmp
        try:
            items, src = mod.load(home=tmp)
            problems = []
            if len(items) != 5:
                problems.append("解析出 %d 条，应为 5 条" % len(items))

            hits = mod.search(items, "Postprocess", limit=10)
            if len(hits) != 1:
                problems.append("搜 Postprocess 得到 %d 条，应为 1 条" % len(hits))
            else:
                h = hits[0]
                if h["cls"] != "NXOpen.CAM.CAMSetup":
                    problems.append("类名不对：%s" % h["cls"])
                if h["signature"].count(",") != 3:
                    problems.append("签名参数个数不对（应为 4 个参数）：%s" % h["signature"])
                if "cam_base" not in (h["remarks"] or ""):
                    problems.append("没把许可要求带出来：%r" % h["remarks"])

            # 这条是真实踩过的坑：GetOperations 在 CAMFeature 上，不在 CAMSetup 上
            cam_setup_ops = mod.search(items, "GetOperations", limit=10,
                                       cls="NXOpen.CAM.CAMSetup")
            if cam_setup_ops:
                problems.append("CAMSetup 上不该搜到 GetOperations")
            if not mod.search(items, "GetOperations", limit=10):
                problems.append("应该在 CAMFeature 上搜到 GetOperations")

            # 3 条而不是 4 条：枚举值 OutputUnits.Metric 挂在嵌套类型
            # CAMSetup.OutputUnits 下，不算 CAMSetup 自己的成员（这是对的）
            n_members = len(mod.members_of(items, "CAMSetup"))
            if n_members != 3:
                problems.append("members_of(CAMSetup) 应为 3 条（枚举值归嵌套类型），实际 %d" % n_members)
            if len(mod.members_of(items, "OutputUnits")) != 1:
                problems.append("枚举值应该归到 OutputUnits 这个嵌套类型下")

            if mod.search(items, "NoSuchApiAtAll", limit=10):
                problems.append("不存在的关键词不该有命中")

            cached = os.path.join(tmp, "state", "nx_api_index.json")
            if not os.path.isfile(cached):
                problems.append("没写出索引缓存")
            else:
                items2, src2 = mod.load(home=tmp)
                if len(items2) != 5 or "cache" not in src2:
                    problems.append("第二次没走缓存（来源=%r）" % src2)
        finally:
            for k, v in (("NX_API_DOC", old_doc), ("NX_AGENT_HOME", old_home)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        record("离线 NXOpen API 查询（解析/签名/许可/缓存）", not problems,
               "5 条成员 | Postprocess 4 参 + cam_base 许可 | GetOperations 只在 CAMFeature 上"
               + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_cutting_params():
    """通用切削参数起点表：给得出值、首件会降速、未知材料会报错、量纲要合理。

    存在理由：用户指出「参数不是卡点 —— 可以问、可以留空、可以先凭通用知识定个值」。
    这三条都对，所以参数收敛的主路径是「先给通用起点 → 对话里问操作者 → 收敛」，
    而不是干等录制。这个表就是第 3 条的落地。
    """
    import importlib.util

    path = os.path.join(ROOT, "tools", "cutting_params.py")
    spec = importlib.util.spec_from_file_location("cutting_params_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    table = mod.load_table(ROOT)

    problems = []

    # 公式与已知值对照：铜 Vc=200，D=8 → S = 200*1000/(π*8) ≈ 7958
    res = mod.suggest(table, "copper", 8.0, "rough", 3)
    s = res["suggested"]
    if abs(s["spindle_rpm"] - 7958) > 3:
        problems.append("转速算错了：%s，应约 7958" % s["spindle_rpm"])
    if abs(s["feed_mm_per_min"] - 1194) > 3:
        problems.append("进给算错了：%s，应约 1194" % s["feed_mm_per_min"])

    # 首件必须四样一起降
    f = res["first_article"]
    for key in ("spindle_rpm", "feed_mm_per_min", "ap_mm", "ae_mm"):
        if key not in f:
            problems.append("首件档缺 %s" % key)
    if f["spindle_rpm"] >= s["spindle_rpm"]:
        problems.append("首件档没降速（%d vs %d）" % (f["spindle_rpm"], s["spindle_rpm"]))

    # 量纲合理：切深/切宽不能超过刀具直径
    for op in ("rough", "finish"):
        r = mod.suggest(table, "carbon_steel", 6.0, op, 4)
        if r["suggested"]["ap_mm"] > 6.0 or r["suggested"]["ae_mm"] > 6.0:
            problems.append("%s 的切深/切宽超过刀具直径了" % op)

    # 未知材料要报错，不能静静给个默认值
    try:
        mod.suggest(table, "unobtainium", 10.0)
        problems.append("未知材料没有报错")
    except KeyError:
        pass
    try:
        mod.suggest(table, "copper", 0)
        problems.append("直径为 0 没有报错")
    except ValueError:
        pass

    # 红线必须带在输出里
    if not any("nc_validate" in c for c in res["required_checks"]):
        problems.append("required_checks 里没有 nc_validate")
    if not res.get("ask_the_operator"):
        problems.append("没给出该问操作者的问题清单")

    # 表本身要完整
    for key, mat in table["materials"].items():
        if "vc_rough" not in mat or "vc_finish" not in mat:
            problems.append("材料 %s 缺切削速度" % key)

    n_mat = len(table["materials"])
    record("通用切削参数起点（公式/首件降速/红线提示）", not problems,
           "%d 种材料 | 铜 φ8 三齿粗加工 → S=%d 转/分 F=%d mm/分（首件 S=%d）"
           % (n_mat, s["spindle_rpm"], s["feed_mm_per_min"], f["spindle_rpm"])
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


def t_bridge_preview():
    """preview 命令 = 网页开发的「刷新一下」：跑仿真 → 截图 → 给人看。

    离线测三件事：
      1. 正常时三步图都出（当前视图 / 刀路重播 / 3D 切除）
      2. 某个仿真步骤抛异常时，不能连「当前视图」这张基线图都拿不到
      3. 部件没有 CAM 设置时优雅降级，而不是崩
    """
    import importlib.util
    import types

    tmp = tempfile.mkdtemp(prefix="nxagent-preview-")
    old_home = os.environ.get("NX_AGENT_HOME")
    os.environ["NX_AGENT_HOME"] = tmp
    try:
        fake = types.ModuleType("NXOpen")
        fake.CAM = types.ModuleType("NXOpen.CAM")
        fake.CAM.CAMSetup = types.SimpleNamespace(
            View=types.SimpleNamespace(ProgramOrder="P", MachineTool="M",
                                       Geometry="G", MachineMethod="MM"))
        fake.UF = types.ModuleType("NXOpen.UF")
        fake.Gateway = types.ModuleType("NXOpen.Gateway")
        fake.Gateway.ImageExportBuilder = types.SimpleNamespace(
            FileFormats=types.SimpleNamespace(Png="png"),
            BackgroundOptions=types.SimpleNamespace(Original="orig"))

        class _Builder:
            def __init__(self):
                self.FileName = None

            def Commit(self):
                # 假截图：造一个非空文件，好让 save_screenshot 的校验通过
                with open(self.FileName, "wb") as fh:
                    fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

            def Destroy(self):
                pass

        class _Views:
            def CreateImageExportBuilder(self):
                return _Builder()

        class _Op:
            def __init__(self, tag, name, has_path=True):
                self.Tag = tag
                self.Name = name
                self._has_path = has_path

            def AskPathExists(self):
                # 关键：没刀路时 NX 的 ReplayToolPath 会弹模态框卡死桥接，
                # 所以 preview 必须先按这个过滤
                return self._has_path

            def GetMembers(self):
                raise AttributeError("leaf")

        class _CAM:
            def __init__(self, replay_raises=False, has_path=True):
                self.replay_raises = replay_raises
                self.has_path = has_path
                self.calls = []

            def GetRoot(self, view_name):
                if view_name != "P":
                    raise RuntimeError("no branch")
                return _Obj(1, "ROOT", members=[_Op(100, "MILL_A", self.has_path)])

            def IsOperation(self, obj):
                return isinstance(obj, _Op)

            def ReplayToolPath(self, ops):
                self.calls.append("replay")
                if self.replay_raises:
                    raise RuntimeError("刀路还没生成")

            def Show3dWorkpiece(self, ops):
                self.calls.append("show3d")

        class _Obj:
            def __init__(self, tag, name, members=None, cam=None):
                self.Tag = tag
                self.Name = name
                self._members = members or []
                self._cam = cam
                self.Views = _Views()

            @property
            def CAMSetup(self):
                if self._cam is None:
                    raise RuntimeError("1345023")
                return self._cam

            def GetMembers(self):
                return self._members

        class _Parts:
            def __init__(self, part):
                self.Work = part

            def OpenActiveDisplay(self, *a):
                pass

        class _Session:
            def __init__(self, part):
                self.Parts = _Parts(part)

        holder = {}

        class _SessionNs:
            @staticmethod
            def GetSession():
                return _Session(holder["part"])

        fake.Session = _SessionNs

        saved = {}
        for n, m in (("NXOpen", fake), ("NXOpen.CAM", fake.CAM), ("NXOpen.UF", fake.UF),
                     ("NXOpen.Gateway", fake.Gateway)):
            saved[n] = sys.modules.get(n)
            sys.modules[n] = m
        try:
            path = os.path.join(ROOT, "bridge", "nx_bridge.py")
            spec = importlib.util.spec_from_file_location("nx_bridge_preview", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            for n, old in saved.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old

        problems = []

        # 1) 正常：三张图
        cam = _CAM()
        holder["part"] = _Obj(999, "PART", cam=cam)
        res = mod.cmd_preview({})
        if len(res.get("images", [])) != 3:
            problems.append("正常时应出 3 张图，实际 %d 张" % len(res.get("images", [])))
        if res.get("operation_count") != 1:
            problems.append("操作数应为 1，实际 %r" % res.get("operation_count"))
        if [s["step"] for s in res.get("steps", [])] != ["replay", "show3d"]:
            problems.append("步骤顺序不对：%s" % [s["step"] for s in res.get("steps", [])])
        for p in res.get("images", []):
            if not os.path.isfile(p):
                problems.append("图没真写出来：%s" % p)

        # 2) 重播失败：基线图在，步骤标记失败，不整体崩
        cam2 = _CAM(replay_raises=True)
        holder["part"] = _Obj(998, "PART", cam=cam2)
        res2 = mod.cmd_preview({})
        if len(res2.get("images", [])) < 1:
            problems.append("重播失败时连基线图都没了")
        replay_step = [s for s in res2.get("steps", []) if s["step"] == "replay"]
        if not replay_step or replay_step[0].get("ok") is not False:
            problems.append("重播失败没有如实标记 ok=False")
        if not [s for s in res2.get("steps", []) if s["step"] == "show3d" and s.get("ok")]:
            problems.append("重播失败不该挡住后面的 3D 切除")

        # 3) 没有 CAM 设置：优雅降级
        holder["part"] = _Obj(997, "PART", cam=None)
        res3 = mod.cmd_preview({})
        if "CAM" not in (res3.get("note") or ""):
            problems.append("没有 CAM 设置时没给出说明：%r" % res3.get("note"))
        if len(res3.get("images", [])) != 1:
            problems.append("没有 CAM 设置时应只截 1 张当前视图")

        # 4) ★ 最危险的场景：操作都没生成刀路 → 绝不能去调重播
        #    （真机实测：调了 NX 就弹模态框，journal 卡在主线程，桥接心跳停 91 秒，stop 都投不进去）
        cam4 = _CAM(has_path=False)
        holder["part"] = _Obj(996, "PART", cam=cam4)
        res4 = mod.cmd_preview({})
        if cam4.calls:
            problems.append("没刀路时仍然调了 %s —— NX 会弹模态框把桥接卡死" % cam4.calls)
        if len(res4.get("images", [])) != 1:
            problems.append("没刀路时应只截当前视图一张，实际 %d 张" % len(res4.get("images", [])))
        if "刀路" not in (res4.get("note") or ""):
            problems.append("没刀路时没说清原因：%r" % res4.get("note"))

        record("桥接 preview（=网页的「刷新」：仿真+截图+给人看）", not problems,
               "三张图/重播失败仍保底/无 CAM 优雅降级 都验证过"
               + ("" if not problems else "\n      问题：" + "；".join(problems)))
    finally:
        if old_home is None:
            os.environ.pop("NX_AGENT_HOME", None)
        else:
            os.environ["NX_AGENT_HOME"] = old_home
        shutil.rmtree(tmp, ignore_errors=True)


def t_dialog_sweeper_rules():
    """弹窗清扫的规则引擎：已知弹窗选保守按钮、未知弹窗绝不乱点。

    存在理由：NX 主线程被模态框卡住时，**NX 内部的代码救不了自己**（它正在等），
    但外部进程可以替它按按钮。所以「弹窗阻断执行」不应该等于「卡死」。
    但这条能力也很危险 —— 所以规则必须是：已知才动，且只选保守项。
    """
    import importlib.util

    path = os.path.join(ROOT, "tools", "dialog_sweeper.py")
    spec = importlib.util.spec_from_file_location("dialog_sweeper_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cases = [
        # (标题, 按钮, 期望点到的按钮 或 None, 说明)
        ("Product Excellence Program", ["&Agree", "&Decline"], "&Decline", "遥测框要选拒绝"),
        ("NX Save", ["&Yes", "&No", "Cancel"], "&No", "是否保存要选不保存（别覆盖）"),
        ("完全没见过的框", ["OK"], None, "未知弹窗绝不自动点"),
        ("Product Excellence Program", ["&Agree"], None, "只剩「同意」时不能点"),
    ]
    problems = []
    for title, buttons, want, why in cases:
        got, reason = mod.choose_action(title, buttons)
        if got != want:
            problems.append("%s：期望点 %r，实际 %r（理由：%s）" % (why, want, got, reason))

    # 归一化要生效：带 & 的按钮名要能匹配
    got, _ = mod.choose_action("Product Excellence Program", ["&Decline", "&Agree"])
    if got != "&Decline":
        problems.append("按钮顺序变了也应优先选 Decline，实际 %r" % got)

    # 正文抓取 + 给大模型看的报错报告（NX 弹窗 = NX 的编译错误输出）
    events = [{
        "time": "2026-10-01 15:00:00", "action": "skipped",
        "title": "Tool Path", "body": ["No tool path to display.", "Generate it first."],
        "buttons": ["OK"], "reason": "未知弹窗，不自动处理（等人来看）",
    }]
    report = mod.format_for_llm(events)
    if "No tool path" not in report:
        problems.append("报错报告里没有正文内容")
    if "GenerateToolPath" not in report:
        problems.append("报错报告没给出成因提示（刀路没生成应先 GenerateToolPath）")
    if mod.format_for_llm([]) == "":
        problems.append("没有事件时应返回提示文字")

    # 日志读写的往返
    tmp = tempfile.mkdtemp(prefix="nxagent-dialog-")
    try:
        mod.record({"time": "t1", "title": "A", "body": ["b"], "buttons": ["OK"]}, home=tmp)
        mod.record({"time": "t2", "title": "B", "body": [], "buttons": ["OK"]}, home=tmp)
        got = mod.read_events(1, home=tmp)
        if len(got) != 1 or got[0].get("title") != "B":
            problems.append("read_events 的条数/顺序不对：%s" % got)
        if len(mod.read_events(10, home=tmp)) != 2:
            problems.append("read_events 没读到全部 2 条")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    record("弹窗清扫规则（已知选保守项 / 未知绝不乱点）", not problems,
           "遥测→Decline ｜ 保存→No ｜ 未知→不动 ｜ 只剩同意→不动"
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


def t_assembly_consistency():
    """装配用的零件，必须**逐字段等于** NX 建模脚本里真正建出来的零件。

    存在理由（2026-10-09 用户当场抓到）：
        装配可视化里的坐标是我手写的，和 NX 里的模型对不上 ——
        底板写成 92×52×6（NX 是 100×60×8），引擎舱写 ±30（NX 是 ±24），
        还凭空加了两个 NX 里根本不存在的"支架"。**这就是造假数据。**

    修法不是"我下次小心点"，而是**单一数据源 + 自动核对**：
        - NX 建模 journal 和装配 spec 都从同一份参数出发；
        - tools/check_assembly_spec.py 用 ast 抠出建模脚本里真实发生的
          make_block / make_cylinder 调用（含 for/enumerate 展开），
          和 spec 逐字段比。差 0.1mm、多一个、少一个都会被抓出来。

    本用例还做**反向测试**：故意改坏一个数字，必须报红。
    否则核对器本身可能只是个永远说"一致"的橡皮图章。
    """
    import json as _json

    j = os.path.join(ROOT, "output", "enterprise", "j_build.py")
    s = os.path.join(ROOT, "output", "enterprise", "assembly_spec.json")
    chk = os.path.join(ROOT, "tools", "check_assembly_spec.py")
    problems = []

    if not (os.path.isfile(j) and os.path.isfile(s) and os.path.isfile(chk)):
        record("装配一致性（spec == 建模脚本）", False,
               "缺文件：j_build.py / assembly_spec.json / check_assembly_spec.py")
        return

    # 1) 正常情况：必须是"一致"
    rc, out, err = run([PY, chk, j, s], timeout=90)
    if rc != 0:
        problems.append("核对器报不一致（退出码 %d）：%s"
                        % (rc, (out or err).strip().splitlines()[-1:]))
    n_parts = out.count("spec 里声明的零件")
    want = "✔ 完全一致"
    if want not in out:
        problems.append("核对器没给出「完全一致」结论")

    # 2) 反向测试：改坏一个数字，必须报红（证明它不是橡皮图章）
    tmp = tempfile.mkdtemp(prefix="nxagent-asm-")
    try:
        with open(s, "r", encoding="utf-8") as fh:
            spec = _json.load(fh)
        hit = False
        for p in spec.get("parts") or []:
            if p.get("type") == "box" and p.get("lengths"):
                p["lengths"][0] = float(p["lengths"][0]) + 0.1   # 差 0.1mm
                hit = True
                break
            if p.get("type") == "cyl":
                p["center"][0] = float(p["center"][0]) + 0.1
                hit = True
                break
        if not hit:
            problems.append("spec 里没有可改的零件，反向测试没做成")
        else:
            bad = os.path.join(tmp, "bad_spec.json")
            with open(bad, "w", encoding="utf-8") as fh:
                _json.dump(spec, fh, ensure_ascii=False)
            rc2, out2, _ = run([PY, chk, j, bad], timeout=90)
            if rc2 == 0:
                problems.append("★ 反向测试失败：把零件尺寸改了 0.1mm，"
                                "核对器居然还说一致 —— 它是橡皮图章")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    record("装配一致性（spec == 建模脚本，含反向测试）", not problems,
           "5 个零件逐字段相同；故意改 0.1mm 会被抓出来"
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


def t_assembly_render():
    """装配可视化渲染器必须与 NX 实测包围盒逐项一致（含反向测试）。

    存在理由：装配可视化曾经被用户抓到两个问题：
      1. 坐标是手写的，和 NX 里的模型对不上；
      2. 圆柱只画了两个圆形边，侧面没描线。
    现在渲染器只吃 NX 导出的 JSON（位置/朝向 = Component.GetPosition()，
    尺寸校验 = UF.ModlGeneral.AskBoundingBox），并在渲染**之前**用
    同一套算法离线算一遍包围盒和 NX 对。对不上就返回 1、HTML 上也标红。

    本用例还做反向测试：故意把一个零件挪 5mm，必须报红。
    """
    import json as _json

    asm_p = os.path.join(ROOT, "output", "enterprise", "assembly_from_nx.json")
    bb_p = os.path.join(ROOT, "output", "enterprise", "assembly_bbox_from_nx.json")
    tool = os.path.join(ROOT, "tools", "render_assembly.py")
    out_p = os.path.join(ROOT, "output", "enterprise", "assembly.html")
    problems = []

    if not all(os.path.isfile(p) for p in (asm_p, bb_p, tool)):
        record("装配渲染一致性（渲染器 vs NX 包围盒）", False,
               "缺文件：assembly_from_nx.json / assembly_bbox_from_nx.json / "
               "render_assembly.py（要先在 NX 里跑 j_asm_build.py + j_asm_verify.py）")
        return

    rc, out, err = run([PY, tool, "--asm", asm_p, "--bbox", bb_p, "-o", out_p],
                       timeout=90)
    if rc != 0:
        problems.append("渲染器报不一致（退出码 %d）：%s"
                        % (rc, (out or err).strip().splitlines()[-1:]))

    # 反向测试：把碟形舰体挪 5mm，渲染就再也对不上 NX 了，必须报红
    tmp = tempfile.mkdtemp(prefix="nxagent-asmbad-")
    try:
        with open(asm_p, "r", encoding="utf-8") as fh:
            bad = _json.load(fh)
        hit = False
        for c in bad.get("components") or []:
            if c["id"] == "hull":
                c["origin"][2] += 5.0
                hit = True
                break
        if not hit:
            problems.append("asm JSON 里没有 hull，反向测试没做成")
        else:
            bad_p = os.path.join(tmp, "bad_asm.json")
            with open(bad_p, "w", encoding="utf-8") as fh:
                _json.dump(bad, fh, ensure_ascii=False)
            rc2, _, _ = run([PY, tool, "--asm", bad_p, "--bbox", bb_p,
                             "-o", os.path.join(tmp, "bad.html")], timeout=90)
            if rc2 == 0:
                problems.append("★ 反向测试失败：把碟形挪了 5mm，"
                                "渲染器居然还说一致 —— 它是橡皮图章")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    record("装配渲染一致性（渲染器 vs NX 包围盒，含反向测试）", not problems,
           "5 个组件偏差 0.000mm；故意挪 5mm 会被抓出来"
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


def t_public_proportions():
    """模型比例必须符合**公开的真实尺寸数据**（含反向测试）。

    存在理由（2026-10-09 用户定调）：「用公开的真实尺寸数据把比例定准」。
    在那之前我的比例是拍脑袋定的，用户连着两轮说「形状不对」：
      工程舰体/碟形 = 26/46 = 0.565（v2，酒桶）→ 16/46 = 0.348（v3，还是粗）
      公开数据是 34.1/127.1 = 0.268。
    现在 tools/check_proportions.py 从 NX 实测包围盒量出各关键尺寸，
    换算回真实米数，逐项和公开数据比，并输出派生比例。

    本用例还做反向测试：把包围盒整体放大 10%，必须报红。
    """
    import json as _json

    bb_p = os.path.join(ROOT, "output", "enterprise", "assembly_v5_bbox.json")
    asm_p = os.path.join(ROOT, "output", "enterprise", "assembly_v5_from_nx.json")
    tool = os.path.join(ROOT, "tools", "check_proportions.py")
    problems = []

    if not all(os.path.isfile(p) for p in (bb_p, asm_p, tool)):
        record("比例符合公开数据（v5，含反向测试）", False,
               "缺文件（要先在 NX 里跑 j_asm5.py）")
        return

    rc, out, err = run([PY, tool, bb_p, asm_p], timeout=60)
    if rc != 0:
        problems.append("核对器报不符合（退出码 %d）：%s"
                        % (rc, (out or err).strip().splitlines()[-1:]))
    if "全部符合公开数据" not in out:
        problems.append("没给出「全部符合」结论")

    # 反向测试：整体放大 10%，全宽/全长/全高必然对不上
    tmp = tempfile.mkdtemp(prefix="nxagent-prop-")
    try:
        with open(bb_p, "r", encoding="utf-8") as fh:
            bad = _json.load(fh)
        if bad.get("envelope"):
            for i in range(3):
                bad["envelope"]["size"][i] *= 1.10
                bad["envelope"]["max"][i] = bad["envelope"]["min"][i] + \
                    bad["envelope"]["size"][i]
            bad_p = os.path.join(tmp, "bad_bbox.json")
            with open(bad_p, "w", encoding="utf-8") as fh:
                _json.dump(bad, fh, ensure_ascii=False)
            rc2, _, _ = run([PY, tool, bad_p, asm_p], timeout=60)
            if rc2 == 0:
                problems.append("★ 反向测试失败：包络放大 10% 竟仍说符合 —— 橡皮图章")
        else:
            problems.append("bbox JSON 里没有 envelope，反测没做成")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    record("比例符合公开数据（v5，含反向测试）", not problems,
           "9 项尺寸 + 3 项派生比例全部 0.00% 偏差；放大 10% 会被抓出来"
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


def t_interference_recheck():
    """NX 干涉逐处复核器 —— 含反向测试（证明它不是橡皮图章）。

    存在理由（2026-10-10）：用户要求"做一个让 Agent 能验证装配错误的方案"。
    `output/enterprise/jc_assembly_check.py` 负责**在 NX 里**跑间隙分析并逐对枚举；
    `tools/check_interference.py` 负责**离线**把 NX 的结论翻译成
    「哪些是套接误报、哪些是真错、错多少毫米」。

    这一项证明两件事：
      1 复核器能正确区分 INFO/ERROR/WARN（拿真实报告跑）
      2 ★ 反向测试：把某处套接量改小，它必须从 INFO 翻成 ERROR
    """
    import json as _json

    chk_p = os.path.join(ROOT, "output", "enterprise", "assembly_check.json")
    lay_p = os.path.join(ROOT, "output", "enterprise", "saturn_layout.json")
    tool = os.path.join(ROOT, "tools", "check_interference.py")
    problems = []

    if not all(os.path.isfile(q) for q in (chk_p, lay_p, tool)):
        record("NX干涉逐处复核（含反向测试）", False,
               "缺文件（要先在 NX 里跑 j_saturn.py）")
        return

    rc, out, _ = run([PY, tool, chk_p, lay_p], timeout=60)
    if "复核结果" not in out:
        problems.append("没有给出复核结论")
    if "ERROR" not in out and "SUMMARY" not in out:
        # 真实报告里应当至少有 INFO 或 ERROR 的分类
        problems.append("没有任何分类结论")

    # 反向测试：把 S-IVB 的套接重叠改到接近 0，穿深 26.69mm 立刻该变 ERROR
    tmp = tempfile.mkdtemp(prefix="nxagent-ifc-")
    try:
        with open(lay_p, "r", encoding="utf-8") as fh:
            lay = _json.load(fh)
        # 让 S-IVB 与 2-3 级间段**零重叠**（底 = 上方的顶 775.88），
        # 穿深 26.69mm 就该从 INFO 翻成 ERROR
        for pt in lay.get("parts") or []:
            if pt.get("label") == "S-IVB 第三级":
                pt["z_bottom"] = 775.88
                pt["z_top"] = 940.2
        bad_p = os.path.join(tmp, "bad_layout.json")
        with open(bad_p, "w", encoding="utf-8") as fh:
            _json.dump(lay, fh, ensure_ascii=False)
        rc2, out2, _ = run([PY, tool, chk_p, bad_p], timeout=60)
        if rc2 == 0:
            problems.append("★ 反向测试失败：把套接量清零后竟仍判通过 —— 橡皮图章")
        elif ("只该套接 0.0mm" not in out2
              and "远超合理上限" not in out2
              and "空隙" not in out2):
            problems.append("反向测试的结论不含「零重叠/超上限」字样，判据可能被绕过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # NX 侧报告里必须有 Summary（证明 PerformAnalysis 真的跑过）
    try:
        with open(chk_p, "r", encoding="utf-8") as fh:
            d = _json.load(fh)
        clr = d.get("clearance") or {}
        sm = clr.get("summary") or {}
        if not sm:
            problems.append("NX 报告里没有 Summary —— 说明没真正跑分析")
        if not clr.get("interferences"):
            problems.append("NX 报告里没有逐处干涉列表")
    except Exception as exc:
        problems.append("读报告失败：%s" % str(exc)[:80])

    record("NX干涉逐处复核（含反向测试）", not problems,
           "能把 NX 的 %s 处干涉分成 INFO/ERROR，"
           "并指出具体哪一对多插了多少 mm；清零套接量会翻成 ERROR"
           % str((d.get("clearance") or {}).get("n_interferences", "?")))
    for q in problems:
        record("  ↳ " + q, False, "")



def t_saturn_stack():
    """土星五号（导入的 NASA 网格件）堆叠核验，含反向测试。

    存在理由（2026-10-10）：用户换模型到 NASA 公有领域的土星五号。
    这批 STL 是**各零件独立建模姿态**，坐标互不相干，所以装配坐标不是手写的 ——
    由 tools/saturn_layout.py 从**零件实测长度**累加算出（级序用公开级构），
    再由本项证明 NX 装完后每级 Z 区间与预期一致、相邻级首尾相接。

    反向测试：把第一级高度改掉 2mm，必须报红。
    """
    import json as _json

    bb_p = os.path.join(ROOT, "output", "enterprise", "saturn_bbox.json")
    asm_p = os.path.join(ROOT, "output", "enterprise", "saturn_from_nx.json")
    tool = os.path.join(ROOT, "tools", "check_saturn_stack.py")
    problems = []

    if not all(os.path.isfile(p) for p in (bb_p, asm_p, tool)):
        record("土星五号装配核验（含反向测试）", False,
               "缺文件（要先在 NX 里跑 j_saturn.py）")
        return

    rc, out, err = run([PY, tool, bb_p, asm_p], timeout=60)
    if rc != 0:
        problems.append("核验不过（退出码 %d）：%s"
                        % (rc, (out or err).strip().splitlines()[-1:]))
    if "装配正确" not in out:
        problems.append("没给出「堆叠完全正确」结论")

    tmp = tempfile.mkdtemp(prefix="nxagent-sat-")
    try:
        with open(bb_p, "r", encoding="utf-8") as fh:
            bad = _json.load(fh)
        for r in bad.get("components") or []:
            if (r.get("label") or "").startswith("S-IC"):
                for b in r.get("bodies") or []:
                    b["asm"]["min"][2] -= 2.0     # 把第一级整体下移 2mm
                break
        bad_p = os.path.join(tmp, "bad_bbox.json")
        with open(bad_p, "w", encoding="utf-8") as fh:
            _json.dump(bad, fh, ensure_ascii=False)
        rc2, _, _ = run([PY, tool, bad_p, asm_p], timeout=60)
        if rc2 == 0:
            problems.append("★ 反向测试失败：把第一级挪了 2mm 竟仍说正确 —— 橡皮图章")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    record("土星五号装配核验（含反向测试）", not problems,
           "10 级 Z 区间全 0.00mm 偏差，相邻级无空隙（对接/套接），总高 1148.7mm；挪 2mm 会被抓出来"
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


def t_dialog_needs_human():
    """要登录的弹窗：必须**认得出**，但**绝不代点**。

    存在理由（2026-10-01 真事）：用户启动 NX 被要求重新登录，一头雾水
    「之前不是验证过了吗」。真实原因是 SSC 日志里的 `身份验证错误 [CSA0002]
    刷新令牌失败` —— 软件授权没过期，过期的是登录令牌。
    脚本没密码、不该有密码，所以不能代点；但**不能只说「未处理」**，
    必须把「是登录框 + 为什么 + 怎么修」说清楚。
    """
    import importlib.util

    path = os.path.join(ROOT, "tools", "dialog_sweeper.py")
    spec = importlib.util.spec_from_file_location("dialog_sweeper_human", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    problems = []

    # 中英文各种写法的登录框都要认出来
    login_cases = [
        ("Sign in", ["Sign in", "Cancel"]),
        ("Sign In - Siemens", ["&Sign in", "Cancel"]),
        ("Siemens Account", ["Log in", "Cancel"]),
        ("需要登录", ["登录", "取消"]),
        ("身份验证", ["重新登录", "取消"]),
        ("Authentication Required", ["OK"]),
        # 标题为空、关键字全在正文里 —— 这种最容易漏
        ("", ["OK"]),
    ]
    for title, buttons in login_cases:
        body = ["Your session has expired. Please sign in again."] if not title else []
        info = mod.classify_dialog(title, body, buttons)
        if info.get("issue") != "login_token_expired":
            problems.append("登录框没认出来：title=%r body=%r → %s"
                            % (title, body, info.get("issue")))
        # 关键：认出来也不许点任何按钮
        if info.get("click") is not None:
            problems.append("登录框居然要自动点 %r —— 涉及账号密码，必须人来" % info["click"])
        if not info.get("steps"):
            problems.append("登录框没给修复步骤：%r" % title)

    # 遥测框仍然走自动处理那条路，不能被新逻辑吃掉
    auto = mod.classify_dialog("Product Excellence Program", [], ["&Agree", "&Decline"])
    if auto["kind"] != "auto" or auto.get("click") != "&Decline":
        problems.append("已知弹窗应继续自动处理，实际 %r" % auto)

    # 真·未知弹窗还是 unknown，不能假装认得
    unknown = mod.classify_dialog("某个从没见过的框", [], ["OK"])
    if unknown["kind"] != "unknown" or unknown.get("click") is not None:
        problems.append("未知弹窗应保持 unknown 且不点，实际 %r" % unknown)

    # 2026-10-01 真机遇到的「授权服务没起来」：必须报这个，而不是笼统的"登录过期"
    real = mod.classify_dialog(
        "Designcenter Student Edition Initialization Error",
        ["Running instance of the server not found."], ["确定"])
    if real.get("issue") != "license_server_not_running":
        problems.append("真实弹窗（server not found）应识别为授权服务未启动，实际 %r" % real.get("issue"))
    if real.get("click") is not None:
        problems.append("该框不该自动点按钮，实际要点 %r" % real["click"])
    steps_text = " ".join(real.get("steps") or [])
    if "SoftwareCenter" not in steps_text:
        problems.append("解法里没提到 SSC 可执行文件路径，人照着做不了")

    # 更具体的规则要赢过更泛的：即使正文里也出现了登录关键字
    both = mod.classify_dialog("Initialization Error",
                               ["Running instance of the server not found.",
                                "Please sign in to continue."], ["确定"])
    if both.get("issue") != "license_server_not_running":
        problems.append("两个规则都命中时应报更具体的那个，实际 %r" % both.get("issue"))

    # 2026-10-01 真机："Work in Progress" 是**正常现象**（桥接 journal 在跑），
    # 而且它上面有个 Stop 按钮 —— 谁点了谁就把桥接当场杀掉。
    wip = mod.classify_dialog("Work in Progress",
                              ["Click Stop to interrupt", "this operation."],
                              ["Stop", "X", "X"])
    if wip.get("kind") != "expected":
        problems.append("Work in Progress 应识别为「正常现象」，实际 %r" % wip.get("kind"))
    if wip.get("click") is not None:
        problems.append("Work in Progress 绝不能点任何按钮（Stop 会中断桥接），实际 %r" % wip["click"])
    if "Stop" not in mod.NEVER_CLICK:
        problems.append("Stop 必须进 NEVER_CLICK，否则哪天规则一改就误杀桥接")
    # 只有正文、没有标题的情况也要认出来
    wip2 = mod.classify_dialog("", ["Work in Progress",
                                    "Click Stop to interrupt this operation."], ["Stop"])
    if wip2.get("kind") != "expected":
        problems.append("标题为空时也该认出 Work in Progress，实际 %r" % wip2.get("kind"))

    record("登录框：认得出 + 给解法 + 绝不代点", not problems,
           "中英文/标题为空都认得；一律不点按钮；遥测框仍自动处理"
           + ("" if not problems else "\n      问题：" + "；".join(problems)))


ALL_TESTS = [
    t_syntax,
    t_nc_lint_demo,
    t_nc_lint_golden,
    t_nc_diff_highrisk,
    t_nc_diff_identical,
    t_nc_diff_ignore,
    t_mcp_protocol,
    t_mcp_nc_diff_tool,
    t_mcp_bridge_offline_fastfail,
    t_nx_api_lookup,
    t_cutting_params,
    t_bridge_preview,
    t_dialog_sweeper_rules,
    t_dialog_needs_human,
    t_bridge_outside_nx,
    t_bridge_probe_heartbeat,
    t_bridge_stale_queue,
    t_bridge_cam_tree,
    t_snapshot_roundtrip,
    t_template_loop,
    t_preflight_runs,
    t_assembly_consistency,
    t_assembly_render,
    t_public_proportions,
    t_saturn_stack,
    t_interference_recheck,
]


def main(argv=None):
    global VERBOSE
    ap = argparse.ArgumentParser(description="nx-agent 全项目离线自检")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印子进程的原始输出")
    args = ap.parse_args(argv)
    VERBOSE = args.verbose

    if not os.path.isdir(os.path.join(ROOT, "mcp")):
        sys.stderr.write("找不到项目根目录（%s 下没有 mcp/）\n" % ROOT)
        return 2

    print("nx-agent 全项目离线自检")
    print("  项目根目录 : %s" % ROOT)
    print("  解释器     : %s" % PY)
    print("  说明       : 本套自检不需要 NX、不需要机床、不需要许可证")
    print("=" * 78)

    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as exc:
            import traceback
            record(getattr(fn, "__name__", str(fn)), False,
                   "自检项自己抛异常：%s\n      %s"
                   % (exc, traceback.format_exc().strip().splitlines()[-1]))

    failed = [r for r in RESULTS if not r["ok"]]
    passed = [r for r in RESULTS if r["ok"]]

    print()
    for r in RESULTS:
        print("%s %s" % ("[ OK ]" if r["ok"] else "[失败]", r["name"]))
        print("       %s" % r["detail"].replace("\n", "\n       "))

    print("\n" + "=" * 78)
    print("汇总：%d 通过 / %d 失败（共 %d 项）" % (len(passed), len(failed), len(RESULTS)))
    if failed:
        print("\n失败项：")
        for r in failed:
            print("  - %s" % r["name"])
        print("\n修完再往下走 —— 否则等 NX 那边出问题，分不清是 NX 的问题还是校验器自己的。")
        return 1

    print("\n全部通过。校验层本身是可信的，NX 那边的问题就不会被校验器自己的 bug 混淆。")
    print("下一步（需要 NX 在线，所以不在这里跑）：")
    print("  1) python tools/preflight_check.py --auto     # 装机自检：关键文件 + 环境变量 + 授权")
    print("  2) python tools/start_bridge.py               # 一键启动 NX + 桥接（不用手点）")
    print("  3) python tools/bridge_probe.py               # 桥接冒烟测试：心跳 + ping + info")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
