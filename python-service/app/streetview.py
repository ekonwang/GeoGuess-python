from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import requests

from .config import GOOGLE_MAPS_API_KEY, get_http_session
from .geo import random_point_in_polygon, fetch_city_geojson


class StreetViewError(Exception):
    pass


def _build_sources_param(all_panorama: bool) -> Optional[str]:
    # Web service uses `source=default|outdoor`. No `google` option here.
    return "default" if not all_panorama else None  # None = let API decide, closer to JS list


def street_view_metadata(lat: float, lng: float, radius: int = 50, all_panorama: bool = False, api_key: Optional[str] = None) -> Dict[str, Any]:
    key = api_key or GOOGLE_MAPS_API_KEY
    if not key:
        raise StreetViewError("GOOGLE_MAPS_API_KEY not configured")

    session = get_http_session()
    params: Dict[str, Any] = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "key": key,
    }
    source = _build_sources_param(all_panorama)
    if source:
        params["source"] = source

    url = "https://maps.googleapis.com/maps/api/streetview/metadata"
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def is_metadata_acceptable(metadata: Dict[str, Any], optimise: bool) -> bool:
    if not metadata or metadata.get("status") != "OK":
        return False
    if optimise:
        # Approximate optimisation similar to frontend (no `links` field available here)
        # Require an image_date when optimising
        if not metadata.get("date") and not metadata.get("image_date"):
            return False
    return True


def global_random_lat_lng() -> Tuple[float, float]:
    import random

    # Uniform sampling on a sphere: pick z in [-1, 1], longitude uniform
    z = 2.0 * random.random() - 1.0
    lat = math.degrees(math.asin(z))
    lng = 360.0 * random.random() - 180.0
    return lat, lng


def find_streetview_random(
    *,
    geojson_area: Optional[Dict[str, Any]] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    all_panorama: bool = False,
    optimise: bool = True,
    max_attempts: int = 10,
    radius: Optional[int] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    if geojson_area is None and city:
        geojson_area = fetch_city_geojson(city, country)

    attempt = 0
    last_metadata: Optional[Dict[str, Any]] = None

    while attempt < max_attempts:
        attempt += 1
        if geojson_area is not None:
            lat, lng = random_point_in_polygon(geojson_area)
            effective_radius = radius or _estimate_radius_from_area(geojson_area)
        else:
            lat, lng = global_random_lat_lng()
            effective_radius = radius or 100000

        metadata = street_view_metadata(lat, lng, radius=effective_radius, all_panorama=all_panorama, api_key=api_key)
        last_metadata = metadata
        if is_metadata_acceptable(metadata, optimise=optimise):
            return {
                "latitude": lat,
                "longitude": lng,
                "radius": effective_radius,
                "metadata": metadata,
                "attempts": attempt,
            }

    raise StreetViewError(f"No Street View found after {max_attempts} attempts; last status={last_metadata and last_metadata.get('status')}")


def _estimate_radius_from_area(geojson_area: Dict[str, Any]) -> int:
    from shapely.geometry import shape, GeometryCollection
    from shapely.ops import unary_union

    geom_input = geojson_area
    geom = None

    if isinstance(geom_input, dict) and geom_input.get("type") == "Feature":
        geom = shape(geom_input["geometry"])
    elif isinstance(geom_input, dict) and geom_input.get("type") == "FeatureCollection":
        geoms = []
        for f in geom_input.get("features", []):
            try:
                geoms.append(shape(f["geometry"]))
            except Exception:
                continue
        geom = unary_union(geoms) if geoms else GeometryCollection()
    else:
        geom = shape(geom_input)

    minx, miny, maxx, maxy = geom.bounds
    # Rough heuristic similar to bbox span times factor
    dx = abs(maxx - minx)
    dy = abs(maxy - miny)
    # 1 degree ~ 111km. Use a factor to widen search a bit.
    km_span = max(dx, dy) * 111.0
    return max(50, int(km_span * 10)) 