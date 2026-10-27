#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py -- NX-Agent 的 MCP 服务端

这是整套系统的「接入口」。任何支持 MCP 的 Agent（WorkBuddy / Codex /
Claude Code / Reasonix 都行）连上它之后，就获得了操作 NX 的手和眼睛，
用户不需要打开 NX 的编辑器，也不需要看脚本。

它本身不碰 NX，只做两件事：
    1. 把 Agent 的意图翻译成请求，投进文件队列（交给 nx_bridge.py 在 NX 里执行）
    2. 把校验类工作（G 代码体检）在本机直接做掉，不需要 NX 在线

传输：stdio，按行分隔的 JSON-RPC 2.0。
重要：stdout 只允许出现协议消息，所有日志走 stderr，否则会污染协议。

启动（给 Agent 配置用）：
    python <repo>/mcp/server.py
环境变量：
    NX_AGENT_HOME   队列根目录，默认取本仓库根目录
"""

import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOME = os.path.dirname(HERE)
HOME = os.environ.get("NX_AGENT_HOME") or DEFAULT_HOME

QUEUE_IN = os.path.join(HOME, "queue", "in")
QUEUE_OUT = os.path.join(HOME, "queue", "out")
STATE_DIR = os.path.join(HOME, "state")
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "nx-agent"
SERVER_VERSION = "0.1.0"

sys.path.insert(0, os.path.join(HOME, "verify"))


def log(msg):
    sys.stderr.write("[nx-agent] %s\n" % msg)
    sys.stderr.flush()


def ensure_dirs():
    for d in (QUEUE_IN, QUEUE_OUT, STATE_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ---------------------------------------------------------------- 桥接调用

HEARTBEAT_STALE_SECONDS = 30


def pid_alive(pid):
    """进程还在不在。

    心跳文件刚写完的一段时间里，即使桥接进程已经被杀掉，文件也还是"新鲜"的。
    只按文件年龄判断会把「死了」误判成「在线」，所以必须直接问操作系统。
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

    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def bridge_status():
    """
    判断桥接是否在线。

    注意一个真实存在的中毒情形：NX 的界面是单线程的，常驻桥接会让 NX 窗口
    显示「未响应」。进程活着不等于它还在干活。所以这里看三件事：
      1. heartbeat.json 存在吗
      2. 心跳里的 PID 还活着吗（这一条能立刻发现「死了」，不用等 30 秒）
      3. 心跳有多旧（这一条用来发现「活着但卡住了」）
    """
    path = os.path.join(STATE_DIR, "heartbeat.json")
    hb = read_json(path)
    if not hb:
        return {
            "online": False,
            "stale": False,
            "process_alive": False,
            "reason": "没有 heartbeat.json，桥接从来没有跑过",
            "how_to_start": "python tools\\start_bridge.py（一键拉起 NX + 桥接，自动处理同意框）",
        }

    age = time.time() - os.path.getmtime(path)
    alive = pid_alive(hb.get("pid"))

    hb["online"] = age <= HEARTBEAT_STALE_SECONDS and alive is not False
    hb["stale"] = age > HEARTBEAT_STALE_SECONDS
    hb["process_alive"] = alive
    hb["heartbeat_age_seconds"] = round(age, 1)

    if alive is False:
        hb["online"] = False
        hb["reason"] = (
            "心跳里记的 PID %s 已经不存在了 —— 桥接进程退出了（NX 可能被关掉或崩了）。"
            % hb.get("pid")
        )
    elif hb["stale"]:
        hb["online"] = False
        hb["reason"] = (
            "进程还在（PID %s），但心跳停了 %.0f 秒（阈值 %d 秒）。"
            "多半是 NX 里弹了模态对话框挡住它了 → 去 NX 那边点掉。"
            % (hb.get("pid"), age, HEARTBEAT_STALE_SECONDS)
        )
    return hb


