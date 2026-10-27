#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
nx_api_lookup.py -- 离线查 NXOpen 的真实 API，别再猜

================================================================
为什么要有这个工具
================================================================
写 NXOpen 代码最大的失败来源是「凭记忆写 API」。2026-10-01 一天之内
在同一个部件上连续踩了 6 个坑：

    你以为的                              2606 实际
    part.CAMSetup 没有时返回 None      →  抛 NXException 1345023
    CAMSetup.GetOperations()           →  不存在
    Postprocess(ops, name, dir) 3 参   →  4 参（多 OutputUnits）
    UF.Disp.CreateImage 截图           →  已被删除
    Parts.NewDisplay 新建部件          →  文件名不受控会撞名
    FileNew 构造器                     →  要传本地化字符串

这些**全都能在装好的 NX 里查到**，不用联网、不用猜、也不用装模型：

    %UGII_BASE_DIR%\NXBIN\managed\NXOpen.xml   （60 MB，.NET 标准 XML 文档）

本工具就是对这份 XML 的检索。实测：140160 个成员，读+扫全量 0.25 秒。

> 背景：社区有个 `mingfeng6684/nxopen-mcp`，用 BGE-M3 + sqlite-vec 做
> NXOpen API 知识检索。思路对，但那要下几百 MB 的嵌入模型、跑向量库，
> 而且面向 .NET。**我们本地就有同一份原始文档**，直接文本检索更快更准，
> 还完全离线。所以自己做一个，不引依赖。

================================================================
用法
================================================================
    python tools/nx_api_lookup.py Postprocess          # 关键词搜（默认）
    python tools/nx_api_lookup.py --class CAMSetup     # 列某个类的全部成员
    python tools/nx_api_lookup.py --summary Postprocess # 带摘要与许可要求
    python tools/nx_api_lookup.py --limit 40 Generate   # 改返回条数
    python tools/nx_api_lookup.py --json Postprocess   # 机器可读（给 agent）
    python tools/nx_api_lookup.py --rebuild            # 强制重建索引缓存

环境变量 `NX_API_DOC` 可以直接指定 XML 路径（测试和跨机时用）。

