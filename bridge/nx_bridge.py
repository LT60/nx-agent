#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nx_bridge.py -- 跑在 Siemens NX 内部的桥接 journal

这是整套 NX-Agent 的「驱动层」。它是一段 journal，用 NX 自己的 Python 解释器
执行，所以能直接调用 NXOpen，操控当前打开的 NX 会话。

它对外只暴露一个极简协议：文件队列。
    <HOME>/queue/in/<id>.json    请求
    <HOME>/queue/out/<id>.json   响应
用文件而不是 socket 的理由：跨机器共享盘也能用，企业网不需要开端口，
NX 版本差异不影响传输层，而且每一步都有留痕可查。


================================================================
为什么必须跑在 NX 图形界面里（2026-09-30 实测结论，别再试错）
================================================================
NX Designcenter Student Edition 2606 走的是 SALT 云端授权。实测：

  ✅ ugraf.exe -student             图形界面启动，许可正常（网络验证通过）
  ✅ ugraf.exe -student -automation 无界面模式也能拿到许可（进程可长期存活）
  ❌ run_journal.exe <任何变体>      固定报错：
        3615094: Unable to reserve license to run journal file
     试过 -nx / -r= / 显式 UGII_USE_NXSAAS_MODE=1 /
     SPLM_LICENSE_SERVER=29000@LocalHost / 28000@localhost，全部同一个错。
     → 命令行模式无法保留 journal 许可，这是学生版的硬限制，不是配置问题。

结论：**驱动层必须寄生在一个已经拿到许可的 NX 图形会话里**，靠 Journal Manager
播放本脚本。这也是当初把桥接设计成「NX 内常驻」的原因。


================================================================
怎么启动（实测可行，2026-09-30 晚）
================================================================
★ 首选：一条命令，NX 启动时自动运行本脚本，不需要人点任何按钮
    <NX安装目录>\\NXBIN\\ugraf.exe -student -auto=<本文件的绝对路径>

  `-auto=<journal>` 是 NX 主程序自带的「启动时自动播放 journal」开关。
  实测在学生版上有效（NX 约 20~90 秒启动，之后 journal 就跑起来了），
  而且因为 ugraf.exe 是拿到 SALT 许可的那个进程，**没有任何许可问题**。
  配合 tools/start_bridge.py 可以直接双击启动。

  注意：journal 返回之后 NX 会自动退出。所以：
    · 常驻模式（不带参数）→ NX 一直活着，桥接一直守着队列
    · --once 模式 → 跑完就退，NX 也跟着退（这不是故障）

★ 备选：在 NX 界面里手动启动（图形界面已经开着的时候）
    Developer 选项卡 -> Journal Manager -> Browse 选中本文件 -> Play

★ 不可用：命令行 run_journal.exe
    学生版固定报 3615094: Unable to reserve license to run journal file（见上）。
    商业版/学术版可以用：run_journal.exe nx_bridge.py --once + Windows 任务计划。

★ 也不可用：外部程序直接调 NX 的 C 接口（libufun.dll 的 UF_initialize）
    实测验返回码 3600045：
      "No C++ author or Designcenter Open development license is available
       to be able to run this native application."
    → 外部/无界面调用需要「开发者许可证」，学生版没有这张证。
    这也解释了为什么学生版只允许「界面内跑脚本」这一条路。


================================================================
参数
================================================================
  （不带参数）   常驻轮询 —— -auto= 和手工 Play 都是这个模式
  --once         处理完当前排队请求就退出。给自检/批处理用。
  --watch        显式声明常驻（等价于不带参数）。
  --seconds=N    常驻模式的最长生存期，到点自行退出。0 = 不限（默认）。
  --interval=S   轮询间隔秒，默认 0.5。
  --max=N        处理满 N 个请求后退出，调试用。
  --stale-seconds=N
                 超过这个时长的排队请求不执行，挪进 queue/stale/。默认 1800。
                 设 0 关闭该机制（不建议）。


================================================================
必须知道的代价：NX 界面会被冻结
================================================================
NX 的界面是单线程的，journal 在 NX 主线程上跑。本脚本的常驻轮询意味着
NX 主线程一直在 while 循环里，NX 窗口会显示「未响应」，直到轮询结束。

已经查过 NXOpen 的完整 API 文档（NXBIN/managed/NXOpen.xml，60 MB）：
**不存在** AddTimer / AddIdle / 通用回调这类「让出主线程又保持唤醒」的机制
（能找到的 Callback 全是 CAE / 特征 / PDM 等领域的专用回调，不是通用定时器）。

所以这是 NX 的限制，不是本脚本的缺陷。工程上的应对：
  · 把「机器人的 NX」和「人自己的 NX」分成两个实例（若许可允许多实例）
  · 或者只在需要 Agent 干活的那段时间启动常驻桥接
  · 换 NX 版本的机器时，用 ugraf.exe -automation 探路（见上面的实测结论）

