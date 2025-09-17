#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate whether provided "city center" coordinates fall inside each city's administrative boundary (Mainland China).
Data source: OpenStreetMap (Nominatim + Overpass).
Output: prints a Python dict {city: "lat,lon"} containing only cities that pass validation.

Usage:
    python validate_city_centers_cn.py

Dependencies:
    pip install requests shapely

Politeness:
    Export an email for Nominatim per usage policy:
        export NOMINATIM_EMAIL="you@example.com"
    This script throttles requests and caches responses to ./cache.
"""

import os
import json
import time
import sys
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from shapely.geometry import shape, Point, Polygon, MultiPolygon

# ---------------------- Configuration ----------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = f"city-center-validator/1.0 (+{os.environ.get('NOMINATIM_EMAIL','no-email-set')})"
CACHE_DIR = Path("./cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

REQUEST_SLEEP = 1.0  # seconds between requests (be nice)
TIMEOUT = 60

# Prefer these admin levels for "prefecture-level cities"; sometimes 5/6/7 appear.
PREFERRED_ADMIN_LEVELS = {"4", "5", "6", "7"}

# Some cities need alias mapping (English -> Chinese names) to improve Overpass/Nominatim hit rate
ALIASES_ZH = {
    "Xi'an": "西安市",
    "Ürümqi": "乌鲁木齐市",
    "Urumqi": "乌鲁木齐市",
    "Tai'an": "泰安市",
    "Huai'an": "淮安市",
    "Changsha": "长沙市",
    "Shijiazhuang": "石家庄市",
    "Nanjing": "南京市",
    "Beijing": "北京市",
    "Tianjin": "天津市",
    "Shanghai": "上海市",
    "Chongqing": "重庆市",
    "Hohhot": "呼和浩特市",
    "Xining": "西宁市",
    "Yinchuan": "银川市",
    "Harbin": "哈尔滨市",
    "Shenyang": "沈阳市",
    "Jilin": "吉林市",
    "Daqing": "大庆市",
    "Changchun": "长春市",
    "Qingdao": "青岛市",
    "Zibo": "淄博市",
    "Weihai": "威海市",
    "Yantai": "烟台市",
    "Jinan": "济南市",
    "Taiyuan": "太原市",
    "Datong": "大同市",
    "Handan": "邯郸市",
    "Tangshan": "唐山市",
    "Zhangjiakou": "张家口市",
    "Langfang": "廊坊市",
    "Fuzhou": "福州市",
    "Quanzhou": "泉州市",
    "Xiamen": "厦门市",
    "Zhuhai": "珠海市",
    "Shantou": "汕头市",
    "Shenzhen": "深圳市",
    "Guangzhou": "广州市",
    "Foshan": "佛山市",
    "Dongguan": "东莞市",
    "Jiangmen": "江门市",
    "Maoming": "茂名市",
    "Zhanjiang": "湛江市",
    "Chaozhou": "潮州市",
    "Jieyang": "揭阳市",
    "Huizhou": "惠州市",
    "Wuxi": "无锡市",
    "Suzhou": "苏州市",
    "Changzhou": "常州市",
    "Kunshan": "昆山市",
    "Yiwu": "义乌市",
    "Nantong": "南通市",
    "Yangzhou": "扬州市",
    "Taizhou": "泰州市",
    "Jiangyin": "江阴市",
    "Hangzhou": "杭州市",
    "Shaoxing": "绍兴市",
    "Ningbo": "宁波市",
    "Wenzhou": "温州市",
    "Hefei": "合肥市",
    "Huainan": "淮南市",
    "Wuhu": "芜湖市",
    "Nanchang": "南昌市",
    "Ganzhou": "赣州市",
    "Shangrao": "上饶市",
    "Lanzhou": "兰州市",
    "Xuzhou": "徐州市",
    "Linyi": "临沂市",
    "Weifang": "潍坊市",
    "Zaozhuang": "枣庄市",
    "Qinhuangdao": "秦皇岛市",
    "Baoding": "保定市",
    "Luoyang": "洛阳市",
    "Zhengzhou": "郑州市",
    "Xinxiang": "新乡市",
    "Nanyang": "南阳市",
    "Yichang": "宜昌市",
    "Wuhan": "武汉市",
    "Xiangyang": "襄阳市",
    "Ezhou": "鄂州市",
    "Changde": "常德市",
    "Zhuzhou": "株洲市",
    "Changsha": "长沙市",
    "Mianyang": "绵阳市",
    "Chengdu": "成都市",
    "Guiyang": "贵阳市",
    "Zunyi": "遵义市",
    "Kunming": "昆明市",
    "Nanning": "南宁市",
    "Liuzhou": "柳州市",
    "Guilin": "桂林市",
    "Haikou": "海口市",
    "Hohhot": "呼和浩特市",
    "Baotou": "包头市",
    "Yinchuan": "银川市",
}

# ---------------------- Input City -> Center Dict ----------------------
CITY_CENTERS = {
  "Shanghai": "31.2304,121.4737",
  "Beijing": "39.9042,116.4074",
  "Shenzhen": "22.5431,114.0579",
  "Guangzhou": "23.1291,113.2644",
  "Chengdu": "30.5728,104.0668",
  "Tianjin": "39.1256,117.1902",
  "Wuhan": "30.5928,114.3055",
  "Dongguan": "23.0207,113.7518",
  "Chongqing": "29.5630,106.5516",
  "Xi'an": "34.3416,108.9398",
  "Hangzhou": "30.2741,120.1551",
  "Foshan": "23.0213,113.1214",
  "Nanjing": "32.0603,118.7969",
  "Shenyang": "41.8057,123.4315",
  "Zhengzhou": "34.7473,113.6249",
  "Qingdao": "36.0671,120.3826",
  "Suzhou": "31.2989,120.5853",
  "Jinan": "36.6512,117.1201",
  "Changsha": "28.2282,112.9388",
  "Kunming": "25.0438,102.7183",
  "Harbin": "45.8038,126.5350",
  "Shijiazhuang": "38.0428,114.5149",
  "Hefei": "31.8206,117.2272",
  "Dalian": "38.9140,121.6147",
  "Xiamen": "24.4798,118.0894",
  "Nanning": "22.8170,108.3669",
  "Changchun": "43.8171,125.3235",
  "Taiyuan": "37.8706,112.5489",
  "Guiyang": "26.6470,106.6302",
  "Wuxi": "31.4912,120.3119",
  "Ürümqi": "43.8256,87.6168",
  "Zhongshan": "22.5170,113.3928",
  "Shantou": "23.3533,116.6822",
  "Ningbo": "29.8683,121.5440",
  "Fuzhou": "26.0745,119.2965",
  "Nanchang": "28.6829,115.8582",
  "Changzhou": "31.8110,119.9740",
  "Lanzhou": "36.0611,103.8343",
  "Nantong": "32.0162,120.8646",
  "Huizhou": "23.1107,114.4158",
  "Xuzhou": "34.2044,117.2857",
  "Zibo": "36.8135,118.0549",
  "Linyi": "35.1047,118.3564",
  "Wenzhou": "27.9949,120.6993",
  "Tangshan": "39.6309,118.1802",
  "Hohhot": "40.8426,111.7492",
  "Haikou": "20.0440,110.1983",
  "Shaoxing": "30.0298,120.5802",
  "Yantai": "37.4638,121.4479",
  "Luoyang": "34.6197,112.4540",
  "Zhuhai": "22.2707,113.5767",
  "Liuzhou": "24.3264,109.4280",
  "Baotou": "40.6574,109.8403",
  "Handan": "36.6256,114.5391",
  "Yangzhou": "32.3936,119.4127",
  "Weifang": "36.7069,119.1619",
  "Baoding": "38.8719,115.4587",
  "Datong": "40.0900,113.2910",
  "Huai'an": "33.6104,119.0153",
  "Jiangmen": "22.5787,113.0819",
  "Ganzhou": "25.8311,114.9350",
  "Jining": "35.4149,116.5872",
  "Xiangyang": "32.0089,112.1229",
  "Xining": "36.6171,101.7782",
  "Zunyi": "27.7058,106.9373",
  "Yinchuan": "38.4872,106.2309",
  "Kunshan": "31.3776,120.9540",
  "Daqing": "46.5907,125.1038",
  "Wuhu": "31.3525,118.4329",
  "Mianyang": "31.4675,104.6796",
  "Putian": "25.4540,119.0078",
  "Qinhuangdao": "39.9354,119.5996",
  "Zhuzhou": "27.8274,113.1339",
  "Jilin": "43.8378,126.5494",
  "Taizhou": "32.4555,119.9255",
  "Yiwu": "29.3151,120.0768",
  "Xingtai": "37.0706,114.5044",
  "Anshan": "41.1078,122.9956",
  "Quanzhou": "24.8741,118.6757",
  "Cixi": "30.1696,121.2660",
  "Tai'an": "36.2009,117.1205",
  "Jinjiang": "24.7817,118.5529",
  "Nanyang": "32.9907,112.5283",
  "Zhanjiang": "21.2707,110.3593",
  "Guilin": "25.2742,110.2991",
  "Yancheng": "33.3473,120.1636",
  "Zaozhuang": "34.8107,117.3230",
  "Shangrao": "28.4547,117.9434",
  "Weihai": "37.5097,122.1164",
  "Zhangjiakou": "40.8244,114.8863",
  "Jiangyin": "31.9119,120.2630",
  "Maoming": "21.6629,110.9255",
  "Heze": "35.2336,115.4812",
  "Yichang": "30.6919,111.2865",
  "Xinxiang": "35.3026,113.9268",
  "Huainan": "32.6595,117.0064",
  "Nanchong": "30.8373,106.1107",
  "Chaozhou": "23.6617,116.6301",
  "Jieyang": "23.5497,116.3728",
  "Changshu": "31.6520,120.7422"
}

# ---------------------- Helpers ----------------------

def cache_get(key: str) -> Optional[dict]:
    h = hashlib.sha256(key.encode('utf-8')).hexdigest()
    p = CACHE_DIR / f"{h}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None

def cache_set(key: str, data: dict) -> None:
    h = hashlib.sha256(key.encode('utf-8')).hexdigest()
    p = CACHE_DIR / f"{h}.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

def http_get(url: str, params: dict) -> dict:
    key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    cached = cache_get(key)
    if cached is not None:
        return cached
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    cache_set(key, data)
    time.sleep(REQUEST_SLEEP)
    return data

def nominatim_search_boundary(city: str) -> Optional[dict]:
    """Try to get a boundary GeoJSON via Nominatim search with polygon_geojson=1."""
    params = {
        "format": "json",
        "limit": 5,
        "country": "China",
        "polygon_geojson": 1,
        "addressdetails": 1,
        "q": city,
        # featuretype filter is optional; some cities are 'city', others 'town' or 'administrative'
    }
    email = os.environ.get("NOMINATIM_EMAIL")
    if email:
        params["email"] = email

    try:
        results = http_get(NOMINATIM_URL, params)
        print(results)
    except Exception as e:
        return None

    # Prefer class=boundary or type=administrative, and admin_level in preferred set
    best = None
    for item in results:
        cls = item.get("class")
        typ = item.get("type")
        addr = item.get("address", {})
        if addr.get("country_code") not in (None, "cn"):
            # Not China country code -> deprioritize
            continue
        importance = item.get("importance", 0)
        admin_level = item.get("extratags", {}).get("admin_level")
        if cls == "boundary" and (typ in ("administrative", "political") or admin_level in PREFERRED_ADMIN_LEVELS):
            # strong candidate
            score = 10 + importance
        elif cls in ("place", "boundary"):
            score = 5 + importance
        else:
            score = importance
        # Slight boost if name matches exactly (en or local)
        if item.get("display_name","").lower().startswith(city.lower()):
            score += 2
        # Keep best
        if best is None or score > best[0]:
            best = (score, item)

    return best[1] if best else None

def overpass_fetch_boundary(city: str, zh_alias: Optional[str]) -> Optional[dict]:
    """Fallback: query Overpass to retrieve the administrative boundary relation as GeoJSON."""
    # We search within China area for a relation with boundary=administrative and name or name:en match
    # Try both English and Chinese names.
    name_filters = []
    name_filters.append(f'["name:en"="{city}"]')
    name_filters.append(f'["name"="{city}"]')
    if zh_alias:
        name_filters.append(f'["name"="{zh_alias}"]')
        name_filters.append(f'["name:zh"="{zh_alias}"]')

    name_filter = "".join(name_filters)

    query = f"""
    [out:json][timeout:60];
    area["name"="China"]["boundary"="administrative"]->.cn;
    (
      relation(area.cn)["boundary"="administrative"]{name_filter};
    );
    out body;
    >;
    out skel qt;
    """
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        time.sleep(REQUEST_SLEEP)
    except Exception:
        return None

    # Convert to GeoJSON via Overpass 'elements' (relations + ways + nodes)
    try:
        from shapely.geometry import mapping
        # A lightweight reconstruction: find relation(s) with tags and 'members' ways to form MultiPolygon via osm2geojson-like build
        # For simplicity and robustness, we'll prefer 'geometries' from Nominatim; Overpass reconstruction is complex.
        # Here we'll try to leverage Nominatim again using the relation id(s) if available.
        rels = [el for el in data.get("elements", []) if el.get("type") == "relation"]
        if not rels:
            return None
        # Choose the first with preferred admin_level
        rels_sorted = sorted(rels, key=lambda r: ("admin_level" in r.get("tags", {}) and r["tags"]["admin_level"] in PREFERRED_ADMIN_LEVELS, r.get("id")), reverse=True)
        rel = rels_sorted[0]
        osm_id = rel["id"]
        # Use Nominatim lookup to fetch polygon for this OSM object
        lookup_params = {
            "format": "json",
            "polygon_geojson": 1,
            "osm_ids": f"R{osm_id}",
        }
        email = os.environ.get("NOMINATIM_EMAIL")
        if email:
            lookup_params["email"] = email
        lookup_url = "https://nominatim.openstreetmap.org/lookup"
        lookup = http_get(lookup_url, lookup_params)
        if isinstance(lookup, list) and lookup:
            return lookup[0]
        return None
    except Exception:
        return None

def extract_shape(geojson_obj: dict):
    """Return a Shapely shape from a Nominatim result item with 'geojson' field."""
    gj = geojson_obj.get("geojson") or geojson_obj.get("GeoJSON") or geojson_obj.get("polygon_geojson")
    if not gj:
        return None
    try:
        geom = shape(gj)
        return geom
    except Exception:
        return None

def latlon_from_str(s: str) -> Tuple[float, float]:
    lat_str, lon_str = s.split(",")
    return float(lat_str.strip()), float(lon_str.strip())

def validate_city(city: str, lat: float, lon: float) -> Tuple[bool, Optional[str]]:
    """Validate if a point (lat, lon) lies within city's administrative boundary polygon."""
    # 1) Try Nominatim search first
    item = nominatim_search_boundary(city)
    geom = extract_shape(item) if item else None

    # 2) Fallback to Overpass -> Nominatim lookup for relation polygon
    if geom is None:
        zh_alias = ALIASES_ZH.get(city)
        item2 = overpass_fetch_boundary(city, zh_alias)
        geom = extract_shape(item2) if item2 else None

    if geom is None:
        return False, "boundary_not_found"

    pt = Point(lon, lat)  # Note: shapely uses (x=lon, y=lat)
    contains = geom.contains(pt) or geom.buffer(0).contains(pt)  # buffer(0) to fix potential topology issues
    if contains:
        return True, None
    else:
        # Some center points may lie very near boundary; allow small tolerance by checking distance < ~1km
        # Note: Distance requires projected CRS; as a rough check, use degrees ~ 0.01 ≈ 1.1km at mid-latitudes
        try:
            dist_deg = geom.boundary.distance(pt)
            if dist_deg < 0.01:
                return True, "near_boundary"
        except Exception:
            pass
        return False, "outside_boundary"

def main():
    passed: Dict[str, str] = {}
    failed: Dict[str, str] = {}
    skipped: Dict[str, str] = {}

    for city, coord in CITY_CENTERS.items():
        try:
            lat, lon = latlon_from_str(coord)
        except Exception:
            failed[city] = "invalid_coord_format"
            continue

        ok, reason = validate_city(city, lat, lon)
        if ok:
            passed[city] = coord
        else:
            failed[city] = reason or "unknown"

        # Flush progress
        print(f"[{city}] -> {'PASS' if ok else 'FAIL'}{(' ('+reason+')') if reason else ''}")

    print("\n# ---- PASSED DICT ----")
    # Print as a Python dict literal with the requested "lat,lon" string values
    print("{")
    first = True
    for k, v in passed.items():
        comma = "," if not first else ""
        print(f'{comma}"{k}": "{v}"')
        first = False
    print("}")

    # Also write JSON files
    Path("./config/0917_validated_passed.json").write_text(json.dumps(passed, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("./config/0917_validated_failed.json").write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSaved: ./config/0917_validated_passed.json")

if __name__ == "__main__":
    main()
