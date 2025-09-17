# file: download_tiles_api.py
from __future__ import annotations

import math
import time
import random
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, Optional, Tuple, List
import concurrent.futures

import requests
from requests.adapters import HTTPAdapter, Retry
from PIL import Image
import os

# Try to import create_session_token as requested.
# We keep robust fallbacks so this module can run even if the path differs.
try:
    from create_session import create_session_token, SessionToken, MapTilesError  # type: ignore
except Exception:
    try:
        from tests_cn.create_session import create_session_token, SessionToken, MapTilesError  # type: ignore
    except Exception:
        create_session_token = None  # type: ignore

        @dataclass(frozen=True)
        class SessionToken:  # minimal stub for typing
            session: str
            expiry: int
            tile_width: Optional[int] = None
            tile_height: Optional[int] = None
            image_format: Optional[str] = None

        class MapTilesError(RuntimeError):
            pass


# -----------------------------
# HTTP helpers
# -----------------------------
def _requests_session(timeout: float = 15.0) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))

    orig_request = s.request

    def _with_timeout(method, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return orig_request(method, url, **kwargs)

    s.request = _with_timeout  # type: ignore
    return s


# -----------------------------
# Data models
# -----------------------------
@dataclass
class PanoramaMeta:
    image_width: int
    image_height: int
    copyright: Optional[str] = None


@dataclass
class TileInfo:
    z: int
    x: int
    y: int
    url: str


# -----------------------------
# API endpoints
# -----------------------------
def _streetview_tile_url(
    z: int,
    x: int,
    y: int,
    *,
    pano_id: str,
    api_key: str,
    session_token: SessionToken,
) -> str:
    # Docs: https://developers.google.com/maps/documentation/tile/streetview?hl=zh-cn#street_view_image_tiles
    return (
        f"https://tile.googleapis.com/v1/streetview/tiles/{z}/{x}/{y}"
        f"?session={session_token.session}&key={api_key}&panoId={pano_id}"
    )


def _streetview_metadata_url(
    *,
    pano_id: str,
    api_key: str,
    session_token: SessionToken,
) -> str:
    # Street View metadata endpoint (fields include imageWidth/imageHeight/copyright)
    return (
        "https://tile.googleapis.com/v1/streetview/metadata"
        f"?session={session_token.session}&key={api_key}&panoId={pano_id}"
    )


# -----------------------------
# Core helpers
# -----------------------------
def fetch_panorama_metadata(
    pano_id: str,
    *,
    api_key: str,
    session_token: SessionToken,
    session: Optional[requests.Session] = None,
) -> PanoramaMeta:
    sess = session or _requests_session()
    url = _streetview_metadata_url(pano_id=pano_id, api_key=api_key, session_token=session_token)
    resp = sess.get(url)
    if resp.status_code // 100 != 2:
        try:
            detail = resp.json()
        except Exception:
            detail = {"error": resp.text}
        raise MapTilesError(f"metadata failed: HTTP {resp.status_code}: {detail}")

    try:
        data = resp.json()
    except Exception as e:
        raise MapTilesError(f"Invalid JSON from metadata: {e}")

    # Expected fields per docs
    iw = data.get("imageWidth")
    ih = data.get("imageHeight")
    if not isinstance(iw, int) or not isinstance(ih, int):
        raise MapTilesError(f"metadata missing imageWidth/imageHeight: {data}")

    return PanoramaMeta(
        image_width=iw,
        image_height=ih,
        copyright=data.get("copyright"),
    )


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def compute_tile_grid(
    *,
    meta: PanoramaMeta,
    session_token: SessionToken,
    zoom: int,
) -> Tuple[int, int, int, int]:
    """
    Compute:
      - width_z_px, height_z_px: panorama pixel size at given zoom level
      - tiles_x, tiles_y: tile grid counts at given zoom

    Zoom per docs: 0..5 (0 widest FOV, 5 full resolution).
    Assumption: Resolution halves each time you go down by 1 zoom level.
    """
    if zoom < 0 or zoom > 5:
        raise ValueError("zoom must be in [0, 5]")

    # Full-res at z=5; scale down by 2^(5-z)
    scale = 2 ** (5 - zoom)
    width_z = math.ceil(meta.image_width / scale)
    height_z = math.ceil(meta.image_height / scale)

    tile_w = session_token.tile_width or 512
    tile_h = session_token.tile_height or 512
    tiles_x = _ceil_div(width_z, tile_w)
    tiles_y = _ceil_div(height_z, tile_h)
    return width_z, height_z, tiles_x, tiles_y


def iter_tiles(
    *,
    pano_id: str,
    api_key: str,
    session_token: SessionToken,
    zoom: int,
    tiles_x: int,
    tiles_y: int,
) -> List[TileInfo]:
    items: List[TileInfo] = []
    for x in range(tiles_x):
        for y in range(tiles_y):
            items.append(
                TileInfo(
                    z=zoom,
                    x=x,
                    y=y,
                    url=_streetview_tile_url(
                        zoom, x, y, pano_id=pano_id, api_key=api_key, session_token=session_token
                    ),
                )
            )
    return items


def fetch_tile_image(
    tile: TileInfo,
    *,
    session: Optional[requests.Session] = None,
    max_retries: int = 3,
) -> Image.Image:
    sess = session or _requests_session()
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Connection": "keep-alive",
    }

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = sess.get(tile.url, headers=headers, stream=True, timeout=30)
            if resp.status_code == 429:
                # backoff on rate-limit
                wait = min(2 ** attempt + random.uniform(0, 1), 60)
                time.sleep(wait)
                continue
            if resp.status_code // 100 != 2:
                # brief jitter then retry
                time.sleep(0.2 + random.uniform(0, 0.3))
                continue

            content_type = resp.headers.get("Content-Type", "").lower()
            if "image" not in content_type and "octet-stream" not in content_type:
                # sometimes small HTML error payloads
                time.sleep(0.5)
                continue

            img = Image.open(BytesIO(resp.content))
            # ensure image is loaded before closing stream
            img.load()
            return img
        except Exception as e:
            last_exc = e
            # small exponential backoff
            time.sleep(0.5 + min(2 ** attempt, 4) + random.uniform(0, 0.3))

    raise MapTilesError(f"Failed to download tile {tile.z}/{tile.x}/{tile.y}: {last_exc}")


