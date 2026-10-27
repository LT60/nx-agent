#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
dialog_sweeper.py -- 盯着 NX 的弹窗，把「卡死」变成「自动恢复」

================================================================
为什么需要它
================================================================
NX 是 GUI 程序，journal 跑在它的**主线程**上。只要弹出一个模态对话框，
主线程就停在那里等 —— 桥接的心跳跟着停，连 `stop` 请求都投不进去。
（2026-10-01 实测：对没刀路的操作调重播 → NX 弹框 → 心跳停 91 秒 → 只能杀进程。）

关键点：**NX 内部的代码救不了自己**（它正在等那个框），
但 **NX 外面可以救它** —— 卡住的只是 NX 的主线程，Windows 上别的进程
照样能枚举它的窗口、按下按钮。本工具就是从外部盯着。

它把「弹窗阻断执行」从「卡死 → 人发现 → 杀进程」变成
「弹窗出现 → 2 秒内被按规则处理 → 桥接继续」。

================================================================
安全策略（和整条红线一致：不替用户做决定）
================================================================
- **已知的弹窗**：按规则选**保守**那个按钮（遥测→Decline，是否保存→No）。
- **未知的弹窗**：**绝不乱点**，只记录 + 在终端告警，等人来看。
- 全程留痕到 `state/dialogs.log`（JSONL），事后能复盘到底弹了什么。

================================================================
用法
================================================================
    python tools\dialog_sweeper.py --once        # 只看一眼当前有什么弹窗（诊断）
    python tools\dialog_sweeper.py --once --json # 机器可读
    python tools\dialog_sweeper.py --watch       # 常驻清扫（桥接开着时另开窗口跑）
    python tools\dialog_sweeper.py --watch --interval 2
    python tools\dialog_sweeper.py --log         # 看历史记录
