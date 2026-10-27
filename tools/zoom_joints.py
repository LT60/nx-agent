# -*- coding: utf-8 -*-
"""放大渲染 v5 装配的 3 条接缝区域（复用 render_assembly_png 的光栅化）。"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_assembly_png as R

ROOT = R.ROOT
ENT = R.ENT

JOINTS = [
    ("j1_coupler_SII",  380.0, 475.0),   # 级间段 -> S-II
    ("j2_lem_SM",       765.0, 855.0),   # LEM 整流罩 -> 服务舱
    ("j3_SM_CM",        855.0, 915.0),   # 服务舱 -> 指令舱
]

def main():
    with io.open(os.path.join(ENT, "saturn_v5_from_nx.json"), "r", encoding="utf-8") as fh:
        nx = json.load(fh)
    src = os.path.join(ENT, "downloaded_centered")
    stls = [x for x in os.listdir(src) if x.lower().endswith(".stl")]

    def find_stl(pf):
        stem = os.path.splitext(pf)[0].replace("_", " ").lower()
        for x in stls:
            if os.path.splitext(x)[0].lower() == stem:
                return os.path.join(src, x)
        for x in stls:
            if stem.replace(" ", "") in x.lower().replace("_", ""):
                return os.path.join(src, x)
        return None

    layers_all = []
    for c in nx["components"]:
        p = find_stl(c.get("part_file", ""))
        if not p:
            continue
        layers_all.append((c["label"], R.worldize(R.stl_tris(p), c["origin"], c["matrix"])))

    for name, z0, z1 in JOINTS:
        layers = []
        for label, Wt in layers_all:
            sel = [t for t in Wt if min(q[2] for q in t) <= z1 and max(q[2] for q in t) >= z0]
            if sel:
                layers.append((label, sel))
        W, H = 640, 640
        rgb = R.render(layers, "front", W, H)
        out = os.path.join(ENT, "zoom_%s.png" % name)
        n = R.write_png(out, W, H, rgb)
        print("已写 %s（%.0f KB，z %.0f-%.0f）" % (out, n / 1024.0, z0, z1))
    return 0

if __name__ == "__main__":
    sys.exit(main())
