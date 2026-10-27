#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""并排对比两版渲染 —— 用来定位"数据对但图错"到底错在哪。

2026-10-10 的用法：左边 = NX 自己渲染的（显示为散）；
右边 = 用 NX 报的同一批坐标、我自己光栅化的（显示为连续）。
两张图**用的是同一份 NX 数据**，却一个散一个连 →
问题在 NX 的显示层，不在几何。

纯标准库：复用 png_thumb 的解码 + 自己的编码。
用法：
    python tools/compose_compare.py 左.png 右.png -o 对比.png --labels "NX渲染,自渲染"
"""

import argparse
import io
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from png_thumb import decode  # noqa: E402


def fit_into(path, box_w, box_h):
    """把图按比例缩放进 box_w×box_h，返回 (w,h,rgb)，居中留白由调用方处理。"""
    w, h, ch, pix = decode(path)
    if ch == 4:
        # RGBA → 合成到白底
        out = bytearray()
        for i in range(w * h):
            r, g, b, a = pix[i * 4:i * 4 + 4]
            f = a / 255.0
            out += bytes((int(r * f + 255 * (1 - f)),
                          int(g * f + 255 * (1 - f)),
                          int(b * f + 255 * (1 - f))))
        pix = bytes(out)
        ch = 3
    elif ch == 1:
        out = bytearray()
        for v in pix:
            out += bytes((v, v, v))
        pix = bytes(out)
        ch = 3
    k = min(box_w / float(w), box_h / float(h))
    nw, nh = max(1, int(w * k)), max(1, int(h * k))
    out = bytearray()
    for y in range(nh):
        sy = min(h - 1, int(y / k))
        for x in range(nw):
            sx = min(w - 1, int(x / k))
            i = (sy * w + sx) * ch
            out += pix[i:i + 3]
    return nw, nh, bytes(out)


def write_png(path, w, h, rgb):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


def main(argv=None):
    ap = argparse.ArgumentParser(description="并排对比两张渲染图")
    ap.add_argument("left")
    ap.add_argument("right")
    ap.add_argument("-o", "--out", default="compare.png")
    ap.add_argument("--labels", default="")
    ap.add_argument("--w", type=int, default=560)
    ap.add_argument("--h", type=int, default=900)
    args = ap.parse_args(argv)

    lw, lh, lp = fit_into(args.left, args.w, args.h)
    rw, rh, rp = fit_into(args.right, args.w, args.h)

    pad = 20
    gap = 26
    banner = 56
    W = pad * 2 + lw + gap + rw
    H = banner + pad * 2 + max(lh, rh)

    # 背景：浅灰
    bg = (244, 245, 247)
    buf = bytearray(bytes(bg) * (W * H))

    def blit(x0, y0, w, h, pix):
        for y in range(h):
            src = y * w * 3
            dst = ((y0 + y) * W + x0) * 3
            buf[dst:dst + w * 3] = pix[src:src + w * 3]

    # 图下面加个白底卡片（视觉上像"照片框"）
    for (x0, w, h) in ((pad, lw, lh), (pad + lw + gap, rw, rh)):
        cy0 = banner + pad + (max(lh, rh) - h) // 2
        white = bytes((255, 255, 255)) * (w * h)
        blit(x0, cy0, w, h, white)

    cy_l = banner + pad + (max(lh, rh) - lh) // 2
    cy_r = banner + pad + (max(lh, rh) - rh) // 2
    blit(pad, cy_l, lw, lh, lp)
    blit(pad + lw + gap, cy_r, rw, rh, rp)

    # 顶部横幅：深色条 + 标题（用极简 5x7 点阵画不出中文，
    # 所以横幅只做色块，标题写在文件旁的 md/终端里）
    bar = bytes((31, 41, 55)) * (W * 4)
    buf[0:len(bar)] = bar
    # 标题位置用两条色块示意"左/右"
    lc = bytes((226, 75, 74)) * (lw * 3)
    rc2 = bytes((29, 158, 117)) * (rw * 3)
    for y in range(18, 34):
        off = (y * W + pad) * 3
        buf[off:off + lw * 3] = lc
        off2 = (y * W + pad + lw + gap) * 3
        buf[off2:off2 + rw * 3] = rc2

    n = write_png(args.out, W, H, bytes(buf))
    print("已写 %s（%d×%d，%.0f KB）" % (args.out, W, H, n / 1024.0))
    if args.labels:
        labs = args.labels.split(",")
        print("横幅：左=%s  右=%s"
              % (labs[0] if labs else "?", labs[1] if len(labs) > 1 else "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main())