停止办法（界面已冻结，点不动按钮）：
  · 往 queue/in 投一个 {"cmd": "stop"} 请求 —— 推荐，MCP 有对应工具
  · 或按 state/bridge.pid 记录的 PID 结束 ugraf.exe
"""

import json
import os
import sys
import time
import traceback

# ---------------------------------------------------------------- NX 环境

try:
    import NXOpen
    import NXOpen.CAM
    import NXOpen.UF

    NX_AVAILABLE = True
except Exception:  # 在 NX 外面做语法检查时会走到这里
    NXOpen = None
    NX_AVAILABLE = False


def _resolve_home():
    """定位队列根目录。

    优先级：环境变量 -> 脚本自身位置 -> 从工作目录往上找 queue/ 目录。
    手工在 NX 的 Journal Manager 里播放时，__file__ 不一定被设置，
    所以必须有后手，不能只靠一个来源。
    """
    env = os.environ.get("NX_AGENT_HOME")
    if env and os.path.isdir(env):
        return os.path.abspath(env)

    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isdir(os.path.join(here, "bridge")):
            return here
    except Exception:
        pass

    probe = os.getcwd()
    for _ in range(6):
        if os.path.isdir(os.path.join(probe, "queue")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent

    return None


HOME = _resolve_home()
QUEUE_IN = os.path.join(HOME, "queue", "in") if HOME else None
QUEUE_OUT = os.path.join(HOME, "queue", "out") if HOME else None
STATE_DIR = os.path.join(HOME, "state") if HOME else None

# 超过这个时长的排队请求一律不执行（挪进 queue/stale/ 留痕）。
# 默认 30 分钟：MCP 服务端只等 60 秒，30 分钟前的请求早就没人等了。
STALE_SECONDS_DEFAULT = 1800


class BridgeError(Exception):
    pass


def ensure_dirs():
    if HOME is None:
        raise BridgeError(
            "定位不到 NX_AGENT_HOME。请设置环境变量 NX_AGENT_HOME 指向 nx-agent 仓库根目录，"
            "或在 Journal Manager 里播放前先切到该目录。"
        )
    for d in (QUEUE_IN, QUEUE_OUT, STATE_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)


def write_json(path, obj):
    """原子写：先写临时文件再改名，避免 Agent 读到半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def get_session():
    if not NX_AVAILABLE:
        raise BridgeError("当前不在 NX 进程内，无法调用 NXOpen")
    return NXOpen.Session.GetSession()


def get_work_part():
    session = get_session()
    part = session.Parts.Work
    if part is None:
        raise BridgeError("NX 当前没有打开任何工作部件（work part）")
    return part


def get_cam(part):
    """拿部件的 CAM 设置；没有 CAM 设置时返回 None。

    ⚠️ 2606 实测：`part.CAMSetup` 在建模部件上**不是返回 None，而是抛
    NXException 1345023**。所以 `getattr(part, "CAMSetup", None)` 这个惯用法
    在 NX 的 .NET 代理对象上是错的 —— 属性访问本身就会炸。一律用这个函数。
    """
    try:
        return part.CAMSetup
    except Exception:
        return None


def iter_operations(cam):
    """枚举 CAM 设置里全部的加工操作（Operation）。

    ⚠️ NX 2606 上 `CAMSetup` 没有 `GetOperations()`（在 60 MB 的
    NXOpen.xml 文档里查过，不存在）。这一版的正路是：

        GetRoot(View)  ->  拿四棵树（程序顺序/机床/几何/加工方法）各自的根 NCGroup
        NCGroup.GetMembers()  ->  单层成员（组里面可能还套组，要递归）
        IsOperation(CAMObject)  ->  判断某个成员是不是「加工操作」

    同一个操作会同时出现在好几棵树里（它既属于程序顺序树也属于机床树），
    所以要用每个对象的 Tag（NX 内部唯一编号）去重。别的 NX 版本可能有
    GetOperations，先用它，失败再走树的递归。
    """
    getter = getattr(cam, "GetOperations", None)
    if callable(getter):
        try:
            return list(getter())
        except Exception:
            pass

    is_op = getattr(cam, "IsOperation", None)
    if not callable(is_op):
        raise BridgeError(
            "这台 NX 的 CAMSetup 上既没有 GetOperations 也没有 IsOperation，"
            "无法枚举加工操作（该版本 API 差异超出预期，请补探测）"
        )

    ops = []
    seen = set()

    def visit(obj):
        if obj is None:
            return
        tag = None
        try:
            tag = int(obj.Tag)
        except Exception:
            try:
                tag = str(obj.Name)
            except Exception:
                pass
        if tag is not None:
            if tag in seen:
                return
            seen.add(tag)
        try:
            if is_op(obj):
                ops.append(obj)
        except Exception:
            pass
        members = None
        try:
            members = obj.GetMembers()
        except Exception:
            pass  # 叶子（Operation 本身）没有 GetMembers，正常
        if members:
            for child in members:
                visit(child)

    view_enum = NXOpen.CAM.CAMSetup.View
    for view_name in ("ProgramOrder", "MachineTool", "Geometry", "MachineMethod"):
        try:
            root = cam.GetRoot(getattr(view_enum, view_name))
        except Exception:
            continue
        visit(root)
    return ops



