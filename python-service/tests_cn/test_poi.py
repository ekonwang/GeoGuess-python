#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Download high-quality "street scene" photos around central Shanghai
using Google Places Nearby Search + Place Photos API.

新增:
- --debug: 打印中间过程信息（请求参数概要、分页进度、候选筛选、下载详情等）

Usage:
  python test_poi_debug.py --help
"""

import os
import json
import time
import argparse
import pathlib
from typing import Dict, List, Set, Tuple, Optional
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

GOOGLE_PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
GOOGLE_PLACES_PHOTO_URL  = "https://maps.googleapis.com/maps/api/place/photo"

# A curated set of types that tend to have exterior / street-facing photos.
DEFAULT_PLACE_TYPES = [
    "tourist_attraction",
    "shopping_mall",
    "department_store",
    "clothing_store",
    "convenience_store",
    "cafe",
    "restaurant",
    "bar",
    "bakery",
    "book_store",
    "supermarket",
    "train_station",
    "subway_station",
    "transit_station",
    "bus_station",
    "light_rail_station",
    "movie_theater",
    "museum",
    "park",
    "lodging",
    "gym",
    "bank",
    "pharmacy",
    "doctor",
    "hospital",
    "university",
    "school",
    "library",
]

def parse_latlng(latlng_str: str) -> Tuple[float, float]:
    """Parse a "lat,lng" string into floats.

    Be tolerant of accidental "lng,lat" ordering: if the first parsed value is not a
    valid latitude (abs > 90) but swapping yields valid (lat,lng) ranges, return the
    swapped pair. If neither ordering yields a valid pair, raise ValueError to fail
    fast with a clear message.
    """
    lat_str, lng_str = latlng_str.split(",")
    a = float(lat_str.strip())
    b = float(lng_str.strip())
    # Preferred interpretation: (a,b) as (lat,lng)
    if -90 <= a <= 90 and -180 <= b <= 180:
        return a, b
    # If not valid, try swapped (lng,lat) -> (lat,lng)
    if -90 <= b <= 90 and -180 <= a <= 180:
        return b, a
    raise ValueError(f"Invalid lat,lng string: {latlng_str}")

def ensure_dir(p: pathlib.Path):
    p.mkdir(parents=True, exist_ok=True)

def _mask_key(k: Optional[str]) -> str:
    if not k:
        return ""
    if len(k) <= 6:
        return "*" * len(k)
    return k[:4] + "..." + k[-3:]

class GooglePlacesClient:
    def __init__(self, api_key: str, session: Optional[requests.Session] = None, debug: bool = False):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.debug = debug

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=8),
           retry=retry_if_exception_type(requests.RequestException))
    def nearby_search(self, location: Tuple[float, float], radius: int, place_type: str,
                      pagetoken: Optional[str] = None) -> Dict:
        params = {
            "key": self.api_key,
            "location": f"{location[0]},{location[1]}",
            "radius": radius,
            "type": place_type,
            "language": "zh-CN",
        }
        if pagetoken:
            params["pagetoken"] = pagetoken

        if self.debug:
            safe_params = dict(params)
            safe_params["key"] = _mask_key(self.api_key)
            print(f"[DBG] NearbySearch params: {safe_params}")

        resp = self.session.get(GOOGLE_PLACES_NEARBY_URL, params=params, timeout=60)
        if self.debug:
            # print(f"[DBG] NearbySearch HTTP {resp.status_code} url(no-key-masked)={resp.url.replace(self.api_key, '***') if self.api_key else resp.url}")
            print(f"[DBG] NearbySearch HTTP {resp.status_code} url(no-key-masked)={resp.url}")
        resp.raise_for_status()
        data = resp.json()

        if self.debug:
            status = data.get("status", "")
            res_ct = len(data.get("results", []))
            has_next = "next_page_token" in data
            print(f"[DBG] NearbySearch status={status} results={res_ct} has_next={has_next}")

        return data

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=8),
           retry=retry_if_exception_type(requests.RequestException))
    def download_photo(self, photo_reference: str, out_path: pathlib.Path,
                       maxwidth: Optional[int] = None, maxheight: Optional[int] = None):
        params = {
            "key": self.api_key,
            "photo_reference": photo_reference,
        }
        if maxwidth:
            params["maxwidth"] = maxwidth
        if maxheight:
            params["maxheight"] = maxheight

        if self.debug:
            safe_params = dict(params)
            safe_params["key"] = _mask_key(self.api_key)
            print(f"[DBG] DownloadPhoto params: {safe_params} -> {out_path}")

        total_bytes = 0
        with self.session.get(GOOGLE_PLACES_PHOTO_URL, params=params, stream=True, timeout=30) as r:
            # print(r.url)
            # import pdb; pdb.set_trace()
            if self.debug:
                dbg_url = r.url.replace(self.api_key, '***') if self.api_key else r.url
                print(f"[DBG] Photo redirect final URL: {dbg_url} status={r.status_code}")
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        total_bytes += len(chunk)
        if self.debug:
            try:
                ct = r.headers.get("Content-Type")
            except Exception:
                ct = None
            print(f"[DBG] Saved photo: {out_path} bytes={total_bytes} content_type={ct}")

def collect_places_with_photos(
    client: GooglePlacesClient,
    center: Tuple[float, float],
    radius: int,
    types: List[str],
    per_type_pages: int = 3,
    page_sleep: float = 2.1,
    debug: bool = False,
    show_progress: bool = False,
) -> List[Dict]:
    """
    Iterate multiple place types and collect places that have photos[].
    Returns a list of {place_id, name, geometry, vicinity, photos, html_attributions}
    """
    results: List[Dict] = []
    seen_place_ids: Set[str] = set()

    total_steps = len(types) * per_type_pages
    pbar = None
    if show_progress and total_steps > 0:
        try:
            from tqdm.auto import tqdm
            pbar = tqdm(total=total_steps, desc="Collecting places", disable=False)
        except Exception:
            pbar = None

    for t in types:
        if debug:
            print(f"[DBG] === Type={t} ===")
        pagetoken = None
        pages_fetched = 0
        while pages_fetched < per_type_pages:
            if debug:
                print(f"[DBG] Fetching page {pages_fetched+1}/{per_type_pages} type={t} pagetoken={bool(pagetoken)}")
            data = client.nearby_search(center, radius, t, pagetoken=pagetoken)
            status = data.get("status", "")
            if status not in ("OK", "ZERO_RESULTS"):
                print(f"[WARN] Nearby status={status} for type={t} token={pagetoken}")
                if pbar:
                    pbar.update(1)
                break

            added_this_page = 0
            for p in data.get("results", []):
                if "photos" in p and p.get("place_id") not in seen_place_ids:
                    results.append(p)
                    seen_place_ids.add(p.get("place_id"))
                    added_this_page += 1
            if debug:
                print(f"[DBG] Added {added_this_page} unique places (cum={len(results)}) for type={t}")
            pages_fetched += 1
            if pbar:
                try:
                    pbar.update(1)
                    pbar.set_postfix(type=t, page=pages_fetched, cum=len(results))
                except Exception:
                    pass

            pagetoken = data.get("next_page_token")
            if not pagetoken:
                if debug:
                    print(f"[DBG] No next_page_token for type={t}")
                break
            time.sleep(page_sleep)
        # TODO: 生产代码，需要注释掉这行
        if debug:
            if len(seen_place_ids) > 0:
                break

        time.sleep(0.4)

    if pbar:
        try:
            pbar.close()
        except Exception:
            pass

    return results

def main():
    parser = argparse.ArgumentParser(description="Download street-scene-like photos around central Shanghai via Google Places Photos API.")
    parser.add_argument("--api-key", type=str, default=os.getenv("GOOGLE_MAPS_API_KEY"), help="Google Maps API Key (or set env GOOGLE_MAPS_API_KEY)")
    parser.add_argument("--center", type=str, default="31.2304,121.4737", help="Lat,Lon of center. Default=人民广场(31.2304,121.4737)")
    parser.add_argument("--radius", type=int, default=3000, help="Search radius in meters (<= 50000). Default=3000")
    parser.add_argument("--types", type=str, nargs="*", default=DEFAULT_PLACE_TYPES, help="Place types to search; default is a curated list.")
    parser.add_argument("--per-type-pages", type=int, default=3, help="Pages to fetch per type (max 3 pages * ~20 results). Default=3")
    parser.add_argument("--limit", type=int, default=120, help="Max number of photos to download. Default=120")
    parser.add_argument("--maxwidth", type=int, default=8000, help="Place Photo maxwidth (px). Mutually exclusive with --maxheight.")
    parser.add_argument("--maxheight", type=int, default=None, help="Place Photo maxheight (px).")
    parser.add_argument("--outdir", type=str, default="./sh_photos", help="Output directory for images and metadata.json")
    parser.add_argument("--min_width", type=int, default=2000, help="Skip photo candidates if width < this value (uses metadata). Default=800")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("ERROR: Missing API key. Provide --api-key or set env GOOGLE_MAPS_API_KEY")

    if args.maxwidth and args.maxheight:
        raise SystemExit("ERROR: --maxwidth and --maxheight are mutually exclusive; choose one.")

    center = parse_latlng(args.center)
    outdir = pathlib.Path(args.outdir)
    ensure_dir(outdir)

    client = GooglePlacesClient(api_key=args.api_key, debug=args.debug)

    if args.debug:
        print(f"[DBG] Config center={center} radius={args.radius} types={len(args.types)} "
              f"per_type_pages={args.per_type_pages} limit={args.limit} "
              f"maxwidth={args.maxwidth} maxheight={args.maxheight} min_width={args.min_width} "
              f"outdir={outdir.resolve()} key={_mask_key(args.api_key)}")

    print(f"[INFO] Searching around {center} radius={args.radius}m for {len(args.types)} types...")
    places = collect_places_with_photos(
        client, center, args.radius, args.types, per_type_pages=args.per_type_pages, debug=args.debug
    )
    print(f"[INFO] Found {len(places)} places with photos across types.")

    # Extract unique photo candidates
    candidates: List[Tuple[Dict, Dict]] = []  # (place, photo_obj)
    seen_photos: Set[str] = set()
    for place in places:
        photos = place.get("photos", [])
        for idx, ph in enumerate(photos):
            ref = ph.get("photo_reference")
            width = ph.get("width", 0)
            height = ph.get("height", 0)
            if not ref or ref in seen_photos:
                continue
            if args.min_width and width and width < args.min_width:
                if args.debug:
                    print(f"[DBG] Skip photo(ref={ref[:10]}...) width={width} < min_width={args.min_width}")
                continue
            candidates.append((place, ph))
            seen_photos.add(ref)
            if args.debug:
                print(f"[DBG] Candidate: {place.get('name')} photo {width}x{height} ref={ref[:10]}...")

    print(f"[INFO] Photo candidates after filtering: {len(candidates)}")

    # Trim to limit
    candidates = candidates[: args.limit]
    print(f"[INFO] Will download {len(candidates)} photos to: {outdir.resolve()}")

    metadata_list: List[Dict] = []
    for i, (place, ph) in enumerate(candidates, start=1):
        place_id = place.get("place_id")
        name = place.get("name")
        vicinity = place.get("vicinity")
        geometry = place.get("geometry", {})
        location = geometry.get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")
        attributions = ph.get("html_attributions", place.get("html_attributions", []))
        ref = ph.get("photo_reference")

        fname = f"{i:03d}__{place_id}.jpg"
        out_path = outdir / fname

        if args.debug:
            print(f"[DBG] Downloading {i:03d}/{len(candidates)} name={name} file={fname}")

        try:
            client.download_photo(
                photo_reference=ref,
                out_path=out_path,
                maxwidth=args.maxwidth,
                maxheight=args.maxheight,
            )
            print(f"[OK] {i:03d}/{len(candidates)}  {name}  -> {fname}")
        except requests.RequestException as e:
            print(f"[ERR] Failed {i:03d} {name}: {e}")
            continue

        md = {
            "index": i,
            "filename": fname,
            "place_id": place_id,
            "place_name": name,
            "address_or_vicinity": vicinity,
            "lat": lat,
            "lng": lng,
            "photo_reference": ref,
            "photo_width": ph.get("width"),
            "photo_height": ph.get("height"),
            "html_attributions": attributions,
            "place_types": place.get("types", []),
        }
        metadata_list.append(md)

        time.sleep(0.15)

    # Write metadata
    meta_path = outdir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "center": {"lat": center[0], "lng": center[1]},
                "radius_m": args.radius,
                "maxwidth": args.maxwidth,
                "maxheight": args.maxheight,
                "count": len(metadata_list),
                "items": metadata_list,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"[DONE] Saved metadata to {meta_path.resolve()}")

if __name__ == "__main__":
    main()
