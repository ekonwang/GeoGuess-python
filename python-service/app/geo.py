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

from .config import NOMINATIM_BASE_URL, get_http_session

GeoJSONType = Union[Dict[str, Any], geojson.GeoJSON]

# Cache: city key -> list of small polygon Features
_CITY_SMALL_POLYGONS_CACHE: Dict[str, List[geojson.Feature]] = {}


logger = logging.getLogger(__name__)


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

    # 先查缓存
    cache_key = _city_cache_key(city, country)
    if cache_key in _CITY_SMALL_POLYGONS_CACHE:
        features_cached = _CITY_SMALL_POLYGONS_CACHE[cache_key]
        print(f"[INFO] Cache hit for city '{cache_key}', polygons: {len(features_cached)}")
        return geojson.FeatureCollection(features=features_cached)

    # 步骤 2：使用关键词查找城市中心（小 polygon/multipolygon）
    synonyms = [
        "downtown", "city centre", "central business district", "CBD",
        "city center", "市中心", "中心城区", "中心商務區", "中央区", "mall",
    ]
    session = get_http_session()
    base = f"{NOMINATIM_BASE_URL.rstrip('/')}/search"

    candidates: List[Tuple[float, geojson.Feature]] = []
    seen_geoms: set = set()

    for syn in synonyms:
        q = f"{syn} {city}" if not country else f"{syn} {city}, {country}"
        params = {"q": q, "format": "jsonv2", "polygon_geojson": 1, "addressdetails": 1, "limit": 5}
        try:
            r = session.get(base, params=params, timeout=20)
            r.raise_for_status()
            items = r.json() or []
        except requests.RequestException as exc:
            logger.exception("Nominatim center search failed for query='%s'", q)
            # Continue to next synonym on transient/network error
            continue
        for it in items:
            gj = it.get("geojson")
            if not gj or gj.get("type") not in {"Polygon", "MultiPolygon"}:
                continue
            try:
                small_geom = shapely.geometry.shape(gj)
            except Exception:
                continue
            # 过滤：必须完整处于城市 polygon 内部（covers 允许边界重合）
            if not city_shape.covers(small_geom):
                continue
            # 去重：基于 WKB
            try:
                geom_key = small_geom.wkb_hex
            except Exception:
                geom_key = json.dumps(gj, sort_keys=True, ensure_ascii=False)
            if geom_key in seen_geoms:
                continue
            seen_geoms.add(geom_key)

            area_value = float(small_geom.area)
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
            candidates.append((area_value, feature))

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
