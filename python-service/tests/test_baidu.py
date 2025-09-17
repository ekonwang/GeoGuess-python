#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Baidu Panorama 静态图抓取（含 debug 模式）
- 默认抓取：上海人民广场，四个朝向
- 加了 DEBUG 输出：URL、状态码、响应头、响应体前 1KB、常见风险检查
"""

import os, time, math, pathlib, requests
from typing import Tuple, Optional

# ======= 可配置 =======
BAIDU_AK = os.getenv("BAIDU_AK", "YOUR_BAIDU_AK")
WGS84_LNG, WGS84_LAT = 121.473701, 31.230416  # 人民广场
HEADINGS = [0, 90, 180, 270]
PITCH, FOV = 10, 120
RES_CANDIDATES = [(4096, 2048), (2048, 1024), (1024, 512)]
OUTDIR = pathlib.Path("baidu_streetview_shanghai")
DEBUG = True  # <—— 打开调试

# ======= 坐标转换：WGS84 -> BD-09 =======
x_pi = math.pi * 3000.0 / 180.0
pi = math.pi
a = 6378245.0
ee = 0.00669342162296594323

def _out_of_china(lng, lat):
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

def _transformlat(lng, lat):
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0/3.0
    ret += (20.0 * math.sin(lat * pi) + 40.0 * math.sin(lat/3.0 * pi)) * 2.0/3.0
    ret += (160.0 * math.sin(lat/12.0 * pi) + 320 * math.sin(lat * pi / 30.0)) * 2.0/3.0
    return ret

def _transformlng(lng, lat):
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0/3.0
    ret += (20.0 * math.sin(lng * pi) + 40.0 * math.sin(lng/3.0 * pi)) * 2.0/3.0
    ret += (150.0 * math.sin(lng/12.0 * pi) + 300.0 * math.sin(lng/30.0 * pi)) * 2.0/3.0
    return ret

def wgs84_to_gcj02(lng, lat):
    if _out_of_china(lng, lat): return lng, lat
    dlat = _transformlat(lng - 105.0, lat - 35.0)
    dlng = _transformlng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = lat + dlat
    mglng = lng + dlng
    return mglng, mglat

def gcj02_to_bd09(lng, lat):
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * x_pi)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * x_pi)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006

def wgs84_to_bd09(lng, lat):
    g_lng, g_lat = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(g_lng, g_lat)

# ======= HTTP 工具 =======
def build_url(bd_lng, bd_lat, width, height, heading, pitch, fov, ak):
    base = "https://api.map.baidu.com/panorama/v2"
    return (
        f"{base}?ak={ak}"
        f"&width={width}&height={height}"
        f"&location={bd_lng:.6f},{bd_lat:.6f}"
        f"&heading={heading}&pitch={pitch}&fov={fov}"
        f"&coordtype=bd09ll"
    )

def fetch(url, timeout=15.0):
    r = requests.get(url, timeout=timeout)
    info = {
        "ok": r.ok,
        "status_code": r.status_code,
        "headers": dict(r.headers),
        "content_type": r.headers.get("Content-Type", ""),
        "raw": r.content[:1024],  # 前1KB
        "text": None
    }
    # 文本尝试用 r.text（已按编码解码）
    try:
        info["text"] = r.text[:1024]
    except Exception:
        pass
    return r, info

def debug_dump(url, info, hint=None):
    if not DEBUG: return
    print("\n[DEBUG] ===== 请求 =====")
    print(url)
    print("[DEBUG] 状态码:", info["status_code"], "ok:", info["ok"])
    print("[DEBUG] Content-Type:", info["content_type"])
    print("[DEBUG] 响应头(部分):", {k: info["headers"].get(k) for k in ["Date","Server","Content-Length","Content-Type"]})
    # 打印文本/JSON 前 1KB（避免中文乱码）
    txt = info.get("text")
    if txt:
        print("[DEBUG] 文本片段:", txt)
    else:
        print("[DEBUG] 原始前1KB:", info.get("raw"))
    if hint: print("[DEBUG] 提示:", hint)

# ======= 主逻辑 =======
def main():
    assert BAIDU_AK and BAIDU_AK != "YOUR_BAIDU_AK", "请先配置 BAIDU_AK！"
    OUTDIR.mkdir(parents=True, exist_ok=True)

    # 1) 打印常见风险项
    if DEBUG:
        print("== Debug Checklist ==")
        print("1) 该 AK 是否在 LBS 控制台为【全景静态图】勾选了权限？（未勾选将返回 status=240）")
        print("2) AK 类型是否正确（浏览器/服务端），是否触发 Referer 或 IP 白名单校验？")
        print("3) 该 AK 是否有效/未被删除/未禁用？\n")

    # 2) 坐标转换
    bd_lng, bd_lat = wgs84_to_bd09(WGS84_LNG, WGS84_LAT)
    print(f"WGS84 -> BD-09: ({WGS84_LNG:.6f},{WGS84_LAT:.6f}) -> ({bd_lng:.6f},{bd_lat:.6f})")

    # 3) 拉取
    for w, h in RES_CANDIDATES:
        print(f"\n尝试分辨率 {w}x{h} ...")
        got_any = False
        for heading in HEADINGS:
            url = build_url(bd_lng, bd_lat, w, h, heading, PITCH, FOV, BAIDU_AK)
            r, info = fetch(url)
            if info["ok"] and info["content_type"].startswith("image"):
                fname = OUTDIR / f"shanghai_{w}x{h}_h{heading}_p{PITCH}_f{FOV}.jpg"
                fname.write_bytes(r.content)
                print(f"Saved: {fname}")
                got_any = True
                time.sleep(0.2)
            else:
                debug_dump(url, info, hint="若看到 status=240/‘APP 服务被禁用’，请到 LBS 控制台为该 AK 开通【全景静态图】并检查校验设置。")
                print(f"该分辨率/朝向失败：{w}x{h}, heading={heading}")
        if got_any:
            print(f"分辨率 {w}x{h} 已成功获取至少一张。")
            break

    print("\n完成。输出目录：", OUTDIR.resolve())

if __name__ == "__main__":
    main()
