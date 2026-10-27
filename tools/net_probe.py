#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""net_probe.py —— 下载通道连通性探测器（纯标准库，离线可用）

SSC / 浏览器下不动西门子安装包时，先跑这个。
它把「DNS 能不能解析 → TCP 能不能连上 443 → TLS 握手要多久 → HTTP 能不能拿到首字节」
四层分开量一遍，这样就能判断是「域名被墙」「端口被拦」「TLS 被重置」还是「只是慢」。

用法：
    python tools/net_probe.py                      # 探测内置的西门子候选域名
    python tools/net_probe.py --host a.com b.com   # 探测自定义域名
    python tools/net_probe.py --proxy 127.0.0.1:7897   # 额外测「走本地代理」这条路
    python tools/net_probe.py --json               # 输出 JSON

退出码：
    0 = 所有域名四层全通
    1 = 有域名在某一层失败（具体看输出）
    2 = 参数错误
"""
from __future__ import print_function

import argparse
import json
import socket
import ssl
import sys
import time

# 西门子下载链路上会碰到的域名。
# download.industrysoftware.automation.siemens.com 是传统上放 NX 安装包的下载服务器，
# 学生版的 SiemensDesigncenterStudentEdition-*.zip 也出自这里。
DEFAULT_HOSTS = [
    "download.industrysoftware.automation.siemens.com",  # 安装包下载服务器（关键）
    "support.sw.siemens.com",                            # 支持中心 / SSC 后台
    "www.siemens.com",                                   # 西门子主站（对照用）
    "sw.siemens.com",                                    # Xcelerator 门户（对照用）
    "www.baidu.com",                                     # 国内基线（对照用，一定通）
]

DEFAULT_TIMEOUT = 10.0


# ---------------------------------------------------------------- 单层探测

def _dns(host, port, timeout):
    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except Exception as e:
        return None, None, "%s: %s" % (type(e).__name__, e)
    ips = sorted({i[4][0] for i in infos})
    ms = int((time.time() - t0) * 1000)
    return ips, ms, None


def _tcp(ip, port, timeout, source_ip=None):
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    if source_ip:
        try:
            s.bind((source_ip, 0))
        except Exception:
            pass
    try:
        s.connect((ip, port))
    except Exception as e:
        s.close()
        return None, int((time.time() - t0) * 1000), "%s: %s" % (type(e).__name__, e)
    return s, int((time.time() - t0) * 1000), None


def _tls(sock, host, timeout):
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(sock, server_hostname=host)
        ms = int((time.time() - t0) * 1000)
        ver = ss.version()
        cipher = ss.cipher()[0] if ss.cipher() else "?"
        ss.close()
        return ms, ver, cipher, None
    except Exception as e:
        try:
            sock.close()
        except Exception:
            pass
        return int((time.time() - t0) * 1000), None, None, "%s: %s" % (type(e).__name__, e)


def probe_host(host, port=443, timeout=DEFAULT_TIMEOUT, http_path=None):
    """四层逐级探测，返回 dict。任何一层失败就停在那层（后面的层标 skipped）。"""
    r = {
        "host": host, "port": port,
        "dns": None, "dns_ms": None, "ips": [],
        "tcp": None, "tcp_ms": None,
        "tls": None, "tls_ms": None, "tls_version": None, "cipher": None,
        "http": None, "first_byte_ms": None,
        "error": None, "error_layer": None, "verdict": "",
    }

    ips, ms, err = _dns(host, port, timeout)
    if err:
        r["dns"], r["error"], r["error_layer"] = "FAIL", err, "DNS"
        r["verdict"] = "域名解析失败 → 被 DNS 污染/不存在"
        return r
    r["dns"], r["dns_ms"], r["ips"] = "OK", ms, ips

    sock, ms, err = _tcp(ips[0], port, timeout)
    if err:
        r["tcp"], r["tcp_ms"], r["error"], r["error_layer"] = "FAIL", ms, err, "TCP"
        r["verdict"] = "TCP %d 端口连不上 → 被防火墙/路由丢弃（典型的墙）" % port
        return r
    r["tcp"], r["tcp_ms"] = "OK", ms

    tls_ms, ver, cipher, err = _tls(sock, host, timeout)
    if err:
        r["tls"], r["tls_ms"], r["error"], r["error_layer"] = "FAIL", tls_ms, err, "TLS"
        r["verdict"] = "TLS 握手失败 → 连接被中途重置（典型的 SNI 阻断）"
        return r
    r["tls"], r["tls_ms"], r["tls_version"], r["cipher"] = "OK", tls_ms, ver, cipher

    if http_path is not None:
        r["http"], r["first_byte_ms"], err = _http(sock_new(ips[0], port, timeout), host, http_path, timeout)
        if err:
            r["error"], r["error_layer"] = err, "HTTP"
            r["verdict"] = "TLS 通了但 HTTP 拿不到响应 → 中间有代理干扰"
            return r

    if r["tls_ms"] is not None and r["tls_ms"] > 3000:
        r["verdict"] = "四层全通，但 TLS 握手偏慢（%dms）→ 通但可能不稳" % r["tls_ms"]
    else:
        r["verdict"] = "四层全通"
    return r


def sock_new(ip, port, timeout):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((ip, port))
    return s


def _http(sock, host, path, timeout):
    """发一个极小的 GET，只等首字节。用来区分「TLS 通了但没数据」的情况。"""
    try:
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(sock, server_hostname=host)
        req = ("GET %s HTTP/1.1\r\nHost: %s\r\n"
               "User-Agent: net_probe/1.0\r\nAccept: */*\r\n"
               "Connection: close\r\n\r\n") % (path, host)
        t0 = time.time()
        ss.sendall(req.encode("ascii"))
        ss.settimeout(timeout)
        first = ss.recv(1)
        ms = int((time.time() - t0) * 1000)
        ss.close()
        if not first:
            return None, ms, "服务端直接关闭连接（返回 0 字节）"
        return "OK", ms, None
    except Exception as e:
        try:
            sock.close()
        except Exception:
            pass
        return None, None, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 代理对照

def probe_via_proxy(proxy, host, timeout):
    """走本地代理时能不能拿到数据。用 urllib，纯标准库。"""
    try:
        import urllib.request
    except ImportError:
        return {"host": host, "proxy": proxy, "ok": None, "error": "urllib 不可用"}
    url = "https://%s/" % host
    handler = urllib.request.ProxyHandler({"http": "http://%s" % proxy,
                                           "https": "http://%s" % proxy})
    opener = urllib.request.build_opener(handler)
    t0 = time.time()
    try:
        resp = opener.open(url, timeout=timeout)
        code = resp.getcode()
        resp.read(64)
        return {"host": host, "proxy": proxy, "ok": True,
                "http_status": code, "ms": int((time.time() - t0) * 1000), "error": None}
    except Exception as e:
        return {"host": host, "proxy": proxy, "ok": False,
                "http_status": None, "ms": int((time.time() - t0) * 1000),
                "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 输出

def fmt_line(r):
    layers = []
    layers.append("DNS %s%s" % (r["dns"], ("(%dms)" % r["dns_ms"]) if r["dns_ms"] is not None else ""))
    layers.append("TCP %s%s" % (r["tcp"] or "skip", ("(%dms)" % r["tcp_ms"]) if r["tcp_ms"] is not None else ""))
    layers.append("TLS %s%s" % (r["tls"] or "skip", ("(%dms %s)" % (r["tls_ms"], r["tls_version"] or "")) if r["tls_ms"] is not None else ""))
    if r["http"] is not None:
        layers.append("HTTP %s" % r["http"])
    return "  ".join(layers)


def main():
    ap = argparse.ArgumentParser(description="下载通道连通性探测器（四层分开量）")
    ap.add_argument("--host", nargs="*", help="要探测的域名，默认探西门子下载链路")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="每层超时秒数，默认 10")
    ap.add_argument("--proxy", help="额外测一条走本地代理的路，例如 127.0.0.1:7897")
    ap.add_argument("--http", dest="http_path", default=None,
                    help="再加一步 HTTP GET，例如 --http /（慢，但能确认能拿到数据）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    hosts = args.host or DEFAULT_HOSTS
    results = []
    if not args.json:
        print("下载通道探测  （超时 %.0fs/层）" % args.timeout)
        print("-" * 78)
    for h in hosts:
        r = probe_host(h, args.port, args.timeout, args.http_path)
        results.append(r)
        if not args.json:
            print("%s" % h)
            print("    %s" % fmt_line(r))
            print("    -> %s" % r["verdict"])
            if r["error"]:
                print("    错误: %s" % r["error"])
            print()

    proxy_results = []
    if args.proxy:
        if not args.json:
            print("-" * 78)
            print("走本地代理 %s：" % args.proxy)
        for h in hosts:
            pr = probe_via_proxy(args.proxy, h, args.timeout)
            proxy_results.append(pr)
            if not args.json:
                print("  %-52s %s" % (h, "OK(%s, %dms)" % (pr["http_status"], pr["ms"]) if pr["ok"] else "FAIL " + str(pr["error"])))
        if not args.json:
            print()

    bad = [r for r in results if r["error"]]
    if args.json:
        print(json.dumps({"direct": results, "via_proxy": proxy_results}, ensure_ascii=False, indent=2))
    else:
        print("-" * 78)
        if not bad:
            print("结论：所有域名四层全通。下载还失败的话，问题在客户端（代理设置/磁盘/权限），不在网络。")
        else:
            print("结论：%d / %d 个域名不通。不通的层：%s"
                  % (len(bad), len(results), ", ".join(sorted({r["error_layer"] for r in bad}))))
            print("对策：")
            print("  1) 让 clash 开 TUN 模式（虚拟网卡）再试下载 —— 这是最可能直接解决的。")
            print("  2) clash 里切「全局/Global」模式再试，用来排除分流规则问题。")
            print("  3) 还不行就换网络环境（手机热点 / 弟弟厂里的机器）下载。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
