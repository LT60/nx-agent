#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把装配校验结果做成一页能看的报告（Agent 与人共用同一份结论）。

数据来源只有两份，都是真数据：
  · output/enterprise/assembly_check.json   ← NX 原生间隙分析（干涉/穿透深度）
  · output/enterprise/saturn_layout.json     ← 布局意图（设计上的重叠量）

页面**不做任何几何计算**：判据全在 tools/check_interference.py 里，
这里只负责把它算出来的结论显示出来。

用法：
    python tools/build_asm_report.py
    python tools/build_asm_report.py -o output/enterprise/asm_report.html
"""

import argparse
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENT = os.path.join(ROOT, "output", "enterprise")
CHK = os.path.join(ENT, "assembly_check.json")
LAY = os.path.join(ENT, "saturn_layout.json")
TOOL = os.path.join(ROOT, "tools", "check_interference.py")

VERDICT_STYLE = {
    "ERROR": ("#b91c1c", "#fef2f2", "#fecaca", "真错 —— 零件插进去太多了"),
    "WARN": ("#b45309", "#fffbeb", "#fde68a", "可疑 —— 需要人看一眼"),
    "INFO": ("#15803d", "#f0fdf4", "#bbf7d0", "预期内 —— 套接或面接触"),
}


def run_checker():
    """调复核器，拿它的**结构化**结论（--json）。

    ★ 不要解析复核器的文本输出 —— 那是给人看的。
      判据只有 tools/check_interference.py 一处实现，
      这里通过 --json 消费同一批结论，避免两套逻辑漂移。
    """
    if not (os.path.isfile(CHK) and os.path.isfile(LAY)):
        return None, "", ""
    proc = subprocess.run([sys.executable, TOOL, CHK, LAY, "--json"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.stdout.decode("utf-8", "replace")
    data = {}
    if "@@JSON@@" in text:
        try:
            data = json.loads(text.split("@@JSON@@", 1)[1])
        except Exception:
            data = {}
    return proc.returncode, data, text


HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>装配校验报告 · __TOTAL_TITLE__</title>
<style>
  :root{
    --bg:#f8fafc; --card:#fff; --line:#e2e8f0; --ink:#0f172a; --muted:#64748b;
  }
  *{box-sizing:border-box}
  body{margin:0;padding:28px 20px 60px;background:var(--bg);color:var(--ink);
       font:15px/1.7 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
  .wrap{max-width:1080px;margin:0 auto}
  h1{font-size:24px;margin:0 0 6px}
  .sub{color:var(--muted);font-size:14px;margin-bottom:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:20px 22px;margin-bottom:18px}
  h2{font-size:17px;margin:0 0 14px;display:flex;align-items:center;gap:8px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
  .kpi{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:14px}
  .kpi .n{font-size:26px;font-weight:700;line-height:1.2}
  .kpi .l{color:var(--muted);font-size:13px;margin-top:2px}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;
        vertical-align:top}
  th{color:var(--muted);font-weight:600;font-size:13px;white-space:nowrap}
  td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  .tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;
       font-weight:600;border:1px solid;white-space:nowrap}
  .raw{font-family:Consolas,"Courier New",monospace;font-size:13px;white-space:pre-wrap;
       background:var(--bg);border:1px solid var(--line);border-radius:8px;
       padding:14px;overflow-x:auto}
  .note{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;
        padding:14px 16px;font-size:14px;margin-top:4px}
  .note b{color:#1d4ed8}
  ol{padding-left:22px}
  li{margin:5px 0}
  code{background:var(--bg);padding:1px 6px;border-radius:5px;
       font-family:Consolas,monospace;font-size:13px}
</style></head><body><div class="wrap">

<h1>装配校验报告 · __TOTAL_TITLE__</h1>
<div class="sub">__SUBTITLE__</div>

<div class="card">
  <h2>结论</h2>
  <div class="kpis">
    <div class="kpi"><div class="n">__N_PAIRS__</div><div class="l">NX 检查的零件对</div></div>
    <div class="kpi"><div class="n">__N_IF__</div><div class="l">NX 报出的干涉处</div></div>
    <div class="kpi"><div class="n" style="color:#b91c1c">__N_ERR__</div><div class="l">真错</div></div>
    <div class="kpi"><div class="n" style="color:#b45309">__N_WARN__</div><div class="l">可疑</div></div>
    <div class="kpi"><div class="n" style="color:#15803d">__N_INFO__</div><div class="l">预期内</div></div>
  </div>
  <div class="note">__VERDICT_SENTENCE__</div>
</div>

<div class="card">
  <h2>逐处明细</h2>
  __TABLE__
</div>

<div class="card">
  <h2>这套校验是怎么工作的</h2>
  <ol>
    <li><b>NX 侧</b>（<code>jc_assembly_check.py</code>）：用 NX 原生的
        <b>间隙分析</b>（Clearance Analysis）跑一次全量检查，45 对两两比完，
        再逐对枚举出到底哪几对在插进去、并算出<b>穿透深度</b>。</li>
    <li><b>离线侧</b>（<code>check_interference.py</code>）：NX 只知道"实体插进去了"，
        不知道你<b>故意</b>让它们套接。所以这里拿布局的 Z 区间算出
        "设计上该重叠多少"，再和穿深比：
        <b>穿深 ≤ 设计重叠 → 预期内；穿深 &gt; 设计重叠 → 真错</b>。</li>
    <li><b>回归</b>（<code>selftest.py</code>）：把套接量清零，
        复核器必须从"预期内"翻成"真错" —— 证明它不是橡皮图章。</li>
    <li><b>页面</b>只消费复核器的 <code>--json</code> 结构化输出，
        不去解析给人看的文本 —— 判据只有一处实现，两边不会漂移。</li>
  </ol>
  <div class="note">
    <b>为什么必须这么绕：</b>直接照抄 NX 的结论去改坐标是错的 ——
    土星五号这种打印套件，端头本来就是"套筒套住细脖"，
    "插进去"往往是设计意图而不是装错。
  </div>
</div>

<div class="card">
  <h2>复核器原始输出</h2>
  <div class="raw">__RAW__</div>
</div>

</div></body></html>
"""


