#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
start_bridge.py -- 一键启动 NX 桥接（不需要手点任何按钮）

它做的事，就是把你原来要在 NX 界面里点的五下鼠标变成一条命令：

    <NX安装目录>\\NXBIN\\ugraf.exe -student -auto=<仓库>\\bridge\\nx_bridge.py

`-auto=<journal>` 是 NX 主程序自带的「启动时自动播放这个脚本」开关。
实测学生版可用，而且因为 ugraf.exe 就是拿到 SALT 许可的那个进程，
完全绕开了 run_journal.exe 的许可墙（3615094）。

用法：
    python tools/start_bridge.py                 # 启动并等到桥接就绪
    python tools/start_bridge.py --timeout 600   # 多等一会儿
    python tools/start_bridge.py --stop          # 让正在跑的桥接体面退出
    python tools/start_bridge.py --check         # 只看状态，不启动任何东西

关于启动时那个「Product Excellence Program」同意框
--------------------------------------------------
NX 每次启动可能先弹一个西门子收集使用数据的同意框（标题
"Product Excellence Program"，选项 Agree / Decline）。
**实测它会阻塞 NX 启动** —— 没点之前 journal 不会跑。

三种处理方式，按推荐顺序：
  1. 一次性关掉它（它自己就是这么说的）：
     NX -> 菜单 -> 首选项 -> 用户默认设置 -> Gateway -> General -> Data Privacy
     -> 取消勾选 "Product Excellence Program - Participate"
     或者直接把用户配置文件里那行改成 value="false"：
       %LOCALAPPDATA%\\Siemens\\Designcenter Student Edition2606\\*_user.dpv
  2. 让本脚本在等待期间自动把它点掉（--consent decline / agree）
  3. 什么都不做，自己手动点（--consent none）

默认是 --consent decline：脚本会自动点「Decline」，也就是**退出数据收集**。
选这个作默认，是因为替别人点「Agree」等于替他同意了数据采集，这种事不该代劳；
而点「Decline」不产生任何对外授权，同时让 NX 继续启动。
要改成同意，显式传 --consent agree。

退出码：0 = 桥接已就绪；1 = 超时；2 = 环境问题（找不到 NX / 脚本等）；
        3 = NX 弹了要人处理的框（最常是要重新登录），已把成因和解法打印出来了。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("NX_AGENT_HOME") or os.path.dirname(HERE)
BRIDGE = os.path.join(HOME, "bridge", "nx_bridge.py")
QUEUE_IN = os.path.join(HOME, "queue", "in")
STATE_DIR = os.path.join(HOME, "state")
HEARTBEAT = os.path.join(STATE_DIR, "heartbeat.json")

STALE_SECONDS = 30  # 心跳超过这个时长算不新鲜

OK, BAD, WARN = "PASS", "FAIL", "WARN"


# ---------------------------------------------------------------- NX 定位

def _as_nx_dirs(found):
    """find_nx_install() 返回的是列表，这里统一成「含 ugraf.exe 的目录列表」。"""
    if not found:
        return []
    if isinstance(found, str):
        found = [found]
    out = []
    for d in found:
        if not d:
            continue
        d = str(d).rstrip("\\")
        if os.path.isfile(os.path.join(d, "NXBIN", "ugraf.exe")):
            out.append(d)
        elif os.path.isfile(os.path.join(d, "ugraf.exe")):
            out.append(os.path.dirname(d))
    return out


def find_nx():
    """复用预检脚本里的查找逻辑，避免出现两套规则。"""
    local = os.path.join(HERE, "preflight_check.py")
    if os.path.isfile(local):
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("_preflight", local)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cands = _as_nx_dirs(mod.find_nx_install())
            if cands:
                return cands[0]
        except Exception:
            pass
    for env in ("UGII_BASE_DIR",):
        v = os.environ.get(env)
        if v and os.path.isfile(os.path.join(v, "NXBIN", "ugraf.exe")):
            return v
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
        v = winreg.QueryValueEx(key, "UGII_BASE_DIR")[0]
        if v and os.path.isfile(os.path.join(v, "NXBIN", "ugraf.exe")):
            return v
    except Exception:
        pass
    # 最后兜底：扫常见位置
    import glob

    for pat in (r"C:\Program Files\Siemens\Dc*", r"C:\Program Files\Siemens\Designcenter*",
                r"D:\Siemens\Dc*", r"D:\Siemens\Designcenter*",
                r"D:\NX", r"E:\Siemens\Dc*", r"D:\Program Files\Siemens\Dc*"):
        for d in sorted(glob.glob(pat), reverse=True):
            if os.path.isfile(os.path.join(d, "NXBIN", "ugraf.exe")):
                return d
    return None


# ---------------------------------------------------------------- 心跳判活

def pid_alive(pid):
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
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == 259
    finally:
        kernel32.CloseHandle(h)