def stitch_panorama(
    *,
    tiles: List[Tuple[int, int, Image.Image]],
    tile_w: int,
    tile_h: int,
    tiles_x: int,
    tiles_y: int,
) -> Image.Image:
    pano_w = tiles_x * tile_w
    pano_h = tiles_y * tile_h
    canvas = Image.new("RGB", (pano_w, pano_h))
    for x, y, img in tiles:
        canvas.paste(img, (x * tile_w, y * tile_h))
    return canvas


# -----------------------------
# Public API
# -----------------------------
def get_panorama(
    pano_id: str,
    *,
    api_key: str,
    session_token: SessionToken,
    zoom: int = 5,
    multi_threaded: bool = False,
    max_retries: int = 3,
    session: Optional[requests.Session] = None,
) -> Image.Image:
    """
    Download and stitch a Street View panorama using Google Map Tiles Street View Tiles API.

    Args:
      - pano_id: Street View panorama ID.
      - api_key: Google Maps Platform API key.
      - session_token: Session token created by createSession (Map Tiles API).
      - zoom: 0..5; 5 is full resolution.
      - multi_threaded: enable concurrent tile downloads.
      - max_retries: per-tile retries.
      - session: optional shared requests.Session with retries.

    Returns:
      - PIL.Image.Image of the stitched panorama.
    """
    sess = session or _requests_session()

    # 1) Fetch metadata to compute tile grid sizes at the given zoom
    meta = fetch_panorama_metadata(pano_id, api_key=api_key, session_token=session_token, session=sess)

    # 2) Determine tile grid
    width_z, height_z, tiles_x, tiles_y = compute_tile_grid(meta=meta, session_token=session_token, zoom=zoom)

    tile_w = session_token.tile_width or 512
    tile_h = session_token.tile_height or 512

    # 3) Build tile list
    tile_infos = iter_tiles(
        pano_id=pano_id,
        api_key=api_key,
        session_token=session_token,
        zoom=zoom,
        tiles_x=tiles_x,
        tiles_y=tiles_y,
    )

    # 4) Download tiles
    results: List[Tuple[int, int, Image.Image]] = []
    if not multi_threaded:
        for info in tile_infos:
            img = fetch_tile_image(info, session=sess, max_retries=max_retries)
            results.append((info.x, info.y, img))
    else:
        with concurrent.futures.ThreadPoolExecutor() as ex:
            fut_map = {
                ex.submit(fetch_tile_image, info, session=sess, max_retries=max_retries): info
                for info in tile_infos
            }
            for fut in concurrent.futures.as_completed(fut_map):
                info = fut_map[fut]
                img = fut.result()
                results.append((info.x, info.y, img))

    # 5) Stitch
    panorama = stitch_panorama(
        tiles=results, tile_w=tile_w, tile_h=tile_h, tiles_x=tiles_x, tiles_y=tiles_y
    )

    # Optionally: you can attach/return meta.copyright for display per docs.
    return panorama


if __name__ == "__main__":
    API_KEY = os.getenv("GOOGLE_API_KEY", None)
    token = create_session_token(API_KEY, map_type="streetview", language="zh-CN", region="CN")
    s = requests.Session()

    # 2) 批量查 panoId（最多 100 个点）
    pano_ids_url = "https://tile.googleapis.com/v1/streetview/panoIds"
    payload = {
        "locations": [
            {"lat": 31.238068, "lng": 121.501901},  # 上海外滩
            {"lat": 31.224361, "lng": 121.469170},  # 人民广场
        ],
        "radius": 50000,
    }
    resp = s.post(
        pano_ids_url,
        params={"session": token.session, "key": API_KEY},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    pano_ids = resp.json().get("panoIds", [])
    print("panoIds:", pano_ids)

    img = get_panorama(
        pano_id=pano_ids[0],
        api_key=API_KEY,
        session_token=token,
        zoom=3,
        multi_threaded=True,
    )
    img.save("pano.png")

