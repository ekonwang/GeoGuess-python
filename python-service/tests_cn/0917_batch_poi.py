#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Batch downloader based on test_poi.py:
- Wraps the core flow into a function that accepts center + radius
- Iterates a city -> center dict with a city-level progress bar
- Saves each photo under output_dir/{city}/{uuid4}/ with:
    - panorama-{uuid}.png (converted to PNG)
    - metadata-{uuid}.json (pano_id uses the place_id)
- API key is read from env GOOGLE_MAPS_API_KEY (required)
"""

import os
import sys
import json
import time
import uuid
import argparse
import pathlib
import tempfile
from typing import Dict, List, Optional, Tuple

from datetime import datetime

# Local reuse
from test_poi import (
    GooglePlacesClient,
    collect_places_with_photos,
    DEFAULT_PLACE_TYPES,
    parse_latlng,
    ensure_dir,
)

# Optional deps
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

try:
    from PIL import Image
except ImportError:
    Image = None


def _require_api_key() -> str:
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        raise SystemExit("ERROR: Missing API key in env GOOGLE_MAPS_API_KEY")
    return key


def _download_photo_as_png(
    client: GooglePlacesClient,
    photo_reference: str,
    png_path: pathlib.Path,
    *,
    maxwidth: Optional[int],
    maxheight: Optional[int],
) -> None:
    """
    Reuse client's download to a temp JPG, then convert to PNG.
    Requires Pillow.
    """
    if Image is None:
        raise SystemExit("ERROR: Pillow is required. Please `pip install pillow`.")

    with tempfile.TemporaryDirectory() as td:
        tmp_jpg = pathlib.Path(td) / "tmp.jpg"
        client.download_photo(
            photo_reference=photo_reference,
            out_path=tmp_jpg,
            maxwidth=maxwidth,
            maxheight=maxheight,
        )
        with Image.open(tmp_jpg) as im:
            im.convert("RGB").save(png_path, format="PNG")


def run_for_center(
    center: str,
    radius: int,
    outdir: pathlib.Path,
    *,
    api_key: str,
    types: List[str] = DEFAULT_PLACE_TYPES,
    per_type_pages: int = 3,
    limit: int = 120,
    min_width: int = 2000,
    maxwidth: Optional[int] = 8000,
    maxheight: Optional[int] = None,
    debug: bool = False,
) -> List[Dict]:
    """
    Core function: given center (lat,lng string) and radius, download photos.
    Returns a list of downloaded item metadata dicts (each includes place_id, lat/lng, etc.).
    """
    client = GooglePlacesClient(api_key=api_key, debug=debug)

    lat, lng = parse_latlng(center)
    places = collect_places_with_photos(
        client=client,
        center=(lat, lng),
        radius=radius,
        types=types,
        per_type_pages=per_type_pages,
        debug=debug,
    )

    # Build candidates with min_width filter
    candidates: List[Tuple[Dict, Dict]] = []
    seen_photos = set()
    for place in places:
        for ph in place.get("photos", []):
            ref = ph.get("photo_reference")
            w = ph.get("width", 0)
            if not ref or ref in seen_photos:
                continue
            if min_width and w and w < min_width:
                if debug:
                    print(f"[DBG] Skip photo width={w} < min_width={min_width}")
                continue
            candidates.append((place, ph))
            seen_photos.add(ref)

    # Trim to limit
    candidates = candidates[:limit]

    # Caller handles city subfolder layout; here we just return metadata useful for saving
    # However, for convenience, we return the raw candidate tuples to the caller
    # The caller (process_city) will create uuid subfolders and save files.
    # For direct use, this function could be expanded to write files; we keep it pure.
    # To align with the user's request, we'll return structured dicts describing each candidate.
    items: List[Dict] = []
    for place, ph in candidates:
        items.append(
            {
                "place": place,
                "photo": ph,
                "place_id": place.get("place_id"),
                "name": place.get("name"),
                "lat": place.get("geometry", {}).get("location", {}).get("lat"),
                "lng": place.get("geometry", {}).get("location", {}).get("lng"),
                "photo_reference": ph.get("photo_reference"),
            }
        )
    return items


def process_city(
    city: str,
    center: str,
    radius: int,
    base_outdir: pathlib.Path,
    *,
    api_key: str,
    types: List[str] = DEFAULT_PLACE_TYPES,
    per_type_pages: int = 3,
    limit: int = 120,
    min_width: int = 2000,
    maxwidth: Optional[int] = 8000,
    maxheight: Optional[int] = None,
    debug: bool = False,
) -> int:
    """
    Run the pipeline for a single city and save to:
        base_outdir/{city}/{uuid}/panorama-{uuid}.png
        base_outdir/{city}/{uuid}/metadata-{uuid}.json
    Returns the number of successfully written items.
    """
    city_outdir = base_outdir / city
    ensure_dir(city_outdir)

    items = run_for_center(
        center=center,
        radius=radius,
        outdir=city_outdir,
        api_key=api_key,
        types=types,
        per_type_pages=per_type_pages,
        limit=limit,
        min_width=min_width,
        maxwidth=maxwidth,
        maxheight=maxheight,
        debug=debug,
    )

    # Reuse client for photo downloads
    client = GooglePlacesClient(api_key=api_key, debug=debug)

    ok = 0
    for it in items:
        uid = uuid.uuid4().hex
        subdir = city_outdir / uid
        ensure_dir(subdir)

        png_path = subdir / f"panorama-{uid}.png"
        meta_path = subdir / f"metadata-{uid}.json"

        try:
            _download_photo_as_png(
                client,
                photo_reference=it["photo_reference"],
                png_path=png_path,
                maxwidth=maxwidth,
                maxheight=maxheight,
            )
        except Exception as e:
            print(f"[ERR] City={city} place_id={it.get('place_id')} download failed: {e}")
            continue

        metadata = {
            "date": datetime.now().isoformat(timespec="seconds"),
            "location": {"lat": it.get("lat"), "lng": it.get("lng")},
            "pano_id": it.get("place_id"),  # per requirement: use place_id
            "city": city,
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ERR] City={city} place_id={it.get('place_id')} write metadata failed: {e}")
            # Best-effort cleanup of image if metadata fails
            try:
                if png_path.exists():
                    png_path.unlink()
                if subdir.exists():
                    subdir.rmdir()
            except Exception:
                pass
            continue

        ok += 1
        time.sleep(0.05)

    return ok


def process_cities(
    city_centers: Dict[str, str],
    radius: int,
    output_dir: str,
    *,
    per_type_pages: int = 3,
    limit: int = 120,
    min_width: int = 2000,
    maxwidth: Optional[int] = 8000,
    maxheight: Optional[int] = None,
    debug: bool = False,
) -> None:
    """
    Iterate the city dict with a city-level progress bar.
    """
    api_key = _require_api_key()
    outdir = pathlib.Path(output_dir)
    ensure_dir(outdir)

    cities = list(city_centers.items())
    total = len(cities)

    if tqdm:
        pbar = tqdm(total=total, desc="Cities", unit="city")
    else:
        pbar = None
        print(f"[INFO] Processing {total} cities... (tqdm missing; install via `pip install tqdm` for a progress bar)")

    for city, center in cities:
        print(f"[INFO] City={city} center={center} radius={radius}")
        try:
            n = process_city(
                city=city,
                center=center,
                radius=radius,
                base_outdir=outdir,
                api_key=api_key,
                per_type_pages=per_type_pages,
                limit=limit,
                min_width=min_width,
                maxwidth=maxwidth,
                maxheight=maxheight,
                debug=debug,
            )
            print(f"[OK] City={city} saved={n}")
        except Exception as e:
            print(f"[ERR] City={city} failed: {e}")
            raise
        finally:
            if pbar:
                pbar.update(1)

    if pbar:
        pbar.close()


def main():
    parser = argparse.ArgumentParser(description="Batch download city photos using Google Places Photos API")
    parser.add_argument("--radius", type=int, default=3000, help="Search radius in meters")
    parser.add_argument("--outdir", type=str, default="./city_photos", help="Output base directory")
    parser.add_argument("--limit", type=int, default=120, help="Max photos per city")
    parser.add_argument("--per-type-pages", type=int, default=3, help="Pages per type")
    parser.add_argument("--min_width", type=int, default=2000, help="Min photo width to keep")
    parser.add_argument("--maxwidth", type=int, default=8000, help="Place Photo maxwidth")
    parser.add_argument("--maxheight", type=int, default=None, help="Place Photo maxheight")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")

    # City dict source:
    # - If --cities-json is provided, load from that JSON file.
    # - Otherwise, use the embedded default dict (extend as needed).
    parser.add_argument(
        "--cities-json",
        type=str,
        default=None,
        help="Path to a JSON file containing a dict of {city: 'lat,lng'}",
    )

    args = parser.parse_args()

    if args.maxwidth and args.maxheight:
        raise SystemExit("ERROR: --maxwidth and --maxheight are mutually exclusive")

    if args.cities_json:
        with open(args.cities_json, "r", encoding="utf-8") as f:
            city_centers = json.load(f)
    else:
        # Extend this dict as needed
        city_centers = {
            "Shanghai": "31.2304,121.4737",
            # "Beijing": "39.9042,116.4074",
            # "Shenzhen": "22.5431,114.0579",
            # ...
        }

    process_cities(
        city_centers=city_centers,
        radius=args.radius,
        output_dir=args.outdir,
        per_type_pages=args.per_type_pages,
        limit=args.limit,
        min_width=args.min_width,
        maxwidth=args.maxwidth,
        maxheight=args.maxheight,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()