================================================================
关于索引缓存
================================================================
第一次会解析 60 MB XML 并把结果缓存到 `state/nx_api_index.json`；
之后只要 XML 没变就只读缓存（秒开）。缓存失效自动重建。
这个缓存是运行时产物，不入版本库。
"""

import argparse
import json
import os
import re
import sys
import time

KIND_NAME = {
    "M": "方法",
    "P": "属性",
    "F": "字段/枚举值",
    "T": "类型",
}
# 搜索排序时的优先级：先给能"做事情"的成员
KIND_RANK = {"M": 0, "P": 1, "F": 2, "T": 3}

MEMBER_RE = re.compile(r'<member\s+name="([^"]+)"\s*>(.*?)</member>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _resolve_home():
    home = os.environ.get("NX_AGENT_HOME")
    if home and os.path.isdir(home):
        return home
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(here, "tools")):
        return here
    return os.getcwd()


def _nx_dir_from_start_bridge():
    """复用 start_bridge.py 的查找逻辑（注册表 + 常见目录），别搞第三套规则。"""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "start_bridge.py")
    if not os.path.isfile(path):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_start_bridge_for_lookup", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.find_nx()
    except Exception:
        return None


def find_doc(nx_dir=None):
    """定位 NXOpen.xml。

    优先级：显式参数 -> NX_API_DOC 环境变量 -> UGII_BASE_DIR 环境变量
           -> start_bridge.find_nx()（注册表 HKLM\\SOFTWARE\\Siemens\\... + 常见目录兜底）

    注意：这台机器上 `UGII_BASE_DIR` 常常**没设进 shell**（注册表里才有，
    学生版注册表键是 HKLM\\SOFTWARE\\Siemens\\NXStudent 2606），所以最后一步必须走注册表。
    """
    candidates = []
    if nx_dir:
        candidates.append(os.path.join(nx_dir, "NXBIN", "managed", "NXOpen.xml"))
        candidates.append(os.path.join(nx_dir, "managed", "NXOpen.xml"))
    env = os.environ.get("NX_API_DOC")
    if env:
        candidates.append(env)
    base = os.environ.get("UGII_BASE_DIR")
    if base:
        candidates.append(os.path.join(base, "NXBIN", "managed", "NXOpen.xml"))
    found = _nx_dir_from_start_bridge()
    if found:
        candidates.append(os.path.join(found, "NXBIN", "managed", "NXOpen.xml"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _clean(s):
    return " ".join(TAG_RE.sub(" ", s).split()).strip()


def parse(xml_path):
    """解析 XML，返回 [{kind, name, cls, member, signature, summary, remarks, params}]。"""
    with open(xml_path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    items = []
    for m in MEMBER_RE.finditer(raw):
        full = m.group(1)
        body = m.group(2)
        if ":" not in full:
            continue
        kind, name = full.split(":", 1)
        if kind not in KIND_NAME:
            continue

        # 方法名和参数分开：M:Type.Method(Args) -> Type.Method / Args
        paren = name.find("(")
        if paren >= 0 and name.endswith(")"):
            path, signature = name[:paren], name[paren + 1:-1]
        else:
            path, signature = name, ""
        cls, _, member = path.rpartition(".")

        summary = ""
        sm = re.search(r"<summary>(.*?)</summary>", body, re.S)
        if sm:
            summary = _clean(sm.group(1))
        remarks = ""
        rm = re.search(r"<remarks>(.*?)</remarks>", body, re.S)
        if rm:
            remarks = _clean(rm.group(1))
        params = []
        for pm in re.finditer(r'<param\s+name="([^"]+)"\s*>(.*?)</param>', body, re.S):
            params.append("%s: %s" % (pm.group(1), _clean(pm.group(2))))

        items.append({
            "kind": kind,
            "name": name,
            "cls": cls,
            "member": member,
            "signature": signature,
            "summary": summary,
            "remarks": remarks,
            "params": params,
        })
    return items


def index_path(home):
    return os.path.join(home, "state", "nx_api_index.json")


def load(nx_dir=None, rebuild=False, home=None):
    """拿索引（优先读缓存）。返回 (items, 来源说明)。"""
    home = home or _resolve_home()
    doc = find_doc(nx_dir)
    if not doc:
        raise RuntimeError(
            "找不到 NXOpen.xml。用 --nx-dir 指定 NX 安装目录，"
            "或设 NX_API_DOC 指向该文件。"
        )
    cache = index_path(home)
    if not rebuild and os.path.isfile(cache):
        try:
            if os.path.getmtime(cache) >= os.path.getmtime(doc):
                with open(cache, "r", encoding="utf-8") as fh:
                    return json.load(fh), "cache %s" % cache
        except Exception:
            pass
    items = parse(doc)
    try:
        state_dir = os.path.dirname(cache)
        if not os.path.isdir(state_dir):
            os.makedirs(state_dir)
        tmp = cache + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False)
        os.replace(tmp, cache)
    except Exception:
        pass  # 缓存写不进去无所谓，下次重新解析
    return items, "doc %s" % doc


def search(items, query, limit=20, cls=None):
    """关键词检索。排序：整名命中 > 成员名前缀 > 子串 > 类名命中 > 全名子串。"""
    q = query.lower()
    hits = []
    for it in items:
        if cls and it["cls"].lower() != cls.lower():
            continue
        member_l = it["member"].lower()
        cls_l = it["cls"].lower()
        name_l = it["name"].lower()
        if member_l == q or name_l == q:
            score = 0
        elif member_l.startswith(q):
            score = 1
        elif q in member_l:
            score = 2
        elif q in cls_l:
            score = 3
        elif q in name_l:
            score = 4
        else:
            continue
        hits.append((score, KIND_RANK.get(it["kind"], 9), len(it["name"]), it))
    hits.sort(key=lambda t: (t[0], t[1], t[2]))
    return [h[3] for h in hits[:limit]]


def members_of(items, cls, limit=200):
    cls_l = cls.lower()
    out = [it for it in items if it["cls"].lower().endswith(cls_l)]
    out.sort(key=lambda it: (KIND_RANK.get(it["kind"], 9), it["member"].lower()))
    return out[:limit]


def render(item, with_summary=True, with_params=False):
    kind = KIND_NAME.get(item["kind"], item["kind"])
    sig = item["signature"]
    line = "[%s] %s.%s%s" % (kind, item["cls"], item["member"],
                             "(" + sig + ")" if sig else "")
    extra = []
    if with_summary and item["summary"]:
        extra.append("     " + item["summary"])
    if with_summary and item["remarks"]:
        extra.append("     " + item["remarks"])
    if with_params:
        for p in item["params"]:
            extra.append("     - " + p)
    return "\n".join([line] + extra)


def main(argv=None):
    ap = argparse.ArgumentParser(description="离线查 NXOpen 的真实 API（不猜）")
    ap.add_argument("query", nargs="?", help="关键词，如 Postprocess")
    ap.add_argument("--class", dest="cls", help="列出某个类的全部成员，如 CAMSetup")
    ap.add_argument("--limit", type=int, default=20, help="返回条数，默认 20")
    ap.add_argument("--summary", action="store_true", help="带摘要与许可要求")
    ap.add_argument("--params", action="store_true", help="带参数说明")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给 agent 用）")
    ap.add_argument("--nx-dir", help="NX 安装目录（用它找 NXBIN/managed/NXOpen.xml）")
    ap.add_argument("--rebuild", action="store_true", help="强制重建索引缓存")
    args = ap.parse_args(argv)

    if not args.query and not args.cls:
        ap.error("给一个查询词，或用 --class 列某个类的成员")

    t0 = time.time()
    items, src = load(args.nx_dir, args.rebuild)

    if args.cls and not args.query:
        hits = members_of(items, args.cls, args.limit)
        title = "%s 的成员（%d 条）" % (args.cls, len(hits))
    else:
        hits = search(items, args.query or "", args.limit, cls=args.cls)
        title = "匹配 %s（%d 条）" % (args.query, len(hits))

    if args.json:
        print(json.dumps({"source": src, "count": len(hits), "items": hits},
                         ensure_ascii=False, indent=2))
        return 0

    print("%s ｜ 来源：%s ｜ 耗时 %.2fs ｜ 索引共 %d 个成员"
          % (title, src, time.time() - t0, len(items)))
    print("=" * 78)
    if not hits:
        print("没找到。换个词试试（可以只用片段，比如 Image 而不是 CreateImageExportBuilder）。")
        return 1
    for it in hits:
        print(render(it, args.summary, args.params))
    return 0


if __name__ == "__main__":
    sys.exit(main())
