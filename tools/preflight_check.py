#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight_check.py -- NX 装机自检（装之前一遍，装之后一遍）

为什么要有这个：NX 的下载包接近 19GB，装完还要几十 GB，
装到一半发现系统不满足要求会非常难受。另外装 NX 时有个
「Programming Tools → Programming Interfaces」几乎所有人都会漏勾，
漏了就没有 run_journal.exe，本项目整套自动化就无从谈起。
这个脚本就是用来在 3 秒内把这两个问题都暴露出来的。

用法:
    python preflight_check.py                        # 装之前：硬件与环境自检
    python preflight_check.py --auto                 # 装之后：自动找 NX 目录并验证
    python preflight_check.py --nx-dir "C:\\Program Files\\Siemens\\NX2606"
    python preflight_check.py --json report.json

退出码:
    0 = 全部通过（可能有 WARN）
    1 = 有 FAIL，必须先解决
    2 = 脚本自身出错

约定：纯标准库。理由见 README —— NX 自带的 Python 环境弱，不能假设有 pip 包，
     而且这个脚本要能在任何一台 Windows 上直接跑起来。
"""

import argparse
import ctypes
import glob
import json
import os
import platform
import shutil
import sys

# ---------------------------------------------------------------- 结果收集

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
MARK = {PASS: "[ OK ]", WARN: "[警告]", FAIL: "[失败]", INFO: "[信息]"}

RESULTS = []


def add(level, name, detail, advice=""):
    RESULTS.append({"level": level, "name": name, "detail": detail, "advice": advice})


# 版本门槛（来自西门子 2026 年 6 月版 Designcenter 官方系统要求 + NX 2606 开发计划）
DISK_NEED_GB = 60          # 下载包 19GB + 安装 9GB + 留余量做 prt 文件
RAM_RECOMMEND_GB = 32      # 官方推荐
RAM_MIN_GB = 8             # 官方最低（实际跑不动，8GB 只是能启动）
VIDEO_RAM_MIN_MB = 2048    # 官方最低 2GB 显存

# 这些虚拟显示适配器不是真实显卡，不算数
VIRTUAL_GPU_HINTS = ("virtual", "remote display", "basic display", "gameviewer",
                     "sunshine", "parsec", "meta ", "displaylink")


# ---------------------------------------------------------------- 硬件探测

def get_os_info():
    info = {
        "product": platform.system(),
        "release": platform.release(),
        "build": platform.version(),
        "caption": "",
        "display_version": "",
        "edition": "",
    }
    if os.name != "nt":
        return info
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        for reg_name, field in (("ProductName", "caption"),
                                ("DisplayVersion", "display_version"),
                                ("EditionID", "edition")):
            try:
                info[field] = winreg.QueryValueEx(key, reg_name)[0]
            except OSError:
                pass
    except Exception:
        pass
    # build 号：platform.version() 形如 10.0.19045，末段才是 build
    try:
        parts = info["build"].split(".")
        info["build_no"] = int(parts[-1])
    except Exception:
        info["build_no"] = 0
    return info


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_ram_gb():
    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.ullTotalPhys / float(1024 ** 3)
    except Exception:
        return None


def get_disks():
    """返回 [(盘符, 总 GB, 可用 GB), ...]，只列固定磁盘。"""
    out = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = letter + ":\\"
        if not os.path.exists(root):
            continue
        try:
            total, _used, free = shutil.disk_usage(root)
        except Exception:
            continue
        if total == 0:
            continue
        out.append((letter, total / float(1024 ** 3), free / float(1024 ** 3)))
    return out


def get_gpus():
    """从注册表枚举显示适配器。返回 [{name, driver, vram_mb, virtual}, ...]"""
    gpus = []
    if os.name != "nt":
        return gpus
    try:
        import winreg
    except Exception:
        return gpus

    class_key = (r"SYSTEM\CurrentControlSet\Control\Class"
                 r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_key)
    except OSError:
        return gpus

    seen = set()
    index = 0
    while True:
        try:
            sub = winreg.EnumKey(root, index)
        except OSError:
            break
        index += 1
        if not sub.isdigit():
            continue
        try:
            sk = winreg.OpenKey(root, sub)
            try:
                name = winreg.QueryValueEx(sk, "DriverDesc")[0]
            except OSError:
                continue
            try:
                driver = winreg.QueryValueEx(sk, "DriverVersion")[0]
            except OSError:
                driver = "?"
            vram = 0
            for vk in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                try:
                    vram = int(winreg.QueryValueEx(sk, vk)[0])
                    break
                except OSError:
                    continue
            low = name.lower()
            entry = {
                "name": name,
                "driver": driver,
                "vram_mb": vram / float(1024 ** 2),
                "virtual": any(h in low for h in VIRTUAL_GPU_HINTS),
            }
            key = (name, round(entry["vram_mb"]))
            if key in seen:
                continue
            seen.add(key)
            gpus.append(entry)
        except OSError:
            continue
    return gpus


# ---------------------------------------------------------------- 装之前检查

def check_os():
    info = get_os_info()
    if info["product"] != "Windows":
        add(FAIL, "操作系统", "当前系统 %s，NX 只支持 Windows 64 位" % info["product"],
            "换一台 Windows 机器，或者用虚拟机（但虚拟机跑 CAD 体验很差）")
        return

    bits = platform.machine()
    build = info["build_no"]
    label = "%s %s（build %s）" % (info["caption"] or "Windows",
                                   info["display_version"] or "",
                                   build)
    label = label.replace("  ", " ").strip()

    if bits not in ("AMD64", "x86_64"):
        add(FAIL, "操作系统", "%s，但是 %s 架构" % (label, bits),
            "NX 只有 64 位版本")
        return

    if build >= 22000:
        add(PASS, "操作系统", "%s —— Windows 11，官方「支持」名单内" % label)
    elif build >= 10240:
        add(WARN, "操作系统",
            "%s —— Windows 10，官方列为「有限支持（Limited support）」，不在「支持」名单内" % label,
            "官方原话：Windows 11 是 Supported，Windows 10 是 Limited support。\n"
            "        实际含义是「还能装、大概率能跑，但官方没测过，出问题不接 bug」。\n"
            "        社区经验：NX 历年来在「不支持」的系统上都能正常装和用（比如当年 NX12 说不支持 Win7 其实也能跑）。\n"
            "        所以不必为此重装系统 —— 照装，跑得起来就用，只是在遇到怪问题时记住这个前提。")
    else:
        add(FAIL, "操作系统", "%s —— 版本过旧" % label, "至少需要 Windows 10 64 位")


def check_ram():
    ram = get_ram_gb()
    if ram is None:
        add(INFO, "内存", "读取失败，请手动确认（任务管理器 → 性能 → 内存）")
        return
    if ram >= RAM_RECOMMEND_GB:
        add(PASS, "内存", "%.1f GB —— 超过官方推荐值 %d GB" % (ram, RAM_RECOMMEND_GB))
    elif ram >= 16:
        add(WARN, "内存", "%.1f GB —— 能跑，但开大型装配体会紧张" % ram,
            "官方推荐 %d GB。做简单零件和 CAM 编程够用。" % RAM_RECOMMEND_GB)
    elif ram >= RAM_MIN_GB:
        add(WARN, "内存", "%.1f GB —— 刚到官方最低线" % ram,
            "官方最低 8GB 只是「能启动」。强烈建议加内存，否则建模时每一步都在等。")
    else:
        add(FAIL, "内存", "%.1f GB —— 低于官方最低 %d GB" % (ram, RAM_MIN_GB),
            "加内存，或者改用 30 天商业试用的云端方案")


def check_disk():
    disks = get_disks()
    if not disks:
        add(INFO, "磁盘", "没有探测到固定磁盘，请手动确认")
        return
    best = max(disks, key=lambda d: d[2])
    table = "、".join("%s: 可用 %.1f GB / 共 %.0f GB" % (d[0], d[2], d[1]) for d in disks)
    if best[2] >= DISK_NEED_GB:
        add(PASS, "磁盘", table,
            "建议装到 %s:（可用最多）。安装包 19GB 解压后还要占空间，"
            "解压目录也放同一个盘。" % best[0])
    elif best[2] >= 30:
        add(WARN, "磁盘", table,
            "空间偏紧。NX 安装包约 19GB + 装完约 9GB，建议先清理出 %d GB 以上，"
            "并优先装到 %s:。" % (DISK_NEED_GB, best[0]))
    else:
        add(FAIL, "磁盘", table, "空间不足，至少需要 %d GB 以上可用空间" % DISK_NEED_GB)


def check_gpu():
    gpus = get_gpus()
    if not gpus:
        add(INFO, "显卡", "注册表里没读到显示适配器信息",
            "手动确认：设备管理器 → 显示适配器。NX 需要支持 OpenGL 4.5 的显卡。")
        return

    real = [g for g in gpus if not g["virtual"]]
    virtual = [g for g in gpus if g["virtual"]]

    if not real:
        add(WARN, "显卡", "只探测到虚拟显示适配器（远程桌面 / 投屏类）",
            "如果你是远程连过去用，本机真实显卡可能被挡住了。"
            "建议直接在物理机上确认显卡型号。")
        return

    lines = []
    verdict = PASS
    advice = []
    for g in real:
        vram = g["vram_mb"]
        marker = ""
        if vram < VIDEO_RAM_MIN_MB:
            marker = "  ← 显存低于官方最低 2GB"
            verdict = WARN
        lines.append("%s（显存 %.0f MB，驱动 %s）%s" % (g["name"], vram, g["driver"], marker))
        low = g["name"].lower()
        if "rtx" in low or "quadro" in low or "radeon pro" in low or "firepro" in low:
            advice.append("%s 属于专业/高性能卡，NX 官方认证显卡列表里通常有它，没问题。" % g["name"])
    if virtual:
        lines.append("（另有 %d 个虚拟/远程显示适配器，不算数：%s）"
                     % (len(virtual), "、".join(g["name"] for g in virtual)))

    add(verdict, "显卡", "；".join(lines),
        "\n".join(advice) if advice else
        "NX 需要支持 OpenGL 4.5 的显卡。集成显卡近年型号也能跑，但曲面显示和转模型会明显卡。")
    add(INFO, "OpenGL 版本",
        "无法从脚本读取，需要人工确认",
        "最省事的办法：装完 NX 能正常打开并显示一个默认零件，就说明 OpenGL 这一关过了。\n"
        "        或者下载一个免费的 GPU Caps Viewer，看 OpenGL 那一栏是否 >= 4.5。")


def check_env_conflicts():
    """装 NX 之前检查有没有会打架的东西。"""
    existing = find_nx_install()
    if existing:
        add(WARN, "已存在的 NX 安装", "发现 %d 处 NX：%s" % (len(existing), "、".join(existing)),
            "学生版安装会**覆盖系统环境变量 SPLM_LICENSE_SERVER**，指向学生版自带的许可证。\n"
            "        如果你机器上还有学校授权版/公司版的 NX，装完学生版之后那个会失效。\n"
            "        两条建议：① 一台机器只留一套 NX；② 装之前记下当前的 SPLM_LICENSE_SERVER 值，"
            "出问题好改回来。")
    else:
        add(PASS, "环境冲突", "没有发现已安装的 NX，不存在许可证打架的问题")

    server = os.environ.get("SPLM_LICENSE_SERVER") or os.environ.get("UGS_LICENSE_SERVER")
    if server:
        add(INFO, "环境变量 SPLM_LICENSE_SERVER", "当前值 = %s" % server,
            "**装之前把这个值记下来**。学生版安装程序会覆盖它。")
    else:
        add(INFO, "环境变量 SPLM_LICENSE_SERVER", "当前未设置",
            "没有旧许可证配置，不会冲突。")

    ssc = find_ssc()
    if ssc:
        add(PASS, "Siemens Software Center", "已安装：%s" % ssc)
    else:
        add(INFO, "Siemens Software Center", "未安装",
            "2026 年 7 月起，学生版必须通过这个客户端下载和安装，"
            "不能直接下 zip 了。第一次装的话先去官网注册试用再下它。")


# ---------------------------------------------------------------- 装之后检查

def read_machine_env(name):
    """从注册表读「机器级环境变量」。

    为什么要绕这一圈：环境变量是写进注册表的，但**已经在运行的进程不会自动拿到新值**。
    你在当前命令行里 os.environ 看不到，不代表它没设置。
    """
    if os.name != "nt":
        return None
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
        val = winreg.QueryValueEx(key, name)[0]
        return val or None
    except Exception:
        return None


def find_nx_install():
    """找 NX 安装目录：先查卸载信息，再查老式注册表键，再扫常见路径。返回 [路径, ...]"""
    found = []

    if os.name == "nt":
        try:
            import winreg
            # ① 现代安装（含学生版）都登记在「卸载」列表里，带 InstallLocation。
            #    学生版的目录名是 DcStudentEdition2606 这种，老的 NX* 通配符根本匹配不到。
            for hive, path in ((winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                               (winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")):
                try:
                    root = winreg.OpenKey(hive, path)
                except OSError:
                    continue
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        sk = winreg.OpenKey(root, sub)
                        display = winreg.QueryValueEx(sk, "DisplayName")[0] or ""
                    except OSError:
                        continue
                    upper = display.upper()
                    if not any(w in upper for w in ("NX", "DESIGNCENTER", "SIEMENS")):
                        continue
                    for value_name in ("InstallLocation", "UGII_BASE_DIR"):
                        try:
                            val = winreg.QueryValueEx(sk, value_name)[0]
                        except OSError:
                            continue
                        val = (val or "").rstrip("\\")
                        if val and os.path.isdir(val) and val not in found:
                            found.append(val)
            # ② 老式 NX 的注册表键
            for hive, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Unigraphics Solutions"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Unigraphics Solutions")):
                try:
                    root = winreg.OpenKey(hive, path)
                except OSError:
                    continue
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    for value_name in ("UGII_BASE_DIR", ""):
                        try:
                            if value_name:
                                val = winreg.QueryValueEx(winreg.OpenKey(root, sub), value_name)[0]
                            else:
                                val = winreg.QueryValue(winreg.OpenKey(root, sub), "")
                        except OSError:
                            continue
                        if val and os.path.isdir(val) and val not in found:
                            found.append(val)
        except Exception:
            pass

    # ③ 环境变量（当前进程 + 注册表里的机器级值）
    for env_dir in (os.environ.get("UGII_BASE_DIR"), read_machine_env("UGII_BASE_DIR")):
        if env_dir and os.path.isdir(env_dir) and env_dir not in found:
            found.append(env_dir)

    # ④ 最后兜底扫常见路径（含学生版的 Dc* 命名）
    if os.name == "nt":
        for pattern in (r"C:\Program Files\Siemens\NX*",
                        r"C:\Program Files\Siemens\Designcenter*",
                        r"C:\Program Files\Siemens\Dc*",
                        r"C:\Program Files\Siemens\NX*\NXBIN",
                        r"D:\Program Files\Siemens\NX*",
                        r"D:\Program Files\Siemens\Designcenter*",
                        r"D:\Program Files\Siemens\Dc*",
                        r"D:\Siemens\NX*",
                        r"D:\Siemens\Designcenter*",
                        r"D:\Siemens\Dc*",
                        r"E:\Program Files\Siemens\Dc*",
                        r"E:\Siemens\NX*",
                        r"E:\Siemens\Designcenter*",
                        r"E:\Siemens\Dc*"):
            for hit in glob.glob(pattern):
                base = hit
                if os.path.basename(base).upper() == "NXBIN":
                    base = os.path.dirname(base)
                if os.path.basename(base).upper() == "NXBIN":
                    continue
                if os.path.isdir(base) and base not in found:
                    found.append(base)
    return found


def find_ssc():
    """找 Siemens Software Center 是否装了。

    注意它真实的位置是 C:\\Program Files\\Siemens\\SoftwareCenter\\（嵌在 Siemens 目录下），
    所以不能只扫一层通配符，要把中间那层也点出来。
    """
    candidates = []
    for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        # 平铺的
        candidates += glob.glob(os.path.join(base, "*Siemens*Software*Center*"))
        candidates += glob.glob(os.path.join(base, "Programs", "*Siemens*Software*Center*"))
        # 嵌在 Siemens 目录里的（实际就是这个）
        candidates += glob.glob(os.path.join(base, "Siemens", "SoftwareCenter"))
        candidates += glob.glob(os.path.join(base, "Siemens", "*Software*Center*"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def find_student_license_key():
    """读学生版写在注册表里的授权信息。

    学生版会在这里登记：HKLM\\SOFTWARE\\Siemens\\NXStudent <版本>
      INSTALLDIR    = 安装目录
      LANGUAGE      = 界面语言
      LICENSESERVER = 形如 29000@LocalHost
      UGII_BASE_DIR = 同安装目录
    """
    if os.name != "nt":
        return None
    try:
        import winreg
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Siemens")
    except OSError:
        return None
    index = 0
    while True:
        try:
            sub = winreg.EnumKey(root, index)
        except OSError:
            break
        index += 1
        if not sub.upper().startswith("NXSTUDENT"):
            continue
        try:
            sk = winreg.OpenKey(root, sub)
        except OSError:
            continue
        info = {"key": sub}
        for value in ("INSTALLDIR", "LANGUAGE", "LICENSESERVER", "UGII_BASE_DIR"):
            try:
                info[value] = winreg.QueryValueEx(sk, value)[0]
            except OSError:
                info[value] = None
        return info
    return None


def scan_latest_ssc_log():
    """从最近一个 SSC 日志里找「激活」相关的结论。

    这是判断学生版授权有没有真正生效的**唯一可靠证据** ——
    文件装没装齐是另一回事，激活失败的话 NX 一样起不来。
    """
    base = r"C:\ProgramData\Siemens\SoftwareCenter\logs"
    if not os.path.isdir(base):
        return None
    logs = glob.glob(os.path.join(base, "ssc_history_*.txt"))
    if not logs:
        return None
    latest = max(logs, key=lambda p: os.path.getmtime(p))
    try:
        with open(latest, "r", encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return None
    result = {"path": latest, "activation_ok": None, "lines": []}
    keywords = ("激活", "SALT", "身份验证", "凭证", "刷新令牌", "激活错误")
    for line in text.splitlines():
        if any(k in line for k in keywords):
            stripped = line.strip()
            if stripped:
                result["lines"].append(stripped)
    blob = "\n".join(result["lines"])
    if "激活失败" in blob or "SALT_status_bad_credentials" in blob or "SALT_status_not_activated" in blob:
        result["activation_ok"] = False
    elif "激活" in blob and "激活失败" not in blob:
        result["activation_ok"] = True
    return result


def student_license_findings():
    """学生版专属的授权检查。返回 [(level, name, detail, advice), ...]

    为什么要单独一条：学生版**不装传统的 FlexLM 许可证服务器**，
    也**不靠 SPLM_LICENSE_SERVER 环境变量**。它走的是 SALT 云端激活：
    SSC 客户端登录 → 服务器下发凭证 → NX 启动时凭这份凭证说话。
    所以「SPLM_LICENSE_SERVER 没设置」在学生版上是正常的，不该报 FAIL。
    """
    out = []
    info = find_student_license_key()

    if info:
        licserver = info.get("LICENSESERVER")
        out.append((PASS, "学生版授权登记",
                    "注册表里有 %s（语言 %s）" % (info["key"], info.get("LANGUAGE") or "?"),
                    "LICENSESERVER = %s。注意这个值是 SALT 组件的地址，不是传统许可证服务器，\n"
                    "        平常没有进程监听它是正常的，NX 启动时才会拉起来。" % (licserver or "?")))

        # 端口是否在监听（通常没在跑，仅供参考）
        port = None
        if licserver and "@" in licserver:
            head = licserver.split("@", 1)[0]
            if head.isdigit():
                port = int(head)
        if port:
            import socket
            s = socket.socket()
            s.settimeout(1.5)
            try:
                s.connect(("127.0.0.1", port))
                listening = True
            except Exception:
                listening = False
            finally:
                s.close()
            out.append((INFO, "本地授权端口 %d" % port,
                        "有服务在监听" if listening else "当前没有服务在监听",
                        "没在监听是正常的 —— NX 起来的时候才会拉起它。"
                        "只有 NX 启动报许可证错误时才需要关心。"))
    else:
        out.append((INFO, "学生版授权登记",
                    "注册表里没找到 NXStudent 键（可能不是学生版）",
                    "如果你装的确实是学生版却找不到这条，说明安装没走完，重跑一次 SSC 里的 Install。"))

    # 日志里的激活结论 —— 这条最关键
    log = scan_latest_ssc_log()
    if not log:
        out.append((INFO, "授权激活状态", "没找到 SSC 日志，无法判断", ""))
        return out
    if log["activation_ok"] is False:
        out.append((FAIL, "授权激活状态", "上一次激活失败了",
                    "证据（来自 %s）：\n%s\n"
                    "        这是**学生版最常见的坑**：安装过程拖太久（下载慢），SSC 的登录令牌中途过期，\n"
                    "        紧接着它拿过期令牌去激活许可证，被服务器拒绝。\n"
                    "        后果：文件都在，但 NX 一启动就报许可证错误。\n"
                    "        解法：打开 SSC → 重新登录 → 让它把激活跑完"
                    "（见 docs/下载安装清单.md 第七节）。"
                    % (os.path.basename(log["path"]),
                       "\n".join("          " + ln for ln in log["lines"][-6:]))))
    elif log["activation_ok"] is True:
        out.append((PASS, "授权激活状态", "日志里没有发现激活失败",
                    "来源：%s" % os.path.basename(log["path"])))
    else:
        out.append((INFO, "授权激活状态", "日志里没有激活相关记录",
                    "来源：%s\n        意味着激活流程可能还没走到那一步。"
                    % os.path.basename(log["path"])))
    return out




def check_nx_dir(nx_dir):
    nx_dir = os.path.normpath(nx_dir)
    print("\n" + "=" * 72)
    print("第二阶段：装完之后的自检")
    print("  目标目录: %s" % nx_dir)
    print("=" * 72)

    results = []

    def probe(level, name, detail, advice=""):
        results.append({"level": level, "name": name, "detail": detail, "advice": advice})
        print("%s %s" % (MARK[level], name))
        print("       %s" % detail.replace("\n", "\n       "))
        if advice:
            print("       → %s" % advice.replace("\n", "\n         "))

    nxbin = os.path.join(nx_dir, "NXBIN")

    # 1. run_journal.exe —— 整套项目的前提
    run_journal = os.path.join(nxbin, "run_journal.exe")
    if os.path.isfile(run_journal):
        probe(PASS, "run_journal.exe", run_journal)
    else:
        probe(FAIL, "run_journal.exe", "在 %s 里没找到" % nxbin,
              "说明装的时候没勾 Programming Tools → Programming Interfaces。\n"
              "补救：重跑安装程序 → Modify → 展开 Programming Tools → 勾上 Programming Interfaces。\n"
              "**不解决这一条，整套 Agent 自动化就跑不起来。**")

    # 2. NXOpen.pyd
    py_dir = os.path.join(nxbin, "python")
    nxopen = None
    if os.path.isdir(py_dir):
        for name in ("NXOpen.pyd", "NXOpen.py"):
            candidate = os.path.join(py_dir, name)
            if os.path.isfile(candidate):
                nxopen = candidate
                break
    if nxopen:
        probe(PASS, "NXOpen.pyd", nxopen)
    else:
        probe(FAIL, "NXOpen.pyd", "在 %s 里没找到" % py_dir,
              "同上，Programming Interfaces 组件没装全。")

    # 3. NX 自带的 Python 版本（决定了桥接脚本能用什么语法）
    py_dlls = sorted(glob.glob(os.path.join(py_dir, "python3*.dll")))
    if py_dlls:
        versions = [os.path.splitext(os.path.basename(p))[0] for p in py_dlls]
        probe(INFO, "NX 自带 Python", "、".join(versions),
              "桥接脚本必须用这个版本的语法。本项目所有脚本只用标准库，"
              "就是为了不依赖外部 pip 包。")
    else:
        probe(WARN, "NX 自带 Python", "在 %s 里没找到 python3*.dll" % py_dir)

    # 4. runugpost.exe —— 未验证的可能性：能不能绕开「学生版不能后处理」
    runugpost = None
    for pattern in (os.path.join(nx_dir, "MACH", "auxilliary", "runugpost.exe"),
                    os.path.join(nx_dir, "MACH", "auxiliary", "runugpost.exe")):
        if os.path.isfile(pattern):
            runugpost = pattern
            break
    if runugpost:
        probe(PASS, "runugpost.exe（意外收获）", runugpost,
              "这是「在 NX 会话外跑后处理器」的工具。学生版理论上禁用了后处理，\n"
              "但如果这个 exe 在、且许可证层没挡住，也许能绕过限制。\n"
              "**值一次 5 分钟的实验**（见 docs/模拟能替代多少真机.md 第六节），不要当成结论。")
    else:
        probe(INFO, "runugpost.exe", "没找到。不影响主线，跳过。")

    # 5. 后处理器配置（顺便看有没有）
    template_post = os.path.join(nx_dir, "MACH", "resource", "postprocessor", "template_post.dat")
    if os.path.isfile(template_post):
        probe(PASS, "后处理器配置", template_post,
              "里面是「机床名, xxx.tcl, xxx.def」的清单。如果哪天要做 runugpost 实验，"
              "环境变量 UGII_CAM_POST_CONFIG_FILE 就指向这个文件。")
    else:
        probe(INFO, "后处理器配置", "没找到 template_post.dat，可能是组件没装全")

    # 6. 环境变量（当前进程没有时，去注册表里读机器级的值）
    env_dir = os.environ.get("UGII_BASE_DIR") or read_machine_env("UGII_BASE_DIR")
    if env_dir:
        same = os.path.normcase(os.path.normpath(env_dir)) == os.path.normcase(os.path.normpath(nx_dir))
        probe(PASS if same else WARN, "UGII_BASE_DIR",
              "= %s%s" % (env_dir, "" if same else "（跟本次检查的目录不一致）"),
              "所有 NX 脚本和工具都用这个变量定位安装目录。")
    else:
        probe(WARN, "UGII_BASE_DIR", "未设置",
              "可能需要重启命令行让它生效，或者装的时候环境变量没写进去。")

    # 7. 授权：学生版和商业版完全是两条路，不能混为一谈
    is_student = (os.path.isdir(os.path.join(nx_dir, "SAAS"))
                  or bool(os.environ.get("UGII_USE_NXSAAS_MODE"))
                  or bool(read_machine_env("UGII_USE_NXSAAS_MODE")))
    server = (os.environ.get("SPLM_LICENSE_SERVER")
              or os.environ.get("UGS_LICENSE_SERVER")
              or read_machine_env("SPLM_LICENSE_SERVER"))

    if is_student:
        if server:
            probe(INFO, "授权方式（学生版）", "检测到 SPLM_LICENSE_SERVER = %s" % server,
                  "学生版正常情况下不需要这个变量，它走的是云端激活。有这个值说明机器上还有别的授权源。")
        else:
            probe(INFO, "授权方式（学生版）", "云端激活模式，不需要 SPLM_LICENSE_SERVER",
                  "你装的是学生版（安装目录里有 SAAS 组件），它靠 SSC 客户端登录后下发许可证，\n"
                  "        不会写 SPLM_LICENSE_SERVER。**所以这里「没设置」是正常的，不是问题。**\n"
                  "        真要确认授权是否生效，看下面那条「许可证激活状态」。")
        for level, name, detail, advice in student_license_findings():
            probe(level, name, detail, advice)
    else:
        if server:
            probe(PASS, "SPLM_LICENSE_SERVER", "= %s" % server,
                  "商用/学校授权版靠这个变量指向许可证服务器。")
        else:
            probe(FAIL, "SPLM_LICENSE_SERVER", "未设置",
                  "非学生版的 NX 启动会报许可证错误。\n"
                  "        要么配置 SPLM_LICENSE_SERVER 指向许可证服务器，要么换学生版（不需要它）。")

    # 8. 最后一步：真跑一次最小脚本
    test_script = os.path.join(os.environ.get("TEMP", "."), "nx_agent_selftest.py")
    probe(INFO, "最小脚本测试", "生成 %s" % test_script,
          "请手动执行下面这行（不要在这里自动跑，因为它要起 NX 进程）：\n"
          '        "%s" "%s"' % (run_journal or r"<NX_DIR>\NXBIN\run_journal.exe", test_script))
    try:
        with open(test_script, "w", encoding="utf-8") as fh:
            fh.write(
                "# 由 preflight_check.py 生成的最小验证脚本\n"
                "# 目的：只验证 NX 自带的 Python 能 import NXOpen 并拿到会话对象。\n"
                "import NXOpen\n"
                "\n"
                "print('NXOpen imported successfully')\n"
                "\n"
                "try:\n"
                "    session = NXOpen.Session.GetSession()\n"
                "    print('Session OK:', session)\n"
                "except Exception as exc:\n"
                "    print('NXOpen 导入成功，但拿 Session 失败:', exc)\n"
                "\n"
                "try:\n"
                "    # 用不存在就为空的接口，避免因版本差异报错\n"
                "    print('Listing version info ...')\n"
                "    for name in dir(NXOpen.Session):\n"
                "        if 'Version' in name or 'Release' in name:\n"
                "            print('  ', name)\n"
                "except Exception as exc:\n"
                "    print('版本信息读取跳过:', exc)\n"
            )
    except Exception:
        pass

    return results


# ---------------------------------------------------------------- 主流程

def banner(text):
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NX 装机自检")
    ap.add_argument("--nx-dir", help="NX 安装目录，给了就做装后自检")
    ap.add_argument("--auto", action="store_true", help="自动查找 NX 安装目录并做装后自检")
    ap.add_argument("--json", dest="json_out", help="把结果写成 JSON")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    print("NX 装机自检  |  项目目录: %s" % os.path.dirname(here))

    banner("第一阶段：装之前的硬件与环境自检")
    check_os()
    check_ram()
    check_disk()
    check_gpu()
    check_env_conflicts()

    for r in RESULTS:
        print("%s %s" % (MARK[r["level"]], r["name"]))
        print("       %s" % r["detail"].replace("\n", "\n       "))
        if r["advice"]:
            print("       → %s" % r["advice"].replace("\n", "\n         "))

    fails = [r for r in RESULTS if r["level"] == FAIL]
    warns = [r for r in RESULTS if r["level"] == WARN]

    banner("第一阶段结论")
    if fails:
        print("有 %d 项 FAIL，先解决这些再下载：%s" % (len(fails), "、".join(r["name"] for r in fails)))
    elif warns:
        print("没有硬性问题，有 %d 项 WARN（不影响，知道就行）：%s"
              % (len(warns), "、".join(r["name"] for r in warns)))
    else:
        print("全部通过，可以开始下载。")
        print("下载入口：https://trials.sw.siemens.com/en-US/trials/nx-cad-cam-student-edition")
        print("注意：2026 年 7 月起必须通过 Siemens Software Center（SSC）客户端下载安装，")
        print("      不能再直接下 zip 包了。")

    nx_results = None
    if args.auto or args.nx_dir:
        nx_dir = args.nx_dir
        if not nx_dir:
            found = find_nx_install()
            if not found:
                print("\n自动查找没找到 NX 安装目录。用 --nx-dir 手动指定，例如：")
                print('  python preflight_check.py --nx-dir "C:\\Program Files\\Siemens\\NX2606"')
                nx_dir = None
            else:
                nx_dir = found[0]
                print("\n自动找到 NX 安装目录: %s" % nx_dir)
        if nx_dir:
            nx_results = check_nx_dir(nx_dir)

    if args.json_out:
        payload = {"preflight": RESULTS}
        if nx_results is not None:
            payload["postinstall"] = nx_results
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("\nJSON 报告已写入: %s" % args.json_out)

    return 1 if fails else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write("自检脚本自身出错: %s\n" % exc)
        sys.exit(2)
