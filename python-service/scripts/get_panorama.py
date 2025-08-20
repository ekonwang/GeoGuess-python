#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Optional
from streetview import get_panorama

import requests
from utils import print_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a Street View panorama PNG via the local app service"
    )
    parser.add_argument(
        "--app-base-url",
        default=os.getenv("APP_BASE_URL", "http://localhost:8001"),
        help="Base URL of the FastAPI service (default: http://localhost:8001)",
    )
    parser.add_argument(
        "--google-api-key",
        default=os.getenv("GOOGLE_MAPS_API_KEY"),
        help="Google Maps API key (fallback to GOOGLE_MAPS_API_KEY env)",
    )
    parser.add_argument(
        "--city",
        default=None,
        help="City name for city mode (e.g. Shanghai)",
    )
    parser.add_argument(
        "--country",
        default=None,
        help="Country code or name to disambiguate city (e.g. CN)",
    )
    parser.add_argument(
        "--heading",
        type=float,
        default=None,
        help="Camera heading in degrees (optional)",
    )
    parser.add_argument(
        "--pitch",
        type=float,
        default=0.0,
        help="Camera pitch in degrees (default: 0)",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=3,
        help="Zoom level (default: 3)",
    )
    parser.add_argument(
        "--fov",
        type=float,
        default=360.0,
        help="Field of view in degrees (default: 90)",
    )
    parser.add_argument(
        "--all-panorama",
        action="store_true",
        help="Include outdoor/default sources when searching",
    )
    parser.add_argument(
        "--optimise/--no-optimise",
        dest="optimise",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Require image date when selecting a panorama (default: optimise)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=10,
        help="Max attempts to find a covered location (default: 10)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="panorama.png",
        help="Output PNG filepath (default: panorama.png)",
    )
    parser.add_argument(
        "--num_query",
        default=2000,
        type=int,
        help="Number of queries to make (default: 1000)",
    )
    parser.add_argument(
        "--batch_out_dir",
        required=True,
        help="Output directory (default: panorama_batch)",
    )
    return parser


def request_random_panorama(
    app_base_url: str,
    *,
    city: Optional[str],
    country: Optional[str],
    all_panorama: bool,
    optimise: bool,
    max_attempts: int,
) -> Dict[str, Any]:
    url = app_base_url.rstrip("/") + "/streetview/random"
    payload: Dict[str, Any] = {
        "city": city,
        "country": country,
        "all_panorama": all_panorama,
        "optimise": optimise,
        "max_attempts": max_attempts,
    }
    resp = requests.post(url, json=payload, timeout=120)
    if 'error' in resp.json():
        raise RuntimeError(f"App service error: {resp.json()['error']}")
    if resp.status_code != 200:
        raise RuntimeError(f"App service error {resp.status_code}: {resp.text}")
    return resp.json()


def download_streetview_png(
    *,
    api_key: str,
    lat: float,
    lng: float,
    pano_id: Optional[str],
    heading: Optional[float],
    pitch: float,
    fov: float,
    output: str,
    zoom: int = 3,
):
    try:
        panorama = get_panorama(pano_id, multi_threaded=False, zoom=zoom)
    except Exception as e:
        print(f"Error downloading panorama: {e}")
        return False

    os.makedirs(os.path.dirname(output), exist_ok=True)
    panorama.save(output, format="PNG")
    return True


def request_pano_pipeline(args) -> int:
    if not args.google_api_key:
        print("ERROR: --google-api-key not provided and GOOGLE_MAPS_API_KEY not set", file=sys.stderr)
        return 2

    print(f"Requesting a random Street View location for city [{args.city}] from app service...")
    try:
        result = request_random_panorama(
            args.app_base_url,
            city=args.city,
            country=args.country,
            all_panorama=bool(args.all_panorama),
            optimise=bool(args.optimise),
            max_attempts=int(args.max_attempts),
        )
    except Exception as e:
        print_error(f"Error requesting loc:")
        print(e)
        return 1, None

    lat = float(result["latitude"])
    lng = float(result["longitude"])
    metadata = result.get("metadata", {})
    pano_id = metadata.get("pano_id") or metadata.get("panoId")

    print(f"Found location: lat={lat}, lng={lng}, pano_id={pano_id}")

    print(f"Downloading PNG to {args.output}...")
    rst = download_streetview_png(
        api_key=args.google_api_key,
        lat=lat,
        lng=lng,
        pano_id=pano_id,
        heading=args.heading,
        pitch=float(args.pitch),
        fov=float(args.fov),
        output=str(args.output),
        zoom=args.zoom,
    )

    if rst:
        print("Done.")
        return 0, metadata
    return 1, None


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    raise SystemExit(request_pano_pipeline(args)) 