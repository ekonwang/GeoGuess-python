#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从百度全景静态图 API 拉取南昌市中心（八一广场）高质量街景图片
- 坐标：WGS84 (115.8935, 28.6757)
- 方向：0/90/180/270
- 分辨率：4096×2048 → 2048×1024 → 1024×512（自上而下尝试）
- Debug: 打印 URL、响应头、返回片段、常见排查提示
"""

import os
import time
import math
import pathlib
import argparse
import requests
from typing import Tuple, Optional

# ================== 坐标转换：WGS84 -> BD-09 ==================
x_pi = math.pi * 3000.0 / 180.0
pi = math.pi
a = 6378245.0
ee = 0.00669342162296594323

def _out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

def _transformlat(lng: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0/3.0
    ret += (20.0 * math.sin(lat * pi) + 40.0 * math.sin(lat/3.0 * pi)) * 2.0/3.0
    ret += (160.0 * math.sin(lat/12.0 * pi) + 320 * math.sin(lat * pi / 30.0)) * 2.0/3.0
    return ret

def _transformlng(lng: float, lat: float) -> float:
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * pi) + 20.0 * math.sin(2.0 * lng * pi)) * 2.0/3.0
    ret += (20.0 * math.sin(lng * pi) + 40.0 * math.sin(lng/3.0 * pi)) * 2.0/3.0
    ret += (150.0 * math.sin(lng/12.0 * pi) + 300.0 * math.sin(lng/30.0 * pi)) * 2.0/3.0
    return ret

def wgs84_to_gcj02(lng: float, lat: float) -> Tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
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

def gcj02_to_bd09(lng: float, lat: float) -> Tuple[float, float]:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * x_pi)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * x_pi)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006

def wgs84_to_bd09(lng: float, lat: float) -> Tuple[float, float]:
    g_lng, g_lat = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(g_lng, g_lat)

# ================== HTTP / API ==================
def build_url(bd_lng: float, bd_lat: float, width: int, height: int,
              heading: int, pitch: int, fov: int, ak: str) -> str:
    base = "https://api.map.baidu.com/panorama/v2"
    return (
        f"{base}?ak={ak}"
        f"&width={width}&height={height}"
        f"&location={bd_lng:.6f},{bd_lat:.6f}"
        f"&heading={heading}&pitch={pitch}&fov={fov}"
        f"&coordtype=bd09ll"
    )

def fetch(url: str, timeout: float = 15.0):
    r = requests.get(url, timeout=timeout)
    info = {
        "ok": r.ok,
        "status_code": r.status_code,
        "headers": dict(r.headers),
        "content_type": r.headers.get("Content-Type", ""),
        "text": None,
        "raw": r.content[:1024],
    }
    try:
        info["text"] = r.text[:1024]
    except Exception:
        pass
    return r, info

def debug_dump(url: str, info: dict, extra_hint: Optional[str] = None):
    print("\n[DEBUG] ===== 请求 =====")
    print(url)
    print("[DEBUG] 状态码:", info["status_code"], "ok:", info["ok"])
    print("[DEBUG] Content-Type:", info["content_type"])
    print("[DEBUG] 响应头(部分):", {k: info["headers"].get(k) for k in ["Date","Server","Content-Length","Content-Type"]})
    if info.get("text"):
        print("[DEBUG] 文本片段:", info["text"])
    else:
        print("[DEBUG] 原始前1KB:", info["raw"])
    if extra_hint:
        print("[DEBUG] 提示:", extra_hint)

# ================== 主逻辑 ==================
def main():
    parser = argparse.ArgumentParser(description="Baidu Panorama 静态图抓取 - 南昌八一广场")
    parser.add_argument("--ak", default=os.getenv("BAIDU_AK", "YOUR_BAIDU_AK"), help="百度地图 AK，或设置环境变量 BAIDU_AK")
    parser.add_argument("--lng", type=float, default=115.8935, help="WGS84 经度（默认：南昌八一广场）")
    parser.add_argument("--lat", type=float, default=28.6757, help="WGS84 纬度（默认：南昌八一广场）")
    parser.add_argument("--headings", type=str, default="0,90,180,270", help="朝向，逗号分隔，如 0,90,180,270")
    parser.add_argument("--pitch", type=int, default=10, help="俯仰角")
    parser.add_argument("--fov", type=int, default=120, help="视场角（0-360）")
    parser.add_argument("--res_list", type=str, default="4096x2048,2048x1024,1024x512", help="分辨率候选，从高到低，逗号分隔")
    parser.add_argument("--outdir", type=str, default="baidu_streetview_nanchang", help="输出目录")
    parser.add_argument("--debug", action="store_true", help="开启 Debug 输出")
    args = parser.parse_args()

    BAIDU_AK = args.ak
    assert BAIDU_AK and BAIDU_AK != "YOUR_BAIDU_AK", "请先通过 --ak 或环境变量 BAIDU_AK 配置你的百度 AK！"

    OUTDIR = pathlib.Path(args.outdir)
    OUTDIR.mkdir(parents=True, exist_ok=True)

    headings = [int(x.strip()) for x in args.headings.split(",") if x.strip()]
    res_candidates = []
    for item in args.res_list.split(","):
        w, h = item.lower().split("x")
        res_candidates.append((int(w), int(h)))

    # Debug checklist
    if args.debug:
        print("== Debug Checklist ==")
        print("1) LBS 控制台是否为该 AK 勾选【全景静态图】服务（否则常见 status=240：APP 服务被禁用）")
        print("2) AK 类型与校验（浏览器端需 Referer 白名单；服务端需 IP 白名单或签名）")
        print("3) AK 是否有效、未禁用；是否达到配额/并发限制\n")

    # 坐标转换
    bd_lng, bd_lat = wgs84_to_bd09(args.lng, args.lat)
    print(f"WGS84 -> BD-09: ({args.lng:.6f},{args.lat:.6f}) -> ({bd_lng:.6f},{bd_lat:.6f})")

    got_any = False
    for (w, h) in res_candidates:
        print(f"\n尝试分辨率 {w}x{h} ...")
        round_ok = False
        for heading in headings:
            url = build_url(bd_lng, bd_lat, w, h, heading, args.pitch, args.fov, BAIDU_AK)
            r, info = fetch(url)
            if info["ok"] and info["content_type"].startswith("image"):
                fname = OUTDIR / f"nanchang_bayi_{w}x{h}_h{heading}_p{args.pitch}_f{args.fov}.jpg"
                fname.write_bytes(r.content)
                print(f"Saved: {fname}")
                round_ok = True
                got_any = True
                time.sleep(0.2)  # 友好限速
            else:
                if args.debug:
                    debug_dump(
                        url, info,
                        extra_hint="若见 status=240/‘APP 服务被禁用’，请到 LBS 控制台为该 AK 开通【全景静态图】并检查校验设置。"
                    )
                print(f"该分辨率/朝向失败：{w}x{h}, heading={heading}")

        if round_ok:
            print(f"分辨率 {w}x{h} 已成功获取至少一张。")
            # 若只想拿到“最高成功分辨率”的一轮结果，可在此 break
            break

    if not got_any:
        print("\n未成功获取图片。请按 Debug Checklist 检查 AK 权限/校验，并确认该点附近确有全景。")

    print("\n完成。输出目录：", OUTDIR.resolve())

if __name__ == "__main__":
    main()
