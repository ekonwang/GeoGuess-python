#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Street View pano lister via Google Map Tiles API + OSM (Nominatim).
- Robust retries with backoff
- Optional bypass of proxies for Google domains (avoid TLS handshake timeout)
- Exports CSV + GeoJSON
"""

import argparse
import csv
import json
import math
import sys
import time
from typing import List, Tuple, Dict, Any, Optional

import requests
from shapely.geometry import shape, Polygon, MultiPolygon, Point
from shapely.ops import unary_union
from pyproj import Transformer
from tqdm import tqdm

# ----------------------------
# Endpoints
# ----------------------------
NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
TILES_CREATE_SESSION = "https://tile.googleapis.com/v1/createSession"
TILES_PANOIDS = "https://tile.googleapis.com/v1/streetview/panoIds"
TILES_METADATA = "https://tile.googleapis.com/v1/streetview/metadata"


# ----------------------------
# HTTP helpers (retries/backoff)
# ----------------------------
def _http_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retries: int = 5,
    backoff: float = 1.5,
    statuses_retry=(429, 500, 502, 503, 504),
    timeout: int = 60,
    **kwargs
) -> requests.Response:
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code in statuses_retry:
                time.sleep((attempt + 1) * backoff)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep((attempt + 1) * backoff)
    if isinstance(last_err, Exception):
        raise last_err
    raise RuntimeError("HTTP request failed without error detail")


# ----------------------------
# Nominatim (OSM)
# ----------------------------
def fetch_place_polygon(
    session: requests.Session,
    place_query: str,
    user_agent: str,
    timeout: int = 60,
    retries: int = 5,
) -> Polygon | MultiPolygon:
    headers = {"User-Agent": user_agent}
    params = {
        "q": place_query,
        "format": "jsonv2",
        "polygon_geojson": 1,
        "addressdetails": 1,
        "limit": 10,
    }
    resp = _http_with_retries(
        session,
        "GET",
        NOMINATIM_ENDPOINT,
        params=params,
        headers=headers,
        timeout=timeout,
        retries=retries,
    )
    items = resp.json()

    def score(item):
        cls = item.get("class")
        typ = item.get("type")
        has_poly = "geojson" in item
        rank = item.get("place_rank", 0)
        admin = 1 if cls == "boundary" and typ in {"administrative", "political"} else 0
        return (admin, has_poly, rank)

    items = sorted(items, key=score, reverse=True)
    for it in items:
        if "geojson" in it:
            geom = shape(it["geojson"])
            if isinstance(geom, (Polygon, MultiPolygon)):
                return geom
    raise RuntimeError(f"Could not find polygon for '{place_query}'")


def fetch_district_union(
    session: requests.Session,
    city_query: str,
    district_names: List[str],
    user_agent: str,
    timeout: int = 60,
    retries: int = 5,
) -> Polygon | MultiPolygon:
    polys = []
    for name in district_names:
        q = f"{name}, {city_query}"
        try:
            poly = fetch_place_polygon(
                session, q, user_agent=user_agent, timeout=timeout, retries=retries
            )
            polys.append(poly)
        except Exception as e:
            print(f"[WARN] District '{q}' polygon not found: {e}", file=sys.stderr)
    if not polys:
        raise RuntimeError("No district polygons found; try without --districts or check names.")
    return unary_union(polys)


# ----------------------------
# Grid generation
# ----------------------------
def polygon_to_3857(poly: Polygon | MultiPolygon) -> Tuple[Polygon | MultiPolygon, Transformer, Transformer]:
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    inv = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

    def proj_geom(g):
        if isinstance(g, Polygon):
            return Polygon(
                [fwd.transform(x, y) for x, y in g.exterior.coords],
                holes=[[fwd.transform(x, y) for x, y in ring.coords] for ring in g.interiors],
            )
        elif isinstance(g, MultiPolygon):
            return MultiPolygon([proj_geom(p) for p in g.geoms])
        else:
            raise TypeError("Unsupported geometry type.")

    return proj_geom(poly), fwd, inv


def generate_grid_points(poly: Polygon | MultiPolygon, spacing_m: float, inv: Transformer) -> List[Tuple[float, float]]:
    minx, miny, maxx, maxy = poly.bounds
    xs = int(math.ceil((maxx - minx) / spacing_m))
    ys = int(math.ceil((maxy - miny) / spacing_m))

    points = []
    for iy in range(ys + 1):
        y = miny + iy * spacing_m
        x_range = range(xs + 1) if (iy % 2 == 0) else range(xs, -1, -1)
        for ix in x_range:
            x = minx + ix * spacing_m
            if poly.contains(Point(x, y)):
                lon, lat = inv.transform(x, y)
                points.append((lat, lon))
    return points


# ----------------------------
# Google Map Tiles API
# ----------------------------
def create_tiles_session(
    session: requests.Session,
    api_key: str,
    referer: Optional[str] = None,
    language: str = "zh-CN",
    region: str = "CN",
    timeout: int = 60,
    retries: int = 5,
) -> str:
    headers = {"Content-Type": "application/json"}
    if referer:
        headers["Referer"] = referer
    payload = {"mapType": "streetview", "language": language, "region": region}
    url = f"{TILES_CREATE_SESSION}?key={api_key}"
    r = _http_with_retries(
        session,
        "POST",
        url,
        json=payload,
        headers=headers,
        timeout=timeout,
        retries=retries,
    )
    data = r.json()
    session_token = data.get("session", {}).get("sessionToken") or data.get("sessionToken") or data.get("session")
    if not session_token:
        raise RuntimeError(f"Failed to create session: {data}")
    return session_token


def fetch_panoids_for_points(
    session: requests.Session,
    api_key: str,
    session_token: str,
    points: List[Tuple[float, float]],
    radius_m: int,
    timeout: int = 60,
    retries: int = 5,
    sleep_s: float = 0.1,
) -> List[Dict[str, Any]]:
    out = []
    headers = {"Content-Type": "application/json"}

    def chunked(iterable, n):
        buf = []
        for item in iterable:
            buf.append(item)
            if len(buf) >= n:
                yield buf
                buf = []
        if buf:
            yield buf

    for batch in tqdm(list(chunked(points, 100)), desc="Query panoIds", unit="batch"):
        body = {
            "locations": [{"lat": lat, "lng": lng} for (lat, lng) in batch],
            "radius": radius_m,
        }
        url = f"{TILES_PANOIDS}?session={session_token}&key={api_key}"
        # per-batch retry
        last_err = None
        for attempt in range(retries):
            try:
                resp = session.post(url, headers=headers, json=body, timeout=timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep((attempt + 1) * 1.5)
                    continue
                resp.raise_for_status()
                data = resp.json()
                pano_ids = data.get("panoIds") or data.get("panoids") or data.get("results")
                if pano_ids is None:
                    if isinstance(data, list):
                        pano_ids = [(x.get("panoId") if isinstance(x, dict) else "") for x in data]
                    else:
                        raise RuntimeError(f"Unexpected panoIds response schema: {data}")
                norm = []
                for i, p in enumerate(pano_ids):
                    pid = p.get("panoId", "") if isinstance(p, dict) else (p or "")
                    lat, lng = batch[i]
                    norm.append({"lat": lat, "lng": lng, "panoId": pid})
                out.extend(norm)
                break
            except Exception as e:
                last_err = e
                if attempt >= retries - 1:
                    print(f"[ERROR] panoIds batch failed permanently: {e}", file=sys.stderr)
                else:
                    time.sleep((attempt + 1) * 1.5)
        time.sleep(sleep_s)
    return out


def fetch_metadata_for_pano(
    session: requests.Session,
    api_key: str,
    session_token: str,
    pano_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_m: int = 50,
    timeout: int = 60,
    retries: int = 5,
) -> Dict[str, Any]:
    if pano_id:
        params = {"panoId": pano_id}
    elif lat is not None and lng is not None:
        params = {"lat": lat, "lng": lng, "radius": radius_m}
    else:
        raise ValueError("Either pano_id or (lat,lng) must be provided.")
    params["session"] = session_token
    params["key"] = api_key
    resp = _http_with_retries(
        session,
        "GET",
        TILES_METADATA,
        params=params,
        timeout=timeout,
        retries=retries,
    )
    if resp.status_code == 404:
        return {}
    return resp.json()


# ----------------------------
# Exports
# ----------------------------
def write_csv(rows: List[Dict[str, Any]], path: str):
    fields = sorted({k for r in rows for k in r.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_geojson(features: List[Dict[str, Any]], path: str):
    fc = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False)


# ----------------------------
# Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="List Street View panoramas using Google Map Tiles API with OSM polygon.")
    parser.add_argument("--api_key", required=True)
    parser.add_argument("--city", default="Beijing, China")
    parser.add_argument("--districts", default="")
    parser.add_argument("--grid_spacing", type=float, default=60.0)
    parser.add_argument("--search_radius", type=int, default=50)
    parser.add_argument("--with_metadata", action="store_true")
    parser.add_argument("--referer", default=None)
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--region", default="CN")
    parser.add_argument("--user_agent", default="SV-Pano-Lister/1.0 (contact: youremail@example.com)")
    parser.add_argument("--out_csv", default="pano_list.csv")
    parser.add_argument("--out_geojson", default="pano_list.geojson")
    parser.add_argument("--max_points", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--no_proxy_google", action="store_true", help="Bypass environment proxies for Google endpoints.")
    args = parser.parse_args()

    # Sessions:
    # - Nominatim: keep trust_env=True to honor ALL_PROXY etc.
    osm_sess = requests.Session()
    # - Google: optionally bypass proxies to avoid TLS handshake timeout
    google_sess = requests.Session()
    if args.no_proxy_google:
        google_sess.trust_env = False  # ignore env proxies completely

    # 1) Polygon(s)
    print("[1/6] Fetching polygon(s) from Nominatim...")
    if args.districts.strip():
        districts = [d.strip() for d in args.districts.split(",") if d.strip()]
        area = fetch_district_union(
            osm_sess, args.city, districts,
            user_agent=args.user_agent, timeout=args.timeout, retries=args.retries
        )
    else:
        area = fetch_place_polygon(
            osm_sess, args.city, user_agent=args.user_agent, timeout=args.timeout, retries=args.retries
        )

    # 2) Grid
    print("[2/6] Generating grid points...")
    area_3857, _, inv = polygon_to_3857(area)
    points = generate_grid_points(area_3857, spacing_m=args.grid_spacing, inv=inv)
    if args.max_points and len(points) > args.max_points:
        points = points[:args.max_points]
    print(f"  - Points sampled: {len(points)}")
    if not points:
        print("No points generated—try larger area or smaller spacing.", file=sys.stderr)
        sys.exit(1)

    # 3) Session
    print("[3/6] Creating Map Tiles API Street View session...")
    session_token = create_tiles_session(
        google_sess, api_key=args.api_key, referer=args.referer,
        language=args.language, region=args.region,
        timeout=args.timeout, retries=args.retries
    )
    print("  - Session token acquired.")

    # 4) PanoIds
    print("[4/6] Querying panoIds...")
    pano_rows = fetch_panoids_for_points(
        google_sess, api_key=args.api_key, session_token=session_token,
        points=[(lat, lng) for (lat, lng) in points],
        radius_m=args.search_radius, timeout=args.timeout, retries=args.retries
    )

    # 5) Dedup + metadata
    print("[5/6] Deduplicating and (optional) fetching metadata...")
    seen = set()
    dedup = []
    for r in pano_rows:
        pid = r.get("panoId", "") or ""
        if not pid or pid in seen:
            continue
        seen.add(pid)
        dedup.append({"panoId": pid, "query_lat": r["lat"], "query_lng": r["lng"]})
    print(f"  - Unique panoIds: {len(dedup)}")

    if args.with_metadata:
        enriched = []
        for r in tqdm(dedup, desc="Fetch metadata", unit="pano"):
            pid = r["panoId"]
            try:
                meta = fetch_metadata_for_pano(
                    google_sess, args.api_key, session_token, pano_id=pid,
                    timeout=args.timeout, retries=args.retries
                )
            except Exception:
                meta = {}
            out = dict(r)
            out["meta_captureDate"] = meta.get("captureDate")
            out["meta_copyright"] = meta.get("copyright")
            out["meta_reportProblemUrl"] = meta.get("reportProblemUrl")
            out["meta_lat"] = (meta.get("location") or {}).get("lat")
            out["meta_lng"] = (meta.get("location") or {}).get("lng")
            out["meta_imageryType"] = meta.get("imageryType")
            enriched.append(out)
        final_rows = enriched
    else:
        final_rows = dedup

    # 6) Exports
    print("[6/6] Writing outputs...")
    write_csv(final_rows, args.out_csv)

    features = []
    for r in final_rows:
        lat = r.get("meta_lat") or r["query_lat"]
        lng = r.get("meta_lng") or r["query_lng"]
        props = dict(r)
        props.pop("meta_lat", None)
        props.pop("meta_lng", None)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": props,
        })
    write_geojson(features, args.out_geojson)

    print(f"Done.\n  CSV: {args.out_csv}\n  GeoJSON: {args.out_geojson}")


if __name__ == "__main__":
    main()