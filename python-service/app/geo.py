from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple, Union

import geojson
import shapely.geometry
import shapely.ops
import requests
import math
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import NOMINATIM_BASE_URL, get_http_session

GeoJSONType = Union[Dict[str, Any], geojson.GeoJSON]

# Cache: city key -> list of small polygon Features
_CITY_SMALL_POLYGONS_CACHE: Dict[str, List[geojson.Feature]] = {}


logger = logging.getLogger(__name__)


def _safe_mkdirs(path: str) -> None:
    try:
        import os
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _to_shapely_geometry(obj: GeoJSONType) -> shapely.geometry.base.BaseGeometry:
    if isinstance(obj, dict):
        gobj = geojson.loads(geojson.dumps(obj))
    else:
        gobj = obj

    if isinstance(gobj, geojson.Feature):
        return shapely.geometry.shape(gobj.geometry)
    if isinstance(gobj, geojson.FeatureCollection):
        geoms = [shapely.geometry.shape(f.geometry) for f in gobj.features]
        return shapely.ops.unary_union(geoms)
    # Geometry
    return shapely.geometry.shape(gobj)


def _random_point_in_bounds(minx: float, miny: float, maxx: float, maxy: float) -> shapely.geometry.Point:
    x = random.uniform(minx, maxx)
    y = random.uniform(miny, maxy)
    return shapely.geometry.Point(x, y)


def random_point_in_polygon(geom: GeoJSONType, max_tries: int = 10_000, center_bias: Optional[float] = None) -> Tuple[float, float]:
    shape = _to_shapely_geometry(geom)
    minx, miny, maxx, maxy = shape.bounds
    centroid = shape.centroid
    # center_bias: 标准差（米）的近似比例；值越小越靠中心，可按 bbox 尺度换算
    for _ in range(max_tries):
        pt = _random_point_in_bounds(minx, miny, maxx, maxy)
        if not shape.contains(pt):
            continue
        if center_bias:
            # 距离中心越远，接受概率越低（近似高斯核）
            d = pt.distance(centroid)
            # bbox 尺度归一化，避免城市大小差异
            scale = max(maxx - minx, maxy - miny)
            accept_p = math.exp(-(d / (center_bias * scale)) ** 2 / 2.0)
            if random.random() > accept_p:
                continue
        return (pt.y, pt.x)
    raise RuntimeError("Failed to sample a point inside polygon after many attempts")


def random_points_in_polygon(geom: GeoJSONType, count: int) -> List[Tuple[float, float]]:
    return [random_point_in_polygon(geom) for _ in range(count)]


def _city_cache_key(city: str, country: Optional[str]) -> str:
    city_key = city.strip().lower()
    country_key = (country or "").strip().lower()
    return f"{city_key},{country_key}" if country_key else city_key


