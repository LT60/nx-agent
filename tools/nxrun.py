# -*- coding: utf-8 -*-
"""nxrun —— 一次性「起 NX + 跑 journal + 拿回结果」。

为什么需要它：
  用工具拉起的 NX 会在「工具调用结束时」被沙箱回收，所以不能
  「先起桥接，再一条条发命令」——第二条命令时 NX 已经没了。
  正解是**把启动和后续操作塞进同一个进程**：本脚本自己 Popen NX，
  等桥接就绪，投递 journal，取回 RESULT，然后才退出。

用法：
    python tools/nxrun.py <journal.py> [--timeout 120] [--keep-nx]

journal 里想回话，就往全局作用域放 RESULT（可 JSON 序列化）和
RESULT_NOTE（一句话人话）。

纯标准库，无第三方依赖。
"""

from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(HERE)
sys.path.insert(0, HERE)

QUEUE_IN = os.path.join(HOME, "queue", "in")
QUEUE_OUT = os.path.join(HOME, "queue", "out")
STATE_DIR = os.path.join(HOME, "state")
HEARTBEAT = os.path.join(STATE_DIR, "heartbeat.json")
BRIDGE = os.path.join(HOME, "bridge", "nx_bridge.py")


# ----------------------------------------------------------------- 基础设施
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def nx_dir():
    return os.environ.get("UGII_BASE_DIR") or r"D:\NX"


def heartbeat_ok(max_age=None):
    """★ 必须复用 start_bridge.heartbeat_state()：

    heartbeat.json 里**没有** online / heartbeat_age_seconds 这两个字段，
    它们是 heartbeat_state() 现算出来加上去的（用文件 mtime + pid 存活）。
    自己读文件判断 online 会永远得到 False —— 2026-10-09 踩过，
    表现是「NX 明明活着，脚本却说等了 180 秒没就绪」。
    """
    try:
        import start_bridge
        return bool(start_bridge.heartbeat_state().get("online"))
    except Exception:
        # 降级：自己按 mtime + pid 算一遍
        if not os.path.isfile(HEARTBEAT):
            return False
        age = time.time() - os.path.getmtime(HEARTBEAT)
        return age <= 20.0


def ugraf_pids():
    """列出活着的 ugraf.exe 的 PID。用 tasklist（GBK 输出）。"""
    pids = []
    try:
        raw = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq ugraf.exe"],
                                      stderr=subprocess.DEVNULL)
        text = raw.decode("gbk", errors="replace")
        for line in text.splitlines():
            if "ugraf.exe" in line.lower():
                parts = line.split()
                for p in parts[1:]:
                    if p.isdigit():
                        pids.append(int(p))
                        break
    except Exception:
        pass
    return pids


def start_nx(timeout=180.0):
    """启动 NX（桥接 journal）。返回 (True, 秒数) 或 (False, 原因)。"""
    exe = os.path.join(nx_dir(), "NXBIN", "ugraf.exe")
    if not os.path.isfile(exe):
        return False, "找不到 NX: %s" % exe

    args = [exe, "-student", "-auto=%s" % BRIDGE]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0 = time.time()
    next_block_check = 25.0
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        if heartbeat_ok():
            return True, time.time() - t0

        elapsed = time.time() - t0
        if elapsed >= next_block_check:
            next_block_check = elapsed + 10.0
            issue = blocking_issue()
            if issue is not None:
                return False, "NX 弹了框要人处理：%s ｜ %s" % (
                    issue.get("title") or "(无标题)",
                    (issue.get("diagnosis") or "")[:200],
                )

        if not ugraf_pids() and elapsed > 30:
            return False, "NX 进程启动后消失了（授权？）"
    return False, "等了 %.0f 秒桥接仍未就绪" % timeout


def blocking_issue():
    """问 dialog_sweeper 有没有卡流程的弹窗。拿不到就当没有。"""
    try:
        import dialog_sweeper
        return dialog_sweeper.blocking_issue()
    except Exception:
        return None


