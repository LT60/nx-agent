#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
bridge_probe.py -- 桥接冒烟测试（宿主侧跑，不在 NX 里跑）

用途：你跑了 `python tools\start_bridge.py`（或手工在 Journal Manager 里 Play）之后，
      想确认「整条链路真的通了」，
      但不想为此打开 WorkBuddy / Codex 那些 Agent。就跑这个。

它做三件事：
    1. 看 state/heartbeat.json 在不在、多旧（判断桥接进程是否还活着）
    2. 往 queue/in 投一个 ping，等 queue/out 的回音（判断轮询真的在转）
    3. 再投一个 info，看它能不能读到当前工作部件（判断 NXOpen 真的可用）

为什么要单独做一个工具：MCP 服务端是给 Agent 用的，人要用它得先把 Agent 拉起来。
装机器、排故障的时候，人需要的是一条命令给出 PASS/FAIL。

用法：
    python tools/bridge_probe.py                 # 完整冒烟（心跳 + ping + info）
    python tools/bridge_probe.py --quick         # 只看心跳，不发请求
    python tools/bridge_probe.py --timeout 20    # 改等待秒数
    python tools/bridge_probe.py --json          # 机器可读输出

退出码：0 = 全通；1 = 有失败项。

注意：这个脚本需要**桥接正在运行**，所以它不进 tools/selftest.py
（自检必须保持离线，不能依赖 NX 在线）。
"""

import argparse
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("NX_AGENT_HOME") or os.path.dirname(HERE)
QUEUE_IN = os.path.join(HOME, "queue", "in")
QUEUE_OUT = os.path.join(HOME, "queue", "out")
STATE_DIR = os.path.join(HOME, "state")
HEARTBEAT = os.path.join(STATE_DIR, "heartbeat.json")
STALE_SECONDS = 30

OK = "PASS"
BAD = "FAIL"
WARN = "WARN"


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def pid_alive(pid):
    """进程还在不在。

    为什么必须查这个：心跳文件刚被写完之后的一段时间里，即使桥接进程
    已经被杀掉，文件也还是"新鲜"的 —— 光看文件年龄会误判成在线。
    PID 是唯一能立刻区分「死了」和「活着但卡住」的证据。
    """
    if not pid:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None

    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def check_heartbeat():
    if not os.path.isfile(HEARTBEAT):
        return BAD, "没有 state/heartbeat.json —— 桥接从来没有跑过", None
    age = time.time() - os.path.getmtime(HEARTBEAT)
    hb = read_json(HEARTBEAT) or {}
    pid = hb.get("pid")
    alive = pid_alive(pid)

    detail = "%s ｜ 文件年龄 %.1f 秒 ｜ pid=%s(%s) ｜ 模式=%s ｜ 已处理 %s 个请求%s" % (
        hb.get("time", "?"),
        age,
        pid,
        {True: "存活", False: "已消失", None: "未知"}[alive],
        hb.get("mode"),
        hb.get("processed"),
        (" ｜ 备注=%s" % hb.get("note")) if hb.get("note") else "",
    )

    if alive is False:
        return (
            BAD,
            detail + "\n         心跳里记的 PID %s 已经不存在了 —— 桥接进程退出了。" % pid
            + "\n         重新启动：python tools\\start_bridge.py",
            hb,
        )
    if age > STALE_SECONDS:
        return (
            WARN,
            detail + "\n         进程还在，但心跳停了 %.0f 秒（阈值 %d 秒）。" % (age, STALE_SECONDS)
            + "\n         多半是 NX 里弹了模态对话框把它挡住了 —— 去 NX 那边点掉。",
            hb,
        )
    return OK, detail, hb


def send(cmd, args=None, timeout=15.0):
    """投一个请求并等回音。返回 (响应 dict 或 None, 耗时秒)。"""
    for d in (QUEUE_IN, QUEUE_OUT, STATE_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)
    req_id = "probe-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    in_path = os.path.join(QUEUE_IN, req_id + ".json")
    out_path = os.path.join(QUEUE_OUT, req_id + ".json")
    tmp = in_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"id": req_id, "cmd": cmd, "args": args or {}}, fh, ensure_ascii=False)
    os.rename(tmp, in_path)

    started = time.time()
    while time.time() - started < timeout:
        if os.path.isfile(out_path):
            resp = read_json(out_path)
            if resp is not None:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                return resp, time.time() - started
        time.sleep(0.1)
    return None, time.time() - started


def render(name, level, detail):
    return "[%s] %s\n       %s" % (level, name, detail)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NX 桥接冒烟测试")
    ap.add_argument("--quick", action="store_true", help="只看心跳，不发请求")
    ap.add_argument("--timeout", type=float, default=15.0, help="每次请求等待秒数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ns = ap.parse_args(argv)

    results = []
    print("桥接目录: %s" % HOME)
    print("=" * 70)

    lvl, detail, hb = check_heartbeat()
    results.append(("心跳", lvl, detail))
    print(render("心跳", lvl, detail))

    if ns.quick:
        pass
    elif lvl == BAD:
        results.append(("ping", BAD, "跳过：桥接没起来，投请求也只会超时"))
        print(render("ping", BAD, "跳过：桥接没起来，投请求也只会超时"))
    else:
        resp, secs = send("ping", {}, ns.timeout)
        if resp is None:
            msg = "等了 %.1f 秒没有回音。桥接进程在，但它没有在转 —— 去看 NX 是不是被弹窗挡住了，" % secs + \
                  "\n       或者别在 NX 外面拿 --once 跑。"
            results.append(("ping", BAD, msg))
            print(render("ping", BAD, msg))
        elif not resp.get("ok"):
            msg = "桥接回了错误: %s" % resp.get("error")
            results.append(("ping", BAD, msg))
            print(render("ping", BAD, msg))
        else:
            r = resp.get("result") or {}
            msg = "%.2f 秒往返 ｜ 在 NX 内=%s ｜ NX 版本=%s" % (
                secs, r.get("nx_available"), r.get("nx_version", "(NX 外，取不到)"),
            )
            lvl2 = OK if r.get("nx_available") else WARN
            if not r.get("nx_available"):
                msg += "\n       注意 nx_available=false：桥接进程活着，但它不在 NX 进程内，NXOpen 用不了。"
            results.append(("ping", lvl2, msg))
            print(render("ping", lvl2, msg))

        if lvl != BAD and results[-1][1] != BAD:
            resp, secs = send("info", {}, ns.timeout)
            if resp is None:
                msg = "等了 %.1f 秒没有回音" % secs
                results.append(("info（读工作部件）", BAD, msg))
                print(render("info（读工作部件）", BAD, msg))
            elif not resp.get("ok"):
                # 最常见的两种：没打开部件 / 不在 NX 内。都不是致命问题。
                msg = "%s\n       错误类型=%s" % (resp.get("error"), resp.get("kind"))
                results.append(("info（读工作部件）", WARN, msg))
                print(render("info（读工作部件）", WARN, msg))
            else:
                r = resp.get("result") or {}
                msg = "工作部件=%s ｜ 特征=%s ｜ CAM 操作=%s ｜ %.2f 秒" % (
                    r.get("part_path"), r.get("feature_count"),
                    r.get("operation_count", "(无 CAM 设置)"), secs,
                )
                results.append(("info（读工作部件）", OK, msg))
                print(render("info（读工作部件）", OK, msg))

    print("=" * 70)
    failed = [r for r in results if r[1] == BAD]
    warned = [r for r in results if r[1] == WARN]
    if failed:
        print("结论：不通（%d 项失败 / %d 项警告）" % (len(failed), len(warned)))
        print("下一步：python tools\\start_bridge.py   （等它就绪后再跑一遍本脚本）")
    elif warned:
        print("结论：链路通了，但有 %d 项要注意（见上面 WARN）" % len(warned))
    else:
        print("结论：全通。Agent 现在可以下发 NX 操作了。")

    if ns.json:
        print()
        print(json.dumps(
            {"home": HOME, "ok": not failed,
             "results": [{"name": n, "level": l, "detail": d} for n, l, d in results]},
            ensure_ascii=False, indent=2))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