def fetch_city_geojson(city: str, country: Optional[str] = None, focus: Optional[str] = None, method: str = "nominatim_name") -> Dict[str, Any]:
    # 步骤 1：获取全市范围 polygon
    full_feature = _fetch_city_full_geojson(city, country)
    city_shape = _to_shapely_geometry(full_feature)
    minx, miny, maxx, maxy = city_shape.bounds
    # Nominatim: viewbox = left,top,right,bottom
    viewbox = f"{minx},{maxy},{maxx},{miny}"

    # 先查缓存
    cache_key = _city_cache_key(city, country)
    if cache_key in _CITY_SMALL_POLYGONS_CACHE:
        features_cached = _CITY_SMALL_POLYGONS_CACHE[cache_key]
        print(f"[INFO] Cache hit for city '{cache_key}', polygons: {len(features_cached)}")
        return geojson.FeatureCollection(features=features_cached)

    # 步骤 2：使用关键词查找城市中心（小 polygon/multipolygon）
    synonyms = [
        # English
        "downtown", "city centre", "city center", "central business district", "CBD",
        "financial district", "business district", "downtown core", "midtown", "uptown",
        "neighborhood", "neighbourhood", "district", "borough", "subdistrict", "suburb",
        "old town", "historic center", "historic centre", "city core", "inner city",
        "central area", "town centre", "town center",
        # Spanish/Portuguese
        "barrio", "centro histórico", "centro", "bairro",
        # French
        "arrondissement", "centre-ville", "centre ville", "vieux quartier",
        # Chinese/Japanese
        "市中心", "中心城区", "中心商務區", "中央区", "中心商圈", "老城区", "老城",
    ]
    base = f"{NOMINATIM_BASE_URL.rstrip('/')}/search"

    candidates: List[Tuple[float, geojson.Feature]] = []
    seen_geoms: set = set()

    # 并发抓取，限制小并发，避免流量过大
    MAX_WORKERS = 10

    def _fetch_for_syn(syn: str) -> Tuple[str, List[Tuple[shapely.geometry.base.BaseGeometry, geojson.Feature]]]:
        # 轻微抖动，降低突发流量
        time.sleep(random.uniform(0.05, 0.2))
        q = f"{syn} {city}" if not country else f"{syn} {city}, {country}"
        params = {
            "q": q,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "addressdetails": 1,
            "limit": 50,
            "viewbox": viewbox,
            "bounded": 1,
        }
        try:
            session = get_http_session()
            r = session.get(base, params=params, timeout=20)
            r.raise_for_status()
            items = r.json() or []
        except requests.RequestException:
            logger.exception("Nominatim center search failed for query='%s'", q)
            return syn, []
        results: List[Tuple[shapely.geometry.base.BaseGeometry, geojson.Feature]] = []
        for it in items:
            gj = it.get("geojson")
            if not gj or gj.get("type") not in {"Polygon", "MultiPolygon"}:
                continue
            try:
                small_geom = shapely.geometry.shape(gj)
            except Exception:
                continue
            # 过滤：必须完整处于城市 polygon 内部（covers 允许边界重合）
            try:
                if not city_shape.covers(small_geom):
                    continue
            except Exception:
                continue
            feature = geojson.Feature(
                geometry=gj,
                properties={
                    "display_name": it.get("display_name"),
                    "type": it.get("type"),
                    "osm_type": it.get("osm_type"),
                    "osm_id": it.get("osm_id"),
                    "class": it.get("class"),
                    "keyword": syn,
                },
            )
            results.append((small_geom, feature))
        return syn, results

    futures = []
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for syn in synonyms:
                futures.append(executor.submit(_fetch_for_syn, syn))
            for fut in as_completed(futures):
                try:
                    syn, results = fut.result()
                except Exception:
                    logger.exception("Unhandled exception while processing synonym task")
                    continue
                # [INFO] 打印该关键词返回的小多边形情况
                print(f"[INFO] Keyword '{syn}' returned {len(results)} small polygons within '{cache_key}'.")
                # if results:
                    # sample_names = [f.properties.get("display_name") for (_, f) in results]
                    # for name in sample_names[:5]:
                    #     if name:
                    #         print(f"[INFO]   - {name}")
                # 汇总并去重
                for small_geom, feature in results:
                    try:
                        geom_key = small_geom.wkb_hex
                    except Exception:
                        try:
                            geom_key = json.dumps(feature.geometry, sort_keys=True, ensure_ascii=False)
                        except Exception:
                            continue
                    if geom_key in seen_geoms:
                        continue
                    seen_geoms.add(geom_key)
                    area_value = float(small_geom.area)
                    candidates.append((area_value, feature))
    except Exception:
        # 保底兜底，任何并发层面的异常都不影响主流程
        logger.exception("Unexpected error during concurrent synonym fetching")

    if candidates:
        print(f"[INFO] Found {len(candidates)} candidate small polygons within '{cache_key}'.")
    else:
        # print(f"[INFO] No candidate small polygons found within '{cache_key}'. Using full city polygon.")
        # # 回退：使用整市 polygon 作为唯一要素
        # fallback_list = [geojson.Feature(geometry=geojson.loads(geojson.dumps(full_feature["geometry"])) if isinstance(full_feature, dict) else full_feature.geometry, properties={"source": "city_full"})]
        # _CITY_SMALL_POLYGONS_CACHE[cache_key] = fallback_list
        # return geojson.FeatureCollection(features=fallback_list)
        raise ValueError(f"No candidate small polygons found within '{cache_key}'.")

    # 步骤 3：按面积从大到小取前 500 个
    top = [f for _, f in sorted(candidates, key=lambda x: x[0], reverse=True)[:500]]
    print(f"[INFO] City '{cache_key}' small polygons count: {len(candidates)}, returning top {len(top)}.")

    # 写入缓存并返回
    _CITY_SMALL_POLYGONS_CACHE[cache_key] = top
    return geojson.FeatureCollection(features=top)


def save_city_small_polygons_cache(cache_dir: str, city: str, country: Optional[str], features: List[geojson.Feature]) -> str:
    """Save small polygons to a cache file for given city.

    File path: {cache_dir}/{cache_key}.geojson where cache_key is from _city_cache_key.
    Returns the file path.
    """
    import os
    _safe_mkdirs(cache_dir)
    cache_key = _city_cache_key(city, country)
    filepath = os.path.join(cache_dir, f"{cache_key}.geojson")
    try:
        fc = geojson.FeatureCollection(features=features)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False)
        print(f"[INFO] Saved cache for '{cache_key}' -> {filepath} ({len(features)} features)")
    except Exception:
        logger.exception("Failed to save city cache: %s", cache_key)
    return filepath