def call_bridge(cmd, args=None, timeout=60):
    """
    投递一个请求到文件队列并等待响应。
    每一笔请求都在磁盘上留痕，出问题可以直接翻日志，这对生产环境很重要。

    关键设计：桥接不在线时立刻报错，绝不干等。
    Agent 卡死 3 分钟比拿到一条明确的错误信息糟糕得多。
    """
    ensure_dirs()

    status = bridge_status()
    if not status.get("online"):
        raise RuntimeError(
            "NX 桥接不在线：%s\n"
            "启动方式：%s\n"
            "（学生版没有命令行入口 —— run_journal.exe 无法保留 journal 许可，"
            "实测固定报 3615094。可行路径是 ugraf.exe -auto=<journal>，"
            "也就是 start_bridge.py 内部做的事。）"
            % (
                status.get("reason"),
                status.get("how_to_start")
                or "python tools\\start_bridge.py",
            )
        )

    req_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
    in_path = os.path.join(QUEUE_IN, req_id + ".json")
    out_path = os.path.join(QUEUE_OUT, req_id + ".json")

    tmp = in_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"id": req_id, "cmd": cmd, "args": args or {}}, fh, ensure_ascii=False)
    os.rename(tmp, in_path)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(out_path):
            resp = read_json(out_path)
            if resp is None:
                time.sleep(0.1)
                continue
            return resp
        time.sleep(0.2)

    status = bridge_status()
    raise RuntimeError(
        "等待 NX 桥接响应超时（%.0f 秒），请求 id=%s。\n"
        "心跳信息：%s\n"
        "排查顺序：\n"
        "  1) 装 NX 的机器上 nx_bridge.py 是否还在运行？（心跳时间是否停在很久以前）\n"
        "  2) 两边 NX_AGENT_HOME 是否指向同一个目录？（服务端当前 HOME=%s）\n"
        "  3) queue/in 里的请求文件是否一直没被消费？"
        % (timeout, req_id, json.dumps(status, ensure_ascii=False), HOME)
    )


# ---------------------------------------------------------------- 本地校验