def send(cmd, args=None, timeout=60.0):
    """投一个请求并等回音。"""
    for d in (QUEUE_IN, QUEUE_OUT, STATE_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)
    req_id = "nxrun-%s-%s" % (time.strftime("%H%M%S"), uuid.uuid4().hex[:6])
    in_path = os.path.join(QUEUE_IN, req_id + ".json")
    out_path = os.path.join(QUEUE_OUT, req_id + ".json")
    tmp = in_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"id": req_id, "cmd": cmd, "args": args or {}}, fh, ensure_ascii=False)
    os.rename(tmp, in_path)

    started = time.time()
    while time.time() - started < timeout:
        if os.path.isfile(out_path):
            resp = _read_json(out_path)
            if resp is not None:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                return resp, time.time() - started
        time.sleep(0.15)
    return None, time.time() - started


# ----------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="起 NX + 跑 journal + 拿回结果（一次搞定）")
    ap.add_argument("journal", help="要在 NX 里执行的 .py journal")
    ap.add_argument("--timeout", type=float, default=180.0, help="等 NX 就绪的秒数")
    ap.add_argument("--cmd-timeout", type=float, default=300.0, help="等单条命令回音的秒数")
    ap.add_argument("--steps", help="后续步骤 JSON 文件：[{'journal':..},{'cmd':..,'args':{..}}]")
    ap.add_argument("--no-start", action="store_true", help="NX 已在线时别再启动")
    ns = ap.parse_args(argv)

    journal = os.path.abspath(ns.journal)
    if not os.path.isfile(journal):
        print("找不到 journal: %s" % journal)
        return 2

    if heartbeat_ok():
        print("桥接已在线，直接复用（不再重复启动 NX）。")
    else:
        print("启动 NX（桥接模式）...")
        ok, info = start_nx(ns.timeout)
        if not ok:
            print("启动失败：%s" % info)
            return 3
        print("  就绪，用时 %.0f 秒" % info)

    print("投递 journal: %s" % os.path.basename(journal))
    resp, took = send("run_journal", {"path": journal}, timeout=ns.cmd_timeout)
    print("  回音耗时 %.1f 秒" % took)

    # 后续步骤：--steps 里描述的 journal / 命令序列，按顺序执行
    steps = []
    if ns.steps:
        try:
            with open(ns.steps, "r", encoding="utf-8") as fh:
                steps = json.load(fh)
        except Exception as exc:
            print("读 --steps 失败：%r" % (exc,))
            return 6

    for i, st in enumerate(steps, 1):
        if "journal" in st:
            jp = st["journal"]
            if not os.path.isabs(jp):
                jp = os.path.join(HOME, jp)
            print("[步骤 %d] journal: %s" % (i, os.path.basename(jp)))
            r2, t2 = send("run_journal", {"path": jp}, timeout=ns.cmd_timeout)
        elif "cmd" in st:
            print("[步骤 %d] 命令: %s" % (i, st["cmd"]))
            r2, t2 = send(st["cmd"], st.get("args") or {}, timeout=ns.cmd_timeout)
        else:
            print("[步骤 %d] 看不懂，跳过：%r" % (i, st))
            continue

        if r2 is None:
            print("     超时无回音（%.0f 秒）" % ns.cmd_timeout)
            continue
        tag = "OK " if r2.get("ok") else "FAIL"
        print("     %s ｜ %.1f 秒" % (tag, t2))
        payload2 = (r2.get("result") or {})
        note2 = payload2.get("result_note")
        if note2:
            print("     摘要: %s" % note2)
        res2 = payload2.get("result")
        if isinstance(res2, dict) and "log" in res2:
            for line in res2["log"]:
                print("       · %s" % line)
        if not r2.get("ok"):
            print("     错误: %s" % json.dumps(r2, ensure_ascii=False)[:1500])

    if resp is None:
        print("\n没有回音（超时 %.0f 秒）。多半是 NX 里卡了模态框，" % ns.cmd_timeout)
        print("跑 python tools/dialog_sweeper.py --once 看看。")
        return 4

    if not resp.get("ok"):
        print("\nNX 那边报错了：")
        print(json.dumps(resp, ensure_ascii=False, indent=2)[:4000])
        return 5

    data = resp.get("result") or {}
    print("\n" + "=" * 70)
    print("RESULT_NOTE: %s" % (data.get("result_note") or "(无)"))
    print("=" * 70)
    payload = data.get("result")
    if payload is not None:
        if isinstance(payload, dict) and "log" in payload:
            for line in payload["log"]:
                print("  %s" % line)
            print("-" * 70)
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:8000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