def load_city_small_polygons_cache(cache_dir: str, city: str, country: Optional[str]) -> bool:
    """Load small polygons from cache file into memory cache. Returns True if loaded."""
    import os
    cache_key = _city_cache_key(city, country)
    filepath = os.path.join(cache_dir, f"{cache_key}.geojson")
    try:
        if not os.path.isfile(filepath):
            return False
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            features = []
            for feat in data.get("features", []):
                try:
                    # normalise to geojson.Feature
                    features.append(geojson.Feature(geometry=feat.get("geometry"), properties=feat.get("properties", {})))
                except Exception:
                    continue
            if features:
                _CITY_SMALL_POLYGONS_CACHE[cache_key] = features
                print(f"[INFO] Loaded cache for '{cache_key}' from file with {len(features)} features")
                return True
    except Exception:
        logger.exception("Failed to load city cache: %s", cache_key)
    return False


def load_all_city_caches_from_dir(cache_dir: str) -> int:
    """Bulk load all *.geojson cache files from directory into memory cache. Returns count loaded."""
    import os
    count = 0
    try:
        if not os.path.isdir(cache_dir):
            return 0
        for name in os.listdir(cache_dir):
            if not name.endswith(".geojson"):
                continue
            filepath = os.path.join(cache_dir, name)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not (isinstance(data, dict) and data.get("type") == "FeatureCollection"):
                    continue
                features = []
                for feat in data.get("features", []):
                    try:
                        features.append(geojson.Feature(geometry=feat.get("geometry"), properties=feat.get("properties", {})))
                    except Exception:
                        continue
                if not features:
                    continue
                # derive cache_key from filename (strip .geojson)
                cache_key = name[:-8]
                _CITY_SMALL_POLYGONS_CACHE[cache_key] = features
                count += 1
            except Exception:
                logger.exception("Failed to load cache file: %s", filepath)
                continue
        if count:
            print(f"[INFO] Loaded {count} city caches from '{cache_dir}'.")
    except Exception:
        logger.exception("Failed to scan cache directory: %s", cache_dir)
    return count


def _fetch_city_full_geojson(city: str, country: Optional[str] = None) -> Dict[str, Any]:
    if not city:
        raise ValueError("city must be provided")

    query = city if not country else f"{city}, {country}"

    params = {
        "q": query,
        "format": "jsonv2",
        "polygon_geojson": 1,
        "addressdetails": 1,
        "limit": 1,
    }

    session = get_http_session()
    url = f"{NOMINATIM_BASE_URL.rstrip('/')}/search"
    try:
        response = session.get(url, params=params, timeout=20)
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as exc:
        logger.exception("Nominatim name search failed for query='%s'", query)
        raise

    if not results:
        raise ValueError(f"No results for city query: {query}")

    print(f"[INFO] Nominatim name search in query {query} returned {len(results)} results.")
    # print(json.dumps(results, indent=2, ensure_ascii=False))
    top = results[0]
    if "geojson" not in top:
        raise ValueError("City result does not include polygon geojson")

    # Return as Feature
    return geojson.Feature(geometry=top["geojson"], properties={
        "display_name": top.get("display_name"),
        "type": top.get("type"),
        "osm_type": top.get("osm_type"),
        "osm_id": top.get("osm_id"),
        "class": top.get("class"),
    })


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Fetch small polygons inside a city and sample random points")
    parser.add_argument("--city", required=True, help="City name, e.g., shanghai")
    parser.add_argument("--country", default=None, help="Optional country name, e.g., china")
    parser.add_argument("--count", type=int, default=5, help="Number of random points to sample")
    parser.add_argument("--center-bias", dest="center_bias", type=float, default=None, help="Smaller means more bias towards center (relative to bbox scale)")
    args = parser.parse_args()

    fc = fetch_city_geojson(args.city, args.country)
    num_features = len(fc.features) if isinstance(fc, geojson.FeatureCollection) else 1
    city_label = args.city if not args.country else f"{args.city}, {args.country}"

    print(f"[TEST] Retrieved {num_features} small polygons for '{city_label}'.")

    points = []
    for _ in range(max(1, args.count)):
        lat, lng = random_point_in_polygon(fc, center_bias=args.center_bias)
        points.append({"lat": lat, "lng": lng})

    print(json.dumps({
        "city": args.city,
        "country": args.country,
        "count": args.count,
        "points": points,
    }, ensure_ascii=False, indent=2))