"""

import argparse
import ctypes
import json
import os
import sys
import time

try:
    from ctypes import wintypes
except Exception:
    wintypes = None

# ---------------------------------------------------------------- 规则
# 每条规则：(标题关键字, 优先按钮列表, 说明)
# 按钮写法用「去掉 & 之后」的可见文字，匹配时两边都归一化。
RULES = [
    ("product excellence", ["Decline", "No"], "遥测同意框 —— 默认不参加（不替用户同意）"),
    ("save", ["No", "Don't Save"], "是否保存 —— 保守选「不保存」，避免覆盖现有文件"),
    ("close", ["OK", "Yes"], "关闭确认"),
]

# 一律不选的按钮（点了等于替用户做重大决定）
# ★ 里面的 "Stop" 是 2026-10-01 加的：NX 的 "Work in Progress" 框上的 Stop
#   会**中断正在跑的 journal** —— 也就是把桥接当场杀掉。绝不能自动点。
NEVER_CLICK = {"Yes", "Agree", "Accept", "Overwrite", "Replace", "Delete",
               "Stop", "确定", "是", "同意", "覆盖", "替换", "删除"}


# ---------------------------------------------------------------- 必须人来
# 有一类弹窗**不能自动点**，不是因为危险，而是**点了也没用**：
# 要输账号密码的框，脚本没有密码，也不该有。
# 但「不自动点」不等于「不管」 —— 之前的表现是干等到超时，人只知道"失败了"，
# 不知道是登录框。这里要做的是：**认出它，并把成因和解法直接说清楚**。
#
# 每条：(关键字元组, issue_id, 一句话诊断, 修复步骤列表)
# 关键字会同时匹配标题、正文和按钮文字。
# 排序有讲究：**越具体的错误串排越前**。
# "server not found" 是很具体的特征串，"password" 这类登录关键字很泛，
# 万一某个框两者都沾边，应该报更具体、更可操作的那个。
NEEDS_HUMAN = [
    (("running instance of the server", "server not found", "initialization error",
      "license server", "初始化错误", "找不到服务器", "授权服务", "许可证服务"),
     "license_server_not_running",
     "NX 找不到本地授权服务 —— **不是授权过期，是发授权的那个服务进程没在跑**"
     "（学生版把授权服务挂在 Siemens Software Center (SSC) 上，SSC 一退出它就没了）",
     [
         "先点掉这个错误框，并关掉这个起不来的 NX",
         "启动 Siemens Software Center：开始菜单搜 Siemens Software Center，"
         "或直接运行 C:\\Program Files\\Siemens\\SoftwareCenter\\SiemensSoftwareCenter.exe",
         "SSC 若要求登录就登录一次（很可能就是 SSC 日志里那条「刷新令牌失败」导致的）",
         "SSC 起来并登录成功后，**从 SSC 里面启动 NX**，别直接双击 NX 图标",
         "确认服务回来了：netstat -ano | findstr 28000 （有 LISTENING 就对了）",
     ]),
    (("sign in", "signin", "log in", "login", "siemens account", "webgate",
      "authenticat", "password", "credential", "passwort",
      "登录", "登錄", "身份验证", "身份驗證", "密码", "密碼", "凭据", "憑證",
      "令牌", "權杖", "重新登录", "重新登錄"),
     "login_token_expired",
     "NX 在要你重新登录 —— **软件授权没过期，过期的是登录令牌**"
     "（等价于 OAuth 的 access token 到期、refresh 续期失败，只好重新输账号密码）",
     [
         "在这个登录框里用你注册学生版时的 Siemens 账号登录一次",
         "如果框卡住不动：先关 NX → 打开 Siemens Software Center (SSC) → 在里面重新登录 → 从 SSC 里启动 NX",
         "登完之后这段时间再启动 NX 就不用再登了（令牌有短时效，过期才会再要）",
         "想确认授权本身有没有过期：看 C:\\ProgramData\\Siemens\\SoftwareCenter\\data\\machineData.json 里有没有 expiration 字段",
     ]),
]


def normalize(text):
    """窗口文字归一化：去掉 & 快捷键标记和首尾空白。"""
    return (text or "").replace("&", "").strip()


def choose_action(title, buttons):
    """给定弹窗标题和按钮列表，返回 (该点的按钮 或 None, 理由)。

    纯函数，可离线单测。原则：
      - 命中已知规则 → 选规则里的第一个可用按钮（顺序即优先级）
      - 该按钮若在 NEVER_CLICK 里 → 不动（宁可不点）
      - 没命中规则 → 返回 None（不动），让人自己看
    """
    t = normalize(title).lower()
    norm_buttons = [normalize(b) for b in buttons]
    for keyword, preferred, why in RULES:
        if keyword not in t:
            continue
        for want in preferred:
            for i, b in enumerate(norm_buttons):
                if b.lower() == want.lower():
                    if b in NEVER_CLICK:
                        return None, "规则想点 %r，但它在禁止名单里，不动（安全优先）" % b
                    return buttons[i], why
        return None, "命中规则 %r，但没找到可点的按钮（现有：%s）" % (keyword, norm_buttons)
    return None, "未知弹窗，不自动处理（等人来看）"


# ---------------------------------------------------------------- 预期现象
# 有一类"弹窗"根本不是错误，而是**正常工作的表现**。
# 最典型的就是 NX 的 "Work in Progress / Click Stop to interrupt this operation"
# —— 它表示**桥接的 journal 正在 NX 里跑着**（常驻模式的必然产物）。
#
# ★★★ 为什么必须单独列一类：那个框上有个 **Stop** 按钮。
#   谁要是"顺手点掉"，就等于**当场把桥接中断**。所以不但不点，还要主动说明。
#
# 2026-10-01 实测：用 --pid 分别扫两个 NX ——
#   桥接 NX（journal 常驻）→ 有这个框；noop journal 已结束的 NX → 没有。
EXPECTED = [
    (("work in progress", "click stop to interrupt"),
     "journal_running",
     "**这是正常现象，不是错误**：桥接的 journal 正在 NX 里运行。"
     "NX 用进度框表示「有个脚本在执行」，常驻模式下它就会一直在。",
     ["★ 千万不要点 Stop —— 那会当场中断桥接，NX 可能跟着退出",
      "同源的还有「菜单栏只剩 File / Ribbon 空白 / 侧边栏空白」："
      "这是 -auto= 启动方式的批处理态，不是坏了",
      "想要一个 UI 完整、能自己操作的 NX：另开一个"
      "（直接双击 NX 图标，不带 -auto=），两个实例可以共存"]),
]


def classify_dialog(title, body=None, buttons=None):
    """把弹窗分成三类，返回结构化结论。

    三类：
      - auto    ：能安全自动点的（沿用 choose_action 的规则）
      - human   ：**必须人来**的（要账号密码 / 要联网授权）—— 不点，但说清成因和解法
      - unknown ：没认出来的 —— 不点，让人看

    存在理由：以前碰到要登录的框，脚本只会干等到超时，人拿到的信息
    只是「失败了」。现在至少要说清「是登录框，因为令牌过期，去这样修」。
    """
    buttons = list(buttons or [])
    body = list(body or [])

    # 标题 + 正文 + 按钮 一起当检索范围：有的框标题是空的、字全在正文里
    haystack = " ".join([title or ""] + body + buttons).lower()

    # ★ 最先判「预期现象」：它们是正常工作的表现，而且按钮往往是破坏性的
    #   （Work in Progress 上的 Stop 会杀掉桥接）。宁可误判成正常，也不能误点。
    for keywords, issue, diagnosis, steps in EXPECTED:
        if all(k in haystack for k in keywords):
            return {"kind": "expected", "issue": issue, "click": None,
                    "diagnosis": diagnosis, "steps": list(steps),
                    "reason": "正常现象，不点任何按钮（Stop 会中断桥接）"}

    btn, why = choose_action(title, buttons)
    if btn is not None:
        return {"kind": "auto", "click": btn, "reason": why,
                "diagnosis": why, "steps": []}
    for keywords, issue, diagnosis, steps in NEEDS_HUMAN:
        for kw in keywords:
            if kw in haystack:
                return {"kind": "human", "issue": issue, "click": None,
                        "diagnosis": diagnosis, "steps": list(steps),
                        "reason": "涉及账号密码/联网授权，脚本不代操作"}

    return {"kind": "unknown", "issue": "unknown_dialog", "click": None,
            "diagnosis": "没认出这个弹窗，所以一个按钮都没点",
            "steps": ["去 NX 窗口看一眼这个框到底写了什么",
                      "处理完后把标题告诉 Agent，好把规则补进 NEEDS_HUMAN / RULES"],
            "reason": why}


def blocking_issue(pid=None):
    """扫一遍当前弹窗，返回第一个「卡住流程」的问题（human/unknown），没有则 None。

    给 start_bridge 用：等待桥接就绪的循环里隔几秒调一次，
    一发现是登录框就立刻报错退出，别让人干等 420 秒。
    """
    try:
        dialogs = scan(pid=pid)
    except Exception as exc:                      # 枚举失败不该让启动流程崩
        return None
    for d in dialogs:
        # ★ 空窗口不等于弹窗。NX 启动早期主窗口还没初始化完成，
        #   这时它没标题、没正文、没按钮 —— 把它当"未知弹窗"会误报卡住、
        #   让 start_bridge 白白退出（2026-10-09 实测踩到）。
        #   一个真正卡流程的框一定有文字，所以三者全空就跳过。
        if not (d.get("title") or "").strip() \
                and not [b for b in (d.get("body") or []) if (b or "").strip()] \
                and not [b for b in (d.get("buttons") or []) if (b or "").strip()]:
            continue

        info = classify_dialog(d.get("title"), d.get("body"), d.get("buttons"))
        if info["kind"] in ("auto", "expected"):
            # auto = 会自己处理；expected = 正常现象（尤其 Work in Progress，
            # 那个框在桥接运行期间会一直挂着，不能当成"卡住了"）
            continue
        info["title"] = d.get("title")
        info["body"] = d.get("body") or []
        return info
    return None


# ---------------------------------------------------------------- Windows 侧

def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    # ⚠️ 必须显式声明 argtypes：HWND 超过 32 位有符号范围时，
    # 默认的 c_int 会抛 OverflowError 并被静默吞掉 → "有时点得到有时点不到"
    u.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.EnumWindows.restype = wintypes.BOOL
    u.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p]
    u.EnumChildWindows.restype = wintypes.BOOL
    u.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p]
    u.SendMessageW.restype = ctypes.c_long
    return u


def scan(pid=None):
    """枚举屏幕上的模态对话框（class #32770）。返回列表。"""
    if sys.platform != "win32":
        return []
    u = _user32()
    found = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_void_p)

    def _enum(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        if cls != "#32770":
            return True
        if pid is not None:
            proc = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
            if proc.value != pid:
                return True
        title_buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, title_buf, 512)
        title = title_buf.value
        buttons = []
        body = []

        # ★ 正文也要抓：错误原因写在 Static / Edit / RichEdit 控件里，
        #   光有标题等于只知道"编译失败"、不知道"第几行错了"。
        TEXT_CLASSES = ("Static", "Edit", "RichEdit20W", "RichEdit50W", "SysLink")

        def _child(child, _):
            cbuf = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(child, cbuf, 256)
            clsname = cbuf.value
            tbuf = ctypes.create_unicode_buffer(1024)
            u.GetWindowTextW(child, tbuf, 1024)
            text = normalize(tbuf.value)
            if clsname == "Button":
                if text:
                    buttons.append((child, text))
            elif clsname in TEXT_CLASSES and text:
                body.append(text)
            return True

        u.EnumChildWindows(hwnd, WNDENUMPROC(_child), 0)
        found.append({"hwnd": hwnd, "class": cls, "title": title,
                      "body": body, "buttons": [b[1] for b in buttons],
                      "_button_hwnds": {b[1]: b[0] for b in buttons}})
        return True

    u.EnumWindows(WNDENUMPROC(_enum), 0)
    return found


