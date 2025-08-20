#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import json
import time
import random
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Allow importing from sibling 'scripts' directory
CUR_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CUR_DIR, os.pardir))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.append(SCRIPTS_DIR)

from gawc_city import list_gawc_city  # type: ignore

try:
    from .geo import fetch_city_geojson, save_city_small_polygons_cache
except Exception:
    from app.geo import fetch_city_geojson, save_city_small_polygons_cache
import geojson


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prebuild city small polygon caches for GaWC cities")
    parser.add_argument("--cache-dir", required=True, help="Directory to store per-city cache files")
    parser.add_argument("--threshold", default="gamma+", help="GaWC threshold; prebuild for strictly higher than this level (default: gamma+)")
    parser.add_argument("--strictly-higher", action="store_true", help="Only include cities strictly higher than threshold (default)")
    parser.add_argument("--include-threshold", dest="strictly_higher", action="store_false", help="Include cities at the threshold level as well")
    parser.set_defaults(strictly_higher=True)
    parser.add_argument("--country", default=None, help="Optional single country hint to apply for all cities (e.g., 'CN')")
    parser.add_argument("--concurrency", type=int, default=2, help="Max parallel fetches (default: 2)")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of cities to process (debug)")
    return parser


def _process_city(cache_dir: str, city: str, country: Optional[str]) -> Tuple[str, bool, Optional[str]]:
    time.sleep(random.uniform(0.05, 0.2))  # jitter to avoid bursts
    try:
        fc = fetch_city_geojson(city, country)
        # Normalise to features list
        features: List[geojson.Feature]
        if isinstance(fc, geojson.FeatureCollection):
            features = list(fc.features)
        elif isinstance(fc, dict) and fc.get("type") == "FeatureCollection":
            features = [
                geojson.Feature(geometry=f.get("geometry"), properties=f.get("properties", {}))
                for f in (fc.get("features") or [])
            ]
        else:
            # Wrap single Feature as a collection
            g = fc["geometry"] if isinstance(fc, dict) else fc.geometry
            features = [geojson.Feature(geometry=g, properties={"source": "city_full"})]
        save_city_small_polygons_cache(cache_dir, city, country, features)
        return city, True, None
    except Exception as e:
        # Do not crash the batch; return failure
        return city, False, str(e)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    print(f"[INFO] Building GeoJSON caches to '{args.cache_dir}'...")
    cities = list_gawc_city(threshold=args.threshold, strictly_higher=bool(args.strictly_higher), debug=True)
    if args.limit:
        cities = cities[: int(args.limit)]
    print(f"[INFO] Total cities to process: {len(cities)}")

    num_ok = 0
    num_fail = 0

    if args.concurrency <= 1:
        for city in cities:
            _, ok, err = _process_city(args.cache_dir, city, args.country)
            if ok:
                num_ok += 1
            else:
                num_fail += 1
                print(f"[WARN] Failed for city='{city}': {err}")
    else:
        with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as executor:
            futures = [executor.submit(_process_city, args.cache_dir, city, args.country) for city in cities]
            for fut in as_completed(futures):
                try:
                    city, ok, err = fut.result()
                except Exception as e:
                    city, ok, err = ("<unknown>", False, str(e))
                if ok:
                    num_ok += 1
                else:
                    num_fail += 1
                    print(f"[WARN] Failed for city='{city}': {err}")

    print(f"[INFO] Done. Success={num_ok} Fail={num_fail}")
    return 0 if num_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main()) 