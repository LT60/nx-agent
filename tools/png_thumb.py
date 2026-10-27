# -*- coding: utf-8 -*-
"""纯标准库 PNG 解码 / 整数倍缩小 / 编码。

为什么需要：项目约定**不引第三方库**（NX 自带的 Python 很弱），
但"N 张 NX 截图塞进一个自包含网页"必须把图压小 —— 5.6MB 的原图
base64 进 HTML 会变成 8MB，预览很吃力。
没有 PIL 就得自己来。PNG 本身不复杂：zlib + 逐行过滤器。

支持：8 位、非隔行、颜色类型 0(灰度)/2(RGB)/4(灰度+A)/6(RGBA)。

    import png_thumb
    w, h, rgb = png_thumb.downscale("a.png", max_w=900)
    png_thumb.encode("a_small.png", w, h, rgb)      # RGB bytes
"""

import os
import struct
import zlib

SIG = b"\x89PNG\r\n\x1a\n"


def _chunks(data):
    pos = 8
    while pos + 8 <= len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        yield typ, body
        pos += 12 + ln


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode(path):
    """返回 (w, h, channels, bytearray)，像素为 8 位、逐行 RGB/RGBA/灰度。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(SIG):
        raise ValueError("不是 PNG: %s" % path)
    w = h = depth = ctype = interlace = None
    idat = []
    for typ, body in _chunks(data):
        if typ == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
    if depth != 8:
        raise ValueError("只支持 8 位（本图 %s 位）" % depth)
    if interlace:
        raise ValueError("不支持隔行 PNG")
    ch = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
    if ch is None:
        raise ValueError("不支持的色彩类型 %s" % ctype)

    raw = zlib.decompress(b"".join(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                c = prev[i - ch] if i >= ch else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ft != 0:
            raise ValueError("未知过滤器 %s" % ft)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, out


def encode(path, w, h, rgb):
    """把 RGB 字节写成 PNG（24 位，无 alpha）。"""
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body
                + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF))

    out = [SIG,
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)),
           chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
           chunk(b"IEND", b"")]
    with open(path, "wb") as fh:
        fh.write(b"".join(out))
    return os.path.getsize(path)


def downscale(path, max_w=None, max_h=None, to_rgb=True):
    """整数倍盒式缩小（质量足够，代码最少）。返回 (w, h, RGB bytes)。"""
    w, h, ch, pix = decode(path)
    k = 1
    # ★ 用四舍五入而不是整除：1063 宽要缩到 640，1063//640 = 1（等于没缩），
    #   round(1063/640) = 2 才对。
    if max_w:
        k = max(k, int(round(w / float(max_w))))
    if max_h:
        k = max(k, int(round(h / float(max_h))))
    k = max(1, int(k))
    nw, nh = w // k, h // k
    out = bytearray(nw * nh * 3)
    for y in range(nh):
        for x in range(nw):
            r = g = b = 0
            for dy in range(k):
                base = ((y * k + dy) * w + x * k) * ch
                for dx in range(k):
                    p = base + dx * ch
                    if ch >= 3:
                        r += pix[p]; g += pix[p + 1]; b += pix[p + 2]
                    else:
                        v = pix[p + (ch - 1)]
                        r += v; g += v; b += v
            n = k * k
            q = (y * nw + x) * 3
            out[q] = r // n; out[q + 1] = g // n; out[q + 2] = b // n
    return nw, nh, bytes(out)


if __name__ == "__main__":
    import sys
    src = sys.argv[1]
    dst = sys.argv[2]
    mw = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    w, h, rgb = downscale(src, max_w=mw)
    n = encode(dst, w, h, rgb)
    print("%s  %.1f KB → %s  %dx%d  %.1f KB"
          % (os.path.basename(src), os.path.getsize(src) / 1024.0,
             os.path.basename(dst), w, h, n / 1024.0))