def click(button_hwnd):
    if sys.platform != "win32":
        return False
    u = _user32()
    BM_CLICK = 0x00F5
    u.SendMessageW(wintypes.HWND(button_hwnd), BM_CLICK, 0, 0)
    return True


# ---------------------------------------------------------------- 日志

def _resolve_home():
    home = os.environ.get("NX_AGENT_HOME")
    if home and os.path.isdir(home):
        return home
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(here, "state")):
        return here
    return os.getcwd()


def log_path(home=None):
    return os.path.join(home or _resolve_home(), "state", "dialogs.log")


def read_events(limit=10, home=None):
    """读最近的弹窗事件（JSONL，新的在后）。"""
    path = log_path(home)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []
    events = []
    for ln in lines[-limit:]:
        try:
            events.append(json.loads(ln))
        except Exception:
            continue
    return events


def format_for_llm(events):
    """把弹窗事件整理成给大模型看的「报错报告」。

    对标编译器：错误信息 + 上下文 + 可能的成因 + 建议动作。
    纯函数，可离线单测。
    """
    if not events:
        return "没有捕获到任何弹窗。"

    known_hints = [
        ("tool path", "刀路还没生成 —— 先调用 GenerateToolPath 再预览/后处理"),
        ("license", "许可证不足 —— 该功能在当前授权下不可用（学生版尤其常见）"),
        ("not found", "文件或对象找不到 —— 检查路径/名称是否写死"),
        ("already exists", "目标文件已存在 —— 换名字或先删除"),
        ("read-only", "文件只读 —— 换个可写目录（别直接开模板文件）"),
        ("cannot", "操作被拒绝 —— 看正文里的具体原因"),
        ("invalid", "参数不合法 —— 核对该 API 的签名（用 nx_api_lookup）"),
    ]

    out = []
    for i, e in enumerate(events, 1):
        out.append("── 弹窗 %d ──────────────────────────" % i)
        out.append("时间：%s ｜ 处理：%s" % (e.get("time"), e.get("action")))
        out.append("标题：%s" % (e.get("title") or "(无)"))
        body = e.get("body") or []
        if body:
            out.append("正文：")
            for line in body:
                out.append("    " + line)
        else:
            out.append("正文：（没抓到，弹窗可能只有标题）")
        out.append("按钮：%s" % "、".join(e.get("buttons") or []))
        if e.get("clicked"):
            out.append("已自动点：%s" % e["clicked"])
        if e.get("reason"):
            out.append("策略：%s" % e["reason"])
        text = " ".join([e.get("title") or ""] + body).lower()
        for key, hint in known_hints:
            if key in text:
                out.append("可能成因：%s" % hint)
                break
        out.append("")
    out.append("建议动作：先读正文定位原因 → 用 nx_api_lookup 核对 API 签名 → "
               "改脚本/参数 → nx_preview 重跑验证。")
    return "\n".join(out)