def run_nc_validate(nc_file, config=None):
    import nc_lint

    if not os.path.isfile(nc_file):
        raise RuntimeError("找不到 NC 文件: %s" % nc_file)
    cfg = nc_lint.load_config(config)
    with open(nc_file, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    checker = nc_lint.Checker(cfg)
    issues, blocks = checker.run(text)
    errors = [i for i in issues if i.severity == "error"]
    report = {
        "file": os.path.abspath(nc_file),
        "machine": cfg.get("machine"),
        "blocks": len(blocks),
        "passed": not errors,
        "error_count": len(errors),
        "warning_count": len(issues) - len(errors),
        "issues": [i.to_dict() for i in issues],
    }
    lines = ["NC 校验：%s" % ("通过" if report["passed"] else "不通过"),
             "error %d / warning %d / 指令块 %d" % (report["error_count"], report["warning_count"], report["blocks"])]
    for i in report["issues"]:
        lines.append("[%s][%s] 第 %d 行: %s  |  %s"
                     % ("E" if i["severity"] == "error" else "W", i["rule"], i["line"], i["message"], i["block"]))
    return "\n".join(lines), report


def run_nc_diff(golden, new, ignore_regex=None, strip_comments=False, json_out=None):
    """
    NC 回归比对：改了后处理器之后，新老程序差在哪。

    这是「把后处理验证变成纯文本回归测试」的落地。真实场景里，改一次 .tcl
    最怕的是「我以为只加了注释，结果动到了进给」。这个工具把差异自动
    分成高危（运动/坐标/进给/换刀/刀补）和低危（注释之类），
    让 Agent 能在没有机床的情况下判断这次改动能不能上机。
    """
    import re as _re

    import nc_diff

    for p in (golden, new):
        if not os.path.isfile(p):
            raise RuntimeError("找不到文件: %s" % p)

    opts = type("Opts", (), {})()
    opts.strip_comments = bool(strip_comments)
    opts.strip_line_no = True
    opts.ignore_res = [_re.compile(p) for p in (ignore_regex or [])]

    old_rows = nc_diff.load(golden, opts)
    new_rows = nc_diff.load(new, opts)
    stats, changes = nc_diff.build_diff(old_rows, new_rows)
    high = [c for c in changes if c["high_risk"]]

    text = nc_diff.format_report(golden, new, old_rows, new_rows, stats, changes)
    report = {
        "golden": os.path.abspath(golden),
        "new": os.path.abspath(new),
        "identical": not changes,
        "stats": stats,
        "high_risk_count": len(high),
        "risk": "高" if high else ("低" if changes else "无差异"),
        "changes": changes,
    }
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        text += "\n\n完整 JSON 报告已写入: %s" % json_out
    return text, report


# ---------------------------------------------------------------- 工具定义

TOOLS = [
    {
        "name": "nx_status",
        "description": "检查 NX 桥接是否在线、NX 版本、以及本机可用的队列目录。任何操作之前先调这个。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_bridge_stop",
        "description": "让 NX 里的常驻桥接体面退出。常驻期间 NX 界面是冻结的，点不到按钮，所以只能靠这个请求让它停。停掉之后 NX 恢复可操作，桥接可以再次用 python tools\\start_bridge.py 启动。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_dialog_log",
        "description": "★ 读 NX 的「编译错误」：列出最近捕获到的模态弹窗（标题 + 正文全文 + 按钮 + 处理动作），并给出成因提示。桥接卡住/心跳变陈旧时先调这个，看 NX 在抱怨什么，再改脚本重跑。不需要 NX 在线。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "看最近几条，默认 10"},
            },
        },
    },
    {
        "name": "nx_suggest_params",
        "description": "没有厂里实测参数时，按通用切削手册给一套起点（转速/进给/切深切宽，含首件降速档），并列出「该问操作者哪几句话」。拿到建议值后应在对话里直接问操作者确认 —— 这是参数收敛的主路径。不需要 NX 在线。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "material": {"type": "string", "description": "材料代号：aluminum / carbon_steel / stainless / tool_steel_hardened / copper / graphite"},
                "diameter_mm": {"type": "number", "description": "刀具直径 mm"},
                "operation": {"type": "string", "description": "rough（粗加工，默认）或 finish（精加工）"},
                "flutes": {"type": "integer", "description": "刀齿数，默认 4"},
                "first_article": {"type": "boolean", "description": "是否按首件降速档输出，默认 true"},
            },
        },
    },
    {
        "name": "nx_api_lookup",
        "description": "离线查本机 NX 安装里 NXOpen 的真实 API（类名/方法/属性/签名/摘要/所需许可证）。写任何 NXOpen 代码之前先调这个，不要凭记忆猜 API —— 猜错是最大的失败来源（比如 CAMSetup 上没有 GetOperations、Postprocess 是 4 参）。不需要 NX 在线。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，如 Postprocess / GetOperations / CreateImage"},
                "class_name": {"type": "string", "description": "限定或列出某个类的成员，如 NXOpen.CAM.CAMSetup 或 CAMSetup"},
                "limit": {"type": "integer", "description": "返回条数，默认 20"},
                "nx_dir": {"type": "string", "description": "NX 安装目录，不填自动从注册表/环境变量找"},
            },
        },
    },
    {
        "name": "nx_probe_capabilities",
        "description": "在真实 NX 会话上逐个探测 API 是否存在（后处理、截图、CAM 操作等），结果写入 state/capabilities.json。不要靠猜 API，先探测。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_open_part",
        "description": "在 NX 中打开一个 .prt 部件并设为工作部件。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": ".prt 文件绝对路径"}},
            "required": ["path"],
        },
    },
    {
        "name": "nx_new_part",
        "description": "从模板新建一个 .prt 部件并设为工作部件。默认用 NX 自带的毫米空白模型模板；实现是复制模板到目标路径再打开（NX 2606 实测最稳的路线）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template": {"type": "string", "description": "模板 .prt 路径，不填用 NX 自带毫米空白模板；CAM 加工模板在 NX安装目录\\MACH\\resource\\template_part\\metric\\ 下（如 mill_planar.prt）"},
                "out_path": {"type": "string", "description": "新部件保存路径，不填自动放 state/scratch/ 下按时间戳命名"},
            },
        },
    },
    {
        "name": "nx_save_part",
        "description": "保存当前工作部件（不关闭）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_part_info",
        "description": "读取当前工作部件的路径、单位、特征数量、CAM 操作数量。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_list_operations",
        "description": "列出当前部件里所有 CAM 操作及其状态，用于决定要后处理哪些操作。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_run_journal",
        "description": "重放一段人工录制的 journal 脚本，可注入 PARAMS 变量把它参数化。这是能力扩展的主通道：弟弟在界面上操作一遍并录制，Agent 把它变成可复用、可参数化的脚本。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "journal 文件路径"},
                "params": {"type": "object", "description": "注入到脚本 PARAMS 字典里的参数"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "nx_postprocess",
        "description": "对指定 CAM 操作做后处理输出 NC 文件。若该 NX 版本不支持此 API，会明确报错并提示改用 nx_run_journal。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_name": {"type": "string", "description": "后处理名称，例如 fanuc_mill_4A_metric"},
                "out_dir": {"type": "string", "description": "NC 输出目录"},
                "operations": {"type": "array", "items": {"type": "string"}, "description": "只处理这些操作；留空表示全部"},
            },
            "required": ["post_name", "out_dir"],
        },
    },
    {
        "name": "nx_fit_view",
        "description": "把 NX 视图缩放到刚好装下模型（相当于人按「适应窗口」）。Agent 干完活应主动调一次，让人在 NX 窗口里一眼看到结果，而不是自己去找模型在哪。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "nx_preview",
        "description": "★ 干完活给人看 —— 相当于网页开发的「刷新一下」。串起：重播刀路 → 显示 3D 材料切除 → 每步截一张图，返回图片路径。Agent 每次改完都应该调一次，把图报给人复核；人说哪里不对就改了再来。每步独立容错，重播失败也会把当前视图截出来。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_dir": {"type": "string", "description": "图片输出目录，默认 <HOME>/state/preview"},
                "operations": {"type": "array", "items": {"type": "string"}, "description": "只看这些名字的操作，不填 = 全部"},
                "replay": {"type": "boolean", "description": "是否重播刀路，默认 true"},
                "show3d": {"type": "boolean", "description": "是否显示 3D 材料切除，默认 true"},
            },
        },
    },
    {
        "name": "nx_screenshot",
        "description": "把当前 NX 视图导出为 PNG，供 Agent 观察加工结果或界面状态。",
        "inputSchema": {
            "type": "object",
            "properties": {"out_path": {"type": "string", "description": "PNG 输出路径"}},
            "required": ["out_path"],
        },
    },
    {
        "name": "nx_snapshot",
        "description": "给当前 .prt 拍一个二进制快照，放到 snapshots 目录。任何改动性操作之前必须先调用它——这是回滚能力的来源。",
        "inputSchema": {
            "type": "object",
            "properties": {"out_dir": {"type": "string", "description": "快照目录，默认 snapshots/"}},
        },
    },
    {
        "name": "nc_validate",
        "description": "对后处理产出的 G 代码做静态体检（行程超限、安全平面下 G00、刀具未登记、缺 M30、S/F 越界、刀补未取消等）。不需要 NX 在线，是上机前的强制关卡。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "nc_file": {"type": "string", "description": "NC 文件路径"},
                "config": {"type": "string", "description": "机床配置 JSON 路径，默认 config/machine_fanuc.json"},
            },
            "required": ["nc_file"],
        },
    },
    {
        "name": "nc_diff",
        "description": "NC 程序回归比对（golden file 测试）。改了后处理器之后，把新程序跟一份已验证过的旧程序比一比，自动把差异分成「高危」（运动指令、坐标值、转速进给、换刀、刀补）和「低危」（注释之类）。高危差异会返回 isError=true，提示这次改动不能直接上机。不需要 NX 在线。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "golden": {"type": "string", "description": "基准 NC 文件（已验证过的旧程序）"},
                "new": {"type": "string", "description": "新生成的 NC 文件"},
                "ignore_regex": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "忽略命中这些正则的行，可多个。例如时间戳 DATE、编程人姓名",
                },
                "strip_comments": {"type": "boolean", "description": "比对时忽略 (...) 注释内容"},
                "json_out": {"type": "string", "description": "把完整结构化报告另存为 JSON 的路径（可选）"},
            },
            "required": ["golden", "new"],
        },
    },
]


