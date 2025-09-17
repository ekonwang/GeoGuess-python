#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import random
import sys
import time
from pathlib import Path

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MAPILLARY_IMAGES_URL = "https://graph.mapillary.com/images"

UA = "MapillaryPanoramaFetcher/1.0 (contact: youremail@example.com)"

def geocode_city_bbox(city: str):
    """
    用 Nominatim 获取城市边界 bbox（west, south, east, north）
    限定中国（countrycodes=cn），避免歧义
    """
    params = {
        "q": city,
        "countrycodes": "cn",
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
        "polygon": 0,
    }
    headers = {"User-Agent": UA}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise RuntimeError(f"未找到城市：{city}")
    bbox = data[0]["boundingbox"]  # [south, north, west, east] (strings)
    south, north, west, east = map(float, [bbox[0], bbox[1], bbox[2], bbox[3]])
    return west, south, east, north

def search_random_pano_in_bbox(token: str, bbox: tuple, tries: int = 5, page_limit: int = 200):
    """
    在 bbox 内搜索全景图，并随机返回一条。
    若 bbox 过大导致超时或结果为 0，尝试缩小随机子框多次。
    """
    w, s, e, n = bbox
    headers = {"Authorization": f"OAuth {token}", "User-Agent": UA}

    for attempt in range(1, tries + 1):
        # 随机取一个子框以减小超时概率
        lon1 = random.uniform(w, e)
        lon2 = random.uniform(w, e)
        lat1 = random.uniform(s, n)
        lat2 = random.uniform(s, n)
        sub_w, sub_e = sorted([lon1, lon2])
        sub_s, sub_n = sorted([lat1, lat2])

        params = {
            "bbox": f"{sub_w},{sub_s},{sub_e},{sub_n}",
            "is_pano": "true",
            # 需要的字段：id / thumb_2048_url / computed_geometry
            "fields": "id,thumb_2048_url,computed_geometry",
            # 一次多拿一些，之后随机挑
            "limit": str(page_limit),
        }

        try:
            resp = requests.get(MAPILLARY_IMAGES_URL, headers=headers, params=params, timeout=60)
            # Mapillary 偶尔会返回 400 并提示 query timeout；此处直接重试
            if resp.status_code >= 400:
                # 小睡一下再换个子框
                time.sleep(0.8)
                continue
            data = resp.json()
            results = data.get("data", [])
            results = [it for it in results if it.get("thumb_2048_url")]
            if results:
                return random.choice(results)
        except requests.RequestException:
            time.sleep(0.8)
            continue

    return None

def download_image(url: str, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, headers={"User-Agent": UA}, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def main():
    parser = argparse.ArgumentParser(description="随机下载指定中国大陆城市的一张 Mapillary 全景图（pano）")
    parser.add_argument("city", help="城市名，例如：上海、北京市、广州市")
    parser.add_argument("output", help="输出文件路径，例如：/path/to/output.jpg")
    parser.add_argument("--token", required=True, help="Mapillary Access Token（以 MLY| 开头）")
    parser.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    parser.add_argument("--tries", type=int, default=7, help="搜索尝试次数（默认 7）")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    try:
        print(f"→ 正在解析城市边界：{args.city}")
        bbox = geocode_city_bbox(args.city)
        print(f"   获得 bbox: {bbox}")
    except Exception as e:
        print(f"地理编码失败：{e}", file=sys.stderr)
        sys.exit(1)

    print("→ 正在搜索全景图（pano）…")
    hit = search_random_pano_in_bbox(args.token, bbox, tries=args.tries)
    if not hit:
        print("未找到全景图；可能该城市覆盖有限，试试更大的城市名或提高 --tries。", file=sys.stderr)
        sys.exit(2)

    image_id = hit["id"]
    img_url = hit["thumb_2048_url"]  # 足够清晰；若需要更大可改成 thumb_original_url（若有）
    coords = hit.get("computed_geometry", {}).get("coordinates", None)
    print(f"   命中 image_id={image_id}  坐标={coords}  下载源={img_url}")

    out_path = Path(args.output)
    try:
        download_image(img_url, out_path)
        print(f"✓ 已保存到：{out_path.resolve()}")
    except Exception as e:
        print(f"下载失败：{e}", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()