def record(entry, home=None):
    path = log_path(home)
    try:
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="盯着 NX 的弹窗，把卡死变成自动恢复")
    ap.add_argument("--once", action="store_true", help="只看一眼（诊断），不点任何按钮")
    ap.add_argument("--watch", action="store_true", help="常驻清扫")
    ap.add_argument("--interval", type=float, default=2.0, help="--watch 的扫描间隔秒")
    ap.add_argument("--seconds", type=float, default=0, help="--watch 最长跑多久，0=不限")
    ap.add_argument("--pid", type=int, help="只看某个进程的窗口")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--log", action="store_true", help="打印历史记录")
    args = ap.parse_args(argv)

    if args.log:
        path = log_path()
        if not os.path.isfile(path):
            print("还没有弹窗记录（%s）" % path)
            return 0
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                print("%s  [%s]  %s  按钮=%s  %s"
                      % (e.get("time"), e.get("action", "?"), e.get("title"),
                         e.get("buttons"), e.get("reason", "")))
        return 0

    if not args.once and not args.watch:
        args.once = True

    started = time.time()
    seen = {}
    while True:
        dialogs = scan(args.pid)
        for d in dialogs:
            info = classify_dialog(d["title"], d.get("body"), d["buttons"])
            want = info.get("click")
            why = info.get("reason")
            key = (d["title"], tuple(d["buttons"]))
            entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "title": d["title"], "body": d.get("body") or [],
                     "buttons": d["buttons"], "kind": info["kind"],
                     "issue": info.get("issue"), "diagnosis": info.get("diagnosis"),
                     "action": "none", "clicked": None, "reason": why}
            if args.watch and want is not None:
                hwnd = d["_button_hwnds"].get(want)
                if hwnd:
                    click(hwnd)
                    entry["action"] = "clicked"
                    entry["clicked"] = want
            elif want is None:
                entry["action"] = "skipped"
            if key not in seen or seen[key] != entry["action"]:
                seen[key] = entry["action"]
                record(entry)
                if args.json:
                    print(json.dumps({k: v for k, v in entry.items()}, ensure_ascii=False))
                else:
                    mark = {"clicked": "[已自动点]",
                            "skipped": {"human": "[必须你来·已给解法]",
                                        "expected": "[正常现象·别点]",
                                        "unknown": "[未处理·需人看]"}.get(info["kind"], "[未处理]"),
                            "none": "{%s}" % info["kind"]}.get(entry["action"], "[?]")
                    print("%s %s" % (mark, d["title"]))
                    for line in (d.get("body") or []):
                        print("     │ %s" % line)
                    print("     按钮：%s" % "、".join(d["buttons"]))
                    print("     理由：%s" % why)
                    # 认出来了（登录框之类）就把成因和解法直接铺出来，
                    # 别让人只看到"未处理"三个字
                    if info["kind"] in ("human", "unknown", "expected") and info.get("steps"):
                        print("     判断：%s" % info.get("diagnosis"))
                        for i, step in enumerate(info["steps"], 1):
                            print("       %d) %s" % (i, step))
        if args.once:
            if not dialogs:
                print("当前没有模态对话框。")
            return 0
        if args.seconds and time.time() - started > args.seconds:
            return 0
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    sys.exit(main())