def handle_tools_call(name, arguments):
    arguments = arguments or {}

    if name == "nx_status":
        status = bridge_status()
        status["queue_in"] = QUEUE_IN
        # ★ 卡住的时候自动把「NX 在抱怨什么」带上 —— 对标编译器：
        #   编译失败时编译器自己就把错误给你了，不用你另外去查。
        #   桥接变陈旧 / 不在线时，很可能就是被某个弹框堵住了，所以顺手附上最近弹窗。
        if not status.get("online") or status.get("stale"):
            try:
                import importlib.util

                _ds = os.path.join(HOME, "tools", "dialog_sweeper.py")
                if os.path.isfile(_ds):
                    spec = importlib.util.spec_from_file_location("_ds_status", _ds)
                    sweeper = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(sweeper)
                    events = sweeper.read_events(3, home=HOME)
                    if events:
                        status["recent_dialogs"] = events
                        status["diagnosis"] = sweeper.format_for_llm(events)
                        status["hint_if_stuck"] = (
                            "桥接看着像卡住了 —— 极可能是被弹窗挡住（NX 主线程在等那个框）。"
                            "看 diagnosis，或直接调 nx_dialog_log 拿全文。"
                            "要自动清道夫：另开窗口跑 tools/dialog_sweeper.py --watch。"
                        )
            except Exception:
                pass
        status["notice"] = (
            "桥接在线期间 NX 界面是冻结的（NX 单线程限制），这是预期行为。"
            "要恢复界面操作，调用 nx_bridge_stop。"
            if status.get("online")
            else "桥接不在线。启动方式见 how_to_start。"
        )
        text = json.dumps(status, ensure_ascii=False, indent=2)
        return text, not status.get("online")

    if name == "nx_bridge_stop":
        status = bridge_status()
        if not status.get("online"):
            return json.dumps(
                {"stopped": False, "reason": "桥接本来就不在线", "status": status},
                ensure_ascii=False,
                indent=2,
            ), False
        resp = call_bridge("stop", {}, timeout=30)
        return json.dumps(resp, ensure_ascii=False, indent=2), not resp.get("ok", False)

    if name == "nx_dialog_log":
        # ★ Agent 读 NX 的「编译错误」：弹窗 = NX 的报错输出。
        # 桥接卡住时（心跳变陈旧）先调这个，看 NX 到底在抱怨什么，
        # 再用 nx_api_lookup 核对签名，改脚本重跑 —— 和看编译错误改代码一个流程。
        import importlib.util

        _ds_path = os.path.join(HOME, "tools", "dialog_sweeper.py")
        if not os.path.isfile(_ds_path):
            return json.dumps({"error": "找不到 tools/dialog_sweeper.py"}), True
        _spec = importlib.util.spec_from_file_location("_dialog_sweeper", _ds_path)
        sweeper = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(sweeper)
        events = sweeper.read_events(int(arguments.get("limit", 10)), home=HOME)
        payload = {
            "count": len(events),
            "events": events,
            "report": sweeper.format_for_llm(events),
            "hint": "弹窗就是 NX 的报错输出。先读 report → 用 nx_api_lookup 核对 API 签名 "
                    "→ 改脚本/参数 → nx_preview 重跑。常驻清扫用 tools/dialog_sweeper.py --watch。",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), False

    if name == "nx_suggest_params":
        # 离线工具：没有厂里实测参数时，先按通用工艺手册给一个起点。
        # 关键：它同时返回「该问操作者哪几句话」——Agent 应该在对话里直接问，
        # 把回答作为下一轮的参数。这才是参数收敛的主路径，不是等录制。
        import importlib.util

        _cp_path = os.path.join(HOME, "tools", "cutting_params.py")
        if not os.path.isfile(_cp_path):
            return json.dumps({"error": "找不到 tools/cutting_params.py"}), True
        _spec = importlib.util.spec_from_file_location("_cutting_params", _cp_path)
        cutting_params = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(cutting_params)
        try:
            table = cutting_params.load_table(HOME)
            res = cutting_params.suggest(
                table,
                arguments.get("material"),
                arguments.get("diameter_mm"),
                arguments.get("operation", "rough"),
                arguments.get("flutes"),
                arguments.get("first_article", True),
            )
        except (KeyError, ValueError) as exc:
            return json.dumps({"error": str(exc),
                               "hint": "用 nx_suggest_params 前可先看支持哪些材料"},
                              ensure_ascii=False, indent=2), True
        res["_warning"] = "这是通用工艺起点，不是厂里的正确答案。必须经操作者确认，且上机前过 nc_validate。"
        return json.dumps(res, ensure_ascii=False, indent=2), False

    if name == "nx_api_lookup":
        # 离线工具：查本机装的那份 NXOpen 文档。写任何 NXOpen 代码之前先查，别猜。
        # 用 importlib 按文件路径加载，而不是把 tools/ 加进 sys.path —— 避免
        # tools/ 下的脚本名（selftest / snapshot ...）污染服务端的可导入命名空间。
        import importlib.util

        _api_path = os.path.join(HOME, "tools", "nx_api_lookup.py")
        if not os.path.isfile(_api_path):
            return json.dumps({"error": "找不到 tools/nx_api_lookup.py"}), True
        _spec = importlib.util.spec_from_file_location("_nx_api_lookup", _api_path)
        nx_api_lookup = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(nx_api_lookup)
        items, src = nx_api_lookup.load(arguments.get("nx_dir"), home=HOME)
        if arguments.get("class_name"):
            hits = nx_api_lookup.members_of(items, arguments["class_name"],
                                            int(arguments.get("limit", 60)))
            title = "类 %s 的成员" % arguments["class_name"]
        else:
            hits = nx_api_lookup.search(items, arguments.get("query", ""),
                                        int(arguments.get("limit", 20)),
                                        cls=arguments.get("class_name"))
            title = "匹配 %s" % arguments.get("query")
        payload = {
            "source": src,
            "description": title,
            "count": len(hits),
            "items": [{"kind": it["kind"], "kind_cn": nx_api_lookup.KIND_NAME.get(it["kind"]),
                       "class": it["cls"], "member": it["member"],
                       "signature": it["signature"],
                       "summary": it["summary"], "remarks": it["remarks"],
                       "params": it["params"]}
                      for it in hits],
            "hint": "签名才可信；写 NXOpen 代码前务必核对该类上是否真有这个成员。",
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), False

    if name == "nc_validate":
        cfg = arguments.get("config") or os.path.join(HOME, "config", "machine_fanuc.json")
        text, report = run_nc_validate(arguments["nc_file"], cfg)
        return text, not report["passed"]

    if name == "nc_diff":
        text, report = run_nc_diff(
            arguments["golden"],
            arguments["new"],
            arguments.get("ignore_regex"),
            arguments.get("strip_comments", False),
            arguments.get("json_out"),
        )
        # 高危差异 = 这次改动不能直接上机，按「关卡不通过」处理
        return text, bool(report["high_risk_count"])

    mapping = {
        "nx_probe_capabilities": ("probe", {}),
        "nx_open_part": ("open_part", {"path": arguments.get("path")}),
        "nx_new_part": ("new_part", {"template": arguments.get("template"), "out_path": arguments.get("out_path")}),
        "nx_part_info": ("info", {}),
        "nx_list_operations": ("list_operations", {}),
        "nx_run_journal": ("run_journal", {"path": arguments.get("path"), "params": arguments.get("params") or {}}),
        "nx_postprocess": (
            "postprocess",
            {
                "post_name": arguments.get("post_name"),
                "out_dir": arguments.get("out_dir"),
                "operations": arguments.get("operations"),
            },
        ),
        "nx_screenshot": ("screenshot", {"out_path": arguments.get("out_path")}),
        "nx_fit_view": ("fit_view", {}),
        "nx_preview": ("preview", {
            "out_dir": arguments.get("out_dir"),
            "operations": arguments.get("operations"),
            "replay": arguments.get("replay", True),
            "show3d": arguments.get("show3d", True),
        }),
        "nx_snapshot": ("snapshot", {"out_dir": arguments.get("out_dir")}),
        "nx_save_part": ("save_part", {}),
    }
    if name not in mapping:
        raise RuntimeError("未知工具: %s" % name)

    cmd, args = mapping[name]
    resp = call_bridge(cmd, args, timeout=int(arguments.get("_timeout", 60)))
    text = json.dumps(resp, ensure_ascii=False, indent=2)
    return text, not resp.get("ok", False)


# ---------------------------------------------------------------- JSON-RPC

def make_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def dispatch(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return make_result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "tools/list":
        return make_result(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        try:
            text, is_error = handle_tools_call(name, params.get("arguments"))
            return make_result(
                req_id,
                {"content": [{"type": "text", "text": text}], "isError": bool(is_error)},
            )
        except Exception as exc:
            return make_result(
                req_id,
                {
                    "content": [{"type": "text", "text": "调用失败: %s" % exc}],
                    "isError": True,
                },
            )

    if method == "ping":
        return make_result(req_id, {})

    if method == "resources/list":
        return make_result(req_id, {"resources": []})

    if method == "prompts/list":
        return make_result(req_id, {"prompts": []})

    if method and method.startswith("notifications/"):
        return None

    if req_id is None:
        return None
    return make_error(req_id, -32601, "不支持的方法: %s" % method)


def main():
    ensure_dirs()
    log("启动，HOME=%s" % HOME)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as exc:
            log("收到非法 JSON: %s" % exc)
            continue
        try:
            out = dispatch(msg)
        except Exception as exc:
            out = make_error(msg.get("id"), -32603, "内部错误: %s" % exc)
        if out is None:
            continue
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
