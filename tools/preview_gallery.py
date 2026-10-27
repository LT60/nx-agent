#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
preview_gallery.py -- 把 NX 的预览截图做成一页 HTML，浏览器里直接看

================================================================
这就是「刷新网页」在 NX 这边的对应物
================================================================
网页开发：改代码 → 刷新浏览器 → 人看 → 提要求 → 再改
NX 开发  ：改 journal → `nx_preview`（重播刀路+3D切除+截图）→ **本工具生成一页 HTML**
           → 人看 → 提要求 → 再改

NX 自己没有"浏览器式的结果页"，所以自己做一张：把每次预览的截图按时间排开，
带时间戳和尺寸，一屏看完。人多半一眼就能看出"这里多切了一刀"。

================================================================
用法
================================================================
    python tools\preview_gallery.py                    # 生成并自动打开
    python tools\preview_gallery.py --no-open          # 只生成，不打开
    python tools\preview_gallery.py --dir state\preview --out gallery.html
    python tools\preview_gallery.py --latest 6         # 只看最近 6 张
    python tools\preview_gallery.py --list             # 只列出图片，不生成
"""

import argparse
import os
import sys
import time
import webbrowser

IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif")


def _resolve_home():
    home = os.environ.get("NX_AGENT_HOME")
    if home and os.path.isdir(home):
        return home
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(here, "state")):
        return here
    return os.getcwd()


def collect(directory):
    if not os.path.isdir(directory):
        return []
    items = []
    for name in os.listdir(directory):
        if not name.lower().endswith(IMG_EXT):
            continue
        path = os.path.join(directory, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        items.append({
            "name": name,
            "path": path,
            "mtime": st.st_mtime,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "bytes": st.st_size,
        })
    items.sort(key=lambda it: it["mtime"])
    return items


def build_html(items, title):
    parts = [
        '<!DOCTYPE html>',
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<title>%s</title>' % title,
        '<style>',
        'body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;'
        'margin:0;padding:24px;background:#f7f7f5;color:#2c2c2a}',
        'h1{font-size:16px;font-weight:500;margin:0 0 4px}',
        '.sub{font-size:12px;color:#5f5e5a;margin-bottom:20px}',
        '.grid{display:flex;flex-wrap:wrap;gap:16px}',
        'figure{margin:0;background:#fff;border:1px solid #e5e3dc;border-radius:10px;'
        'padding:10px;width:320px}',
        'figure img{width:100%;border-radius:6px;display:block;background:#faf9f6}',
        'figcaption{font-size:12px;color:#5f5e5a;margin-top:8px;line-height:1.5}',
        '.tag{display:inline-block;background:#eef3e4;color:#27500a;border-radius:6px;'
        'padding:1px 6px;font-size:11px;margin-right:4px}',
        '.empty{font-size:13px;color:#5f5e5a}',
        '</style></head><body>',
        '<h1>%s</h1>' % title,
        '<div class="sub">共 %d 张 ｜ 按时间从旧到新 ｜ 最新在最右下</div>' % len(items),
    ]
    if items:
        parts.append('<div class="grid">')
        for it in items:
            step = ""
            for key, label in (("replay", "刀路重播"), ("show3d", "3D 切除"), ("current", "当前视图")):
                if key in it["name"]:
                    step = '<span class="tag">%s</span>' % label
                    break
            parts.append(
                '<figure><img src="%s" alt="%s">'
                '<figcaption>%s%s<br>%s ｜ %.0f KB</figcaption></figure>'
                % (it["name"], it["name"], step, it["name"], it["time"], it["bytes"] / 1024.0)
            )
        parts.append('</div>')
    else:
        parts.append('<p class="empty">还没有任何预览图。先在 NX 里跑一次 '
                     '<code>nx_preview</code>（或桥接的 preview 命令）。</p>')
    parts.append('</body></html>')
    return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 NX 预览截图做成一页 HTML（相当于刷新网页看结果）")
    ap.add_argument("--dir", dest="directory", help="预览图目录，默认 <HOME>/state/preview")
    ap.add_argument("--out", help="输出 HTML 路径，默认写到预览目录下的 gallery.html")
    ap.add_argument("--latest", type=int, help="只看最近 N 张")
    ap.add_argument("--no-open", action="store_true", help="生成但不自动打开浏览器")
    ap.add_argument("--list", action="store_true", help="只列出图片，不生成 HTML")
    args = ap.parse_args(argv)

    home = _resolve_home()
    directory = args.directory or os.path.join(home, "state", "preview")
    items = collect(directory)

    if args.list:
        if not items:
            print("（目录里没有图片）%s" % directory)
            return 1
        for it in items:
            print("%s  %7.0f KB  %s" % (it["time"], it["bytes"] / 1024.0, it["name"]))
        return 0

    if args.latest and args.latest > 0:
        items = items[-args.latest:]

    out = args.out or os.path.join(directory, "gallery.html")
    out_dir = os.path.dirname(out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build_html(items, "NX 预览画廊"))

    print("预览目录：%s" % directory)
    print("图片 %d 张 → %s" % (len(items), out))
    if not args.no_open and items:
        try:
            webbrowser.open("file:///" + out.replace("\\", "/"))
            print("已在浏览器打开。跑完一次 nx_preview 后再执行本命令，就是「刷新」。")
        except Exception as exc:
            print("打开浏览器失败（%s），自己双击打开这个文件即可。" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