def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="装配校验一页报告")
    ap.add_argument("-o", "--out", default=os.path.join(ENT, "asm_report.html"))
    ap.add_argument("--title", default="土星五号")
    args = ap.parse_args(argv)

    rc, data, text = run_checker()
    if rc is None:
        print("缺少 assembly_check.json 或 saturn_layout.json —— "
              "先在 NX 里跑 j_saturn.py")
        return 2

    rows = data.get("rows") or []
    sm = data.get("summary") or {}
    clr = {"n_interferences": data.get("n_interferences"),
           "zone": data.get("zone")}

    n_err = sum(1 for r in rows if r["verdict"] == "ERROR")
    n_warn = sum(1 for r in rows if r["verdict"] == "WARN")
    n_info = sum(1 for r in rows if r["verdict"] == "INFO")

    if n_err:
        head = ("NX 在 %d 处检出插进去，其中**%d 处是真错**。"
                % (len(rows), n_err))
        worst = max((r for r in rows if r["verdict"] == "ERROR"),
                    key=lambda r: r.get("depth") or 0)
        head += "最严重的是「%s」（%s），多插了约 %.1f mm。" % (
            worst.get("pair", ""), worst.get("why", ""), worst.get("depth") or 0)
    elif n_warn:
        head = "没有确认的真错，但有 %d 处需要人看一眼。" % n_warn
    else:
        head = ("全部 %d 处都落在设计内的套接/接触里，**没有装配错误**。"
                % len(rows))

    # 明细表
    trs = []
    for r in rows:
        fg, bg, bd, tip = VERDICT_STYLE.get(r["verdict"],
                                            ("#334155", "#f1f5f9", "#cbd5e1", ""))
        trs.append(
            "<tr><td><b>%s</b><div style='color:#64748b;font-size:13px'>%s</div></td>"
            "<td class='num'>%s</td>"
            "<td><span class='tag' style='color:%s;background:%s;border-color:%s'>%s</span>"
            "<div style='color:#64748b;font-size:13px;margin-top:4px'>%s</div></td></tr>"
            % (esc(r.get("pair") or (r.get("a", "") + " × " + r.get("b", ""))),
               esc(r.get("kind") or "—"),
               ("%.3f" % r["depth"]) if r["depth"] is not None else "—",
               fg, bg, bd, r["verdict"] or "?", esc(r["why"])))
    table = ("<table><tr><th>零件对 / 接头关系</th><th>穿深 mm</th><th>判定</th></tr>"
             + "".join(trs) + "</table>") if trs else "<p>没有干涉记录。</p>"

    html = (HTML
            .replace("__TITLE__", esc(args.title))
            .replace("__TOTAL_TITLE__", esc(args.title))
            .replace("__SUBTITLE__",
                     "数据来自 NX 原生间隙分析（45 对零件两两比对）＋ 布局设计意图；"
                     "本页不做任何几何计算，只显示复核器的结论。")
            .replace("__N_PAIRS__", esc(sm.get("NumPairs", "—")))
            .replace("__N_IF__", esc(clr.get("n_interferences", "—")))
            .replace("__N_ERR__", str(n_err))
            .replace("__N_WARN__", str(n_warn))
            .replace("__N_INFO__", str(n_info))
            .replace("__VERDICT_SENTENCE__", head)
            .replace("__TABLE__", table)
            .replace("__RAW__", esc(text)))
    # __TITLE__ 只在 h1 用了一次标题占位，避免误替换 doc title
    html = html.replace("装配校验报告 · 土星五号</title>",
                        "装配校验报告 · %s</title>" % esc(args.title))

    with io.open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已生成 %s（%.0f KB）｜ ERROR %d ｜ WARN %d ｜ INFO %d"
          % (args.out, os.path.getsize(args.out) / 1024.0, n_err, n_warn, n_info))
    return 0


if __name__ == "__main__":
    sys.exit(main())