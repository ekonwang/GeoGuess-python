from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Tuple, Union

import geojson
import shapely.geometry
import shapely.ops
import requests

from .config import NOMINATIM_BASE_URL, get_http_session

GeoJSONType = Union[Dict[str, Any], geojson.GeoJSON]


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


def random_point_in_polygon(geom: GeoJSONType, max_tries: int = 10_000) -> Tuple[float, float]:
    shape = _to_shapely_geometry(geom)
    minx, miny, maxx, maxy = shape.bounds

    for _ in range(max_tries):
        pt = _random_point_in_bounds(minx, miny, maxx, maxy)
        if shape.contains(pt):
            return (pt.y, pt.x)  # lat, lng
    raise RuntimeError("Failed to sample a point inside polygon after many attempts")


def random_points_in_polygon(geom: GeoJSONType, count: int) -> List[Tuple[float, float]]:
    return [random_point_in_polygon(geom) for _ in range(count)]


def fetch_city_geojson(city: str, country: Optional[str] = None) -> Dict[str, Any]:
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
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    results = response.json()

    if not results:
        raise ValueError(f"No results for city query: {query}")

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