def read_heartbeat():
    try:
        with open(HEARTBEAT, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def heartbeat_state():
    if not os.path.isfile(HEARTBEAT):
        return {"online": False, "reason": "没有 heartbeat.json，桥接没跑过"}
    hb = read_heartbeat() or {}
    age = time.time() - os.path.getmtime(HEARTBEAT)
    alive = pid_alive(hb.get("pid"))
    hb["heartbeat_age_seconds"] = round(age, 1)
    hb["process_alive"] = alive
    if alive is False:
        hb["online"] = False
        hb["reason"] = "心跳里的 PID %s 已消失 —— 桥接退出了" % hb.get("pid")
    elif age > STALE_SECONDS:
        hb["online"] = False
        hb["reason"] = "进程还在，但心跳停了 %.0f 秒（被弹窗挡住了？）" % age
    else:
        hb["online"] = True
    return hb


# ---------------------------------------------------------------- 自动点同意框

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
BM_CLICK = 0x00F5

# 必须显式声明 argtypes。
# 不声明的话 ctypes 会把 HWND 当成 c_int 处理，而句柄值常常超过 32 位有符号范围，
# 于是回调里抛 OverflowError、被 ctypes 静默吞掉 —— 表现是「有时候点得到、有时候点不到」，
# 极难排查。实测踩过这个坑。
user32.EnumWindows.argtypes = [EnumProc, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.EnumChildWindows.argtypes = [wt.HWND, EnumProc, wt.LPARAM]
user32.EnumChildWindows.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t

# 同意框的特征：标准 Win32 对话框 (#32770)，里面有这些按钮
CONSENT_TITLE = "product excellence program"
CONSENT_BUTTONS = {"agree": "&agree", "decline": "&decline"}


def _text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _cls(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def ugraf_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ugraf.exe", "/FO", "CSV"],
                         capture_output=True).stdout.decode("gbk", "replace")
    pids = []
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) >= 2 and "ugraf" in parts[0]:
            d = "".join(c for c in parts[1] if c.isdigit())
            if d:
                pids.append(int(d))
    return pids


def dismiss_consent(consent):
    """把阻塞启动的同意框点掉。返回这次点掉的按钮文字列表。"""
    if consent == "none":
        return []
    want = CONSENT_BUTTONS.get(consent)
    if not want:
        return []

    targets = set(ugraf_pids())
    if not targets:
        return []
    clicked = []

    def on_window(hwnd, lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in targets:
            return True
        if _cls(hwnd) != "#32770":
            return True
        title = _text(hwnd).strip().lower()
        if CONSENT_TITLE not in title:
            return True
        hit = []

        def on_child(child, lp):
            if _cls(child).strip().lower() != "button":
                return True
            label = _text(child).strip().lower()
            if label == want:
                hit.append(child)
            return True

        user32.EnumChildWindows(hwnd, EnumProc(on_child), 0)
        for btn in hit:
            user32.SendMessageW(btn, BM_CLICK, 0, 0)
            clicked.append(consent)
        return True

    user32.EnumWindows(EnumProc(on_window), 0)
    return clicked


# ---------------------------------------------------------------- 启停

def stop_bridge():
    st = heartbeat_state()
    if not st.get("online"):
        print("桥接当前不在线：%s" % st.get("reason"))
        return 0
    rid = "stop-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    path = os.path.join(QUEUE_IN, rid + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"id": rid, "cmd": "stop", "args": {}}, fh)
    os.rename(tmp, path)
    print("已投递停止请求 %s，等它退出..." % rid)
    for _ in range(30):
        time.sleep(0.5)
        if not heartbeat_state().get("online"):
            print("桥接已退出。")
            return 0
    print("30 秒内没退出。可以去 NX 里点掉弹窗，或按 state/bridge.pid 结束进程。")
    return 1


def _load_sweeper():
    """按路径加载 dialog_sweeper（不把它所在的 tools/ 加进 sys.path，避免脚本名污染）。"""
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dialog_sweeper.py")
    spec = importlib.util.spec_from_file_location("dialog_sweeper_bridge", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def launch(nx_dir, timeout, consent, journal=BRIDGE):
    if not os.path.isfile(journal):
        print("找不到桥接脚本：%s" % journal)
        return 2

    exe = os.path.join(nx_dir, "NXBIN", "ugraf.exe")
    if not os.path.isfile(exe):
        print("找不到 %s" % exe)
        return 2

    # 学生版需要 -student 才能走 SALT 授权；普通安装不需要。
    args = [exe]
    if os.path.isdir(os.path.join(nx_dir, "SAAS")):
        args.append("-student")
        kind = "学生版（SALT 授权）"
    else:
        kind = "普通安装"
    args.append("-auto=" + journal)

    st = heartbeat_state()
    if st.get("online"):
        print("桥接**已经在线**了（pid=%s，心跳 %.1f 秒前）。" % (st.get("pid"), st.get("heartbeat_age_seconds")))
        print("不需要重复启动。要重启就先 python tools/start_bridge.py --stop")
        return 0

    print("NX 安装: %s  [%s]" % (nx_dir, kind))
    print("启动命令: %s" % " ".join('"%s"' % a if " " in a else a for a in args))
    print()

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0 = time.time()
    dismissed = 0
    announced_dialog = False
    next_block_check = 25.0        # 25 秒后再开始查弹窗（前 20 秒 NX 还在起，框还没出来）
    while time.time() - t0 < timeout:
        time.sleep(2)
        if consent != "none":
            hits = dismiss_consent(consent)
            if hits:
                dismissed += len(hits)
                print("  [%.0fs] 自动点了同意框的「%s」（共 %d 次）"
                      % (time.time() - t0, consent, dismissed))

        # ★ 主动查「卡住流程」的弹窗：最常见的是要重新登录。
        #   以前的表现是干等到 timeout，人只看到"失败"，不知道是登录框。
        elapsed = time.time() - t0
        if elapsed >= next_block_check:
            next_block_check = elapsed + 10.0
            issue = _load_sweeper().blocking_issue()
            if issue is not None:
                print()
                print("  [%.0fs] ⛔ 卡住了 —— NX 弹了个框要你来处理" % elapsed)
                print("     窗口标题： %s" % (issue.get("title") or "(无标题)"))
                for line in (issue.get("body") or [])[:6]:
                    print("     │ %s" % line)
                print("     判断： %s" % issue.get("diagnosis"))
                print("     怎么办：")
                for i, step in enumerate(issue.get("steps") or [], 1):
                    print("       %d) %s" % (i, step))
                print()
                print("     处理完直接重跑本命令即可（NX 不用关）。")
                return 3

        st = heartbeat_state()
        if st.get("online"):
            print("  [%.0fs] ✅ 桥接已就绪" % (time.time() - t0))
            print()
            print("心跳: pid=%s ｜ 模式=%s ｜ 已轮询 %s 次 ｜ 在 NX 内=%s"
                  % (st.get("pid"), st.get("mode"), st.get("loops"), st.get("nx_available")))
            if dismissed:
                print("（期间自动点掉 %d 次同意框；想永久消除，见本脚本头部说明）" % dismissed)
            print()
            print("下一步验证：python tools/bridge_probe.py")
            return 0

        if not ugraf_pids() and time.time() - t0 > 20:
            print("  [%.0fs] ⚠️ NX 进程不见了" % (time.time() - t0))
            break

        if not announced_dialog and consent == "none":
            # 提示可能是被弹窗挡住了
            if os.path.isfile(HEARTBEAT) is False and time.time() - t0 > 90:
                announced_dialog = True
                print("  [%.0fs] 一直没动静 —— 大概率 NX 里有个同意框在等你点。" % (time.time() - t0))
                print("           去 NX 窗口点一下；或者下次加 --consent decline 让脚本自动点。")

    print()
    print("超时（%.0f 秒）内桥接没有就绪。" % timeout)
    print("排查顺序：")
    print("  1) NX 那边是不是有窗口在等点击？（同意框会挡住 journal）")
    print("  2) 手动跑一次 NX 看能不能正常进界面")
    print("  3) python tools/preflight_check.py --auto  查授权与关键文件")
    return 1


def check_only():
    st = heartbeat_state()
    print("桥接目录: %s" % HOME)
    print("状态: %s" % ("在线" if st.get("online") else "不在线"))
    for k in ("pid", "process_alive", "mode", "loops", "processed", "stale_skipped",
              "uptime_seconds", "heartbeat_age_seconds", "note", "reason"):
        if k in st:
            print("  %-22s %s" % (k + ":", st[k]))
    print("NX 进程: %s" % (ugraf_pids() or "无"))
    return 0 if st.get("online") else 1


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="一键启动 NX 桥接")
    ap.add_argument("--stop", action="store_true", help="让正在跑的桥接体面退出")
    ap.add_argument("--check", action="store_true", help="只看状态，不启动任何东西")
    ap.add_argument("--timeout", type=float, default=420, help="等待桥接就绪的秒数，默认 420")
    ap.add_argument("--consent", choices=["decline", "agree", "none"], default="decline",
                    help="遇到「Product Excellence Program」同意框时怎么办，默认 decline（退出数据收集）")
    ap.add_argument("--nx-dir", help="NX 安装目录，默认自动查找")
    ns = ap.parse_args(argv)

    if ns.check:
        return check_only()
    if ns.stop:
        return stop_bridge()

    nx_dir = ns.nx_dir or find_nx()
    if not nx_dir:
        print("找不到 NX 安装目录。用 --nx-dir 指定，或先跑 tools/preflight_check.py --auto。")
        return 2
    return launch(nx_dir, ns.timeout, ns.consent)


if __name__ == "__main__":
    sys.exit(main())
