#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapshot.py -- .prt 快照与回滚

这是整套 NX-Agent 的「回滚层」。git 管不了 .prt（二进制，改一个特征就整个文件变样），
所以必须自己造。任何改动性操作之前拍一张快照，出事就退回去。

用法:
    python snapshot.py snapshot D:/mold/E-0021.prt
    python snapshot.py list
    python snapshot.py restore 1 --to D:/mold/E-0021.prt          # 按序号（list 里显示的那个）
    python snapshot.py restore E-0021.prt.20260930-183238.bak --to D:/mold/E-0021.prt
    python snapshot.py restore snapshots/E-0021.prt.20260930-183238.bak

    指定快照有三种写法（序号 / 文件名 / 完整路径），因为 `list` 的输出里
    这三种都出现了，用户会照着抄任意一种。`--to` 省略时会从清单里
    反查原始路径。
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("NX_AGENT_HOME") or os.path.dirname(HERE)
SNAP_DIR = os.path.join(HOME, "snapshots")
MANIFEST = os.path.join(HOME, "state", "snapshot_manifest.json")


def load_manifest():
    if not os.path.isfile(MANIFEST):
        return []
    try:
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def save_manifest(items):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)


def resolve_snapshot(token):
    """
    把用户给的东西解析成快照文件的绝对路径。

    为什么要支持三种写法：因为 `list` 的输出里这三种都出现了
    （序号、文件名、完整路径），用户会照着抄任意一种。
    之前只认完整路径，用户抄序号会直接失败——这是实测出来的 bug。

      1. 完整路径            D:/nx-agent/snapshots/x.prt.20260930-183238.bak
      2. 文件名（自动去 snapshots/ 里找）   x.prt.20260930-183238.bak
      3. 序号（跟 list 一样，1 = 最新）      1

    找不到返回 None，由调用方给出可选清单。
    """
    token = (token or "").strip()
    if not token:
        return None

    # 1. 直接当路径试
    p = os.path.abspath(token)
    if os.path.isfile(p):
        return p

    # 2. 当 snapshots/ 下的文件名试
    cand = os.path.abspath(os.path.join(SNAP_DIR, token))
    if os.path.isfile(cand):
        return cand

    # 3. 当序号试（和 list 一样：1 是最新的那条）
    if token.isdigit():
        ordered = list(reversed(load_manifest()))
        idx = int(token)
        if 1 <= idx <= len(ordered):
            p = ordered[idx - 1]["snapshot"]
            if os.path.isfile(p):
                return os.path.abspath(p)

    return None


def list_snapshots_brief():
    """给报错信息用的：列出当前可选快照。"""
    ordered = list(reversed(load_manifest()))
    if not ordered:
        return "（当前没有任何快照，先跑一次 snapshot）"
    lines = []
    for i, it in enumerate(ordered, start=1):
        lines.append("    %d) %s  %s%s"
                     % (i, it["time"], os.path.basename(it["source"]),
                        ("  " + it["note"]) if it.get("note") else ""))
    return "\n".join(lines)


def cmd_snapshot(args):
    src = os.path.abspath(args.prt)
    if not os.path.isfile(src):
        sys.stderr.write("找不到部件文件: %s\n" % src)
        return 2
    os.makedirs(SNAP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(src)
    dst = os.path.join(SNAP_DIR, "%s.%s.bak" % (base, stamp))
    shutil.copy2(src, dst)
    items = load_manifest()
    items.append(
        {
            "snapshot": dst,
            "source": src,
            "size": os.path.getsize(dst),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": args.note or "",
        }
    )
    save_manifest(items)
    sys.stdout.write("已快照: %s -> %s (%d 字节)\n" % (src, dst, os.path.getsize(dst)))
    return 0


def cmd_list(args):
    items = load_manifest()
    if not items:
        sys.stdout.write("还没有任何快照。\n")
        return 0
    for i, it in enumerate(reversed(items), start=1):
        sys.stdout.write(
            "%2d) %s  %s  %d 字节  %s\n     -> %s\n"
            % (i, it["time"], os.path.basename(it["source"]), it["size"], it.get("note", ""), it["snapshot"])
        )
    sys.stdout.write(
        "\n回滚用：snapshot.py restore <序号> --to <要还原的 .prt 路径>\n"
        "        序号、文件名、完整路径都可以。\n"
    )
    return 0


def cmd_restore(args):
    snap = resolve_snapshot(args.snapshot)
    if snap is None:
        sys.stderr.write(
            "找不到这个快照: %s\n"
            "可以这样写：完整路径 / 文件名 / 序号（序号见下面的清单）\n"
            "当前可选的快照：\n%s\n"
            % (args.snapshot, list_snapshots_brief())
        )
        return 2

    target = os.path.abspath(args.to) if args.to else None
    if target is None:
        for it in reversed(load_manifest()):
            if os.path.abspath(it["snapshot"]) == snap:
                target = it["source"]
                break
    # 也允许用文件名匹配清单里的记录（用户从 list 里抄文件名时走这条路）
    if target is None:
        wanted = os.path.basename(snap)
        for it in reversed(load_manifest()):
            if os.path.basename(it["snapshot"]) == wanted:
                target = it["source"]
                break
    if target is None:
        sys.stderr.write(
            "无法确定要回滚到哪个 .prt，请显式给 --to 参数。\n"
            "例如: snapshot.py restore %s --to D:/mold/E-0021.prt\n" % args.snapshot
        )
        return 2

    # 回滚本身也是破坏性操作：先把现状再存一份，避免回滚错了没法回头
    if os.path.isfile(target):
        os.makedirs(SNAP_DIR, exist_ok=True)
        safety = os.path.join(SNAP_DIR, "%s.%s.prerestore.bak" % (os.path.basename(target), time.strftime("%Y%m%d-%H%M%S")))
        shutil.copy2(target, safety)
        sys.stdout.write("回滚前先备份了现状: %s\n" % safety)

    shutil.copy2(snap, target)
    sys.stdout.write("已回滚: %s -> %s\n" % (snap, target))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NX .prt 快照与回滚")
    sub = ap.add_subparsers(dest="action", required=True)

    p1 = sub.add_parser("snapshot", help="给 .prt 拍快照")
    p1.add_argument("prt")
    p1.add_argument("--note", default=None, help="备注，例如 '后处理之前'")

    sub.add_parser("list", help="列出快照")

    p3 = sub.add_parser("restore", help="从快照回滚")
    p3.add_argument("snapshot")
    p3.add_argument("--to", default=None, help="目标 .prt 路径，省略则用清单里的原始路径")

    args = ap.parse_args(argv)
    if args.action == "snapshot":
        return cmd_snapshot(args)
    if args.action == "list":
        return cmd_list(args)
    return cmd_restore(args)


if __name__ == "__main__":
    sys.exit(main())