# ---------------------------------------------------------------- 指令注册

COMMANDS = {}


def command(name):
    def deco(fn):
        COMMANDS[name] = fn
        return fn

    return deco


@command("ping")
def cmd_ping(args):
    """只回答「桥接进程活着吗」，不需要 NXOpen，所以 NX 外面也能答。"""
    info = {
        "nx_available": NX_AVAILABLE,
        "home": HOME,
        "pid": os.getpid(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if NX_AVAILABLE:
        session = get_session()
        try:
            info["nx_version"] = session.GetEnvironmentVariableValue("UGII_VERSION") or "unknown"
        except Exception:
            info["nx_version"] = "unknown"
    return info


@command("probe")
def cmd_probe(args):
    """
    能力侦测：在真实机器上逐个 getattr，把「这台 NX 到底支持什么」写成清单。
    AI 不该猜 API，应该先读这张清单再决定怎么写脚本。

    2026-10-01 起新增 CAM 树的探测：NX 2606 上没有 GetOperations，
    正路是 GetRoot(View) + 递归 GetMembers + IsOperation 过滤（见 iter_operations）。
    """
    if not NX_AVAILABLE:
        raise BridgeError("需要先运行在 NX 内")
    session = get_session()
    report = {"nx_version": None, "session": {}, "part": {}, "cam": {}, "ui": {}}

    try:
        report["nx_version"] = session.GetEnvironmentVariableValue("UGII_VERSION")
    except Exception:
        pass

    def has(obj, name):
        try:
            return hasattr(obj, name)
        except Exception:
            return False

    for name in ("Parts", "ListingWindow", "Undo", "NewDisplay", "GetEnvironmentVariableValue"):
        report["session"][name] = has(NXOpen.Session, name)
    report["session"]["Parts.NewDisplay"] = has(session.Parts, "NewDisplay")
    report["session"]["Parts.OpenActiveDisplay"] = has(session.Parts, "OpenActiveDisplay")

    part = session.Parts.Work
    if part is not None:
        for name in ("CAMSetup", "Features", "MeasureManager", "ExportImage", "Modeling", "Drafting"):
            report["part"][name] = has(part, name)
        try:
            report["part"]["Views.CreateImageExportBuilder"] = has(part.Views, "CreateImageExportBuilder")
        except Exception:
            report["part"]["Views.CreateImageExportBuilder"] = False
        # 注意：has() 只能探「名字在不在」；CAMSetup 这种「访问时抛异常」的属性
        # 要用 get_cam() 才能真正探明
        report["part"]["CAMSetup(访问级)"] = get_cam(part) is not None
        cam = get_cam(part)
        if cam is not None:
            for name in ("Postprocess", "PostProcess", "GetOperations", "GenerateToolPath",
                         "GetRoot", "IsOperation", "OutputClsf", "ParallelGenerate"):
                report["cam"][name] = has(cam, name)
            try:
                root = cam.GetRoot(NXOpen.CAM.CAMSetup.View.ProgramOrder)
                report["cam"]["root.NCGroup.GetMembers"] = has(root, "GetMembers")
            except Exception as exc:
                report["cam"]["root.NCGroup.GetMembers"] = "未探测: %s" % exc
            try:
                report["cam"]["operation_count"] = len(iter_operations(cam))
            except Exception as exc:
                report["cam"]["operation_count"] = "未探测: %s" % exc
    for name in ("DisplayMessage", "SetStatus"):
        try:
            report["ui"][name] = has(NXOpen.UF.UFSession.GetUFSession().Ui, name)
        except Exception:
            report["ui"][name] = False
    # 历史教训：2606 上 UF.Disp.CreateImage 已不存在，截图走
    # part.Views.CreateImageExportBuilder（上面 part 段探测）。这里不再探旧 API。

    write_json(os.path.join(STATE_DIR, "capabilities.json"), report)
    return report


@command("info")
def cmd_info(args):
    part = get_work_part()
    out = {}
    try:
        out["part_path"] = part.FullPath
    except Exception:
        out["part_path"] = None
    try:
        out["units"] = str(part.PartUnits)
    except Exception:
        pass
    try:
        out["feature_count"] = len(list(part.Features))
    except Exception:
        pass
    try:
        cam = get_cam(part)
        if cam is not None:
            out["operation_count"] = len(iter_operations(cam))
    except Exception:
        pass
    return out


def _short_path(path):
    """取 Windows 短路径（8.3）。拿不到就原样返回。

    ★ 存在理由（2026-10-01 实测踩坑）：`Parts.OpenActiveDisplay` 传**含中文的路径**
    时会**静默失败**（返回 ok=False，工作部件不变，也不抛异常）。
    换成英文名立刻成功。转成 8.3 短路径（纯 ASCII）是最省事的绕法。
    """
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k.GetShortPathNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        if k.GetShortPathNameW(path, buf, 1024):
            return buf.value
    except Exception:
        pass
    return path


@command("open_part")
def cmd_open_part(args):
    path = args.get("path")
    if not path or not os.path.isfile(path):
        raise BridgeError("找不到部件文件: %s" % path)
    session = get_session()
    # ★ 中文路径会让 OpenActiveDisplay 静默失败 —— 统一走短路径（纯 ASCII）
    safe = _short_path(path)
    session.Parts.OpenActiveDisplay(safe, NXOpen.DisplayPartOption.AllowAdditional)
    work = str(get_work_part().FullPath)
    # 短路径打开后，工作部件的 FullPath 可能也是短路径形式，正常
    return {"opened": path, "opened_as": safe, "work_part": work}


@command("new_part")
def cmd_new_part(args):
    """从模板新建一个部件（毫米单位空白模型为默认）。

    路线选择（2606 实测撞出来的）：
    - `Parts.NewDisplay(template, units)` 会按模板名在工作目录落一个新文件，
      第二次调用就撞名报 "File already exists"，而且文件名不受控。
    - `Parts.FileNew()` 构造器需要 `ApplicationName` / `TemplatePresentationName`
      这类**随界面语言变化的本地化字符串**，跨版本/跨语言不可靠。
    - 所以用最笨但确定的办法：**把模板 .prt 复制到目标路径，再打开它**。
      .prt 就是 .prt，复制不会坏；而且部件直接落在文件上，后面做快照/保存都方便。
    """
    session = get_session()
    template = args.get("template")
    if not template:
        base = session.GetEnvironmentVariableValue("UGII_BASE_DIR")
        template = os.path.join(base, "UGII", "templates", "model-plain-1-mm-template.prt")
    if not os.path.isfile(template):
        raise BridgeError("找不到部件模板: %s" % template)

    out_path = args.get("out_path")
    if not out_path:
        scratch = os.path.join(STATE_DIR, "scratch")
        if not os.path.isdir(scratch):
            os.makedirs(scratch)
        out_path = os.path.join(scratch, "part_%s.prt" % time.strftime("%Y%m%d-%H%M%S"))
    if os.path.exists(out_path):
        os.remove(out_path)
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    with open(template, "rb") as fi, open(out_path, "wb") as fo:
        while True:
            chunk = fi.read(1024 * 1024)
            if not chunk:
                break
            fo.write(chunk)

    session.Parts.OpenActiveDisplay(out_path, NXOpen.DisplayPartOption.AllowAdditional)
    part = get_work_part()
    return {"template": template, "work_part": str(part.FullPath),
            "units": str(part.PartUnits)}


@command("save_part")
def cmd_save_part(args):
    part = get_work_part()
    part.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.FalseValue)
    return {"saved": str(part.FullPath)}


@command("list_operations")
def cmd_list_operations(args):
    part = get_work_part()
    cam = get_cam(part)
    if cam is None:
        raise BridgeError("当前部件没有 CAM 设置（建模部件上访问 CAMSetup 会抛 NXException 1345023，不是加工文件）")
    ops = []
    for op in iter_operations(cam):
        item = {}
        for attr, key in (("Name", "name"), ("GetStatus", "status"), ("AskPathExists", "has_path")):
            try:
                val = getattr(op, attr)
                item[key] = str(val() if callable(val) else val)
            except Exception:
                pass
        ops.append(item)
    return {"count": len(ops), "operations": ops}


@command("run_journal")
def cmd_run_journal(args):
    """
    万能逃生舱：重放一段人工录制的 journal。

    这是整套系统最重要的一个原语。任何用户在界面上做得到的事情，
    录一遍就能被重放；Agent 再把录制出来的硬编码参数改成变量，
    就获得了新的自主能力。自主性靠「录制」累积，不靠猜 API。
    """
    path = args.get("path")
    params = args.get("params") or {}
    if not path or not os.path.isfile(path):
        raise BridgeError("找不到 journal 文件: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    scope = {
        "__name__": "__nx_journal__",
        "__file__": path,
        "PARAMS": params,
        "NXOpen": NXOpen,
    }
    try:
        code = compile(source, path, "exec")
    except SyntaxError as exc:
        raise BridgeError("journal 语法错误 (第 %s 行): %s" % (exc.lineno, exc.msg))
    exec(code, scope)
    # ★ journal 想把数据带回来，就在全局作用域里放一个 RESULT（任意可 JSON 序列化的对象），
    #   想补充一句人话就放 RESULT_NOTE。这是 journal → Agent 的回话通道：
    #   没有它，journal 只能说"跑完了"，说不了"我做成了什么、失败了什么、下次该怎么改"。
    out = {"journal": path, "params": params, "executed": True}
    for key in ("RESULT", "RESULT_NOTE"):
        val = scope.get(key)
        if val is not None:
            try:
                json.dumps(val)
                out[key.lower()] = val
            except (TypeError, ValueError):
                out[key.lower()] = str(val)
    return out


@command("postprocess")
def cmd_postprocess(args):
    """
    后处理出 NC。不同 NX 版本的 API 名字/签名不一致，所以这里做能力探测，
    探测不到就明确告诉调用方改用 run_journal 重放录制文件。

    实测：NX 2606 的签名是 4 参
        Postprocess(CAMObject[], String, String, CAMSetup.OutputUnits)
    老版本是 3 参。两种都试，按 TypeError 分流。
    """
    part = get_work_part()
    cam = get_cam(part)
    if cam is None:
        raise BridgeError("当前部件没有 CAM 设置")
    post_name = args.get("post_name")
    out_dir = args.get("out_dir")
    if not post_name or not out_dir:
        raise BridgeError("需要 post_name 与 out_dir 两个参数")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    if hasattr(cam, "Postprocess"):
        all_ops = iter_operations(cam)
        if args.get("operations"):
            wanted = set(args["operations"])
            operations = [op for op in all_ops if str(op.Name) in wanted]
        else:
            operations = all_ops  # 不点名 = 全部（之前留空表是 bug，会什么都不处理）
        if not operations:
            raise BridgeError("没有可后处理的操作（operations 为空）")

        units_name = (args.get("output_units") or "metric").lower()
        try:
            units_enum = {
                "metric": NXOpen.CAM.CAMSetup.OutputUnits.Metric,
                "inch": NXOpen.CAM.CAMSetup.OutputUnits.Inch,
            }[units_name]
            result = cam.Postprocess(operations, post_name, out_dir, units_enum)
            return {"method": "CAMSetup.Postprocess(4参/2606)", "result": str(result),
                    "operation_count": len(operations)}
        except TypeError:
            # 老版本 3 参签名
            result = cam.Postprocess(operations, post_name, out_dir)
            return {"method": "CAMSetup.Postprocess(3参/老版本)", "result": str(result),
                    "operation_count": len(operations)}

    raise BridgeError(
        "这台 NX 的 CAMSetup 上没有 Postprocess 方法。请改用 run_journal："
        "在 NX 里手动做一次后处理并录制 journal，然后重放它。"
    )


def refresh_display():
    """让 NX 窗口立刻把最新模型画出来。

    ★ 这条很重要：**给人看的地方是 NX 窗口本身，不是 HTML 画廊。**
    画廊只是历史留档。桥接每执行完一条命令就刷一次，
    人在 NX 里就能实时看到 Agent 改了什么。

    实测（2606）：`UF.Disp.Refresh()` 无参调用可用；
    `RegenerateDisplay()` 会报 1105042，别用它。
    注：UF 这类函数不在 NXOpen.xml 文档里（那份只含 NXOpen 命名空间），
    只能在 NX 里反射出来 —— 所以别去文档里找 UF。
    """
    if not NX_AVAILABLE:
        return False
    try:
        NXOpen.UF.UFSession.GetUFSession().Disp.Refresh()
        return True
    except Exception:
        return False


def fit_view():
    """视图适配到模型大小（相当于按一下「适应窗口」）。

    实测：`UF.View.AskWorkView()` 必须有工作部件才不报错（否则 1105012），
    拿到 view tag 后 `FitView(tag, 1.0)` 生效。
    """
    if not NX_AVAILABLE:
        return "不在 NX 进程内"
    try:
        uf = NXOpen.UF.UFSession.GetUFSession()
        tag = uf.View.AskWorkView()
        uf.View.FitView(tag, 1.0)
        refresh_display()
        return True
    except Exception as exc:
        return str(exc)


def save_screenshot(part, out):
    """把当前 NX 视图导成 PNG 文件。返回字节数。

    2606 实测：`UF.Disp.CreateImage` 已不存在。
    现行 API 是 `part.Views.CreateImageExportBuilder()`（GUI 里「文件 → 导出 → 图像」）。
    """
    out_dir = os.path.dirname(out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    try:
        builder = part.Views.CreateImageExportBuilder()
        builder.RegionMode = False  # False = 整个窗口；True 才是手工框选区域
        builder.FileFormat = NXOpen.Gateway.ImageExportBuilder.FileFormats.Png
        builder.FileName = out
        builder.BackgroundOption = NXOpen.Gateway.ImageExportBuilder.BackgroundOptions.Original
        builder.EnhanceEdges = False
        builder.Commit()
        builder.Destroy()
    except Exception as exc:
        raise BridgeError("截图失败(%s)。改用 run_journal 重放手工录制的「导出图像」操作。" % exc)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise BridgeError("截图调用没报错，但文件没生成或为空: %s" % out)
    return os.path.getsize(out)


@command("screenshot")
def cmd_screenshot(args):
    """把当前 NX 视图导成 PNG。让 Agent 有眼睛，是自治闭环里不可省的一环。"""
    out = args.get("out_path")
    if not out:
        raise BridgeError("需要 out_path 参数")
    size = save_screenshot(get_work_part(), out)
    return {"method": "Views.CreateImageExportBuilder", "file": out, "bytes": size}


@command("fit_view")
def cmd_fit_view(args):
    """把视图缩放到刚好装下模型 —— 相当于人按一下「适应窗口」。

    Agent 干完活应该主动调一次，让人在 NX 窗口里一眼看到结果，
    而不是让人自己去找模型在哪。
    """
    ok = fit_view()
    if ok is not True:
        raise BridgeError("视图适配失败：%s（可能没有打开部件）" % ok)
    return {"fitted": True}


@command("preview")
def cmd_preview(args):
    """干完活给人看 —— **相当于网页开发的「刷新一下」**。

    网页循环是「改代码 → 刷新 → 人看 → 提要求 → 再改」。
    NX 这边「改」和「跑」早就有了，缺的正是「把结果呈给人看」这一步。
    本命令把三件事串起来：重播刀路 → 显示 3D 材料切除 → 每步截一张图。

    Agent 干完活调一次，把图片路径报给人；人说哪里不对，Agent 改了再来一遍。
    每步独立 try：重播失败不该挡住后面的截图（和网页部分渲染失败一个道理）。
    """
    part = get_work_part()
    cam = get_cam(part)
    out_dir = args.get("out_dir") or os.path.join(STATE_DIR, "preview")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    images = []
    steps = []

    # 先把视图适配到模型，否则截图里模型只有一小点，人看不清
    fit_note = fit_view()

    def shot(tag):
        path = os.path.join(out_dir, "%s_%s.png" % (stamp, tag))
        try:
            save_screenshot(part, path)
            images.append(path)
            return path
        except Exception as exc:
            return None, str(exc)

    # 0) 先记下当前画面，作为基线
    base = shot("00-current")
    if isinstance(base, tuple):
        raise BridgeError("连当前视图都截不出来，后面没法给人看：%s" % base[1])

    if cam is None:
        return {"images": images, "steps": steps, "operation_count": 0,
                "fit_view": fit_note,
                "note": "当前部件没有 CAM 设置，只截了当前视图（建模部件上访问 CAMSetup 会抛异常）"}

    ops = iter_operations(cam)
    if args.get("operations"):
        wanted = set(args["operations"])
        ops = [op for op in ops if str(op.Name) in wanted]
    if not ops:
        return {"images": images, "steps": steps, "operation_count": 0,
                "note": "没有可预览的操作（模板自带的操作通常还没生成刀路）"}

    # ★★ 只挑「已经生成刀路」的操作去预览。2026-10-01 实测：
    # 对没刀路的操作调 ReplayToolPath，NX 会弹一个模态对话框，
    # journal 跑在主线程上 → 整个桥接被卡死（心跳停 91 秒，连 stop 都投不进去）。
    # 这是本命令最危险的一个坑，别删这段过滤。
    def _has_path(op):
        fn = getattr(op, "AskPathExists", None)
        if not callable(fn):
            return True  # 探不到就放行，但 2606 上这个方法存在
        try:
            return bool(fn())
        except Exception:
            return False

    with_path = [op for op in ops if _has_path(op)]
    if not with_path:
        return {"images": images, "steps": steps,
                "operation_count": len(ops), "with_path": 0,
                "note": "这 %d 个操作都还没生成刀路（先 GenerateToolPath）。"
                        "只截了当前视图 —— 不对没刀路的操作调重播，否则 NX 弹模态框会卡死桥接。"
                        % len(ops)}

    ops = with_path

    plan = []
    if args.get("replay", True):
        plan.append(("replay", "01-replay", lambda: cam.ReplayToolPath(ops)))
    if args.get("show3d", True):
        plan.append(("show3d", "02-show3d", lambda: cam.Show3dWorkpiece(ops)))

    for name, tag, action in plan:
        try:
            action()
        except Exception as exc:
            steps.append({"step": name, "ok": False, "error": str(exc)})
            continue
        got = shot(tag)
        if isinstance(got, tuple):
            steps.append({"step": name, "ok": True, "image": None,
                          "note": "步骤执行了但截图失败：%s" % got[1]})
        else:
            steps.append({"step": name, "ok": True, "image": got})

    return {"images": images, "steps": steps, "operation_count": len(ops),
            "fit_view": fit_note, "out_dir": out_dir,
            "hint": "给人看的图在 out_dir 下；用 tools/preview_gallery.py 生成一页 HTML 一起看"}


@command("snapshot")
def cmd_snapshot(args):
    """在改动之前给 .prt 拍快照，这是回滚能力的地基。"""
    part = get_work_part()
    src = str(part.FullPath)
    snap_dir = args.get("out_dir") or os.path.join(HOME, "snapshots")
    if not os.path.isdir(snap_dir):
        os.makedirs(snap_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(src)
    dst = os.path.join(snap_dir, "%s.%s.bak" % (base, stamp))
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            chunk = fi.read(1024 * 1024)
            if not chunk:
                break
            fo.write(chunk)
    write_json(os.path.join(STATE_DIR, "last_snapshot.json"), {"from": src, "to": dst})
    return {"source": src, "snapshot": dst}


@command("stop")
def cmd_stop(args):
    return {"stop": True}


# ---------------------------------------------------------------- 主循环

def handle_one(req_path):
    """处理一个请求，返回 True 表示应该退出循环。"""
    name = os.path.basename(req_path)
    req_id = name[:-5] if name.endswith(".json") else name
    out_path = os.path.join(QUEUE_OUT, req_id + ".json")
    try:
        with open(req_path, "r", encoding="utf-8") as fh:
            req = json.load(fh)
    except Exception as exc:
        write_json(out_path, {"id": req_id, "ok": False, "error": "请求 JSON 解析失败: %s" % exc})
        return False

    cmd = req.get("cmd")
    args = req.get("args") or {}
    handler = COMMANDS.get(cmd)
    started = time.time()
    if handler is None:
        write_json(
            out_path,
            {
                "id": req_id,
                "ok": False,
                "error": "未知指令: %s" % cmd,
                "available": sorted(COMMANDS.keys()),
            },
        )
        return cmd == "stop"
    try:
        result = handler(args)
        # ★ 每执行完一条就把画面刷到 NX 窗口 —— 人在 NX 里实时看到 Agent 干了什么。
        #   （常驻期间界面点不动，但**看得见**；这已经覆盖了「人复核」这个主要目的。）
        refresh_display()
        write_json(
            out_path,
            {"id": req_id, "ok": True, "result": result, "seconds": round(time.time() - started, 3)},
        )
        return cmd == "stop"
    except BridgeError as exc:
        write_json(out_path, {"id": req_id, "ok": False, "error": str(exc), "kind": "BridgeError"})
        return False
    except Exception:
        write_json(
            out_path,
            {
                "id": req_id,
                "ok": False,
                "error": traceback.format_exc(),
                "kind": "NXOpenError",
                "hint": "把这段 traceback 原样交给 Agent，它需要真实报错才能改对脚本。",
            },
        )
        return False


def pending(stale_seconds):
    """把 queue/in 里的请求分成「该执行的」和「已经过期的」。

    为什么必须有过期机制：桥接是「重启后接着处理队列」的设计。如果队列里躺着
    昨天某个时刻投下的请求（尤其是 stop），今天一启动就会去执行它 ——
    实测踩过这个坑：启动后 0.2 秒就自己退出了，note=clean，看起来像"正常结束"，
    非常难查。所以超过阈值的请求一律不进执行环节。
    """
    if not os.path.isdir(QUEUE_IN):
        return [], []
    try:
        files = sorted(f for f in os.listdir(QUEUE_IN) if f.endswith(".json"))
    except OSError:
        return [], []
    fresh, stale = [], []
    now = time.time()
    for name in files:
        path = os.path.join(QUEUE_IN, name)
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if stale_seconds and age > stale_seconds:
            stale.append((path, age))
        else:
            fresh.append(path)
    return fresh, stale


def quarantine(path, age):
    """把过期请求挪到 queue/stale/ 并写明原因，而不是删掉 —— 留痕便于排查。"""
    dest_dir = os.path.join(HOME, "queue", "stale")
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    dest = os.path.join(dest_dir, os.path.basename(path))
    try:
        os.replace(path, dest)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    try:
        with open(dest + ".why.txt", "w", encoding="utf-8") as fh:
            fh.write(
                "这个请求被桥接跳过，没有执行。\n"
                "它投递于 %.0f 秒前（%.1f 分钟），超过了过期阈值。\n"
                "超过阈值说明投递方早就放弃了（MCP 服务端默认只等 60 秒），\n"
                "此时执行它只会造成意外后果 —— 尤其是 stop 会让桥接立刻退出。\n"
                % (age, age / 60.0)
            )
    except OSError:
        pass


def write_heartbeat(mode, loops, done, started, note=None, stale_skipped=0):
    """每一轮都刷新心跳。Agent 靠文件时间判断桥接是否还活着。"""
    payload = {
        "pid": os.getpid(),
        "nx_available": NX_AVAILABLE,
        "mode": mode,
        "loops": loops,
        "processed": done,
        "stale_skipped": stale_skipped,
        "uptime_seconds": round(time.time() - started, 1),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if note:
        payload["note"] = note
    try:
        write_json(os.path.join(STATE_DIR, "heartbeat.json"), payload)
    except Exception:
        pass


def main(argv):
    try:
        ensure_dirs()
    except BridgeError as exc:
        sys.stderr.write("[nx_bridge] %s\n" % exc)
        return 2

    watch = "--watch" in argv
    once = "--once" in argv
    # 不带参数时默认常驻：手工在 NX 的 Journal Manager 里播放就是这种情况，
    # 而「常驻」才是这一层存在的意义。
    if not watch and not once:
        watch = True

    interval = 0.5
    max_seconds = 0.0
    max_requests = 0
    stale_seconds = STALE_SECONDS_DEFAULT
    for token in argv:
        if token.startswith("--interval="):
            try:
                interval = max(0.05, float(token.split("=", 1)[1]))
            except ValueError:
                pass
        elif token.startswith("--seconds="):
            try:
                max_seconds = float(token.split("=", 1)[1])
            except ValueError:
                pass
        elif token.startswith("--max="):
            try:
                max_requests = int(token.split("=", 1)[1])
            except ValueError:
                pass
        elif token.startswith("--stale-seconds="):
            try:
                stale_seconds = float(token.split("=", 1)[1])
            except ValueError:
                pass

    started = time.time()
    mode = "watch" if watch else "once"

    try:
        write_json(
            os.path.join(STATE_DIR, "bridge.pid"),
            {"pid": os.getpid(), "mode": mode, "started": time.strftime("%Y-%m-%d %H:%M:%S")},
        )
    except Exception:
        pass

    write_heartbeat(mode, 0, 0, started, note="started")

    if not NX_AVAILABLE:
        # NX 外跑一次：用来验证队列通路，而不是用来操作 NX
        sys.stderr.write("[nx_bridge] 警告：不在 NX 进程内，仅能验证队列通路。\n")
        if watch:
            sys.stderr.write("[nx_bridge] NX 外的常驻没有意义，自动降为 --once。\n")
            watch = False
            mode = "once"
            write_heartbeat(mode, 0, 0, started, note="outside-nx")

    stopped = False
    loops = 0
    done = 0
    stale_skipped = 0
    exit_note = "clean"
    try:
        while True:
            loops += 1
            fresh, stale = pending(stale_seconds)
            for path, age in stale:
                quarantine(path, age)
                stale_skipped += 1
                sys.stderr.write(
                    "[nx_bridge] 跳过过期请求（%.0f 秒前投递）: %s\n"
                    % (age, os.path.basename(path))
                )

            for path in fresh:
                try:
                    if handle_one(path):
                        stopped = True
                except Exception:
                    # 单个请求处理炸了不该拖垮整个桥接
                    sys.stderr.write("[nx_bridge] handle_one 异常:\n%s\n" % traceback.format_exc())
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                done += 1
                if max_requests and done >= max_requests:
                    stopped = True
                    exit_note = "max-requests"
                    break

            write_heartbeat(mode, loops, done, started, stale_skipped=stale_skipped)

            if stopped or not watch:
                break
            if max_seconds and (time.time() - started) >= max_seconds:
                exit_note = "max-seconds"
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        exit_note = "interrupted"
    except Exception:
        exit_note = "crash"
        try:
            with open(os.path.join(STATE_DIR, "bridge_crash.log"), "a", encoding="utf-8") as fh:
                fh.write("\n===== %s =====\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
        except Exception:
            pass
        write_heartbeat(mode, loops, done, started, note="crash", stale_skipped=stale_skipped)
        raise

    write_heartbeat(mode, loops, done, started, note=exit_note, stale_skipped=stale_skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
